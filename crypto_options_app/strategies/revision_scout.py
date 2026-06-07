from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect
from crypto_options_app.signals.validation.result_store import validation_status


def build_strategy_revision_scout(conn: Any) -> dict[str, Any]:
    signal_status = validation_status(conn)
    signal_rows = list(signal_status.get("signals") or [])
    strategy_rows = _strategy_promotion_rows(conn)
    signal_coverage = summarize_signal_coverage(signal_rows)
    strategy_summary = summarize_strategy_promotion(strategy_rows)
    return {
        "schema_version": "crypto_options_strategy_revision_scout_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
        "signal_gate": {
            "signal_count": signal_status.get("signal_count", 0),
            "promotion_ready_signal_count": signal_status.get("by_promotion_state", {}).get("PROMOTION_READY", 0),
            "revision_signal_count": signal_status.get("by_promotion_state", {}).get("NEEDS_V2_REVIEW", 0),
            "strict_replay_required_count": signal_status.get("by_promotion_state", {}).get("STRUCTURAL_PASS", 0),
        },
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


def summarize_signal_coverage(signal_rows: list[dict[str, Any]]) -> dict[str, Any]:
    promotion_ready = _count_signal_rows(signal_rows, promotion_state="PROMOTION_READY")
    revision = _count_signal_rows(signal_rows, promotion_state="NEEDS_V2_REVIEW")
    return {
        "promotion_ready_by_type_source": promotion_ready,
        "revision_by_type_source": revision,
        "top_promotion_ready": _top_counts(promotion_ready),
        "top_revision": _top_counts(revision),
    }


def summarize_strategy_promotion(strategy_rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        if recent_samples > 0:
            strategies_with_recent += 1
        clears = recent_samples >= 12 and recent_win_rate is not None and float(recent_win_rate) > 0.70 and recent_pnl > 0
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
                "blockers": list(evidence_payload.get("blockers") or []),
                "next_action": evidence_payload.get("next_action"),
            }
        )
    return {
        "strategy_count": len(strategy_rows),
        "by_promotion_state": dict(sorted(by_state.items())),
        "strategies_with_recent_economics": strategies_with_recent,
        "strategies_clearing_live_policy": strategies_clearing,
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
