from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect
from crypto_options_app.signals.validation.result_store import validation_status
from crypto_options_app.strategies.promotion import promotion_policy_contract


def build_strategy_revision_scout(conn: Any) -> dict[str, Any]:
    policy_contract = promotion_policy_contract()
    signal_status = validation_status(conn)
    signal_rows = list(signal_status.get("signals") or [])
    strategy_rows = _strategy_promotion_rows(conn)
    signal_coverage = summarize_signal_coverage(signal_rows, policy_contract=policy_contract)
    strategy_summary = summarize_strategy_promotion(strategy_rows, policy_contract=policy_contract)
    signal_gate = summarize_signal_gate(signal_status, signal_rows, policy_contract=policy_contract)
    return {
        "schema_version": "crypto_options_strategy_revision_scout_v1",
        "policy_contract_schema_version": policy_contract["schema_version"],
        "policy_contract": policy_contract,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
        "signal_gate": signal_gate,
        "signal_coverage": signal_coverage,
        "strategy_summary": strategy_summary,
        "recommended_next_lanes": recommend_next_lanes(
            promotion_ready_counts=signal_coverage["promotion_ready_by_type_source"],
            revision_counts=signal_coverage["revision_by_type_source"],
            strategy_summary=strategy_summary,
        ),
    }


