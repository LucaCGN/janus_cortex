from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.store import append_jsonl, artifacts_root, reports_root, session_date, write_json, write_text


MicrocycleActor = Literal["janus", "operator", "unknown"]
MicrocycleLegType = Literal["buy", "sell", "rebuy"]
MicrocycleLegStatus = Literal["missing", "open", "filled", "stale", "blocked", "waiting"]
MicrocycleStatus = Literal[
    "awaiting_buy",
    "sell_candidate",
    "sell_open_waiting",
    "sell_stale_replace",
    "sell_filled_review_rebuy",
    "rebuy_candidate",
    "rebuy_blocked",
    "blocked",
]
MicrocycleNextAction = Literal[
    "wait_for_buy_fill",
    "place_paired_sell",
    "wait_for_sell_fill",
    "replace_paired_sell",
    "review_rebuy",
    "place_paired_rebuy",
    "blocked",
]
MicrocycleFillabilityStatus = Literal[
    "awaiting_buy_fill",
    "paired_sell_candidate",
    "paired_sell_open_unfilled",
    "paired_sell_stale",
    "round_trip_filled",
    "rebuy_review_ready",
    "rebuy_blocked",
    "blocked",
]


class MicrocycleLegEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leg_type: MicrocycleLegType
    status: MicrocycleLegStatus
    token_id: str = Field(min_length=1)
    shares: float = Field(default=0.0, ge=0.0)
    price: float | None = Field(default=None, ge=0.0, le=1.0)
    actor: MicrocycleActor = "unknown"
    external_order_id: str | None = None
    external_trade_id: str | None = None
    source: str | None = None
    observed_at_utc: datetime | None = None


class PairedMicrocycleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: str = Field(min_length=1)
    sleeve_id: str = Field(min_length=1)
    sleeve_role: str | None = None
    strategy_id: str | None = None
    token_id: str = Field(min_length=1)
    outcome_id: str | None = None
    outcome_label: str | None = None
    configured_entry_shares: float = Field(default=0.0, ge=0.0)
    configured_target_price: float | None = Field(default=None, ge=0.0, le=1.0)
    status: MicrocycleStatus
    next_action: MicrocycleNextAction
    buy_leg: MicrocycleLegEvidence
    sell_leg: MicrocycleLegEvidence | None = None
    rebuy_leg: MicrocycleLegEvidence | None = None
    manual_fill_imported: bool = False
    duplicate_buy_blocked: bool = False
    next_leg_candidate: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class PairedMicrocycleEngineEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "sports_live_paired_microcycle_evidence_v1"
    event_id: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cycle_count: int = Field(default=0, ge=0)
    next_leg_candidate_count: int = Field(default=0, ge=0)
    duplicate_buy_block_count: int = Field(default=0, ge=0)
    manual_fill_import_count: int = Field(default=0, ge=0)
    budget_block_count: int = Field(default=0, ge=0)
    execution_boundary: Literal["evidence_only"] = "evidence_only"
    cycles: list[PairedMicrocycleEvidence] = Field(default_factory=list)


class PairedMicrocycleReadbackScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: str = Field(min_length=1)
    sleeve_id: str = Field(min_length=1)
    token_id: str = Field(min_length=1)
    outcome_label: str | None = None
    cycle_status: MicrocycleStatus
    fillability_status: MicrocycleFillabilityStatus
    buy_shares: float = Field(default=0.0, ge=0.0)
    sell_shares: float = Field(default=0.0, ge=0.0)
    open_sell_shares: float = Field(default=0.0, ge=0.0)
    realized_pnl_usd: float | None = None
    final_mark_pnl_usd: float | None = None
    final_mark_price: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_seconds: float | None = Field(default=None, ge=0.0)
    missed_transition_codes: list[str] = Field(default_factory=list)
    blocker_codes: list[str] = Field(default_factory=list)


class PairedMicrocycleReadbackScoringEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "sports_live_paired_microcycle_readback_score_v1"
    source_schema_version: str = "sports_live_paired_microcycle_evidence_v1"
    event_id: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cycle_count: int = Field(default=0, ge=0)
    realized_cycle_count: int = Field(default=0, ge=0)
    missed_transition_count: int = Field(default=0, ge=0)
    latency_scored_count: int = Field(default=0, ge=0)
    fillability_block_count: int = Field(default=0, ge=0)
    net_realized_pnl_usd: float = 0.0
    net_final_mark_pnl_usd: float = 0.0
    execution_boundary: Literal["read_only"] = "read_only"
    scores: list[PairedMicrocycleReadbackScore] = Field(default_factory=list)


def paired_microcycle_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "sports-live-paired-microcycle" / session_date(day)


def build_paired_microcycle_evidence(
    *,
    event_id: str,
    plan: dict[str, Any],
    direct_clob: dict[str, Any],
    target_management: dict[str, Any] | BaseModel | None = None,
    event_risk_budget: dict[str, Any] | BaseModel | None = None,
    known_external_order_ids: set[str] | None = None,
    min_size: float = 5.0,
    default_target_delta_cents: float = 5.0,
) -> PairedMicrocycleEngineEvidence:
    strategies = _microcycle_strategies(plan)
    buy_trades = _trades_by_token(direct_clob, side="buy", known_external_order_ids=known_external_order_ids)
    sell_trades = _trades_by_token(direct_clob, side="sell", known_external_order_ids=known_external_order_ids)
    sell_orders = _orders_by_token(direct_clob, side="sell")
    target_rows = _target_rows_by_sleeve(target_management)
    budget = _model_or_dict(event_risk_budget)

    cycles: list[PairedMicrocycleEvidence] = []
    for strategy in strategies:
        token_id = strategy["token_id"]
        entry_shares = strategy["entry_shares"] or min_size
        sleeve_id = strategy["sleeve_id"]
        target_row = target_rows.get(sleeve_id) or {}
        configured_target = _target_price_from_strategy_or_row(
            strategy=strategy,
            target_row=target_row,
            default_target_delta_cents=default_target_delta_cents,
        )

        buys = _take_trade_legs(buy_trades.get(token_id) or [], entry_shares)
        sells = _take_trade_legs(sell_trades.get(token_id) or [], _sum_leg_shares(buys) or entry_shares)
        open_sells = _take_order_legs(sell_orders.get(token_id) or [], _sum_leg_shares(buys) or entry_shares)
        cycles.append(
            _build_cycle(
                event_id=event_id,
                strategy=strategy,
                entry_shares=entry_shares,
                configured_target_price=configured_target,
                buy_legs=buys,
                sell_trade_legs=sells,
                open_sell_legs=open_sells,
                target_row=target_row,
                budget=budget,
                min_size=min_size,
            )
        )

    return PairedMicrocycleEngineEvidence(
        event_id=event_id,
        cycle_count=len(cycles),
        next_leg_candidate_count=sum(1 for cycle in cycles if cycle.next_leg_candidate),
        duplicate_buy_block_count=sum(1 for cycle in cycles if cycle.duplicate_buy_blocked),
        manual_fill_import_count=sum(1 for cycle in cycles if cycle.manual_fill_imported),
        budget_block_count=sum(1 for cycle in cycles if "event_budget_exhausted" in cycle.reason_codes),
        cycles=cycles,
    )


def build_paired_microcycle_readback_score(
    evidence: PairedMicrocycleEngineEvidence | dict[str, Any],
    *,
    final_prices_by_token: dict[str, float] | None = None,
) -> PairedMicrocycleReadbackScoringEvidence:
    parsed = _parse_engine_evidence(evidence)
    final_prices = {str(key): float(value) for key, value in (final_prices_by_token or {}).items()}
    scores = [_score_cycle(cycle, final_prices_by_token=final_prices) for cycle in parsed.cycles]

    return PairedMicrocycleReadbackScoringEvidence(
        event_id=parsed.event_id,
        cycle_count=len(scores),
        realized_cycle_count=sum(1 for score in scores if score.realized_pnl_usd is not None),
        missed_transition_count=sum(1 for score in scores if score.missed_transition_codes),
        latency_scored_count=sum(1 for score in scores if score.latency_seconds is not None),
        fillability_block_count=sum(1 for score in scores if score.blocker_codes),
        net_realized_pnl_usd=round(sum(score.realized_pnl_usd or 0.0 for score in scores), 6),
        net_final_mark_pnl_usd=round(sum(score.final_mark_pnl_usd or 0.0 for score in scores), 6),
        scores=scores,
    )


def write_paired_microcycle_readback_score(
    score: PairedMicrocycleReadbackScoringEvidence,
    *,
    day: str | None = None,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_day = session_date(day)
    timestamp = score.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = paired_microcycle_root(resolved_day, root=artifact_root)
    json_path = root / f"paired_microcycle_readback_score_{timestamp}.json"
    write_json(json_path, score.model_dump(mode="json"))
    append_jsonl(
        root / "paired_microcycle_readback_scores.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": resolved_day,
            "event_id": score.event_id,
            "cycle_count": score.cycle_count,
            "realized_cycle_count": score.realized_cycle_count,
            "missed_transition_count": score.missed_transition_count,
            "net_realized_pnl_usd": score.net_realized_pnl_usd,
            "net_final_mark_pnl_usd": score.net_final_mark_pnl_usd,
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / (
        f"paired_microcycle_readback_score_{timestamp}.md"
    )
    write_text(markdown_path, render_paired_microcycle_readback_score_markdown(score, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "sports_live_paired_microcycle_readback_score_write_result_v1",
        "session_date": resolved_day,
        "event_id": score.event_id,
        "cycle_count": score.cycle_count,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def render_paired_microcycle_readback_score_markdown(
    score: PairedMicrocycleReadbackScoringEvidence,
    *,
    json_path: str | None = None,
) -> str:
    lines = [
        f"# Paired Microcycle Readback Score - {score.event_id}",
        "",
        f"- generated_at_utc: `{score.generated_at_utc.isoformat()}`",
        "- issue: `#77`",
        f"- execution_boundary: `{score.execution_boundary}`",
        f"- cycle_count: `{score.cycle_count}`",
        f"- realized_cycle_count: `{score.realized_cycle_count}`",
        f"- missed_transition_count: `{score.missed_transition_count}`",
        f"- net_realized_pnl_usd: `{score.net_realized_pnl_usd:.6f}`",
        f"- net_final_mark_pnl_usd: `{score.net_final_mark_pnl_usd:.6f}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Cycle Scores"])
    if not score.scores:
        lines.append("- no cycles scored")
    for row in score.scores:
        lines.append(
            f"- `{row.cycle_id}` `{row.fillability_status}` {row.outcome_label or row.token_id}: "
            f"realized=`{_format_optional_money(row.realized_pnl_usd)}`, "
            f"final_mark=`{_format_optional_money(row.final_mark_pnl_usd)}`, "
            f"latency_seconds=`{_format_optional_seconds(row.latency_seconds)}`, "
            f"missed=`{','.join(row.missed_transition_codes) or 'none'}`"
        )
    lines.extend(["", "## Boundary"])
    lines.append("- read-only scoring only; no order, cancel, replace, submit, sign, broadcast, redeem, worker, or event-control action is authorized by this artifact.")
    return "\n".join(lines) + "\n"


def _score_cycle(
    cycle: PairedMicrocycleEvidence,
    *,
    final_prices_by_token: dict[str, float],
) -> PairedMicrocycleReadbackScore:
    buy = cycle.buy_leg
    sell = cycle.sell_leg
    sell_filled = sell is not None and sell.status == "filled"
    sell_open = sell is not None and sell.status == "open"
    final_mark = final_prices_by_token.get(cycle.token_id)
    buy_shares = buy.shares if buy.status == "filled" else 0.0
    sell_shares = sell.shares if sell_filled and sell is not None else 0.0
    open_sell_shares = sell.shares if sell_open and sell is not None else 0.0
    realized_pnl = _round_money((sell.price - buy.price) * min(buy_shares, sell_shares)) if (
        buy.price is not None and sell is not None and sell.price is not None and sell_shares > 0.0
    ) else None
    open_buy_shares = max(0.0, buy_shares - sell_shares)
    final_mark_pnl = _round_money((final_mark - buy.price) * open_buy_shares) if (
        final_mark is not None and buy.price is not None and open_buy_shares > 0.0
    ) else None
    missed = _missed_transition_codes(cycle)
    blockers = _readback_blocker_codes(cycle, missed)

    return PairedMicrocycleReadbackScore(
        cycle_id=cycle.cycle_id,
        sleeve_id=cycle.sleeve_id,
        token_id=cycle.token_id,
        outcome_label=cycle.outcome_label,
        cycle_status=cycle.status,
        fillability_status=_fillability_status(cycle),
        buy_shares=round(buy_shares, 6),
        sell_shares=round(sell_shares, 6),
        open_sell_shares=round(open_sell_shares, 6),
        realized_pnl_usd=realized_pnl,
        final_mark_pnl_usd=final_mark_pnl,
        final_mark_price=final_mark,
        latency_seconds=_leg_latency_seconds(buy, sell) if sell_filled and sell is not None else None,
        missed_transition_codes=missed,
        blocker_codes=blockers,
    )


def _build_cycle(
    *,
    event_id: str,
    strategy: dict[str, Any],
    entry_shares: float,
    configured_target_price: float | None,
    buy_legs: list[MicrocycleLegEvidence],
    sell_trade_legs: list[MicrocycleLegEvidence],
    open_sell_legs: list[MicrocycleLegEvidence],
    target_row: dict[str, Any],
    budget: dict[str, Any],
    min_size: float,
) -> PairedMicrocycleEvidence:
    token_id = strategy["token_id"]
    sleeve_id = strategy["sleeve_id"]
    filled_buy_shares = _sum_leg_shares(buy_legs)
    filled_sell_shares = _sum_leg_shares(sell_trade_legs)
    open_sell_shares = _sum_leg_shares(open_sell_legs)
    budget_exhausted = _budget_exhausted(budget, min_required_notional=entry_shares * (strategy.get("entry_price") or 0.01))
    manual_imported = any(leg.actor in {"operator", "unknown"} for leg in buy_legs + sell_trade_legs)
    reasons: list[str] = []
    sell_leg = _combined_leg("sell", token_id=token_id, legs=sell_trade_legs or open_sell_legs)
    rebuy_leg: MicrocycleLegEvidence | None = None
    duplicate_buy_blocked = False
    next_leg_candidate = False

    if filled_buy_shares <= 1e-9:
        buy_leg = MicrocycleLegEvidence(leg_type="buy", status="missing", token_id=token_id)
        if budget_exhausted:
            status: MicrocycleStatus = "blocked"
            next_action: MicrocycleNextAction = "blocked"
            reasons.append("event_budget_exhausted")
        else:
            status = "awaiting_buy"
            next_action = "wait_for_buy_fill"
            reasons.append("buy_fill_required_before_paired_sell")
    else:
        buy_leg = _combined_leg("buy", token_id=token_id, legs=buy_legs)
        if filled_sell_shares + 1e-9 >= filled_buy_shares:
            if budget_exhausted:
                status = "rebuy_blocked"
                next_action = "blocked"
                reasons.append("event_budget_exhausted")
                rebuy_leg = MicrocycleLegEvidence(leg_type="rebuy", status="blocked", token_id=token_id)
            else:
                status = "rebuy_candidate"
                next_action = "place_paired_rebuy"
                reasons.append("sell_fill_allows_rebuy_review")
                next_leg_candidate = True
                rebuy_leg = MicrocycleLegEvidence(
                    leg_type="rebuy",
                    status="waiting",
                    token_id=token_id,
                    shares=round(min(filled_sell_shares, entry_shares), 6),
                    price=strategy.get("entry_price"),
                )
        elif open_sell_shares > 1e-9:
            status = "sell_open_waiting"
            next_action = "wait_for_sell_fill"
            duplicate_buy_blocked = True
            reasons.append("open_paired_sell_blocks_duplicate_buy")
            if open_sell_shares + 1e-9 < filled_buy_shares:
                reasons.append("paired_sell_partial_coverage")
        elif _target_status(target_row) == "target_stale":
            status = "sell_stale_replace"
            next_action = "replace_paired_sell"
            next_leg_candidate = True
            reasons.append("paired_sell_target_stale")
            sell_leg = MicrocycleLegEvidence(
                leg_type="sell",
                status="stale",
                token_id=token_id,
                shares=round(filled_buy_shares, 6),
                price=configured_target_price,
                source="target_management",
            )
        else:
            status = "sell_candidate"
            next_action = "place_paired_sell"
            next_leg_candidate = filled_buy_shares >= min_size
            if next_leg_candidate:
                reasons.append("filled_buy_requires_paired_sell")
            else:
                reasons.append("filled_buy_below_min_size")
            sell_leg = MicrocycleLegEvidence(
                leg_type="sell",
                status="missing",
                token_id=token_id,
                shares=round(filled_buy_shares, 6),
                price=configured_target_price,
                source="target_management",
            )

    if manual_imported:
        reasons.append("manual_or_unknown_fill_imported")

    return PairedMicrocycleEvidence(
        cycle_id=_cycle_id(event_id, sleeve_id, token_id),
        sleeve_id=sleeve_id,
        sleeve_role=strategy.get("sleeve_role"),
        strategy_id=strategy.get("strategy_id"),
        token_id=token_id,
        outcome_id=strategy.get("outcome_id"),
        outcome_label=strategy.get("outcome_label"),
        configured_entry_shares=round(entry_shares, 6),
        configured_target_price=configured_target_price,
        status=status,
        next_action=next_action,
        buy_leg=buy_leg,
        sell_leg=sell_leg,
        rebuy_leg=rebuy_leg,
        manual_fill_imported=manual_imported,
        duplicate_buy_blocked=duplicate_buy_blocked,
        next_leg_candidate=next_leg_candidate,
        reason_codes=_unique(reasons),
    )


def _microcycle_strategies(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        token_id = str(entry_rules.get("token_id") or entry_rules.get("asset_id") or "").strip()
        if not token_id or not _is_grid_scalp_strategy(strategy, entry_rules):
            continue
        strategy_id = str(strategy.get("strategy_id") or "").strip() or token_id
        rows.append(
            {
                "sleeve_id": str(strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id).strip()
                or strategy_id,
                "sleeve_role": str(strategy.get("sleeve_role") or entry_rules.get("sleeve_role") or "").strip()
                or None,
                "strategy_id": strategy_id,
                "token_id": token_id,
                "outcome_id": str(entry_rules.get("outcome_id") or "").strip() or None,
                "outcome_label": str(strategy.get("side") or entry_rules.get("outcome_label") or "").strip() or None,
                "entry_shares": _float(entry_rules.get("size") or entry_rules.get("shares")) or 0.0,
                "entry_price": _float(entry_rules.get("price") or entry_rules.get("max_price")),
                "exit_rules": strategy.get("exit_rules") if isinstance(strategy.get("exit_rules"), dict) else {},
            }
        )
    return rows


def _is_grid_scalp_strategy(strategy: dict[str, Any], entry_rules: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            strategy.get("family"),
            strategy.get("sleeve_role"),
            strategy.get("sleeve_id"),
            strategy.get("strategy_id"),
            entry_rules.get("sleeve_role"),
        )
    )
    return any(
        marker in haystack
        for marker in (
            "grid",
            "scalp",
            "microcycle",
            "micro_grid",
            "price_stability",
            "manual_imported",
            "manual-imported",
            "ultra_low",
            "ultralow",
            "subpenny",
            "decimal_grid",
        )
    )


def _trades_by_token(
    direct_clob: dict[str, Any],
    *,
    side: Literal["buy", "sell"],
    known_external_order_ids: set[str] | None,
) -> dict[str, list[MicrocycleLegEvidence]]:
    rows: dict[str, list[MicrocycleLegEvidence]] = {}
    side_aliases = {"buy": {"buy", "long"}, "sell": {"sell", "short"}}[side]
    for trade in _direct_trades(direct_clob):
        token_id = str(trade.get("asset") or trade.get("asset_id") or trade.get("token_id") or "").strip()
        trade_side = str(trade.get("side") or trade.get("taker_side") or "").strip().lower()
        shares = _float(trade.get("size") or trade.get("matched_amount") or trade.get("shares")) or 0.0
        price = _float(trade.get("price"))
        if not token_id or trade_side not in side_aliases or shares <= 0.0:
            continue
        rows.setdefault(token_id, []).append(
            MicrocycleLegEvidence(
                leg_type=side,
                status="filled",
                token_id=token_id,
                shares=shares,
                price=price,
                actor=_trade_actor(trade, known_external_order_ids=known_external_order_ids),
                external_order_id=_trade_order_id(trade),
                external_trade_id=_external_trade_id(trade),
                source="direct_clob_trade",
                observed_at_utc=_observed_at(trade),
            )
        )
    return rows


def _orders_by_token(direct_clob: dict[str, Any], *, side: Literal["buy", "sell"]) -> dict[str, list[MicrocycleLegEvidence]]:
    rows: dict[str, list[MicrocycleLegEvidence]] = {}
    for order in ((direct_clob.get("open_orders") or {}).get("orders") or []):
        if not isinstance(order, dict):
            continue
        order_side = str(order.get("side") or "").strip().lower()
        token_id = str(order.get("asset") or order.get("asset_id") or order.get("token_id") or "").strip()
        shares = _float(order.get("size") or order.get("original_size") or order.get("remaining_size")) or 0.0
        price = _float(order.get("price") or order.get("limit_price"))
        if order_side != side or not token_id or shares <= 0.0:
            continue
        rows.setdefault(token_id, []).append(
            MicrocycleLegEvidence(
                leg_type=side,
                status="open",
                token_id=token_id,
                shares=shares,
                price=price,
                actor="unknown",
                external_order_id=_external_order_id(order),
                source="direct_clob_open_order",
                observed_at_utc=_observed_at(order),
            )
        )
    return rows


def _target_rows_by_sleeve(target_management: dict[str, Any] | BaseModel | None) -> dict[str, dict[str, Any]]:
    payload = _model_or_dict(target_management)
    rows: dict[str, dict[str, Any]] = {}
    for row in payload.get("sleeves") or []:
        if not isinstance(row, dict):
            continue
        sleeve_id = str(row.get("sleeve_id") or "").strip()
        if sleeve_id:
            rows[sleeve_id] = row
    return rows


def _target_price_from_strategy_or_row(
    *,
    strategy: dict[str, Any],
    target_row: dict[str, Any],
    default_target_delta_cents: float,
) -> float | None:
    row_target = _float(target_row.get("target_price"))
    if row_target is not None:
        return row_target
    exit_rules = strategy.get("exit_rules") if isinstance(strategy.get("exit_rules"), dict) else {}
    explicit = _float(exit_rules.get("target_price"))
    if explicit is not None:
        return explicit
    basis = _float(target_row.get("weighted_basis_price")) or strategy.get("entry_price")
    if basis is None:
        return None
    cents = _float(exit_rules.get("min_target_cents") or exit_rules.get("minimum_target_cents")) or default_target_delta_cents
    return round(min(0.95, max(0.01, basis + cents / 100.0)), 4)


def _combined_leg(
    leg_type: MicrocycleLegType,
    *,
    token_id: str,
    legs: list[MicrocycleLegEvidence],
) -> MicrocycleLegEvidence:
    if not legs:
        return MicrocycleLegEvidence(leg_type=leg_type, status="missing", token_id=token_id)
    shares = _sum_leg_shares(legs)
    avg_price = _weighted_price(legs)
    status: MicrocycleLegStatus = "filled" if any(leg.status == "filled" for leg in legs) else legs[0].status
    return MicrocycleLegEvidence(
        leg_type=leg_type,
        status=status,
        token_id=token_id,
        shares=shares,
        price=avg_price,
        actor=legs[0].actor if len({leg.actor for leg in legs}) == 1 else "unknown",
        external_order_id=legs[0].external_order_id,
        external_trade_id=legs[0].external_trade_id,
        source=legs[0].source,
        observed_at_utc=_first_observed_at(legs),
    )


def _take_trade_legs(legs: list[MicrocycleLegEvidence], target_size: float) -> list[MicrocycleLegEvidence]:
    return _take_legs(legs, target_size)


def _take_order_legs(legs: list[MicrocycleLegEvidence], target_size: float) -> list[MicrocycleLegEvidence]:
    return _take_legs(legs, target_size)


def _take_legs(legs: list[MicrocycleLegEvidence], target_size: float) -> list[MicrocycleLegEvidence]:
    remaining = target_size
    taken: list[MicrocycleLegEvidence] = []
    for index, leg in enumerate(list(legs)):
        if remaining <= 1e-9:
            break
        take = min(leg.shares, remaining)
        if take <= 1e-9:
            continue
        taken.append(leg.model_copy(update={"shares": round(take, 6)}))
        legs[index] = leg.model_copy(update={"shares": round(leg.shares - take, 6)})
        remaining -= take
    return taken


def _budget_exhausted(budget: dict[str, Any], *, min_required_notional: float) -> bool:
    status = str(budget.get("budget_status") or "").strip().lower()
    if status in {"exhausted", "over_budget"}:
        return True
    remaining = _float(budget.get("remaining_notional_usd"))
    return remaining is not None and remaining + 1e-9 < max(min_required_notional, 0.0)


def _target_status(target_row: dict[str, Any]) -> str:
    return str(target_row.get("target_status") or "").strip().lower()


def _direct_trades(direct_clob: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for key in ("current_token_trades", "event_trades", "direct_trades", "trades"):
        value = direct_clob.get(key)
        if isinstance(value, dict):
            candidates.extend(value.get("trades") or value.get("items") or [])
        elif isinstance(value, list):
            candidates.extend(value)
    return [dict(item) for item in candidates if isinstance(item, dict)]


def _trade_actor(trade: dict[str, Any], *, known_external_order_ids: set[str] | None) -> MicrocycleActor:
    actor = str(trade.get("actor") or trade.get("source_actor") or trade.get("owner") or "").strip().lower()
    if actor in {"janus", "operator"}:
        return actor  # type: ignore[return-value]
    order_id = _trade_order_id(trade)
    if order_id and known_external_order_ids is not None:
        known = {item.lower() for item in known_external_order_ids}
        return "janus" if order_id.lower() in known else "operator"
    return "unknown"


def _trade_order_id(trade: dict[str, Any]) -> str | None:
    for key in ("taker_order_id", "maker_order_id", "order_id", "orderID", "orderId", "external_order_id"):
        value = str(trade.get(key) or "").strip()
        if value:
            return value
    return None


def _external_order_id(order: dict[str, Any]) -> str | None:
    for key in ("id", "orderID", "orderId", "order_id", "external_order_id"):
        value = str(order.get(key) or "").strip()
        if value:
            return value
    return None


def _external_trade_id(trade: dict[str, Any]) -> str | None:
    for key in ("id", "trade_id", "external_trade_id", "hash", "tx_hash", "transaction_hash"):
        value = str(trade.get(key) or "").strip()
        if value:
            return value
    return None


def _observed_at(row: dict[str, Any]) -> datetime | None:
    for key in ("timestamp_utc", "created_at_utc", "created_at", "matched_at", "updated_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cycle_id(event_id: str, sleeve_id: str, token_id: str) -> str:
    digest = hashlib.sha1(f"{event_id}:{sleeve_id}:{token_id}".encode("utf-8")).hexdigest()[:12]
    return f"pmc-{digest}"


def _sum_leg_shares(legs: list[MicrocycleLegEvidence]) -> float:
    return round(sum(leg.shares for leg in legs if leg.shares > 0.0), 6)


def _first_observed_at(legs: list[MicrocycleLegEvidence]) -> datetime | None:
    timestamps = [leg.observed_at_utc for leg in legs if leg.observed_at_utc is not None]
    return min(timestamps) if timestamps else None


def _weighted_price(legs: list[MicrocycleLegEvidence]) -> float | None:
    priced = [leg for leg in legs if leg.price is not None and leg.shares > 0.0]
    shares = sum(leg.shares for leg in priced)
    if shares <= 0.0:
        return None
    return round(sum(leg.shares * float(leg.price) for leg in priced) / shares, 6)


def _model_or_dict(value: dict[str, Any] | BaseModel | None) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _parse_engine_evidence(evidence: PairedMicrocycleEngineEvidence | dict[str, Any]) -> PairedMicrocycleEngineEvidence:
    if isinstance(evidence, PairedMicrocycleEngineEvidence):
        return evidence
    return PairedMicrocycleEngineEvidence.model_validate(evidence)


def _fillability_status(cycle: PairedMicrocycleEvidence) -> MicrocycleFillabilityStatus:
    if cycle.status == "awaiting_buy":
        return "awaiting_buy_fill"
    if cycle.status == "sell_candidate":
        return "paired_sell_candidate"
    if cycle.status == "sell_open_waiting":
        return "paired_sell_open_unfilled"
    if cycle.status == "sell_stale_replace":
        return "paired_sell_stale"
    if cycle.status == "sell_filled_review_rebuy":
        return "round_trip_filled"
    if cycle.status == "rebuy_candidate":
        return "rebuy_review_ready"
    if cycle.status == "rebuy_blocked":
        return "rebuy_blocked"
    return "blocked"


def _missed_transition_codes(cycle: PairedMicrocycleEvidence) -> list[str]:
    codes: list[str] = []
    if cycle.status == "sell_candidate":
        codes.append("paired_sell_missing_after_buy_fill")
    if cycle.status == "sell_open_waiting":
        codes.append("paired_sell_unfilled_before_readback")
    if cycle.status == "sell_stale_replace":
        codes.append("paired_sell_stale_before_readback")
    if cycle.status == "rebuy_candidate":
        codes.append("rebuy_review_pending_after_sell_fill")
    if cycle.status == "rebuy_blocked":
        codes.append("rebuy_blocked_after_sell_fill")
    if cycle.duplicate_buy_blocked:
        codes.append("duplicate_buy_blocked_while_cycle_unresolved")
    return _unique(codes)


def _readback_blocker_codes(cycle: PairedMicrocycleEvidence, missed: list[str]) -> list[str]:
    blockers = list(missed)
    if "event_budget_exhausted" in cycle.reason_codes:
        blockers.append("event_budget_exhausted")
    if cycle.buy_leg.status == "missing":
        blockers.append("buy_fill_missing")
    return _unique(blockers)


def _leg_latency_seconds(buy: MicrocycleLegEvidence, sell: MicrocycleLegEvidence | None) -> float | None:
    if sell is None or buy.observed_at_utc is None or sell.observed_at_utc is None:
        return None
    delta = (sell.observed_at_utc - buy.observed_at_utc).total_seconds()
    return round(max(0.0, delta), 3)


def _round_money(value: float) -> float:
    return round(value, 6)


def _format_optional_money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"


def _format_optional_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


__all__ = [
    "MicrocycleLegEvidence",
    "PairedMicrocycleEngineEvidence",
    "PairedMicrocycleEvidence",
    "PairedMicrocycleReadbackScore",
    "PairedMicrocycleReadbackScoringEvidence",
    "build_paired_microcycle_evidence",
    "build_paired_microcycle_readback_score",
    "render_paired_microcycle_readback_score_markdown",
    "write_paired_microcycle_readback_score",
]
