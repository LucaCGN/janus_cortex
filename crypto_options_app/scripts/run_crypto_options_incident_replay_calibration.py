from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crypto_options_app.db.postgres_connection import PostgresCompatConnection
from crypto_options_app.replay.fill_simulation import (
    PathFillSimulationConfig,
    ReplayOrder,
    simulate_fill_path,
)
from crypto_options_app.replay.frames import ReplayFrame


DEFAULT_INCIDENT_JSON = Path(
    "crypto_options_app/artifacts/team_coordination/incident_live_strategy_performance_20260608.json"
)
DEFAULT_REPORT_JSON = Path(
    "crypto_options_app/artifacts/reports/incident_replay_calibration_latest.json"
)
DEFAULT_REPORT_MD = Path(
    "crypto_options_app/artifacts/reports/incident_replay_calibration_latest.md"
)


@dataclass(frozen=True)
class LiveDecision:
    run_id: str
    strategy_id: str
    strategy_version: str
    decision_at_utc: datetime
    event_slug: str
    event_token_key: str
    outcome: str
    filled_shares: float
    fill_price: float
    measured_latency_ms: float | None = None
    source_latency_ms: float | None = None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay the 2026-06-08 live-loss incident fills against captured quote paths."
    )
    parser.add_argument("--incident-json", type=Path, default=DEFAULT_INCIDENT_JSON)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--start-utc", default="2026-06-08T08:00:00+00:00")
    parser.add_argument("--end-utc", default="2026-06-08T10:45:00+00:00")
    parser.add_argument("--latency-ms", type=float, default=500.0)
    parser.add_argument(
        "--latency-mode",
        choices=("fixed", "measured", "source"),
        default="measured",
        help="Latency source for path replay. measured uses live submission timing when present, source uses option-path source latency, fixed always uses --latency-ms.",
    )
    parser.add_argument("--min-latency-ms", type=float, default=250.0)
    parser.add_argument("--max-latency-ms", type=float, default=5000.0)
    parser.add_argument("--ttl-seconds", type=float, default=5.0)
    parser.add_argument("--max-decisions", type=int, default=200)
    args = parser.parse_args()

    incident = json.loads(args.incident_json.read_text(encoding="utf-8"))
    start_at = _parse_datetime(args.start_utc)
    end_at = _parse_datetime(args.end_utc)
    outcomes = _incident_outcomes(incident)
    actual_by_strategy = {
        _strategy_key(row["strategy"], row["version"]): row
        for row in incident.get("strategy_summary", [])
    }

    with PostgresCompatConnection(readonly=True) as conn:
        decisions = _load_live_decisions(
            conn,
            start_at=start_at,
            end_at=end_at,
            limit=args.max_decisions,
        )
        calibrated_rows = [
            _calibrate_decision(
                conn,
                decision=decision,
                outcomes=outcomes,
                latency_ms=args.latency_ms,
                latency_mode=args.latency_mode,
                min_latency_ms=args.min_latency_ms,
                max_latency_ms=args.max_latency_ms,
                ttl_seconds=args.ttl_seconds,
            )
            for decision in decisions
        ]

    strategy_rows = _aggregate_by_strategy(calibrated_rows, actual_by_strategy)
    event_rows = _aggregate_by_event(calibrated_rows)
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "promotion_scoring_enabled": True,
        "window": {"start": start_at.isoformat(), "end": end_at.isoformat()},
        "latency_ms": args.latency_ms,
        "latency_mode": args.latency_mode,
        "latency_bounds_ms": {"min": args.min_latency_ms, "max": args.max_latency_ms},
        "ttl_seconds": args.ttl_seconds,
        "counts": {
            "decisions": len(decisions),
            "calibrated_filled": sum(1 for row in calibrated_rows if row["simulated_status"] in {"filled", "partial"}),
            "calibrated_unfilled": sum(1 for row in calibrated_rows if row["simulated_status"] == "unfilled"),
            "calibrated_blocked": sum(1 for row in calibrated_rows if row["simulated_status"] == "blocked"),
        },
        "strategy_summary": strategy_rows,
        "event_summary": event_rows,
        "data_quality": _data_quality_summary(calibrated_rows),
        "decision_rows": calibrated_rows,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.write_text(_markdown_report(report), encoding="utf-8")
    print(json.dumps({"report_json": str(args.report_json), "report_md": str(args.report_md), "counts": report["counts"]}, indent=2))


def _load_live_decisions(
    conn: PostgresCompatConnection,
    *,
    start_at: datetime,
    end_at: datetime,
    limit: int,
) -> list[LiveDecision]:
    rows = conn.execute(
        """
        SELECT run_id, strategy_id, strategy_version, started_at_utc, evidence_json
        FROM strategy_validation_runs
        WHERE started_at_utc >= ?
          AND started_at_utc <= ?
          AND lower(run_type) IN ('live', 'supervised_live')
          AND evidence_json IS NOT NULL
        ORDER BY started_at_utc ASC
        LIMIT ?
        """,
        (start_at.isoformat(), end_at.isoformat(), int(limit)),
    ).fetchall()
    decisions: list[LiveDecision] = []
    for row in rows:
        evidence = json.loads(row["evidence_json"] or "{}")
        result = evidence.get("result") or {}
        filled_shares = _optional_float(result.get("filled_shares"))
        fill_price = _optional_float(result.get("fill_price"))
        outcome = str(result.get("outcome") or "").strip()
        event_slug = str(result.get("event_slug") or "").strip()
        event_token_key = str(result.get("event_token_key") or "").strip()
        if not event_slug:
            event_slug = str(
                (((result.get("attribution") or {}).get("signal_context") or {}).get("option_path") or {}).get(
                    "event_slug"
                )
                or ""
            ).strip()
        if (filled_shares is None or fill_price is None) and result.get("attribution"):
            attribution = result["attribution"]
            filled_shares = _optional_float(attribution.get("filled_shares"))
            fill_price = _optional_float(attribution.get("fill_price"))
        if not event_token_key and event_slug and outcome:
            event_token_key = f"{event_slug}:{outcome.lower()}"
        if not event_slug or not event_token_key or not outcome or not filled_shares or fill_price is None:
            continue
        attribution = result.get("attribution") if isinstance(result.get("attribution"), dict) else {}
        source_context = attribution.get("signal_context") if isinstance(attribution.get("signal_context"), dict) else {}
        option_path = source_context.get("option_path") if isinstance(source_context.get("option_path"), dict) else {}
        decisions.append(
            LiveDecision(
                run_id=str(row["run_id"]),
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                decision_at_utc=_parse_datetime(row["started_at_utc"]),
                event_slug=event_slug,
                event_token_key=event_token_key,
                outcome=outcome,
                filled_shares=float(filled_shares),
                fill_price=float(fill_price),
                measured_latency_ms=_extract_measured_latency_ms(result),
                source_latency_ms=_first_float(
                    option_path.get("avg_source_latency_ms"),
                    option_path.get("max_source_latency_ms"),
                    source_context.get("avg_source_latency_ms"),
                    source_context.get("max_source_latency_ms"),
                ),
            )
        )
    return decisions


def _calibrate_decision(
    conn: PostgresCompatConnection,
    *,
    decision: LiveDecision,
    outcomes: dict[str, str],
    latency_ms: float,
    latency_mode: str,
    min_latency_ms: float,
    max_latency_ms: float,
    ttl_seconds: float,
) -> dict[str, Any]:
    effective_latency_ms, latency_source = _effective_latency_ms(
        decision,
        fallback_latency_ms=latency_ms,
        latency_mode=latency_mode,
        min_latency_ms=min_latency_ms,
        max_latency_ms=max_latency_ms,
    )
    event_token_key = _resolve_event_token_key(
        conn,
        event_token_key=decision.event_token_key,
        event_slug=decision.event_slug,
        outcome=decision.outcome,
    )
    frames = _load_price_path_frames(
        conn,
        event_token_key=event_token_key,
        start_at=decision.decision_at_utc - timedelta(seconds=2),
        end_at=decision.decision_at_utc + timedelta(milliseconds=effective_latency_ms) + timedelta(seconds=ttl_seconds + 2),
    )
    order = ReplayOrder(
        order_type="LIMIT",
        side="BUY",
        shares=decision.filled_shares,
        decision_at_utc=decision.decision_at_utc,
        limit_price=decision.fill_price,
    )
    result = simulate_fill_path(
        order,
        frames,
        config=PathFillSimulationConfig(latency_ms=effective_latency_ms, ttl_seconds=ttl_seconds),
    )
    frame_quality = _frame_quality(frames, decision_at=decision.decision_at_utc, latency_ms=effective_latency_ms)
    resolved = outcomes.get(decision.event_slug)
    simulated_cost = 0.0
    simulated_pnl = 0.0
    if result.fillability_status in {"filled", "partial"} and result.fill_price is not None:
        simulated_cost = float(result.filled_shares) * float(result.fill_price)
        simulated_pnl = _settlement_pnl(
            outcome=decision.outcome,
            resolved=resolved,
            shares=float(result.filled_shares),
            price=float(result.fill_price),
        )
    actual_cost = decision.filled_shares * decision.fill_price
    actual_pnl = _settlement_pnl(
        outcome=decision.outcome,
        resolved=resolved,
        shares=decision.filled_shares,
        price=decision.fill_price,
    )
    return {
        "run_id": decision.run_id,
        "strategy_id": decision.strategy_id,
        "strategy_version": decision.strategy_version,
        "strategy_key": _strategy_key(decision.strategy_id, decision.strategy_version),
        "decision_at_utc": decision.decision_at_utc.isoformat(),
        "event_slug": decision.event_slug,
        "event_token_key": event_token_key,
        "recorded_event_token_key": decision.event_token_key,
        "outcome": decision.outcome,
        "resolved_outcome": resolved,
        "actual_filled_shares": round(decision.filled_shares, 8),
        "actual_fill_price": round(decision.fill_price, 8),
        "actual_cost": round(actual_cost, 8),
        "actual_pnl": round(actual_pnl, 8) if resolved else None,
        "simulated_status": result.fillability_status,
        "simulated_blockers": list(result.blockers),
        "simulated_filled_shares": round(result.filled_shares, 8),
        "simulated_fill_price": round(result.fill_price, 8) if result.fill_price is not None else None,
        "simulated_cost": round(simulated_cost, 8),
        "simulated_pnl": round(simulated_pnl, 8) if resolved else None,
        "simulated_slippage": round(result.slippage, 8),
        "latency_ms": round(effective_latency_ms, 6),
        "latency_source": latency_source,
        "measured_latency_ms": None if decision.measured_latency_ms is None else round(decision.measured_latency_ms, 6),
        "source_latency_ms": None if decision.source_latency_ms is None else round(decision.source_latency_ms, 6),
        "frames_loaded": len(frames),
        "frame_quality": frame_quality,
        "simulation": result.simulation,
    }


def _load_price_path_frames(
    conn: PostgresCompatConnection,
    *,
    event_token_key: str,
    start_at: datetime,
    end_at: datetime,
) -> list[ReplayFrame]:
    rows = conn.execute(
        """
        SELECT event_key, event_token_key, event_slug, outcome, system_received_at_utc,
               best_bid, best_ask, mid_price, depth_top3_bid_size, depth_top3_ask_size
        FROM polymarket_price_ticks
        WHERE event_token_key = ?
          AND system_received_at_utc >= ?
          AND system_received_at_utc <= ?
        ORDER BY system_received_at_utc ASC
        LIMIT 1000
        """,
        (event_token_key, start_at.isoformat(), end_at.isoformat()),
    ).fetchall()
    frames: list[ReplayFrame] = []
    for index, row in enumerate(rows):
        item = dict(row)
        observed_at = _parse_datetime(item["system_received_at_utc"])
        frames.append(
            ReplayFrame(
                replay_frame_key=f"incident:{event_token_key}:{index}",
                event_key=str(item.get("event_key") or item.get("event_slug") or ""),
                event_token_key=str(item.get("event_token_key") or event_token_key),
                replay_timestamp_utc=observed_at,
                source_observed_at_utc=observed_at,
                decision_at_utc=observed_at,
                market_state={
                    "system_received_at_utc": observed_at.isoformat(),
                    "best_bid": _optional_float(item.get("best_bid")),
                    "best_ask": _optional_float(item.get("best_ask")),
                    "mid_price": _optional_float(item.get("mid_price")),
                    "depth_top3_bid_size": _optional_float(item.get("depth_top3_bid_size")),
                    "depth_top3_ask_size": _optional_float(item.get("depth_top3_ask_size")),
                },
                underlying_state={"observed_at_utc": observed_at.isoformat(), "price": None},
            )
        )
    return frames


def _resolve_event_token_key(
    conn: PostgresCompatConnection,
    *,
    event_token_key: str,
    event_slug: str,
    outcome: str,
) -> str:
    existing = conn.execute(
        "SELECT event_token_key FROM polymarket_price_ticks WHERE event_token_key = ? LIMIT 1",
        (event_token_key,),
    ).fetchone()
    if existing is not None:
        return event_token_key
    row = conn.execute(
        """
        SELECT event_token_key
        FROM event_tokens
        WHERE event_slug = ?
          AND lower(outcome) = lower(?)
        LIMIT 1
        """,
        (event_slug, outcome),
    ).fetchone()
    if row is None:
        return event_token_key
    return str(row["event_token_key"])


def _aggregate_by_strategy(
    rows: list[dict[str, Any]],
    actual_by_strategy: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["strategy_key"]].append(row)
    output = []
    for key, items in sorted(grouped.items()):
        actual = actual_by_strategy.get(key, {})
        simulated_pnl = sum(float(item.get("simulated_pnl") or 0.0) for item in items)
        actual_pnl = actual.get("resolved_pnl")
        output.append(
            {
                "strategy_key": key,
                "decisions": len(items),
                "actual_resolved_pnl": actual_pnl,
                "actual_live_win_rate": actual.get("live_win_rate"),
                "simulated_pnl": round(simulated_pnl, 8),
                "simulated_filled": sum(1 for item in items if item["simulated_status"] in {"filled", "partial"}),
                "simulated_unfilled": sum(1 for item in items if item["simulated_status"] == "unfilled"),
                "simulated_blocked": sum(1 for item in items if item["simulated_status"] == "blocked"),
                "data_quality_score": round(
                    sum(float((item.get("frame_quality") or {}).get("score") or 0.0) for item in items) / max(1, len(items)),
                    6,
                ),
                "divergence_vs_actual": round(simulated_pnl - float(actual_pnl), 8) if actual_pnl is not None else None,
            }
        )
    return output


def _aggregate_by_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["event_slug"], row["outcome"])].append(row)
    output = []
    for (event_slug, outcome), items in sorted(grouped.items(), key=lambda entry: sum(float(item.get("actual_cost") or 0.0) for item in entry[1]), reverse=True):
        output.append(
            {
                "event_slug": event_slug,
                "outcome": outcome,
                "strategies": len({item["strategy_key"] for item in items}),
                "decisions": len(items),
                "actual_cost": round(sum(float(item.get("actual_cost") or 0.0) for item in items), 8),
                "actual_pnl": round(sum(float(item.get("actual_pnl") or 0.0) for item in items), 8),
                "simulated_cost": round(sum(float(item.get("simulated_cost") or 0.0) for item in items), 8),
                "simulated_pnl": round(sum(float(item.get("simulated_pnl") or 0.0) for item in items), 8),
            }
        )
    return output


def _incident_outcomes(incident: dict[str, Any]) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for row in incident.get("event_summary", []):
        event = str(row.get("event") or "")
        resolved = str(row.get("resolved") or "")
        if event and resolved:
            outcomes[event] = resolved
    return outcomes


def _settlement_pnl(*, outcome: str, resolved: str | None, shares: float, price: float) -> float:
    if not resolved:
        return 0.0
    payout = shares if outcome.strip().lower() == resolved.strip().lower() else 0.0
    return payout - (shares * price)


def _effective_latency_ms(
    decision: LiveDecision,
    *,
    fallback_latency_ms: float,
    latency_mode: str,
    min_latency_ms: float,
    max_latency_ms: float,
) -> tuple[float, str]:
    candidates: list[tuple[float | None, str]] = []
    if latency_mode == "measured":
        candidates.append((decision.measured_latency_ms, "measured_submission_latency"))
        candidates.append((decision.source_latency_ms, "option_path_source_latency"))
    elif latency_mode == "source":
        candidates.append((decision.source_latency_ms, "option_path_source_latency"))
        candidates.append((decision.measured_latency_ms, "measured_submission_latency"))
    candidates.append((fallback_latency_ms, "fixed_fallback_latency"))
    for value, source in candidates:
        if value is None:
            continue
        bounded = min(max_latency_ms, max(min_latency_ms, float(value)))
        return bounded, source
    return max(min_latency_ms, float(fallback_latency_ms)), "fixed_fallback_latency"


def _extract_measured_latency_ms(result: dict[str, Any]) -> float | None:
    attribution = result.get("attribution") if isinstance(result.get("attribution"), dict) else {}
    payload = attribution.get("order_source_payload") if isinstance(attribution.get("order_source_payload"), dict) else {}
    response = payload.get("live_executor_response") if isinstance(payload.get("live_executor_response"), dict) else {}
    legacy = response.get("legacy_submission") if isinstance(response.get("legacy_submission"), dict) else {}
    jit_quote = legacy.get("jit_quote") if isinstance(legacy.get("jit_quote"), dict) else {}
    latency = legacy.get("latency") if isinstance(legacy.get("latency"), dict) else {}
    return _first_float(
        jit_quote.get("latency_ms"),
        latency.get("submit_total_ms"),
        latency.get("asset_check_ms"),
        response.get("latency_ms"),
    )