def write_strategy_revision_scout_report(payload: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Strategy Revision Scout",
        "",
        f"Generated UTC: `{payload['generated_at_utc']}`",
        "",
        "## Signal Gate",
        "",
        f"- Signals: {payload['signal_gate']['signal_count']}",
        f"- Promotion ready: {payload['signal_gate']['promotion_ready_signal_count']}",
        f"- Needs revision: {payload['signal_gate']['revision_signal_count']}",
        f"- Strict replay / not promotable labels: {payload['signal_gate']['strict_replay_required_count']}",
        f"- Policy contract: `{payload.get('policy_contract_schema_version') or 'missing'}`",
        "",
        "## Strategy State",
        "",
        f"- Promotion states: `{json.dumps(payload['strategy_summary']['by_promotion_state'], sort_keys=True)}`",
        f"- Strategies with recent economic samples: {payload['strategy_summary']['strategies_with_recent_economics']}",
        f"- Strategies clearing live policy: {payload['strategy_summary']['strategies_clearing_live_policy']}",
        "",
        "## Recommended Next Lanes",
        "",
    ]
    for lane in payload["recommended_next_lanes"]:
        lines.extend(
            [
                f"### {lane['lane_id']}",
                "",
                f"- Priority: {lane['priority']}",
                f"- Goal: {lane['goal']}",
                f"- Rationale: {lane['rationale']}",
                f"- Suggested strategy shape: `{lane['suggested_strategy_shape']}`",
                f"- Validation: {lane['validation']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Safety",
            "",
            "- This scout is read-only.",
            "- It does not authorize orders.",
            "- Supervised live promotion still requires the policy gates.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def summarize_signal_gate(
    signal_status: dict[str, Any],
    signal_rows: list[dict[str, Any]],
    *,
    policy_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = policy_contract or promotion_policy_contract()
    signal_policy = contract.get("signals") if isinstance(contract.get("signals"), dict) else {}
    promotable_state = str(signal_policy.get("promotable_state") or "PROMOTION_READY")
    not_promotable_labels = {str(label) for label in signal_policy.get("not_promotable_labels") or ()}
    by_state = dict(signal_status.get("by_promotion_state") or {})
    return {
        "signal_count": signal_status.get("signal_count", 0),
        "promotable_state": promotable_state,
        "promotion_ready_signal_count": by_state.get(promotable_state, 0),
        "revision_signal_count": by_state.get("NEEDS_V2_REVIEW", 0),
        "strict_replay_required_count": sum(1 for row in signal_rows if str(row.get("promotion_state") or "") in not_promotable_labels),
        "not_promotable_labels": sorted(not_promotable_labels),
        "policy_contract_schema_version": contract.get("schema_version"),
    }


def summarize_signal_coverage(
    signal_rows: list[dict[str, Any]],
    *,
    policy_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = policy_contract or promotion_policy_contract()
    signal_policy = contract.get("signals") if isinstance(contract.get("signals"), dict) else {}
    promotable_state = str(signal_policy.get("promotable_state") or "PROMOTION_READY")
    not_promotable_labels = {str(label) for label in signal_policy.get("not_promotable_labels") or ()}
    promotion_ready = _count_signal_rows(signal_rows, promotion_state=promotable_state)
    revision = _count_signal_rows(signal_rows, promotion_state="NEEDS_V2_REVIEW")
    not_promotable = _count_signal_rows_by_states(signal_rows, promotion_states=not_promotable_labels)
    return {
        "promotable_state": promotable_state,
        "not_promotable_labels": sorted(not_promotable_labels),
        "promotion_ready_by_type_source": promotion_ready,
        "revision_by_type_source": revision,
        "not_promotable_by_state": not_promotable["by_state"],
        "not_promotable_by_type_source": not_promotable["by_type_source"],
        "top_promotion_ready": _top_counts(promotion_ready),
        "top_revision": _top_counts(revision),
        "top_not_promotable": _top_counts(not_promotable["by_type_source"]),
    }


def summarize_strategy_promotion(
    strategy_rows: list[dict[str, Any]],
    *,
    policy_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = policy_contract or promotion_policy_contract()
    strategy_policy = contract.get("strategies") if isinstance(contract.get("strategies"), dict) else {}
    requirements = strategy_policy.get("live_candidate_requirements") if isinstance(strategy_policy.get("live_candidate_requirements"), dict) else {}
    sample_threshold = int(requirements.get("recent_distinct_economic_samples") or 12)
    win_rate_threshold = float(requirements.get("recent_shadow_live_win_rate_gt") or 0.70)
    pnl_threshold = float(requirements.get("recent_shadow_live_pnl_usd_gt") or 0.0)
    by_state = Counter(str(row.get("promotion_state") or "UNKNOWN") for row in strategy_rows)
    strategies_with_recent = 0
    strategies_clearing = 0
    reviewed: list[dict[str, Any]] = []
    for row in strategy_rows:
        evidence_payload = _json_load(row.get("evidence_json"), {})
        evidence = evidence_payload.get("evidence") if isinstance(evidence_payload.get("evidence"), dict) else {}
        recent_samples = int(evidence.get("recent_shadow_live_economic_sample_count") or 0)
        recent_win_rate = evidence.get("recent_shadow_live_win_rate")
        recent_pnl = float(evidence.get("recent_shadow_live_simulated_pnl_usd") or 0.0)
        blockers = list(evidence_payload.get("blockers") or [])
        if recent_samples > 0:
            strategies_with_recent += 1
        clears = (
            recent_samples >= sample_threshold
            and recent_win_rate is not None
            and float(recent_win_rate) > win_rate_threshold
            and recent_pnl > pnl_threshold
            and not blockers
        )
        if clears:
            strategies_clearing += 1
        reviewed.append(
            {
                "strategy_id": row.get("strategy_id"),
                "promotion_state": row.get("promotion_state"),
                "recent_samples": recent_samples,
                "recent_win_rate": recent_win_rate,
                "recent_pnl": recent_pnl,
                "clears_live_policy": clears,
                "blockers": blockers,
                "next_action": evidence_payload.get("next_action"),
            }
        )
    return {
        "strategy_count": len(strategy_rows),
        "by_promotion_state": dict(sorted(by_state.items())),
        "strategies_with_recent_economics": strategies_with_recent,
        "strategies_clearing_live_policy": strategies_clearing,
        "policy_contract_schema_version": contract.get("schema_version"),
        "live_candidate_requirements": {
            "recent_distinct_economic_samples": sample_threshold,
            "recent_shadow_live_win_rate_gt": win_rate_threshold,
            "recent_shadow_live_pnl_usd_gt": pnl_threshold,
        },
        "strategies": reviewed,
    }


def recommend_next_lanes(
    *,
    promotion_ready_counts: dict[str, int],
    revision_counts: dict[str, int],
    strategy_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    lanes: list[dict[str, Any]] = []
    if revision_counts.get("outcome_prediction|B", 0) or revision_counts.get("hedge_ratio|B", 0):
        lanes.append(
            {
                "lane_id": "repair_profile_distribution_executable_signal",
                "priority": 1,
                "goal": "Turn B Profiles from observation pressure into executable outcome/hedge signals.",
                "rationale": "Profile outcome and hedge-ratio signals dominate the revision queue, while failed strategies depend on B-side executable pressure.",
                "suggested_strategy_shape": "profile_distribution_threshold_shadow_v1",
                "validation": "Run forward-shadow only after B signal variants pass stricter review; require 12 samples, >70% win rate, positive PnL.",
            }
        )
    if promotion_ready_counts.get("outcome_prediction|A", 0) and (
        promotion_ready_counts.get("support_resistance|C", 0) or promotion_ready_counts.get("grid_spacing|C", 0)
    ):
        lanes.append(
            {
                "lane_id": "crypto_outcome_option_context_control",
                "priority": 2,
                "goal": "Test simple A Crypto directional control gated by C Options support/grid context.",
                "rationale": "A-only outcome prediction and C option context have promotion-ready coverage; this is the simplest non-profile candidate family.",
                "suggested_strategy_shape": "crypto_direction_option_context_hold_60s_v1",
                "validation": "Use the multi-scenario forward-mark sampler before any supervised live attempt.",
            }
        )
    if promotion_ready_counts.get("liquidity_depth|C", 0) and promotion_ready_counts.get("stale_order_review|C", 0):
        lanes.append(
            {
                "lane_id": "option_liquidity_micro_scalp_shadow",
                "priority": 3,
                "goal": "Validate an option-price-only liquidity/scalp lane without profile dependency.",
                "rationale": "C-only liquidity and stale-order-review signals are promotion-ready and directly address previous fillability/slippage gaps.",
                "suggested_strategy_shape": "option_liquidity_depth_spread_reclaim_v1",
                "validation": "Shadow only; compare entry ask to 30s and 60s forward bid marks.",
            }
        )
    if strategy_summary.get("strategies_clearing_live_policy", 0) == 0:
        lanes.append(
            {
                "lane_id": "retire_or_revision_existing_live_lanes",
                "priority": 4,
                "goal": "Stop recycling current failed lanes unchanged.",
                "rationale": "No registered strategy currently clears the promotion policy after forward-mark economics.",
                "suggested_strategy_shape": "revision backlog, not a live lane",
                "validation": "Keep current failed lanes in shadow/review until revised variants are registered.",
            }
        )
    return lanes


def _strategy_promotion_rows(conn: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT strategy_id, strategy_version, promotion_state, evidence_json, updated_at_utc
              FROM strategy_promotion_state
             ORDER BY strategy_id
            """
        ).fetchall()
    ]


def _count_signal_rows(signal_rows: list[dict[str, Any]], *, promotion_state: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in signal_rows:
        if str(row.get("promotion_state") or "") != promotion_state:
            continue
        signal_type = str(row.get("signal_type") or row.get("type") or "unknown")
        blocks = ",".join(str(block) for block in (row.get("source_blocks") or [])) or "unknown"
        counter[f"{signal_type}|{blocks}"] += 1
    return dict(sorted(counter.items()))


def _count_signal_rows_by_states(
    signal_rows: list[dict[str, Any]],
    *,
    promotion_states: set[str],
) -> dict[str, dict[str, int]]:
    by_state: Counter[str] = Counter()
    by_type_source: Counter[str] = Counter()
    for row in signal_rows:
        state = str(row.get("promotion_state") or "")
        if state not in promotion_states:
            continue
        by_state[state] += 1
        signal_type = str(row.get("signal_type") or row.get("type") or "unknown")
        blocks = ",".join(str(block) for block in (row.get("source_blocks") or [])) or "unknown"
        by_type_source[f"{signal_type}|{blocks}"] += 1
    return {
        "by_state": dict(sorted(by_state.items())),
        "by_type_source": dict(sorted(by_type_source.items())),
    }


def _top_counts(counts: dict[str, int], *, limit: int = 12) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _json_load(value: Any, fallback: Any) -> Any:
    if value in {None, ""}:
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback
