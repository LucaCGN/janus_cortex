from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_options_app.api.db import to_jsonable
from crypto_options_app.data_nodes.crypto.reference import normalize_reference_price_reports
from crypto_options_app.data_nodes.polymarket_crypto.markets import market_type_priority
from crypto_options_app.pipelines.options.audit import evaluate_crypto_options_data_sufficiency
from crypto_options_app.pipelines.options.backtests import compare_crypto_options_strategies
from crypto_options_app.pipelines.options.contracts import CRYPTO_OPTIONS_REPORT_SCHEMA_VERSION, schema_contracts
from crypto_options_app.pipelines.options.engine import run_crypto_options_replay_engine
from crypto_options_app.pipelines.options.labels import build_event_labels
from crypto_options_app.pipelines.options.panels import build_event_state_panel
from crypto_options_app.runtime.local_paths import resolve_shared_root


def build_research_report(
    *,
    events_df: pd.DataFrame,
    clob_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    reference_df: pd.DataFrame | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    reference_df = reference_df if reference_df is not None else normalize_reference_price_reports([])
    labels_df = build_event_labels(events_df, reference_df)
    panel_df = build_event_state_panel(events_df, clob_df, candles_df, labels_df=labels_df)
    audit = evaluate_crypto_options_data_sufficiency(
        events_df,
        clob_df,
        candles_df,
        reference_df=reference_df,
        labels_df=labels_df,
        panel_df=panel_df,
        audited_at=generated_at,
    )
    comparison = compare_crypto_options_strategies(panel_df)
    replay = run_crypto_options_replay_engine(panel_df, allow_midpoint_entries=True)
    blockers = sorted(set((audit.get("blockers") or []) + (comparison.get("blockers") or [])))
    ready_for_shadow = _shadow_ready(audit, comparison)
    return {
        "schema_version": CRYPTO_OPTIONS_REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated_at.isoformat(),
        "issue": 47,
        "branch": "codex/crypto-options-research-module",
        "scope": "crypto-options research/backtest foundation only",
        "live_trading_authorized": False,
        "orders_allowed": False,
        "data_contracts": schema_contracts(),
        "supported_event_type_priority": market_type_priority(),
        "data_audit": audit,
        "strategy_comparison": comparison,
        "chronological_replay": replay,
        "joined_panel_rows": int(len(panel_df)),
        "reference_label_rows": int(len(labels_df)),
        "blockers": blockers,
        "recommended_next_data_source": {
            "kind": "resolution_source_compatible_crypto_reference_data",
            "symbols": ["BTC", "ETH", "SOL", "XRP"],
            "minimum": (
                "Chainlink BTC/USD reference stream data for 5m BTC up/down settlement labels; "
                "1m exchange candles for directional research features; second-level trades/orderbook if scalping latency is studied."
            ),
            "reason": (
                "Underlying technical indicators and settlement labels require reference-market data, not Polymarket outcome odds. "
                "For 5m BTC up/down contracts, the settlement source can be Chainlink rather than a generic exchange spot feed."
            ),
        },
        "shadow_testing_ready": ready_for_shadow,
        "promotion_path": ["research", "historical_replay", "shadow", "min_size_test", "live_limited_with_explicit_approval"],
        "reference_label_preview": labels_df.head(25).to_dict(orient="records") if not labels_df.empty else [],
        "panel_preview": panel_df.head(25).to_dict(orient="records") if not panel_df.empty else [],
    }


def write_research_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(output_dir) if output_dir else resolve_shared_root() / "artifacts" / "crypto-options-research" / day
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"crypto_options_research_{stamp}.json"
    md_path = root / f"crypto_options_research_{stamp}.md"
    json_path.write_text(json.dumps(strict_jsonable(payload), allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_research_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_research_markdown(payload: dict[str, Any]) -> str:
    audit = payload.get("data_audit") or {}
    comparison = payload.get("strategy_comparison") or {}
    polymarket_verdict = (audit.get("polymarket_only_underlying_price_reconstruction") or {}).get("answer")
    lines = [
        "# Crypto Options Research Report",
        "",
        f"- Generated at: `{payload.get('generated_at_utc')}`",
        f"- Live trading authorized: `{payload.get('live_trading_authorized')}`",
        f"- Audit status: `{audit.get('status')}`",
        f"- Strategy comparison: `{comparison.get('status')}`",
        f"- Polymarket-only BTC reconstruction: `{polymarket_verdict}`",
        "",
        "## Data Counts",
        "",
        f"```json\n{json.dumps(audit.get('counts') or {}, indent=2, sort_keys=True)}\n```",
        "",
        "## Blockers",
        "",
    ]
    blockers = payload.get("blockers") or []
    if blockers:
        lines.extend([f"- `{blocker}`" for blocker in blockers])
    else:
        lines.append("- None")
    lines.extend(["", "## Initial Conclusion", "", str(comparison.get("initial_conclusion") or "")])
    metric_summary = comparison.get("metric_summary") or {}
    metric_rows = metric_summary.get("results") or []
    if metric_rows:
        lines.extend(["", "## Strategy Metrics", ""])
        for row in metric_rows[:12]:
            lines.append(
                "- `{strategy}` status=`{status}` trades=`{trades}` win_rate=`{win_rate}` return_sum=`{return_sum}` max_loss_streak=`{losses}`".format(
                    strategy=row.get("strategy_id"),
                    status=row.get("status"),
                    trades=row.get("trade_count"),
                    win_rate=row.get("win_rate"),
                    return_sum=row.get("return_sum"),
                    losses=row.get("max_sequential_losses"),
                )
            )
    replay = payload.get("chronological_replay") or {}
    if replay:
        lines.extend(
            [
                "",
                "## Chronological Replay",
                "",
                f"- Status: `{replay.get('status')}`",
                f"- Allow midpoint entries: `{replay.get('allow_midpoint_entries')}`",
                f"- Live trading authorized: `{replay.get('live_trading_authorized')}`",
            ]
        )
        for row in replay.get("results") or []:
            lines.append(
                "- `{strategy}` status=`{status}` trades=`{trades}` return_sum=`{return_sum}` blockers=`{blockers}`".format(
                    strategy=row.get("strategy_id"),
                    status=row.get("status"),
                    trades=row.get("trade_count"),
                    return_sum=(row.get("metrics") or {}).get("return_sum"),
                    blockers=row.get("blockers") or [],
                )
            )
    service = payload.get("service") or {}
    if service:
        lines.extend(
            [
                "",
                "## Service Boundary",
                "",
                f"- Service: `{service.get('name')}`",
                f"- Runtime mode: `{service.get('runtime_mode')}`",
                f"- Strategy catalog entries: `{len(service.get('strategy_catalog') or [])}`",
                f"- Data sources tracked: `{len(service.get('data_source_registry') or [])}`",
            ]
        )
    ingestion_attempts = payload.get("ingestion_attempts") or {}
    if ingestion_attempts:
        lines.extend(["", "## Ingestion Attempts", "", f"```json\n{json.dumps(strict_jsonable(ingestion_attempts), indent=2, sort_keys=True)}\n```"])
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This artifact is research/backtest evidence only. It does not authorize live trading, order placement, cancellation, signing, broadcasting, redeeming, or portfolio action.",
        ]
    )
    return "\n".join(lines) + "\n"


def _shadow_ready(audit: dict[str, Any], comparison: dict[str, Any]) -> bool:
    if audit.get("live_trading_authorized") is not False:
        return False
    valid = audit.get("strategy_validity") or {}
    return bool(
        valid.get("directional_prediction", {}).get("valid")
        or valid.get("microstructure_scalping", {}).get("valid")
        or comparison.get("status") == "comparison_partial"
    )


def strict_jsonable(value: Any) -> Any:
    """Convert payloads to standards-compliant JSON values."""

    value = to_jsonable(value)
    if isinstance(value, dict):
        return {str(key): strict_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strict_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [strict_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


__all__ = [
    "build_research_report",
    "render_research_markdown",
    "strict_jsonable",
    "write_research_artifacts",
]