def _frame_quality(frames: list[ReplayFrame], *, decision_at: datetime, latency_ms: float) -> dict[str, Any]:
    target = decision_at + timedelta(milliseconds=latency_ms)
    post_latency = [frame for frame in frames if frame.replay_timestamp_utc >= target]
    first_post_age_ms = None
    if post_latency:
        first_post_age_ms = (post_latency[0].replay_timestamp_utc - target).total_seconds() * 1000.0
    score = 0.0
    blockers: list[str] = []
    if not frames:
        blockers.append("no_quote_frames_loaded")
    if not post_latency:
        blockers.append("no_post_latency_quote_frame")
    else:
        if first_post_age_ms is not None and first_post_age_ms > 2000.0:
            blockers.append("post_latency_quote_frame_stale")
        score += 0.55
        if len(post_latency) >= 2:
            score += 0.25
        if first_post_age_ms is not None and first_post_age_ms <= 1000.0:
            score += 0.20
    return {
        "score": round(min(1.0, score), 6),
        "frames_loaded": len(frames),
        "post_latency_frame_count": len(post_latency),
        "first_post_latency_frame_age_ms": None if first_post_age_ms is None else round(first_post_age_ms, 6),
        "blockers": blockers,
    }


def _data_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"score": 0.0, "decision_count": 0, "blockers": ["no_decisions"]}
    scores = [float((row.get("frame_quality") or {}).get("score") or 0.0) for row in rows]
    blockers: dict[str, int] = {}
    for row in rows:
        for blocker in (row.get("frame_quality") or {}).get("blockers") or []:
            blockers[str(blocker)] = blockers.get(str(blocker), 0) + 1
    score = sum(scores) / max(1, len(scores))
    return {
        "score": round(score, 6),
        "decision_count": len(rows),
        "low_quality_decision_count": sum(1 for value in scores if value < 0.55),
        "blockers": dict(sorted(blockers.items())),
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Incident Replay Calibration",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        "",
        f"Window: `{report['window']['start']}` to `{report['window']['end']}`",
        f"Latency: `{report['latency_ms']}` ms",
        f"Latency mode: `{report.get('latency_mode')}`",
        f"TTL: `{report['ttl_seconds']}` seconds",
        f"Data quality score: `{(report.get('data_quality') or {}).get('score')}`",
        "",
        "## Counts",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Strategy Calibration",
            "",
            "| Strategy | Decisions | Actual PnL | Actual WR | Sim PnL | Sim filled | Sim unfilled | Quality | Divergence |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["strategy_summary"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['strategy_key']}`",
                    str(row["decisions"]),
                    _fmt_money(row.get("actual_resolved_pnl")),
                    _fmt_pct(row.get("actual_live_win_rate")),
                    _fmt_money(row.get("simulated_pnl")),
                    str(row["simulated_filled"]),
                    str(row["simulated_unfilled"]),
                    _fmt_ratio_pct(row.get("data_quality_score")),
                    _fmt_money(row.get("divergence_vs_actual")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Event/Outcome Exposure",
            "",
            "| Event | Outcome | Strategies | Decisions | Actual Cost | Actual PnL | Sim Cost | Sim PnL |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["event_summary"][:20]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['event_slug']}`",
                    str(row["outcome"]),
                    str(row["strategies"]),
                    str(row["decisions"]),
                    _fmt_money(row.get("actual_cost")),
                    _fmt_money(row.get("actual_pnl")),
                    _fmt_money(row.get("simulated_cost")),
                    _fmt_money(row.get("simulated_pnl")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This report replays actual recorded live decisions against captured quote paths with latency and TTL. It does not rerun strategy signal logic yet. It is the first calibration layer for issue #172.",
        ]
    )
    return "\n".join(lines) + "\n"


def _strategy_key(strategy_id: str, version: str) -> str:
    return f"{strategy_id}:{version}"


def _fmt_money(value: Any) -> str:
    if value is None:
        return ""
    return f"${float(value):.4f}"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%"


def _fmt_ratio_pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100.0:.1f}%"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


if __name__ == "__main__":
    main()
