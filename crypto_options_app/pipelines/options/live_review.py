from __future__ import annotations

import json
import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_options_app.data_nodes.polymarket_crypto.markets import fetch_gamma_event_by_slug, normalize_polymarket_crypto_events
from crypto_options_app.pipelines.options.labels import build_event_labels
from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.runtime.local_paths import resolve_shared_root


CRYPTO_OPTIONS_LIVE_REVIEW_SCHEMA_VERSION = "crypto_options_live_decision_review_v1"
CRYPTO_OPTIONS_LIVE_GATE_AGGREGATE_SCHEMA_VERSION = "crypto_options_live_gate_aggregate_v1"
POLYMARKET_CRYPTO_TAKER_FEE_RATE = 0.07
REFERENCE_LADDER_BANDS = (
    ("cheap_convex_ladder", 0.20, 0.40),
    ("lower_mid_ladder", 0.40, 0.55),
    ("upper_mid_ladder", 0.55, 0.70),
    ("dominant_side_ladder", 0.70, 0.90),
    ("late_high_confidence_ladder", 0.90, 0.98),
)


def build_live_decision_review(
    capture_dir: str | Path,
    *,
    generated_at: datetime | None = None,
    max_budget_usd: float = 20.0,
    min_order_size: float = 5.0,
    max_quote_age_seconds: float = 20.0,
    max_quote_history_seconds: float | None = None,
    max_quote_rows: int | None = None,
    fetch_settlements: bool = False,
    max_settlement_fetches: int = 60,
    settlement_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build read-only trade/no-trade review rows from live capture artifacts.

    The output is a decision-review artifact, not an execution instruction. It
    intentionally encodes budget and no-trade gates but never authorizes orders.
    """

    generated_at = generated_at or datetime.now(timezone.utc)
    root = Path(capture_dir)
    metadata = _read_json(root / "metadata.json")
    status = _read_json(root / "status.json")
    quotes = _load_quote_rows(
        root / "polymarket_ws_normalized.jsonl",
        generated_at=generated_at,
        max_history_seconds=max_quote_history_seconds,
        max_rows=max_quote_rows,
    )
    ticker_rows = _read_jsonl(root / "binance_ticker.jsonl")
    account_rows = _read_jsonl(root / "account_snapshots.jsonl")
    ticker_frame = _ticker_frame(ticker_rows)
    account_context = _account_context(account_rows)
    blockers: list[str] = []
    warnings: list[str] = []
    if not root.exists():
        blockers.append("capture_dir_missing")
    if quotes.empty:
        blockers.append("no_polymarket_quote_rows")
    if not ticker_rows:
        warnings.append("no_underlying_proxy_ticks")
    if not account_rows:
        warnings.append("no_profile_account_snapshots")
    if status.get("last_error"):
        warnings.append(f"capture_last_error:{status.get('last_error')}")

    latest_quotes = _latest_quotes(quotes)
    outcome_reviews = [
        _outcome_review(row, generated_at=generated_at, max_budget_usd=max_budget_usd, min_order_size=min_order_size)
        for row in latest_quotes.to_dict(orient="records")
    ]
    market_reviews = _market_reviews(outcome_reviews, max_budget_usd=max_budget_usd, min_order_size=min_order_size)
    candidate_reviews = _candidate_reviews(
        outcome_reviews,
        market_reviews,
        max_budget_usd=max_budget_usd,
        min_order_size=min_order_size,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    candidate_reviews = _apply_feature_attribution(candidate_reviews, ticker_frame, account_context)
    candidate_reviews = _add_profile_filtered_candidates(candidate_reviews)
    settlement_labels, settlement_attempts = _settlement_labels(
        outcome_reviews,
        generated_at=generated_at,
        fetch_settlements=fetch_settlements,
        max_settlement_fetches=max_settlement_fetches,
        settlement_events=settlement_events,
    )
    candidate_reviews = _apply_settlement_reconciliation(candidate_reviews, settlement_labels, min_order_size=min_order_size)
    rolling_candidate_reviews = _rolling_candidate_reviews(
        quotes,
        max_budget_usd=max_budget_usd,
        min_order_size=min_order_size,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    rolling_candidate_reviews = _apply_feature_attribution(rolling_candidate_reviews, ticker_frame, account_context)
    rolling_candidate_reviews = _add_profile_filtered_candidates(rolling_candidate_reviews)
    rolling_candidate_reviews = _apply_settlement_reconciliation(rolling_candidate_reviews, settlement_labels, min_order_size=min_order_size)
    shadow_metrics = _live_shadow_metrics(
        rolling_candidate_reviews,
        max_budget_usd=max_budget_usd,
        hard_stop_full_losses=2,
    )
    ready_candidates = [
        row
        for row in candidate_reviews
        if row.get("status") == "candidate_for_manual_review" and not row.get("validation_only")
    ]
    validation_candidates = [
        row
        for row in candidate_reviews
        if row.get("status") == "candidate_for_manual_review" and row.get("validation_only")
    ]
    payload = {
        "schema_version": CRYPTO_OPTIONS_LIVE_REVIEW_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "capture_dir": str(root),
        "capture_status": status,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "budget_policy": {
            "max_budget_usd": float(max_budget_usd),
            "min_order_size": float(min_order_size),
            "max_simultaneous_live_positions": 1,
            "max_orders_per_5m_window": 1,
            "hard_stop_recommended_full_losses": 2,
            "minimum_viable_test": "one 5-share position at a time; no ladders; no overlap; manual approval only",
        },
        "data_counts": {
            "quote_rows": int(len(quotes)),
            "latest_outcome_quotes": int(len(outcome_reviews)),
            "market_reviews": int(len(market_reviews)),
            "candidate_reviews": int(len(candidate_reviews)),
            "ready_candidate_count": int(len(ready_candidates)),
            "validation_candidate_count": int(len(validation_candidates)),
            "binance_tick_rows": int(len(ticker_rows)),
            "account_snapshot_rows": int(len(account_rows)),
            "settlement_label_rows": int(len(settlement_labels)),
            "settled_candidate_rows": int(sum(1 for row in candidate_reviews if row.get("settlement_status") == "settled")),
            "pending_settlement_candidate_rows": int(sum(1 for row in candidate_reviews if row.get("settlement_status") == "pending")),
            "rolling_candidate_rows": int(len(rolling_candidate_reviews)),
            "rolling_settled_candidate_rows": int(
                sum(1 for row in rolling_candidate_reviews if row.get("settlement_status") == "settled")
            ),
            "rolling_trade_candidate_rows": int(
                sum(1 for row in rolling_candidate_reviews if row.get("status") == "candidate_for_manual_review")
            ),
        },
        "settlement_attempts": settlement_attempts[:250],
        "settlement_labels": strict_jsonable(settlement_labels.head(250).to_dict(orient="records")) if not settlement_labels.empty else [],
        "latest_underlying_proxy_ticks": _latest_ticks(ticker_rows),
        "account_context": account_context,
        "strategy_framework": [
            {
                "strategy_id": "late_convergence_min_size",
                "purpose": "Bonereaper/wuhuuuuuuli-style near-window evidence collection for high-probability side only.",
                "core_gates": ["quote_age", "time_remaining", "executable_ask", "spread", "top_of_book_depth", "budget"],
            },
            {
                "strategy_id": "cheap_tail_min_size",
                "purpose": "0xb55fa/baloneigh-style asymmetric tail/reversal evidence collection at tiny notional.",
                "core_gates": ["quote_age", "cheap_executable_ask", "spread", "top_of_book_depth", "budget"],
            },
            {
                "strategy_id": "two_sided_box_check",
                "purpose": "Check whether buying both outcomes is net-profitable after fees; expected to be rare.",
                "core_gates": ["both_outcomes_present", "ask_sum_after_fees_below_payout", "budget"],
            },
            {
                "strategy_id": "reference_ladder_all_bands_min_size",
                "purpose": "Bonereaper-style validation-only ladder across cheap, mid, dominant, and high-confidence prices.",
                "core_gates": ["quote_age", "time_remaining", "executable_ask_20c_98c", "spread", "top_of_book_depth", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "reference_ladder_convex_tail_min_size",
                "purpose": "Validation-only cheap convex sub-family matching repeated low-price accumulation.",
                "core_gates": ["quote_age", "time_remaining", "executable_ask_20c_40c", "spread", "top_of_book_depth", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "reference_ladder_mid_min_size",
                "purpose": "Validation-only mid-price sub-family matching 40c-70c ladder entries.",
                "core_gates": ["quote_age", "time_remaining", "executable_ask_40c_70c", "spread", "top_of_book_depth", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "reference_ladder_dominant_min_size",
                "purpose": "Validation-only dominant-side sub-family matching 70c-98c entries.",
                "core_gates": ["quote_age", "time_remaining", "executable_ask_70c_98c", "spread", "top_of_book_depth", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "profile_mid_fade_60s_validation",
                "purpose": "Profile-mined candidate: mid-price ladder entry on the side fading the last 60s underlying proxy move.",
                "core_gates": ["reference_mid_ladder", "opposes_60s_proxy_return", "time_remaining_20_300s", "spread_2c", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "profile_mid_ask_gt_50_validation",
                "purpose": "Profile-mined candidate: mid-price ladder entries above 50c with tight spreads.",
                "core_gates": ["reference_mid_ladder", "ask_above_50c", "spread_2c", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "profile_ladder_late_20_120_validation",
                "purpose": "Profile-mined candidate: broad ladder entries in the final 20-120 seconds with tight spreads.",
                "core_gates": ["reference_all_ladder", "time_remaining_20_120s", "spread_2c", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "profile_mid_fade_lower_mid_validation",
                "purpose": "Profile-mined candidate: lower-mid price ladder entry on the side fading the last 60s underlying proxy move.",
                "core_gates": [
                    "reference_mid_ladder",
                    "lower_mid_ladder_band",
                    "opposes_60s_proxy_return",
                    "time_remaining_20_300s",
                    "spread_2c",
                    "budget",
                ],
                "validation_only": True,
            },
            {
                "strategy_id": "profile_late_btc_deep_60_120_validation",
                "purpose": "Profile-mined candidate: BTC-only broad ladder entries 60-120 seconds before close with deep executable asks.",
                "core_gates": ["reference_all_ladder", "btc_only", "time_remaining_60_120s", "depth_100plus", "spread_1c_2c", "budget"],
                "validation_only": True,
            },
            {
                "strategy_id": "profile_convex_tail_depth_20_100_validation",
                "purpose": "Profile-mined candidate: cheap convex entries only where top-three ask depth is present but not overcrowded.",
                "core_gates": ["reference_convex_tail_ladder", "depth_20_100", "spread_2c", "budget"],
                "validation_only": True,
            },
        ],
        "outcome_reviews": outcome_reviews[:250],
        "market_reviews": market_reviews[:250],
        "candidate_reviews": candidate_reviews[:500],
        "rolling_shadow_metrics": shadow_metrics,
        "rolling_decision_preview": rolling_candidate_reviews[-250:],
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "next_required_development": _next_required_development(blockers, candidate_reviews),
    }
    return strict_jsonable(payload)


def write_live_decision_review_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_dir) if output_dir else resolve_shared_root() / "artifacts" / "crypto-options-research" / day
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_live_decision_review_{stamp}.json"
    md_path = root / f"crypto_options_live_decision_review_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_live_decision_review_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def build_live_gate_aggregate(
    capture_dirs: list[str | Path],
    *,
    generated_at: datetime | None = None,
    max_budget_usd: float = 20.0,
    min_order_size: float = 5.0,
    max_quote_age_seconds: float = 90.0,
    fetch_settlements: bool = False,
    max_settlement_fetches_per_capture: int = 60,
    settlement_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate rolling gate evidence across completed read-only capture dirs."""

    generated_at = generated_at or datetime.now(timezone.utc)
    all_rows: list[dict[str, Any]] = []
    capture_summaries: list[dict[str, Any]] = []
    settlement_attempts: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    quote_rows_total = 0
    settlement_label_rows_total = 0
    for raw_dir in capture_dirs:
        root = Path(raw_dir)
        metadata = _read_json(root / "metadata.json")
        status = _read_json(root / "status.json")
        quotes = _load_quote_rows(root / "polymarket_ws_normalized.jsonl")
        ticker_rows = _read_jsonl(root / "binance_ticker.jsonl")
        account_rows = _read_jsonl(root / "account_snapshots.jsonl")
        ticker_frame = _ticker_frame(ticker_rows)
        account_context = _account_context(account_rows)
        if not root.exists():
            blockers.append(f"capture_dir_missing:{root}")
        if quotes.empty:
            blockers.append(f"no_polymarket_quote_rows:{root}")
        if status.get("last_error"):
            warnings.append(f"{root.name}:capture_last_error:{status.get('last_error')}")

        latest_quotes = _latest_quotes(quotes)
        outcome_reviews = [
            _outcome_review(row, generated_at=generated_at, max_budget_usd=max_budget_usd, min_order_size=min_order_size)
            for row in latest_quotes.to_dict(orient="records")
        ]
        settlement_labels, attempts = _settlement_labels(
            outcome_reviews,
            generated_at=generated_at,
            fetch_settlements=fetch_settlements,
            max_settlement_fetches=max_settlement_fetches_per_capture,
            settlement_events=settlement_events,
        )
        rolling_rows = _rolling_candidate_reviews(
            quotes,
            max_budget_usd=max_budget_usd,
            min_order_size=min_order_size,
            max_quote_age_seconds=max_quote_age_seconds,
            trade_candidates_only=True,
        )
        rolling_rows = _apply_feature_attribution(rolling_rows, ticker_frame, account_context)
        rolling_rows = _add_profile_filtered_candidates(rolling_rows)
        rolling_rows = _apply_settlement_reconciliation(rolling_rows, settlement_labels, min_order_size=min_order_size)
        for row in rolling_rows:
            row["source_capture_dir"] = str(root)
        all_rows.extend(rolling_rows)
        quote_rows_total += int(len(quotes))
        settlement_label_rows_total += int(len(settlement_labels))
        settlement_attempts.extend({"capture_dir": str(root), **attempt} for attempt in attempts)
        capture_summaries.append(
            {
                "capture_dir": str(root),
                "status": status.get("status"),
                "started_at_utc": status.get("started_at_utc"),
                "completed_at_utc": status.get("completed_at_utc"),
                "target_count": status.get("target_count") or metadata.get("target_count"),
                "quote_rows": int(len(quotes)),
                "binance_tick_rows": int(len(ticker_rows)),
                "account_snapshot_rows": int(len(account_rows)),
                "settlement_label_rows": int(len(settlement_labels)),
                "rolling_candidate_rows": int(len(rolling_rows)),
                "rolling_settled_candidate_rows": int(
                    sum(1 for row in rolling_rows if row.get("settlement_status") == "settled")
                ),
                "rolling_trade_candidate_rows": int(
                    sum(1 for row in rolling_rows if row.get("status") == "candidate_for_manual_review")
                ),
                "last_error": status.get("last_error"),
                "orders_allowed": False,
            }
        )

    shadow_metrics = _live_shadow_metrics(all_rows, max_budget_usd=max_budget_usd, hard_stop_full_losses=2)
    payload = {
        "schema_version": CRYPTO_OPTIONS_LIVE_GATE_AGGREGATE_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "capture_dirs": [str(Path(path)) for path in capture_dirs],
        "capture_summaries": capture_summaries,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "budget_policy": {
            "max_budget_usd": float(max_budget_usd),
            "min_order_size": float(min_order_size),
            "max_simultaneous_live_positions": 1,
            "max_orders_per_5m_window": 1,
            "hard_stop_recommended_full_losses": 2,
            "minimum_viable_test": "one 5-share position at a time; no ladders; no overlap; manual approval only",
        },
        "data_counts": {
            "capture_count": int(len(capture_dirs)),
            "quote_rows": int(quote_rows_total),
            "settlement_label_rows": int(settlement_label_rows_total),
            "rolling_candidate_rows": int(len(all_rows)),
            "rolling_settled_candidate_rows": int(sum(1 for row in all_rows if row.get("settlement_status") == "settled")),
            "rolling_trade_candidate_rows": int(sum(1 for row in all_rows if row.get("status") == "candidate_for_manual_review")),
        },
        "settlement_attempts": settlement_attempts[:500],
        "rolling_shadow_metrics": shadow_metrics,
        "rolling_decision_preview": all_rows[-250:],
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "next_required_development": _next_required_development(blockers, all_rows),
    }
    return strict_jsonable(payload)


def write_live_gate_aggregate_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_dir) if output_dir else resolve_shared_root() / "artifacts" / "crypto-options-research" / day
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_live_gate_aggregate_{stamp}.json"
    md_path = root / f"crypto_options_live_gate_aggregate_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_live_gate_aggregate_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_live_gate_aggregate_markdown(payload: dict[str, Any]) -> str:
    counts = payload.get("data_counts") or {}
    lines = [
        "# Crypto Options Live Gate Aggregate",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Capture count: `{counts.get('capture_count')}`",
        f"- Live trading authorized: `{payload.get('live_trading_authorized')}`",
        f"- Orders allowed: `{payload.get('orders_allowed')}`",
        f"- Quote rows: `{counts.get('quote_rows')}`",
        f"- Rolling trade candidate rows: `{counts.get('rolling_trade_candidate_rows')}`",
        f"- Rolling settled candidate rows: `{counts.get('rolling_settled_candidate_rows')}`",
        "",
        "## Captures",
        "",
    ]
    for row in payload.get("capture_summaries") or []:
        lines.append(
            "- `{name}` status=`{status}` quotes=`{quotes}` settled_labels=`{labels}` trade_candidates=`{trade_candidates}`".format(
                name=Path(str(row.get("capture_dir"))).name,
                status=row.get("status"),
                quotes=row.get("quote_rows"),
                labels=row.get("settlement_label_rows"),
                trade_candidates=row.get("rolling_trade_candidate_rows"),
            )
        )
    shadow = payload.get("rolling_shadow_metrics") or {}
    strategy_rows = shadow.get("strategies") or []
    if strategy_rows:
        lines.extend(["", "## Aggregate Gate Metrics", ""])
        for row in strategy_rows:
            metrics = row.get("trade_metrics") or {}
            budget = row.get("budget_simulation") or {}
            gate_report = row.get("minimum_live_test_gate_report") or {}
            lines.append(
                "- `{strategy}` deduped_trades=`{trades}` win_rate=`{win_rate}` return_sum=`{return_sum}` final_budget=`{budget}` ready=`{ready}` failed_gates=`{failed}`".format(
                    strategy=row.get("strategy_id"),
                    trades=row.get("deduped_trade_count"),
                    win_rate=metrics.get("win_rate"),
                    return_sum=metrics.get("return_sum"),
                    budget=budget.get("final_budget"),
                    ready=row.get("minimum_live_test_ready"),
                    failed=gate_report.get("failed_gates") or [],
                )
            )
    if payload.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend([f"- `{blocker}`" for blocker in payload.get("blockers") or []])
    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend([f"- `{warning}`" for warning in payload.get("warnings") or []])
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This aggregate is a read-only evidence report. It does not authorize live trading, order placement, cancellation, signing, broadcasting, redeeming, or portfolio action.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_live_decision_review_markdown(payload: dict[str, Any]) -> str:
    counts = payload.get("data_counts") or {}
    candidates = payload.get("candidate_reviews") or []
    ready = [row for row in candidates if row.get("status") == "candidate_for_manual_review"]
    lines = [
        "# Crypto Options Live Decision Review",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Capture dir: `{payload.get('capture_dir')}`",
        f"- Live trading authorized: `{payload.get('live_trading_authorized')}`",
        f"- Orders allowed: `{payload.get('orders_allowed')}`",
        f"- Quote rows: `{counts.get('quote_rows')}`",
        f"- Latest outcome quotes: `{counts.get('latest_outcome_quotes')}`",
        f"- Ready manual-review candidates: `{counts.get('ready_candidate_count')}`",
        f"- Settlement labels: `{counts.get('settlement_label_rows')}`",
        f"- Settled candidate rows: `{counts.get('settled_candidate_rows')}`",
        f"- Rolling trade candidate rows: `{counts.get('rolling_trade_candidate_rows')}`",
        "",
        "## Budget Policy",
        "",
        f"```json\n{json.dumps(payload.get('budget_policy') or {}, indent=2, sort_keys=True)}\n```",
        "",
        "## Candidate Preview",
        "",
    ]
    preview = ready[:12] or candidates[:12]
    if not preview:
        lines.append("- No candidate rows were generated.")
    for row in preview:
        lines.append(
            "- `{strategy}` `{status}` `{symbol}` `{event_slug}` `{outcome}` ask=`{ask}` spread=`{spread}` cost=`{cost}` reasons=`{reasons}`".format(
                strategy=row.get("strategy_id"),
                status=row.get("status"),
                symbol=row.get("symbol"),
                event_slug=row.get("event_slug"),
                outcome=row.get("outcome"),
                ask=row.get("best_ask"),
                spread=row.get("spread"),
                cost=row.get("min_order_total_cost"),
                reasons=row.get("reasons") or [],
            )
        )
        if row.get("settlement_status") == "settled":
            lines.append(
                "  - settlement=`{outcome}` would_win=`{win}` pnl=`{pnl}` authority=`{authority}`".format(
                    outcome=row.get("resolved_outcome"),
                    win=row.get("would_win"),
                    pnl=row.get("hypothetical_hold_to_settlement_pnl_net"),
                    authority=row.get("label_authority"),
                )
            )
    if payload.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend([f"- `{blocker}`" for blocker in payload.get("blockers") or []])
    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend([f"- `{warning}`" for warning in payload.get("warnings") or []])
    shadow = payload.get("rolling_shadow_metrics") or {}
    strategy_rows = shadow.get("strategies") or []
    if strategy_rows:
        lines.extend(["", "## Rolling Shadow Metrics", ""])
        for row in strategy_rows:
            metrics = row.get("trade_metrics") or {}
            budget = row.get("budget_simulation") or {}
            gate_report = row.get("minimum_live_test_gate_report") or {}
            lines.append(
                "- `{strategy}` settled=`{settled}` raw_candidates=`{raw}` deduped_trades=`{trades}` return_sum=`{return_sum}` max_loss_streak=`{losses}` final_budget=`{budget}` ready=`{ready}` failed_gates=`{failed}`".format(
                    strategy=row.get("strategy_id"),
                    settled=row.get("settled_rows"),
                    raw=row.get("raw_trade_candidate_rows"),
                    trades=row.get("deduped_trade_count"),
                    return_sum=metrics.get("return_sum"),
                    losses=metrics.get("max_sequential_losses"),
                    budget=budget.get("final_budget"),
                    ready=row.get("minimum_live_test_ready"),
                    failed=gate_report.get("failed_gates") or [],
                )
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This artifact is a read-only decision review. It does not authorize live trading, order placement, cancellation, signing, broadcasting, redeeming, or portfolio action.",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_quote_rows(
    path: Path,
    *,
    generated_at: datetime | None = None,
    max_history_seconds: float | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    rows = _read_jsonl(path, max_lines=max_rows)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    for column in (
        "best_bid",
        "best_ask",
        "spread",
        "ask_size",
        "bid_size",
        "depth_top3_ask_size",
        "depth_top3_bid_size",
        "mid_price",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("observed_at", "received_at_utc", "window_start_time", "window_end_time"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    if generated_at is not None and max_history_seconds is not None:
        cutoff = pd.Timestamp(generated_at) - pd.Timedelta(seconds=max(0.0, float(max_history_seconds)))
        quote_at = frame.get("observed_at")
        if quote_at is None:
            quote_at = frame.get("received_at_utc")
        elif "received_at_utc" in frame.columns:
            quote_at = quote_at.fillna(frame["received_at_utc"])
        if quote_at is not None:
            frame = frame[quote_at >= cutoff]
    if "event_slug" not in frame.columns:
        frame["event_slug"] = None
    if "outcome" not in frame.columns:
        frame["outcome"] = None
    if "symbol" not in frame.columns:
        frame["symbol"] = None
    return frame.dropna(subset=["event_slug", "outcome"], how="any")


def _latest_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return quotes
    frame = quotes.copy()
    frame["_sort_at"] = frame.get("observed_at")
    if "received_at_utc" in frame.columns:
        frame["_sort_at"] = frame["_sort_at"].fillna(frame["received_at_utc"])
    frame = frame.sort_values(["event_slug", "outcome", "_sort_at"], kind="mergesort")
    return frame.groupby(["event_slug", "outcome"], as_index=False, dropna=False).tail(1).drop(columns=["_sort_at"], errors="ignore")


def _outcome_review(
    row: dict[str, Any],
    *,
    generated_at: datetime,
    max_budget_usd: float,
    min_order_size: float,
) -> dict[str, Any]:
    ask = _float(row.get("best_ask"))
    bid = _float(row.get("best_bid"))
    spread = _float(row.get("spread"))
    observed_at = _timestamp(row.get("observed_at"))
    received_at = _timestamp(row.get("received_at_utc"))
    window_start = _timestamp(row.get("window_start_time"))
    window_end = _timestamp(row.get("window_end_time"))
    quote_at = observed_at or received_at
    fee_per_share = taker_fee_per_share(ask) if ask is not None else None
    min_order_total_cost = min_order_size * (ask + fee_per_share) if ask is not None and fee_per_share is not None else None
    return {
        "symbol": row.get("symbol"),
        "event_slug": row.get("event_slug"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "condition_id": row.get("condition_id"),
        "token_id": row.get("token_id"),
        "outcome": row.get("outcome"),
        "window_start_time": _iso(window_start),
        "window_end_time": _iso(window_end),
        "quote_at_utc": _iso(quote_at),
        "quote_age_seconds": _age_seconds(generated_at, quote_at),
        "time_remaining_seconds": _seconds_between(window_end, generated_at) if window_end else None,
        "best_bid": bid,
        "best_ask": ask,
        "spread": spread,
        "ask_size": _float(row.get("ask_size")),
        "bid_size": _float(row.get("bid_size")),
        "depth_top3_ask_size": _float(row.get("depth_top3_ask_size")),
        "depth_top3_bid_size": _float(row.get("depth_top3_bid_size")),
        "min_order_size": min_order_size,
        "taker_fee_per_share": fee_per_share,
        "min_order_total_cost": min_order_total_cost,
        "full_loss_budget_share": _safe_div(min_order_total_cost, max_budget_usd),
        "max_full_losses_at_budget": math.floor(max_budget_usd / min_order_total_cost) if min_order_total_cost else None,
        "price_meaning": "polymarket_executable_quote_not_underlying_price",
        "orders_allowed": False,
    }


def _market_reviews(outcome_reviews: list[dict[str, Any]], *, max_budget_usd: float, min_order_size: float) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    frame = pd.DataFrame(outcome_reviews)
    if frame.empty:
        return reviews
    for event_slug, group in frame.groupby("event_slug", dropna=False, sort=False):
        up = _first_outcome(group, "Up")
        down = _first_outcome(group, "Down")
        if not up or not down:
            reviews.append(
                {
                    "event_slug": event_slug,
                    "status": "blocked",
                    "reason": "missing_up_or_down_latest_quote",
                    "orders_allowed": False,
                }
            )
            continue
        up_cost = _float(up.get("min_order_total_cost"))
        down_cost = _float(down.get("min_order_total_cost"))
        total_cost = up_cost + down_cost if up_cost is not None and down_cost is not None else None
        guaranteed_payout = float(min_order_size)
        quote_ages = [_float(up.get("quote_age_seconds")), _float(down.get("quote_age_seconds"))]
        quote_ages = [age for age in quote_ages if age is not None]
        reviews.append(
            {
                "symbol": up.get("symbol") or down.get("symbol"),
                "event_slug": event_slug,
                "window_start_time": up.get("window_start_time") or down.get("window_start_time"),
                "window_end_time": up.get("window_end_time") or down.get("window_end_time"),
                "up_ask": up.get("best_ask"),
                "down_ask": down.get("best_ask"),
                "up_min_order_total_cost": up_cost,
                "down_min_order_total_cost": down_cost,
                "two_sided_min_order_total_cost": total_cost,
                "two_sided_guaranteed_payout": guaranteed_payout,
                "two_sided_edge_after_fees": guaranteed_payout - total_cost if total_cost is not None else None,
                "two_sided_budget_ok": bool(total_cost is not None and total_cost <= max_budget_usd),
                "max_quote_age_seconds": max(quote_ages) if quote_ages else None,
                "orders_allowed": False,
            }
        )
    return reviews


def _candidate_reviews(
    outcome_reviews: list[dict[str, Any]],
    market_reviews: list[dict[str, Any]],
    *,
    max_budget_usd: float,
    min_order_size: float,
    max_quote_age_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for review in outcome_reviews:
        rows.append(_late_convergence_candidate(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds))
        rows.append(_cheap_tail_candidate(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds))
        rows.extend(_reference_ladder_candidates(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds))
    for market in market_reviews:
        rows.append(
            _two_sided_box_candidate(
                market,
                max_budget_usd=max_budget_usd,
                min_order_size=min_order_size,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        )
    return rows


def _late_convergence_candidate(review: dict[str, Any], *, max_budget_usd: float, max_quote_age_seconds: float) -> dict[str, Any]:
    reasons = _base_reasons(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds)
    ask = _float(review.get("best_ask"))
    time_remaining = _float(review.get("time_remaining_seconds"))
    spread = _float(review.get("spread"))
    if time_remaining is None or time_remaining < 0 or time_remaining > 120:
        reasons.append("not_inside_last_120_seconds")
    if ask is None or ask < 0.78 or ask > 0.98:
        reasons.append("ask_not_in_late_convergence_band_0.78_0.98")
    if spread is None or spread > 0.04:
        reasons.append("spread_above_4c_or_missing")
    return _candidate_row("late_convergence_min_size", review, reasons)


def _cheap_tail_candidate(review: dict[str, Any], *, max_budget_usd: float, max_quote_age_seconds: float) -> dict[str, Any]:
    reasons = _base_reasons(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds)
    ask = _float(review.get("best_ask"))
    spread = _float(review.get("spread"))
    if ask is None or ask > 0.12:
        reasons.append("ask_above_cheap_tail_12c")
    if spread is None or spread > 0.05:
        reasons.append("spread_above_5c_or_missing")
    return _candidate_row("cheap_tail_min_size", review, reasons)


def _reference_ladder_candidates(review: dict[str, Any], *, max_budget_usd: float, max_quote_age_seconds: float) -> list[dict[str, Any]]:
    return [
        _reference_ladder_candidate(
            "reference_ladder_all_bands_min_size",
            review,
            max_budget_usd=max_budget_usd,
            max_quote_age_seconds=max_quote_age_seconds,
            ask_min=0.20,
            ask_max=0.98,
            spread_max=0.05,
            depth_top3_min=25.0,
            time_remaining_min=20.0,
            time_remaining_max=900.0,
        ),
        _reference_ladder_candidate(
            "reference_ladder_convex_tail_min_size",
            review,
            max_budget_usd=max_budget_usd,
            max_quote_age_seconds=max_quote_age_seconds,
            ask_min=0.20,
            ask_max=0.40,
            spread_max=0.05,
            depth_top3_min=25.0,
            time_remaining_min=20.0,
            time_remaining_max=900.0,
        ),
        _reference_ladder_candidate(
            "reference_ladder_mid_min_size",
            review,
            max_budget_usd=max_budget_usd,
            max_quote_age_seconds=max_quote_age_seconds,
            ask_min=0.40,
            ask_max=0.70,
            spread_max=0.03,
            depth_top3_min=25.0,
            time_remaining_min=20.0,
            time_remaining_max=900.0,
        ),
        _reference_ladder_candidate(
            "reference_ladder_dominant_min_size",
            review,
            max_budget_usd=max_budget_usd,
            max_quote_age_seconds=max_quote_age_seconds,
            ask_min=0.70,
            ask_max=0.98,
            spread_max=0.04,
            depth_top3_min=25.0,
            time_remaining_min=20.0,
            time_remaining_max=900.0,
        ),
    ]


def _reference_ladder_candidate(
    strategy_id: str,
    review: dict[str, Any],
    *,
    max_budget_usd: float,
    max_quote_age_seconds: float,
    ask_min: float,
    ask_max: float,
    spread_max: float,
    depth_top3_min: float,
    time_remaining_min: float,
    time_remaining_max: float,
) -> dict[str, Any]:
    reasons = _base_reasons(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds)
    ask = _float(review.get("best_ask"))
    spread = _float(review.get("spread"))
    depth = _float(review.get("depth_top3_ask_size"))
    time_remaining = _float(review.get("time_remaining_seconds"))
    if ask is None or ask < ask_min or ask > ask_max:
        reasons.append(f"ask_not_in_reference_ladder_band_{ask_min:.2f}_{ask_max:.2f}")
    if spread is None or spread > spread_max:
        reasons.append(f"spread_above_{spread_max:.2f}_or_missing")
    if depth is None or depth < depth_top3_min:
        reasons.append(f"depth_top3_ask_below_{depth_top3_min:g}_or_missing")
    if time_remaining is None or time_remaining < time_remaining_min or time_remaining > time_remaining_max:
        reasons.append(f"time_remaining_not_in_{time_remaining_min:g}_{time_remaining_max:g}s")
    row = _candidate_row(strategy_id, review, reasons)
    row.update(
        {
            "validation_only": True,
            "not_live_test_eligible": True,
            "reference_ladder_band": _reference_ladder_band(ask),
            "candidate_priority_score": _reference_ladder_priority(strategy_id, ask, spread, depth),
        }
    )
    return row


def _two_sided_box_candidate(
    market: dict[str, Any],
    *,
    max_budget_usd: float,
    min_order_size: float,
    max_quote_age_seconds: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    total_cost = _float(market.get("two_sided_min_order_total_cost"))
    edge = _float(market.get("two_sided_edge_after_fees"))
    if total_cost is None:
        reasons.append("missing_two_sided_cost")
    elif total_cost > max_budget_usd:
        reasons.append("two_sided_cost_above_budget")
    if edge is None or edge <= 0:
        reasons.append("no_positive_box_edge_after_fees")
    quote_age = _float(market.get("max_quote_age_seconds"))
    if quote_age is None or quote_age > max_quote_age_seconds:
        reasons.append("quote_stale")
    row = {
        "strategy_id": "two_sided_box_check",
        "status": "candidate_for_manual_review" if not reasons else "no_trade",
        "symbol": market.get("symbol"),
        "event_slug": market.get("event_slug"),
        "outcome": "Up+Down",
        "window_start_time": market.get("window_start_time"),
        "window_end_time": market.get("window_end_time"),
        "best_ask": None,
        "spread": None,
        "min_order_size": min_order_size,
        "min_order_total_cost": total_cost,
        "expected_payout_if_box": market.get("two_sided_guaranteed_payout"),
        "expected_edge_after_fees": edge,
        "quote_age_seconds": quote_age,
        "reasons": reasons,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
    return row


def _base_reasons(review: dict[str, Any], *, max_budget_usd: float, max_quote_age_seconds: float) -> list[str]:
    reasons: list[str] = []
    if _float(review.get("best_ask")) is None:
        reasons.append("missing_executable_ask")
    if _float(review.get("ask_size")) is None or _float(review.get("ask_size")) < _float(review.get("min_order_size")):
        reasons.append("top_ask_size_below_min_order")
    if _float(review.get("min_order_total_cost")) is None or _float(review.get("min_order_total_cost")) > max_budget_usd:
        reasons.append("min_order_cost_above_budget")
    if _float(review.get("quote_age_seconds")) is None or _float(review.get("quote_age_seconds")) > max_quote_age_seconds:
        reasons.append("quote_stale")
    return reasons


def _candidate_row(strategy_id: str, review: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "status": "candidate_for_manual_review" if not reasons else "no_trade",
        "symbol": review.get("symbol"),
        "event_slug": review.get("event_slug"),
        "event_id": review.get("event_id"),
        "market_id": review.get("market_id"),
        "condition_id": review.get("condition_id"),
        "token_id": review.get("token_id"),
        "outcome": review.get("outcome"),
        "window_start_time": review.get("window_start_time"),
        "window_end_time": review.get("window_end_time"),
        "quote_at_utc": review.get("quote_at_utc"),
        "quote_age_seconds": review.get("quote_age_seconds"),
        "time_remaining_seconds": review.get("time_remaining_seconds"),
        "best_bid": review.get("best_bid"),
        "best_ask": review.get("best_ask"),
        "spread": review.get("spread"),
        "ask_size": review.get("ask_size"),
        "depth_top3_ask_size": review.get("depth_top3_ask_size"),
        "min_order_size": review.get("min_order_size"),
        "min_order_total_cost": review.get("min_order_total_cost"),
        "max_full_losses_at_budget": review.get("max_full_losses_at_budget"),
        "reasons": reasons,
        "validation_only": False,
        "not_live_test_eligible": False,
        "candidate_priority_score": 0.0,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _rolling_candidate_reviews(
    quotes: pd.DataFrame,
    *,
    max_budget_usd: float,
    min_order_size: float,
    max_quote_age_seconds: float,
    sample_seconds: int = 15,
    trade_candidates_only: bool = False,
) -> list[dict[str, Any]]:
    if quotes.empty:
        return []
    frame = quotes.copy()
    frame["_decision_at"] = frame.get("observed_at")
    if "received_at_utc" in frame.columns:
        frame["_decision_at"] = frame["_decision_at"].fillna(frame["received_at_utc"])
    frame = frame.dropna(subset=["_decision_at", "event_slug", "outcome"])
    if frame.empty:
        return []
    frame["_decision_bucket"] = frame["_decision_at"].dt.floor(f"{int(sample_seconds)}s")
    frame = frame.sort_values(["event_slug", "outcome", "_decision_bucket", "_decision_at"], kind="mergesort")
    sampled = frame.groupby(["event_slug", "outcome", "_decision_bucket"], as_index=False, dropna=False).tail(1)
    rows: list[dict[str, Any]] = []
    for record in sampled.to_dict(orient="records"):
        decision_at = _timestamp(record.get("_decision_at"))
        if decision_at is None:
            continue
        review = _outcome_review(
            record,
            generated_at=decision_at,
            max_budget_usd=max_budget_usd,
            min_order_size=min_order_size,
        )
        review["decision_bucket_utc"] = _iso(_timestamp(record.get("_decision_bucket")))
        rows.append(_late_convergence_candidate(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds))
        rows.append(_cheap_tail_candidate(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds))
        rows.extend(_reference_ladder_candidates(review, max_budget_usd=max_budget_usd, max_quote_age_seconds=max_quote_age_seconds))
    if trade_candidates_only:
        rows = [row for row in rows if row.get("status") == "candidate_for_manual_review"]
    return rows


def _live_shadow_metrics(
    candidate_reviews: list[dict[str, Any]],
    *,
    max_budget_usd: float,
    hard_stop_full_losses: int,
) -> dict[str, Any]:
    strategies: list[dict[str, Any]] = []
    if not candidate_reviews:
        return {
            "schema_version": "crypto_options_live_shadow_metrics_v1",
            "status": "blocked",
            "blockers": ["no_rolling_candidate_rows"],
            "strategies": [],
        }
    frame = pd.DataFrame(candidate_reviews)
    for strategy_id, group in frame.groupby("strategy_id", dropna=False, sort=False):
        settled = group[group.get("settlement_status").astype(str) == "settled"] if "settlement_status" in group.columns else group.iloc[0:0]
        tradeable = settled[settled["status"].astype(str) == "candidate_for_manual_review"] if not settled.empty else settled
        deduped = _dedupe_trade_candidates(tradeable)
        trade_metrics = _shadow_trade_metrics(deduped)
        budget_simulation = _budget_simulation(
            deduped,
            starting_budget=max_budget_usd,
            hard_stop_full_losses=hard_stop_full_losses,
        )
        feature_breakdowns = _feature_breakdowns(deduped)
        feature_quality = _feature_quality(deduped)
        gate_report = _minimum_live_test_gate_report(
            strategy_id=str(strategy_id),
            metrics=trade_metrics,
            budget_simulation=budget_simulation,
            feature_breakdowns=feature_breakdowns,
            feature_quality=feature_quality,
            deduped_trade_count=int(len(deduped)),
            settled_rows=int(len(settled)),
            rows=int(len(group)),
            max_budget_usd=max_budget_usd,
            hard_stop_full_losses=hard_stop_full_losses,
        )
        strategies.append(
            {
                "strategy_id": str(strategy_id),
                "rows": int(len(group)),
                "settled_rows": int(len(settled)),
                "pending_rows": int((group.get("settlement_status").astype(str) == "pending").sum())
                if "settlement_status" in group.columns
                else 0,
                "raw_trade_candidate_rows": int(len(tradeable)),
                "deduped_trade_count": int(len(deduped)),
                "no_trade_reason_counts": _reason_counts(group.to_dict(orient="records")),
                "trade_metrics": trade_metrics,
                "budget_simulation": budget_simulation,
                "feature_breakdowns": feature_breakdowns,
                "feature_quality": feature_quality,
                "minimum_live_test_gate_report": gate_report,
                "minimum_live_test_ready": bool(gate_report.get("ready")),
            }
        )
    return {
        "schema_version": "crypto_options_live_shadow_metrics_v1",
        "status": "complete",
        "starting_budget_usd": float(max_budget_usd),
        "hard_stop_full_losses": int(hard_stop_full_losses),
        "strategies": strategies,
    }


def _dedupe_trade_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    working = frame.copy()
    working["_quote_at"] = pd.to_datetime(working.get("quote_at_utc"), utc=True, errors="coerce")
    working["_priority"] = pd.to_numeric(working.get("candidate_priority_score"), errors="coerce").fillna(0.0)
    working = working.sort_values(["strategy_id", "event_slug", "_quote_at", "_priority"], ascending=[True, True, True, False], kind="mergesort")
    return working.groupby(["strategy_id", "event_slug"], as_index=False, dropna=False).head(1).reset_index(drop=True)


def _shadow_trade_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return compute_trade_metrics(pd.DataFrame())
    trades = frame.copy()
    trades["pnl_net"] = pd.to_numeric(trades.get("hypothetical_hold_to_settlement_pnl_net"), errors="coerce")
    if "would_win" in trades.columns:
        trades["win"] = trades["would_win"].map(lambda value: bool(value) if pd.notna(value) else False)
    else:
        trades["win"] = trades["pnl_net"] > 0
    trades["sample_id"] = trades.get("event_slug")
    return compute_trade_metrics(trades.dropna(subset=["pnl_net"]), sample_column="sample_id")


def _budget_simulation(frame: pd.DataFrame, *, starting_budget: float, hard_stop_full_losses: int) -> dict[str, Any]:
    if frame.empty:
        return {
            "starting_budget": float(starting_budget),
            "final_budget": float(starting_budget),
            "min_budget": float(starting_budget),
            "executed_trades": 0,
            "budget_denied_trades": 0,
            "hard_stop_denied_trades": 0,
            "max_sequential_full_losses": 0,
        }
    working = frame.copy()
    working["_quote_at"] = pd.to_datetime(working.get("quote_at_utc"), utc=True, errors="coerce")
    working = working.sort_values("_quote_at", kind="mergesort")
    budget = float(starting_budget)
    min_budget = budget
    executed = 0
    budget_denied = 0
    hard_stop_denied = 0
    current_losses = 0
    max_losses = 0
    for row in working.to_dict(orient="records"):
        if current_losses >= int(hard_stop_full_losses):
            hard_stop_denied += 1
            continue
        cost = _float(row.get("min_order_total_cost"))
        pnl = _float(row.get("hypothetical_hold_to_settlement_pnl_net"))
        if cost is None or pnl is None or cost > budget:
            budget_denied += 1
            continue
        budget += pnl
        min_budget = min(min_budget, budget)
        executed += 1
        if pnl < 0:
            current_losses += 1
            max_losses = max(max_losses, current_losses)
        else:
            current_losses = 0
    return {
        "starting_budget": float(starting_budget),
        "final_budget": float(budget),
        "min_budget": float(min_budget),
        "executed_trades": int(executed),
        "budget_denied_trades": int(budget_denied),
        "hard_stop_denied_trades": int(hard_stop_denied),
        "max_sequential_full_losses": int(max_losses),
        "budget_return": float(budget - float(starting_budget)),
    }


def _minimum_live_test_gate_report(
    *,
    strategy_id: str,
    metrics: dict[str, Any],
    budget_simulation: dict[str, Any],
    feature_breakdowns: dict[str, Any],
    feature_quality: dict[str, Any],
    deduped_trade_count: int,
    settled_rows: int,
    rows: int,
    max_budget_usd: float,
    hard_stop_full_losses: int,
) -> dict[str, Any]:
    """Return explicit gates before a strategy can be proposed for a tiny live test."""

    gates: list[dict[str, Any]] = []

    def add_gate(name: str, passed: bool, *, observed: Any, threshold: Any) -> None:
        gates.append(
            {
                "name": name,
                "passed": bool(passed),
                "observed": observed,
                "threshold": threshold,
            }
        )

    settlement_coverage = _safe_div(float(settled_rows), float(rows)) if rows else 0.0
    win_rate = _float(metrics.get("win_rate"))
    expectancy = _float(metrics.get("expectancy"))
    return_sum = _float(metrics.get("return_sum"))
    max_losses = int(metrics.get("max_sequential_losses") or 0)
    final_budget = _float(budget_simulation.get("final_budget"))
    min_budget = _float(budget_simulation.get("min_budget"))
    starting_budget = _float(budget_simulation.get("starting_budget")) or float(max_budget_usd)
    budget_drawdown = starting_budget - min_budget if min_budget is not None else None
    max_budget_drawdown = max(2.0, float(max_budget_usd) * 0.10)
    hard_stop_denied = int(budget_simulation.get("hard_stop_denied_trades") or 0)
    underlying_coverage = _float(feature_quality.get("underlying_proxy_coverage"))

    add_gate("min_30_deduped_settled_trades", deduped_trade_count >= 30, observed=deduped_trade_count, threshold=">=30")
    add_gate("settlement_coverage_95pct", (settlement_coverage or 0.0) >= 0.95, observed=settlement_coverage, threshold=">=0.95")
    add_gate("win_rate_60pct", win_rate is not None and win_rate >= 0.60, observed=win_rate, threshold=">=0.60")
    add_gate("positive_expectancy", expectancy is not None and expectancy > 0.0, observed=expectancy, threshold=">0")
    add_gate("positive_return_sum", return_sum is not None and return_sum > 0.0, observed=return_sum, threshold=">0")
    add_gate(
        "max_loss_streak_within_hard_stop",
        max_losses <= int(hard_stop_full_losses),
        observed=max_losses,
        threshold=f"<={int(hard_stop_full_losses)}",
    )
    add_gate(
        "budget_finishes_above_start",
        final_budget is not None and final_budget > starting_budget,
        observed=final_budget,
        threshold=f">{starting_budget}",
    )
    add_gate("no_hard_stop_denied_trades", hard_stop_denied == 0, observed=hard_stop_denied, threshold="0")
    add_gate(
        "budget_drawdown_within_10pct_or_2usd",
        budget_drawdown is not None and budget_drawdown <= max_budget_drawdown,
        observed=budget_drawdown,
        threshold=f"<={max_budget_drawdown}",
    )
    add_gate(
        "underlying_proxy_coverage_80pct",
        underlying_coverage is not None and underlying_coverage >= 0.80,
        observed=underlying_coverage,
        threshold=">=0.80",
    )

    if strategy_id == "late_convergence_min_size":
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="ask_bucket",
            accepted_buckets=("90-98c", "98c+"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.50,
            min_win_rate=0.80,
            gate_prefix="late_high_conf_ask_bucket",
        )
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="spread_bucket",
            accepted_buckets=("0-1c", "1-2c"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.80,
            min_win_rate=0.80,
            gate_prefix="late_tight_spread_bucket",
        )
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="depth_top3_ask_bucket",
            accepted_buckets=("100+",),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.50,
            min_win_rate=0.80,
            gate_prefix="late_depth_100plus_bucket",
        )
    elif strategy_id == "cheap_tail_min_size":
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="ask_bucket",
            accepted_buckets=("0-10c", "10-30c"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.50,
            min_win_rate=0.20,
            gate_prefix="cheap_tail_ask_bucket",
        )
    elif strategy_id.startswith("reference_ladder_"):
        add_gate(
            "reference_ladder_validation_only_not_approved_for_live",
            False,
            observed="validation_only",
            threshold="requires separate strategy approval after larger settled sample",
        )
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="spread_bucket",
            accepted_buckets=("0-1c", "1-2c", "2-5c"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.75,
            min_win_rate=0.50,
            gate_prefix="reference_ladder_executable_spread_bucket",
        )
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="depth_top3_ask_bucket",
            accepted_buckets=("20-100", "100+"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.75,
            min_win_rate=0.50,
            gate_prefix="reference_ladder_depth_bucket",
        )
    elif strategy_id.startswith("profile_"):
        add_gate(
            "profile_filtered_validation_only_not_approved_for_live",
            False,
            observed="validation_only",
            threshold="requires repeated settled validation and explicit promotion",
        )
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="spread_bucket",
            accepted_buckets=("0-1c", "1-2c"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.75,
            min_win_rate=0.60,
            gate_prefix="profile_filtered_tight_spread_bucket",
        )
        _add_bucket_gate(
            gates,
            feature_breakdowns,
            column="depth_top3_ask_bucket",
            accepted_buckets=("20-100", "100+"),
            deduped_trade_count=deduped_trade_count,
            min_trade_share=0.75,
            min_win_rate=0.60,
            gate_prefix="profile_filtered_depth_bucket",
        )

    failed = [gate["name"] for gate in gates if not gate.get("passed")]
    return {
        "schema_version": "crypto_options_minimum_live_test_gate_v1",
        "ready": not failed,
        "failed_gates": failed,
        "gates": gates,
        "policy": {
            "max_budget_usd": float(max_budget_usd),
            "hard_stop_full_losses": int(hard_stop_full_losses),
            "note": "Read-only evidence gate; passing this does not authorize trading.",
        },
    }


def _add_bucket_gate(
    gates: list[dict[str, Any]],
    feature_breakdowns: dict[str, Any],
    *,
    column: str,
    accepted_buckets: tuple[str, ...],
    deduped_trade_count: int,
    min_trade_share: float,
    min_win_rate: float,
    gate_prefix: str,
) -> None:
    evidence = _aggregate_bucket_evidence(feature_breakdowns, column=column, accepted_buckets=accepted_buckets)
    trade_share = _safe_div(float(evidence["trade_count"]), float(deduped_trade_count)) if deduped_trade_count else 0.0
    gates.extend(
        [
            {
                "name": f"{gate_prefix}_trade_share",
                "passed": bool(trade_share is not None and trade_share >= min_trade_share),
                "observed": trade_share,
                "threshold": f">={min_trade_share}",
                "accepted_buckets": list(accepted_buckets),
            },
            {
                "name": f"{gate_prefix}_win_rate",
                "passed": bool(evidence["win_rate"] is not None and evidence["win_rate"] >= min_win_rate),
                "observed": evidence["win_rate"],
                "threshold": f">={min_win_rate}",
                "accepted_buckets": list(accepted_buckets),
            },
            {
                "name": f"{gate_prefix}_positive_return",
                "passed": bool(evidence["return_sum"] is not None and evidence["return_sum"] > 0.0),
                "observed": evidence["return_sum"],
                "threshold": ">0",
                "accepted_buckets": list(accepted_buckets),
            },
        ]
    )


def _aggregate_bucket_evidence(
    feature_breakdowns: dict[str, Any],
    *,
    column: str,
    accepted_buckets: tuple[str, ...],
) -> dict[str, Any]:
    rows = feature_breakdowns.get(column) or []
    accepted = {str(bucket) for bucket in accepted_buckets}
    trade_count = 0
    win_count = 0.0
    return_sum = 0.0
    saw_return = False
    for row in rows:
        if str(row.get("bucket")) not in accepted:
            continue
        count = int(row.get("trade_count") or 0)
        trade_count += count
        win_rate = _float(row.get("win_rate"))
        if win_rate is not None:
            win_count += win_rate * count
        row_return = _float(row.get("return_sum"))
        if row_return is not None:
            return_sum += row_return
            saw_return = True
    return {
        "trade_count": int(trade_count),
        "win_rate": _safe_div(win_count, float(trade_count)) if trade_count else None,
        "return_sum": float(return_sum) if saw_return else None,
    }


def _feature_quality(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "underlying_proxy_coverage": 0.0,
            "bonereaper_largest_event_match_rate": 0.0,
            "bonereaper_largest_outcome_match_rate": 0.0,
        }
    underlying = frame.get("underlying_proxy_price")
    event_match = frame.get("bonereaper_largest_event_match")
    outcome_match = frame.get("bonereaper_largest_outcome_match")
    return {
        "underlying_proxy_coverage": _float(pd.to_numeric(underlying, errors="coerce").notna().mean()) if underlying is not None else 0.0,
        "bonereaper_largest_event_match_rate": _float(event_match.fillna(False).astype(bool).mean()) if event_match is not None else 0.0,
        "bonereaper_largest_outcome_match_rate": _float(outcome_match.fillna(False).astype(bool).mean()) if outcome_match is not None else 0.0,
    }


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("reasons") or []:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _apply_feature_attribution(
    rows: list[dict[str, Any]],
    ticker_frame: pd.DataFrame,
    account_context: dict[str, Any],
) -> list[dict[str, Any]]:
    attributed: list[dict[str, Any]] = []
    for row in rows:
        decision_at = _timestamp(row.get("quote_at_utc"))
        symbol = str(row.get("symbol") or "").upper()
        ticker_features = _ticker_features(ticker_frame, symbol=symbol, decision_at=decision_at)
        account_features = _account_features(account_context, row)
        row = {
            **row,
            **ticker_features,
            **account_features,
            "time_remaining_bucket": _time_remaining_bucket(_float(row.get("time_remaining_seconds"))),
            "ask_bucket": _ask_bucket(_float(row.get("best_ask"))),
            "spread_bucket": _spread_bucket(_float(row.get("spread"))),
            "depth_top3_ask_bucket": _depth_bucket(_float(row.get("depth_top3_ask_size"))),
        }
        attributed.append(row)
    return attributed


def _add_profile_filtered_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = list(rows)
    for row in rows:
        if row.get("status") != "candidate_for_manual_review":
            continue
        strategy_id = str(row.get("strategy_id") or "")
        if strategy_id == "reference_ladder_mid_min_size":
            fade = _profile_mid_fade_60s_candidate(row)
            if fade is not None:
                enriched.append(fade)
            lower_mid_fade = _profile_mid_fade_lower_mid_candidate(row)
            if lower_mid_fade is not None:
                enriched.append(lower_mid_fade)
            ask_high = _profile_mid_ask_gt_50_candidate(row)
            if ask_high is not None:
                enriched.append(ask_high)
        if strategy_id == "reference_ladder_all_bands_min_size":
            late = _profile_ladder_late_20_120_candidate(row)
            if late is not None:
                enriched.append(late)
            late_btc = _profile_late_btc_deep_60_120_candidate(row)
            if late_btc is not None:
                enriched.append(late_btc)
        if strategy_id == "reference_ladder_convex_tail_min_size":
            convex_depth = _profile_convex_tail_depth_20_100_candidate(row)
            if convex_depth is not None:
                enriched.append(convex_depth)
    return enriched


def _profile_mid_fade_60s_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    align60 = _side_sign(row.get("outcome")) * (_float(row.get("underlying_proxy_return_60s_bps")) or 0.0)
    time_remaining = _float(row.get("time_remaining_seconds"))
    spread = _float(row.get("spread"))
    if align60 >= 0:
        return None
    if time_remaining is None or time_remaining < 20 or time_remaining > 300:
        return None
    if spread is None or spread > 0.02:
        return None
    return _profile_filtered_clone(
        row,
        strategy_id="profile_mid_fade_60s_validation",
        filter_name="mid_ladder_fade_60s",
        candidate_priority_score=abs(align60) + max(0.0, 0.02 - spread) * 100.0,
    )


def _profile_mid_fade_lower_mid_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("reference_ladder_band") != "lower_mid_ladder":
        return None
    fade = _profile_mid_fade_60s_candidate(row)
    if fade is None:
        return None
    return _profile_filtered_clone(
        row,
        strategy_id="profile_mid_fade_lower_mid_validation",
        filter_name="mid_ladder_fade_60s_lower_mid",
        candidate_priority_score=float(fade.get("candidate_priority_score") or 0.0) + 0.25,
    )


def _profile_mid_ask_gt_50_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    ask = _float(row.get("best_ask"))
    spread = _float(row.get("spread"))
    if ask is None or ask <= 0.50:
        return None
    if spread is None or spread > 0.02:
        return None
    return _profile_filtered_clone(
        row,
        strategy_id="profile_mid_ask_gt_50_validation",
        filter_name="mid_ladder_ask_above_50",
        candidate_priority_score=float(ask) + max(0.0, 0.02 - spread) * 10.0,
    )


def _profile_ladder_late_20_120_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    time_remaining = _float(row.get("time_remaining_seconds"))
    spread = _float(row.get("spread"))
    if time_remaining is None or time_remaining < 20 or time_remaining > 120:
        return None
    if spread is None or spread > 0.02:
        return None
    return _profile_filtered_clone(
        row,
        strategy_id="profile_ladder_late_20_120_validation",
        filter_name="all_ladder_late_20_120",
        candidate_priority_score=max(0.0, 120.0 - float(time_remaining)) / 120.0 + max(0.0, 0.02 - spread) * 10.0,
    )


def _profile_late_btc_deep_60_120_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("symbol") or "").upper() != "BTC":
        return None
    time_remaining = _float(row.get("time_remaining_seconds"))
    spread = _float(row.get("spread"))
    depth = _float(row.get("depth_top3_ask_size"))
    if time_remaining is None or time_remaining < 60 or time_remaining > 120:
        return None
    if spread is None or spread <= 0.01 or spread > 0.02:
        return None
    if depth is None or depth < 100.0:
        return None
    return _profile_filtered_clone(
        row,
        strategy_id="profile_late_btc_deep_60_120_validation",
        filter_name="late_btc_deep_60_120",
        candidate_priority_score=max(0.0, 120.0 - float(time_remaining)) / 120.0 + min(depth, 500.0) / 500.0,
    )


def _profile_convex_tail_depth_20_100_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    spread = _float(row.get("spread"))
    depth = _float(row.get("depth_top3_ask_size"))
    if spread is None or spread > 0.02:
        return None
    if depth is None or depth < 20.0 or depth >= 100.0:
        return None
    return _profile_filtered_clone(
        row,
        strategy_id="profile_convex_tail_depth_20_100_validation",
        filter_name="convex_tail_depth_20_100",
        candidate_priority_score=(100.0 - float(depth)) / 100.0 + max(0.0, 0.02 - spread) * 10.0,
    )


def _profile_filtered_clone(row: dict[str, Any], *, strategy_id: str, filter_name: str, candidate_priority_score: float) -> dict[str, Any]:
    return {
        **row,
        "strategy_id": strategy_id,
        "profile_filter_name": filter_name,
        "profile_filter_source_strategy_id": row.get("strategy_id"),
        "validation_only": True,
        "not_live_test_eligible": True,
        "candidate_priority_score": float(candidate_priority_score),
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _side_sign(outcome: Any) -> int:
    if str(outcome) == "Up":
        return 1
    if str(outcome) == "Down":
        return -1
    return 0


def _ticker_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if "captured_at_utc" not in frame.columns or "symbol" not in frame.columns or "price" not in frame.columns:
        return pd.DataFrame()
    frame["captured_at_utc"] = pd.to_datetime(frame["captured_at_utc"], utc=True, errors="coerce")
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    return frame.dropna(subset=["captured_at_utc", "symbol", "price"]).sort_values(["symbol", "captured_at_utc"], kind="mergesort")


def _ticker_features(ticker_frame: pd.DataFrame, *, symbol: str, decision_at: datetime | None) -> dict[str, Any]:
    if ticker_frame.empty or decision_at is None or not symbol:
        return {
            "underlying_proxy_source": None,
            "underlying_proxy_price": None,
            "underlying_proxy_age_seconds": None,
            "underlying_proxy_return_60s_bps": None,
            "underlying_proxy_return_300s_bps": None,
        }
    symbol_ticks = ticker_frame[ticker_frame["symbol"] == symbol]
    if symbol_ticks.empty:
        return {
            "underlying_proxy_source": None,
            "underlying_proxy_price": None,
            "underlying_proxy_age_seconds": None,
            "underlying_proxy_return_60s_bps": None,
            "underlying_proxy_return_300s_bps": None,
        }
    at_tick = _last_tick_at_or_before(symbol_ticks, decision_at)
    if at_tick is None:
        return {
            "underlying_proxy_source": "binance_public_ticker_price",
            "underlying_proxy_price": None,
            "underlying_proxy_age_seconds": None,
            "underlying_proxy_return_60s_bps": None,
            "underlying_proxy_return_300s_bps": None,
        }
    price = _float(at_tick.get("price"))
    return {
        "underlying_proxy_source": "binance_public_ticker_price",
        "underlying_proxy_price": price,
        "underlying_proxy_age_seconds": _age_seconds(decision_at, _timestamp(at_tick.get("captured_at_utc"))),
        "underlying_proxy_return_60s_bps": _return_bps(symbol_ticks, decision_at, price, seconds=60),
        "underlying_proxy_return_300s_bps": _return_bps(symbol_ticks, decision_at, price, seconds=300),
    }


def _last_tick_at_or_before(frame: pd.DataFrame, timestamp: datetime) -> dict[str, Any] | None:
    candidates = frame[frame["captured_at_utc"] <= pd.Timestamp(timestamp)]
    if candidates.empty:
        return None
    return candidates.iloc[-1].to_dict()


def _return_bps(frame: pd.DataFrame, decision_at: datetime, current_price: float | None, *, seconds: int) -> float | None:
    if current_price in (None, 0):
        return None
    target_at = pd.Timestamp(decision_at) - pd.Timedelta(seconds=int(seconds))
    candidates = frame[frame["captured_at_utc"] <= target_at]
    if candidates.empty:
        return None
    prior = _float(candidates.iloc[-1].get("price"))
    if prior in (None, 0):
        return None
    return (float(current_price) / float(prior) - 1.0) * 10_000.0


def _account_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        report = row.get("report") if isinstance(row, dict) else None
        if not isinstance(report, dict):
            continue
        largest = (report.get("position_summary") or {}).get("largest_position")
        snapshots.append(
            {
                "captured_at_utc": row.get("captured_at_utc"),
                "handle": row.get("handle"),
                "largest_position": largest if isinstance(largest, dict) else None,
                "two_sided_market_count": (report.get("position_summary") or {}).get("two_sided_market_count"),
                "total_current_value": (report.get("position_summary") or {}).get("total_current_value"),
                "activity_summary": report.get("activity_summary") or {},
            }
        )
    return {
        "source": "polymarket_public_account_snapshot",
        "snapshot_count": len(snapshots),
        "snapshots": snapshots[-25:],
    }


def _account_features(account_context: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    snapshots = account_context.get("snapshots") or []
    decision_at = _timestamp(row.get("quote_at_utc"))
    snapshot = _latest_account_snapshot(snapshots, decision_at)
    largest = snapshot.get("largest_position") if isinstance(snapshot, dict) else None
    if not isinstance(largest, dict):
        return {
            "bonereaper_snapshot_at_utc": snapshot.get("captured_at_utc") if isinstance(snapshot, dict) else None,
            "bonereaper_largest_event_match": False,
            "bonereaper_largest_outcome_match": False,
            "bonereaper_largest_outcome": None,
            "bonereaper_largest_current_value": None,
            "bonereaper_two_sided_market_count": snapshot.get("two_sided_market_count") if isinstance(snapshot, dict) else None,
        }
    event_match = str(largest.get("event_slug") or largest.get("slug") or "") == str(row.get("event_slug") or "")
    outcome_match = event_match and str(largest.get("outcome") or "") == str(row.get("outcome") or "")
    return {
        "bonereaper_snapshot_at_utc": snapshot.get("captured_at_utc"),
        "bonereaper_largest_event_match": bool(event_match),
        "bonereaper_largest_outcome_match": bool(outcome_match),
        "bonereaper_largest_outcome": largest.get("outcome"),
        "bonereaper_largest_current_value": _float(largest.get("current_value")),
        "bonereaper_two_sided_market_count": snapshot.get("two_sided_market_count"),
    }


def _latest_account_snapshot(snapshots: list[dict[str, Any]], decision_at: datetime | None) -> dict[str, Any]:
    if not snapshots:
        return {}
    if decision_at is None:
        return snapshots[-1]
    candidates = [snapshot for snapshot in snapshots if (_timestamp(snapshot.get("captured_at_utc")) or datetime.min.replace(tzinfo=timezone.utc)) <= decision_at]
    return candidates[-1] if candidates else snapshots[0]


def _feature_breakdowns(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    output: dict[str, Any] = {}
    for column in (
        "symbol",
        "time_remaining_bucket",
        "ask_bucket",
        "spread_bucket",
        "depth_top3_ask_bucket",
        "reference_ladder_band",
        "bonereaper_largest_event_match",
        "bonereaper_largest_outcome_match",
    ):
        if column not in frame.columns:
            continue
        rows: list[dict[str, Any]] = []
        for value, group in frame.groupby(column, dropna=False, sort=False):
            pnl = pd.to_numeric(group.get("hypothetical_hold_to_settlement_pnl_net"), errors="coerce")
            wins = group.get("would_win")
            rows.append(
                {
                    "bucket": str(value),
                    "trade_count": int(len(group)),
                    "win_rate": _float(wins.mean()) if wins is not None else None,
                    "return_sum": _float(pnl.sum()),
                    "return_avg": _float(pnl.mean()),
                }
            )
        output[column] = rows
    for column in ("underlying_proxy_return_60s_bps", "underlying_proxy_return_300s_bps"):
        if column in frame.columns:
            output[column] = {
                "avg": _float(pd.to_numeric(frame[column], errors="coerce").mean()),
                "min": _float(pd.to_numeric(frame[column], errors="coerce").min()),
                "max": _float(pd.to_numeric(frame[column], errors="coerce").max()),
            }
    return output


def _time_remaining_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 0:
        return "expired"
    if value <= 30:
        return "0-30s"
    if value <= 60:
        return "30-60s"
    if value <= 120:
        return "60-120s"
    if value <= 300:
        return "120-300s"
    return "300s+"


def _ask_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value <= 0.10:
        return "0-10c"
    if value <= 0.30:
        return "10-30c"
    if value < 0.70:
        return "30-70c"
    if value < 0.90:
        return "70-90c"
    if value < 0.98:
        return "90-98c"
    return "98c+"


def _spread_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value <= 0.01:
        return "0-1c"
    if value <= 0.02:
        return "1-2c"
    if value <= 0.05:
        return "2-5c"
    return "5c+"


def _depth_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 5:
        return "<5"
    if value < 20:
        return "5-20"
    if value < 100:
        return "20-100"
    return "100+"


def _reference_ladder_band(value: float | None) -> str:
    if value is None:
        return "missing"
    for name, lower, upper in REFERENCE_LADDER_BANDS:
        if lower <= value <= upper:
            return name
    return "outside_reference_ladder"


def _reference_ladder_priority(strategy_id: str, ask: float | None, spread: float | None, depth: float | None) -> float:
    if ask is None:
        return 0.0
    spread_score = max(0.0, 0.05 - float(spread or 0.05))
    depth_score = min(float(depth or 0.0), 500.0) / 500.0
    if strategy_id == "reference_ladder_convex_tail_min_size":
        price_score = max(0.0, 0.40 - ask)
    elif strategy_id == "reference_ladder_dominant_min_size":
        price_score = ask
    elif strategy_id == "reference_ladder_mid_min_size":
        price_score = 1.0 - abs(ask - 0.55)
    else:
        price_score = abs(ask - 0.50)
    return float(price_score + spread_score + depth_score * 0.10)


def _settlement_labels(
    outcome_reviews: list[dict[str, Any]],
    *,
    generated_at: datetime,
    fetch_settlements: bool,
    max_settlement_fetches: int,
    settlement_events: list[dict[str, Any]] | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    events = list(settlement_events or [])
    if fetch_settlements and not settlement_events:
        slugs = _expired_event_slugs(outcome_reviews, generated_at)[: max(0, int(max_settlement_fetches))]
        fetched = _fetch_settlement_events(slugs)
        events.extend(row["event"] for row in fetched if row.get("event"))
        attempts.extend({key: value for key, value in row.items() if key != "event"} for row in fetched)
    elif settlement_events:
        attempts.append({"status": "provided_settlement_events", "event_count": len(settlement_events)})
    else:
        attempts.append({"status": "not_requested", "reason": "fetch_settlements_false"})
    if not events:
        return pd.DataFrame(), attempts
    events_df = normalize_polymarket_crypto_events(events)
    labels = build_event_labels(events_df, pd.DataFrame())
    if labels.empty:
        return labels, attempts
    key_columns = [column for column in ("event_id", "market_id", "event_slug") if column in events_df.columns]
    if {"event_id", "market_id", "event_slug"}.issubset(set(key_columns)):
        slugs = events_df[key_columns].drop_duplicates(subset=["event_id", "market_id"])
        labels = labels.merge(slugs, on=["event_id", "market_id"], how="left")
    return labels, attempts


def _fetch_settlement_events(slugs: list[str]) -> list[dict[str, Any]]:
    if not slugs:
        return []
    results: list[dict[str, Any]] = []
    max_workers = min(8, max(1, len(slugs)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_slug = {executor.submit(_fetch_gamma_event_by_slug_cached, slug): slug for slug in slugs}
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                event = future.result()
            except Exception as exc:  # noqa: BLE001 - settlement fetch failures become structured attempts.
                results.append({"event_slug": slug, "status": "blocked", "blocker": f"{type(exc).__name__}:{exc}"})
                continue
            if event:
                results.append({"event_slug": slug, "status": "fetched", "event_id": event.get("id"), "event": event})
            else:
                results.append({"event_slug": slug, "status": "missing", "blocker": "empty_gamma_response"})
    results.sort(key=lambda row: slugs.index(str(row.get("event_slug"))) if row.get("event_slug") in slugs else len(slugs))
    return results


@lru_cache(maxsize=2048)
def _fetch_gamma_event_by_slug_cached(slug: str) -> dict[str, Any]:
    return fetch_gamma_event_by_slug(slug)


def _apply_settlement_reconciliation(
    candidate_reviews: list[dict[str, Any]],
    settlement_labels: pd.DataFrame,
    *,
    min_order_size: float,
) -> list[dict[str, Any]]:
    if settlement_labels.empty:
        return [{**row, "settlement_status": "unavailable"} for row in candidate_reviews]
    labels = _label_lookup(settlement_labels)
    reconciled: list[dict[str, Any]] = []
    for row in candidate_reviews:
        label = _find_label(row, labels)
        if not label:
            reconciled.append({**row, "settlement_status": "pending"})
            continue
        status = str(label.get("label_status") or "")
        if status != "complete":
            reconciled.append(
                {
                    **row,
                    "settlement_status": "blocked",
                    "label_blockers": label.get("label_blockers") or [],
                    "label_authority": label.get("label_authority"),
                }
            )
            continue
        resolved = str(label.get("resolved_outcome") or "")
        row = {
            **row,
            "settlement_status": "settled",
            "resolved_outcome": resolved,
            "label_authority": label.get("label_authority"),
            "label_version": label.get("label_version"),
        }
        if row.get("strategy_id") == "two_sided_box_check":
            payout = float(min_order_size)
            cost = _float(row.get("min_order_total_cost"))
            row.update(
                {
                    "would_win": True,
                    "hypothetical_hold_to_settlement_payout": payout,
                    "hypothetical_hold_to_settlement_pnl_net": payout - cost if cost is not None else None,
                }
            )
        elif str(row.get("outcome") or "") in {"Up", "Down"}:
            cost = _float(row.get("min_order_total_cost"))
            would_win = str(row.get("outcome")) == resolved
            payout = float(min_order_size) if would_win else 0.0
            row.update(
                {
                    "would_win": would_win,
                    "hypothetical_hold_to_settlement_payout": payout,
                    "hypothetical_hold_to_settlement_pnl_net": payout - cost if cost is not None else None,
                }
            )
        reconciled.append(row)
    return reconciled


def _expired_event_slugs(outcome_reviews: list[dict[str, Any]], generated_at: datetime) -> list[str]:
    rows: list[tuple[datetime, str]] = []
    seen: set[str] = set()
    for review in outcome_reviews:
        slug = str(review.get("event_slug") or "")
        if not slug or slug in seen:
            continue
        end_at = _timestamp(review.get("window_end_time"))
        if end_at is None or end_at > generated_at:
            continue
        rows.append((end_at, slug))
        seen.add(slug)
    rows.sort(key=lambda item: item[0], reverse=True)
    return [slug for _, slug in rows]


def _label_lookup(labels: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in labels.to_dict(orient="records"):
        for key_name in ("event_slug", "event_id", "market_id"):
            value = row.get(key_name)
            if value not in (None, ""):
                lookup[(key_name, str(value))] = row
    return lookup


def _find_label(row: dict[str, Any], labels: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    for key_name in ("event_slug", "event_id", "market_id"):
        value = row.get(key_name)
        if value not in (None, ""):
            label = labels.get((key_name, str(value)))
            if label:
                return label
    return None


def taker_fee_per_share(price: float | None, *, fee_rate: float = POLYMARKET_CRYPTO_TAKER_FEE_RATE) -> float | None:
    if price is None:
        return None
    return float(fee_rate) * float(price) * (1.0 - float(price))


def _latest_ticks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    if "captured_at_utc" in frame.columns:
        frame["captured_at_utc"] = pd.to_datetime(frame["captured_at_utc"], utc=True, errors="coerce")
        frame = frame.sort_values(["symbol", "captured_at_utc"], kind="mergesort")
    latest = frame.groupby("symbol", as_index=False, dropna=False).tail(1)
    return strict_jsonable(latest.to_dict(orient="records"))


def _next_required_development(blockers: list[str], candidates: list[dict[str, Any]]) -> str:
    if blockers:
        return "fix_live_capture_inputs_before_strategy_review"
    if not any(row.get("status") == "candidate_for_manual_review" for row in candidates):
        return "continue_capture_and_tune_no_trade_thresholds_against_settled_windows"
    return "add_settlement_reconciliation_for_candidate_rows_before_any_min_size_live_test"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path, *, max_lines: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        source = deque(handle, maxlen=max_lines) if max_lines is not None and max_lines > 0 else handle
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _first_outcome(group: pd.DataFrame, outcome: str) -> dict[str, Any] | None:
    rows = group[group["outcome"].astype(str).str.lower() == outcome.lower()]
    if rows.empty:
        return None
    return rows.iloc[0].to_dict()


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return float((later - earlier).total_seconds())


def _age_seconds(later: datetime | None, earlier: datetime | None) -> float | None:
    seconds = _seconds_between(later, earlier)
    return max(0.0, seconds) if seconds is not None else None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


__all__ = [
    "build_live_gate_aggregate",
    "build_live_decision_review",
    "render_live_gate_aggregate_markdown",
    "render_live_decision_review_markdown",
    "taker_fee_per_share",
    "write_live_gate_aggregate_artifacts",
    "write_live_decision_review_artifacts",
]
