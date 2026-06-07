from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.indicators.event_path_stats import compute_tail_comeback_table
from crypto_options_app.signals.validation.models import (
    SignalObservation,
    SignalValidationFrame,
    SignalValidationPhaseResult,
    SignalValidationRunResult,
)
from crypto_options_app.signals.validation.registry import get_signal_spec
from crypto_options_app.signals.validation.result_store import (
    claim_next_queue_item,
    finish_validation_run,
    start_validation_run,
    sync_signal_catalog,
)


def run_next_signal_validation(
    *,
    db_path: str | Path | None = None,
    owner_id: str,
    max_frames: int = 100,
    ttl_minutes: int = 10,
    now_utc: datetime | None = None,
) -> SignalValidationRunResult:
    """Run one read-only signal validation queue item.

    This first implementation validates the Pydantic/DB/replay plumbing. It never
    imports trading executors and never authorizes orders.
    """

    path = initialize_schema(db_path)
    now = _as_utc(now_utc or datetime.now(UTC))
    with connect(path) as conn:
        create_schema(conn)
        sync_signal_catalog(conn, enqueue=True, now_utc=now)
        queue_item = claim_next_queue_item(conn, owner_id=owner_id, ttl_minutes=ttl_minutes, now_utc=now)
        if queue_item is None:
            return SignalValidationRunResult(status="blocked", blockers=("no_claimable_signal_queue_item",))
        run_key = start_validation_run(conn, queue_item=queue_item, owner_id=owner_id, now_utc=now)
        spec = get_signal_spec(queue_item.signal_id)
        if spec is None:
            result = SignalValidationPhaseResult(
                signal_id=queue_item.signal_id,
                phase=queue_item.phase,
                status="blocked",
                evaluated_at_utc=now.isoformat(),
                sample_count=0,
                blockers=("signal_spec_missing",),
            )
            finish_validation_run(conn, validation_run_key=run_key, phase_result=result, observations=(), now_utc=now)
            return SignalValidationRunResult(validation_run_key=run_key, queue_item=queue_item, phase_result=result, status="blocked", blockers=result.blockers)

        frames = load_signal_validation_frames(
            conn,
            signal_id=queue_item.signal_id,
            phase=queue_item.phase,
            max_frames=max_frames,
            now_utc=now,
        )
        result, observations = evaluate_catalog_signal(spec, phase=queue_item.phase, frames=frames, evaluated_at_utc=now)
        finish_validation_run(conn, validation_run_key=run_key, phase_result=result, observations=observations, now_utc=now)
        return SignalValidationRunResult(
            validation_run_key=run_key,
            queue_item=queue_item,
            phase_result=result,
            observations=observations,
            status=result.status,
            blockers=result.blockers,
        )


def load_signal_validation_frames(
    conn: Any,
    *,
    signal_id: str,
    phase: str = "last_week_backtest",
    max_frames: int = 100,
    now_utc: datetime | None = None,
) -> tuple[SignalValidationFrame, ...]:
    rows = conn.execute(
        """
        SELECT replay_frame_key, event_key, event_token_key, decision_at_utc,
               source_observed_at_utc, frame_json
          FROM replay_frames
         ORDER BY decision_at_utc DESC
         LIMIT ?
        """,
        (max(1, int(max_frames)),),
    ).fetchall()
    frames: list[SignalValidationFrame] = []
    for row in rows:
        frame_json = _json_load(row["frame_json"], {})
        frames.append(
            SignalValidationFrame(
                frame_key=row["replay_frame_key"],
                event_key=row["event_key"],
                event_token_key=row["event_token_key"],
                decision_at_utc=row["decision_at_utc"],
                source_observed_at_utc=row["source_observed_at_utc"],
                data_blocks=_extract_data_blocks(frame_json),
                frame_json=frame_json,
            )
        )
    if frames:
        return tuple(frames)
    strict_frames = _load_strict_frames_from_pair_snapshots(
        conn,
        signal_id=signal_id,
        phase=phase,
        max_frames=max_frames,
        now_utc=now_utc or datetime.now(UTC),
    )
    if strict_frames:
        return strict_frames
    return _load_frames_from_abc_tables(conn, signal_id=signal_id, max_frames=max_frames)


def evaluate_catalog_signal(
    spec: Any,
    *,
    phase: str,
    frames: tuple[SignalValidationFrame, ...],
    evaluated_at_utc: datetime,
) -> tuple[SignalValidationPhaseResult, tuple[SignalObservation, ...]]:
    structural_blockers = list(spec.structural_blockers())
    result_blockers = list(structural_blockers)
    if not frames:
        result_blockers.append("no_signal_validation_frames_available")
    missing_blocks = sorted(
        block
        for block in spec.required_data_blocks
        if frames and not any(block in frame.data_blocks for frame in frames)
    )
    result_blockers.extend(f"missing_data_block_{block}" for block in missing_blocks)
    observations: list[SignalObservation] = []
    hit_values: list[bool] = []
    forward_returns: list[float] = []
    emitted_count = 0
    for frame in frames:
        frame_blockers = list(structural_blockers)
        for block in spec.required_data_blocks:
            if block not in frame.data_blocks:
                frame_blockers.append(f"missing_data_block_{block}")
        emitted = not frame_blockers
        if emitted:
            emitted_count += 1
        expected_direction = _expected_direction_for_spec(spec, frame) if emitted else None
        outcome_direction = _outcome_direction_from_frame(frame)
        hit = _hit_for_spec(spec, frame, expected_direction=expected_direction, outcome_direction=outcome_direction) if emitted else None
        if hit is not None:
            hit_values.append(bool(hit))
        forward_return = _forward_return_from_frame(frame, expected_direction=expected_direction) if emitted else None
        if forward_return is not None:
            forward_returns.append(float(forward_return))
        observations.append(
            SignalObservation(
            signal_id=spec.signal_id,
            version=spec.version,
            phase=phase,
            event_key=frame.event_key,
            event_token_key=frame.event_token_key,
            decision_at_utc=frame.decision_at_utc,
            emitted_signal=emitted,
            observed_value=_observed_value_for_spec(spec, frame),
            expected_direction=expected_direction,
            outcome_direction=outcome_direction,
            hit=hit,
            payload={
                "purpose": spec.purpose,
                "sources": list(spec.sources),
                "required_data_blocks": list(spec.required_data_blocks),
                "structural_validation_only": _frames_are_structural_only((frame,)),
                "frame_source": frame.frame_json.get("frame_source"),
                "orders_allowed": False,
                "live_trading_authorized": False,
            },
            blockers=tuple(dict.fromkeys(frame_blockers)),
            )
        )
    observations_tuple = tuple(observations)
    status = "blocked" if result_blockers or not emitted_count else "passed"
    hit_rate = None if not hit_values else sum(1 for value in hit_values if value) / len(hit_values)
    average_forward_return = None if not forward_returns else sum(forward_returns) / len(forward_returns)
    frame_sources: dict[str, int] = {}
    for frame in frames:
        source = str(frame.frame_json.get("frame_source") or "unknown")
        frame_sources[source] = frame_sources.get(source, 0) + 1
    structural_validation_only = _frames_are_structural_only(frames)
    result = SignalValidationPhaseResult(
        signal_id=spec.signal_id,
        phase=phase,
        status=status,
        evaluated_at_utc=evaluated_at_utc.isoformat(),
        sample_count=len(frames),
        hit_rate=hit_rate,
        average_forward_return=average_forward_return,
        blockers=tuple(dict.fromkeys(result_blockers)),
        metrics={
            "frame_count": len(frames),
            "emitted_signal_count": emitted_count,
            "hit_evaluable_count": len(hit_values),
            "structural_validation_only": structural_validation_only,
            "required_data_blocks": list(spec.required_data_blocks),
            "frame_sources": frame_sources,
            "orders_allowed": False,
            "live_trading_authorized": False,
        },
    )
    return result, observations_tuple


def _frames_are_structural_only(frames: tuple[SignalValidationFrame, ...]) -> bool:
    if not frames:
        return True
    for frame in frames:
        if frame.frame_json.get("structural_validation_only") is True:
            return True
        if str(frame.frame_json.get("frame_source") or "") in {"abc_tables_synthetic", "structural_fixture"}:
            return True
    return False


def _load_strict_frames_from_pair_snapshots(
    conn: Any,
    *,
    signal_id: str,
    phase: str,
    max_frames: int,
    now_utc: datetime,
) -> tuple[SignalValidationFrame, ...]:
    now = _as_utc(now_utc)
    lower_bound = _phase_lower_bound(phase, now)
    candidate_limit = max(500, int(max_frames) * 80)
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                p.*,
                e.event_start_time_utc,
                e.event_end_time_utc
              FROM polymarket_updown_pair_snapshots p
              JOIN events e
                ON e.event_key = p.event_key
             WHERE p.up_mid_price IS NOT NULL
               AND p.down_mid_price IS NOT NULL
               AND e.event_start_time_utc IS NOT NULL
               AND e.event_end_time_utc IS NOT NULL
               AND e.event_end_time_utc <= ?
               AND e.event_end_time_utc >= ?
             ORDER BY p.bucket_timestamp_utc DESC
             LIMIT ?
            """,
            (now.isoformat(), lower_bound.isoformat(), candidate_limit),
        ).fetchall()
    ]
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_key = str(row.get("event_key") or "")
        if not event_key or event_key in selected:
            continue
        bucket = _parse_datetime(row.get("bucket_timestamp_utc"))
        start = _parse_datetime(row.get("event_start_time_utc"))
        end = _parse_datetime(row.get("event_end_time_utc"))
        if bucket < start + timedelta(seconds=30) or bucket > end - timedelta(seconds=30):
            continue
        selected[event_key] = row
        if len(selected) >= max(1, int(max_frames)):
            break

    frames: list[SignalValidationFrame] = []
    for row in selected.values():
        frame = _strict_frame_from_pair_snapshot(conn, snapshot=row, signal_id=signal_id)
        if frame is not None:
            frames.append(frame)
    return tuple(frames)


def _phase_lower_bound(phase: str, now: datetime) -> datetime:
    if phase == "last_week_backtest":
        return now - timedelta(days=7)
    if phase == "last_month_backtest":
        return now - timedelta(days=31)
    if phase == "random_sampling_backtest":
        return now - timedelta(days=120)
    return now - timedelta(days=3)


def _strict_frame_from_pair_snapshot(conn: Any, *, snapshot: dict[str, Any], signal_id: str) -> SignalValidationFrame | None:
    event_key = str(snapshot.get("event_key") or "")
    decision_at = _parse_datetime(snapshot.get("bucket_timestamp_utc"))
    path_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
              FROM polymarket_updown_pair_snapshots
             WHERE event_key=?
               AND bucket_timestamp_utc <= ?
               AND up_mid_price IS NOT NULL
               AND down_mid_price IS NOT NULL
             ORDER BY bucket_timestamp_utc ASC
            """,
            (event_key, decision_at.isoformat()),
        ).fetchall()
    ]
    future_row = conn.execute(
        """
        SELECT *
          FROM polymarket_updown_pair_snapshots
         WHERE event_key=?
           AND bucket_timestamp_utc >= ?
           AND up_mid_price IS NOT NULL
           AND down_mid_price IS NOT NULL
         ORDER BY bucket_timestamp_utc DESC
         LIMIT 1
        """,
        (event_key, decision_at.isoformat()),
    ).fetchone()
    if not path_rows or future_row is None:
        return None
    future = dict(future_row)
    market_state = _market_state_from_strict_path(snapshot=snapshot, path_rows=path_rows)
    final_up = _to_float(future.get("up_mid_price"))
    final_down = _to_float(future.get("down_mid_price"))
    current_up = _to_float(snapshot.get("up_mid_price"))
    outcome_direction = None
    if final_up is not None and final_down is not None and final_up != final_down:
        outcome_direction = "up" if final_up > final_down else "down"
    evaluation = {
        "outcome_direction": outcome_direction,
        "decision_up_mid_price": current_up,
        "decision_down_mid_price": _to_float(snapshot.get("down_mid_price")),
        "final_up_mid_price": final_up,
        "final_down_mid_price": final_down,
        "up_forward_return_to_final": None if final_up is None or current_up is None else final_up - current_up,
        "final_snapshot_at_utc": future.get("bucket_timestamp_utc"),
    }
    profile_state = _latest_profile_state_before(conn, event_key=event_key, before_utc=decision_at.isoformat())
    technical_state = _latest_technical_state(conn, symbol=snapshot.get("symbol"), before_utc=decision_at.isoformat())
    data_blocks: dict[str, Any] = {"C": market_state}
    if technical_state:
        data_blocks["A"] = technical_state
    if profile_state:
        data_blocks["B"] = profile_state
    frame_json = {
        "frame_source": "strict_pair_snapshot_replay",
        "structural_validation_only": False,
        "signal_id": signal_id,
        "underlying_state": technical_state.get("underlying_state") if technical_state else None,
        "indicator_state": technical_state.get("indicator_state") if technical_state else None,
        "technical_observer_state": technical_state.get("technical_observer_state") if technical_state else None,
        "profile_state": profile_state,
        "market_state": market_state,
        "evaluation": evaluation,
        "no_lookahead_inputs": True,
    }
    return SignalValidationFrame(
        frame_key=f"strict:{event_key}:{decision_at.isoformat()}",
        event_key=event_key,
        event_token_key=str(snapshot.get("up_event_token_key") or snapshot.get("down_event_token_key") or ""),
        decision_at_utc=decision_at.isoformat(),
        source_observed_at_utc=decision_at.isoformat(),
        data_blocks=data_blocks,
        frame_json=frame_json,
    )


def _latest_profile_state_before(conn: Any, *, event_key: str | None, before_utc: str) -> dict[str, Any] | None:
    if not event_key:
        return None
    row = conn.execute(
        """
        SELECT *
          FROM profile_distribution_snapshots
         WHERE event_key=?
           AND computed_at_utc <= ?
         ORDER BY computed_at_utc DESC
         LIMIT 1
        """,
        (event_key, before_utc),
    ).fetchone()
    if row is None:
        return _latest_profile_state(conn, event_key=event_key)
    data = dict(row)
    components = [
        dict(component)
        for component in conn.execute(
            """
            SELECT handle, grade, trading_style, outcome, net_shares,
                   net_notional_usd, cost_basis_usd, final_weight
              FROM profile_distribution_components
             WHERE distribution_snapshot_key=?
             ORDER BY final_weight DESC
             LIMIT 25
            """,
            (data["distribution_snapshot_key"],),
        ).fetchall()
    ]
    return {
        "event_key": data.get("event_key"),
        "event_slug": data.get("event_slug"),
        "symbol": data.get("symbol"),
        "computed_at_utc": data.get("computed_at_utc"),
        "source_mode": data.get("source_mode"),
        "canonical_method": data.get("canonical_method"),
        "profile_count": data.get("profile_count"),
        "component_count": data.get("component_count"),
        "weights": {
            "up": data.get("up_weight"),
            "down": data.get("down_weight"),
            "up_cost": data.get("up_cost_weight"),
            "down_cost": data.get("down_cost_weight"),
            "up_share": data.get("up_share_weight"),
            "down_share": data.get("down_share_weight"),
            "up_count": data.get("up_count_weight"),
            "down_count": data.get("down_count_weight"),
        },
        "distribution": _json_load(data.get("distribution_json"), {}),
        "blockers": _json_load(data.get("blockers_json"), []),
        "top_components": components,
    }


def _market_state_from_strict_path(*, snapshot: dict[str, Any], path_rows: list[dict[str, Any]]) -> dict[str, Any]:
    up_values = [_to_float(row.get("up_mid_price")) for row in path_rows]
    down_values = [_to_float(row.get("down_mid_price")) for row in path_rows]
    up_values = [value for value in up_values if value is not None]
    down_values = [value for value in down_values if value is not None]
    up_first = up_values[0] if up_values else None
    up_last = up_values[-1] if up_values else None
    swings = [abs(up_values[index] - up_values[index - 1]) for index in range(1, len(up_values))]
    direction_changes = _direction_change_count(up_values)
    level_crossings = _level_crossing_count(up_values)
    rolling_30 = _rolling_range(path_rows, seconds=30)
    rolling_60 = _rolling_range(path_rows, seconds=60)
    current_direction = None
    if up_first is not None and up_last is not None and up_first != up_last:
        current_direction = "up" if up_last > up_first else "down"
    pair = dict(snapshot)
    event_start = _parse_optional_datetime(snapshot.get("event_start_time_utc"))
    event_end = _parse_optional_datetime(snapshot.get("event_end_time_utc"))
    up_points = [
        (_parse_datetime(row.get("bucket_timestamp_utc")), value)
        for row in path_rows
        for value in [_to_float(row.get("up_mid_price"))]
        if row.get("bucket_timestamp_utc") and value is not None
    ]
    down_points = [
        (_parse_datetime(row.get("bucket_timestamp_utc")), value)
        for row in path_rows
        for value in [_to_float(row.get("down_mid_price"))]
        if row.get("bucket_timestamp_utc") and value is not None
    ]
    up_points.sort(key=lambda item: item[0])
    down_points.sort(key=lambda item: item[0])
    return {
        "event_key": snapshot.get("event_key"),
        "event_slug": snapshot.get("event_slug"),
        "symbol": snapshot.get("symbol"),
        "event_start_time_utc": snapshot.get("event_start_time_utc"),
        "event_end_time_utc": snapshot.get("event_end_time_utc"),
        "computed_at_utc": snapshot.get("bucket_timestamp_utc"),
        "path_direction": current_direction,
        "snapshot_count": len(path_rows),
        "up_first_price": up_first,
        "up_last_price": up_last,
        "up_min_price": min(up_values) if up_values else None,
        "up_max_price": max(up_values) if up_values else None,
        "up_range": (max(up_values) - min(up_values)) if up_values else None,
        "up_stddev": _stddev(up_values),
        "avg_swing_distance": (sum(swings) / len(swings)) if swings else 0.0,
        "max_swing_distance": max(swings) if swings else 0.0,
        "avg_rolling_30s_range": rolling_30["avg"],
        "max_rolling_30s_range": rolling_30["max"],
        "avg_rolling_60s_range": rolling_60["avg"],
        "max_rolling_60s_range": rolling_60["max"],
        "level_crossing_count": level_crossings,
        "near_50c_sample_count": sum(1 for value in up_values if 0.45 <= value <= 0.55),
        "rebound_direction_flip_count": direction_changes,
        "strong_rebound_touch_count": sum(1 for swing in swings if swing >= 0.05),
        "pair_sum_range": _pair_sum_range(up_values, down_values),
        "avg_pair_depth_pressure": _avg_depth_pressure(path_rows),
        "avg_source_latency_ms": _avg_float(row.get("source_latency_ms") for row in path_rows),
        "max_source_latency_ms": _max_float(row.get("source_latency_ms") for row in path_rows),
        "tail_comeback_table": compute_tail_comeback_table(
            up_points=up_points,
            down_points=down_points,
            event_start=event_start,
            event_end=event_end,
        ),
        "latest_pair_snapshot": pair,
    }


def _direction_change_count(values: list[float]) -> int:
    previous: int | None = None
    changes = 0
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        direction = 1 if delta > 0 else -1 if delta < 0 else 0
        if direction == 0:
            continue
        if previous is not None and direction != previous:
            changes += 1
        previous = direction
    return changes


def _level_crossing_count(values: list[float]) -> int:
    crossings = 0
    for index in range(1, len(values)):
        previous_bucket = int(values[index - 1] * 20)
        current_bucket = int(values[index] * 20)
        crossings += abs(current_bucket - previous_bucket)
    return crossings


def _rolling_range(path_rows: list[dict[str, Any]], *, seconds: int) -> dict[str, float | None]:
    ranges: list[float] = []
    parsed = [
        (_parse_datetime(row.get("bucket_timestamp_utc")), _to_float(row.get("up_mid_price")))
        for row in path_rows
        if row.get("bucket_timestamp_utc") and _to_float(row.get("up_mid_price")) is not None
    ]
    for timestamp, _value in parsed:
        start = timestamp - timedelta(seconds=seconds)
        values = [value for other_timestamp, value in parsed if start <= other_timestamp <= timestamp and value is not None]
        if values:
            ranges.append(max(values) - min(values))
    return {
        "avg": (sum(ranges) / len(ranges)) if ranges else None,
        "max": max(ranges) if ranges else None,
    }


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = sum(values) / len(values)
    return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5


def _pair_sum_range(up_values: list[float], down_values: list[float]) -> float | None:
    pair_sums = [up + down for up, down in zip(up_values, down_values, strict=False)]
    if not pair_sums:
        return None
    return max(pair_sums) - min(pair_sums)


def _avg_depth_pressure(path_rows: list[dict[str, Any]]) -> float | None:
    values = []
    for row in path_rows:
        up_bid = _to_float(row.get("up_depth_top3_bid_size")) or 0.0
        up_ask = _to_float(row.get("up_depth_top3_ask_size")) or 0.0
        down_bid = _to_float(row.get("down_depth_top3_bid_size")) or 0.0
        down_ask = _to_float(row.get("down_depth_top3_ask_size")) or 0.0
        denom = up_bid + up_ask + down_bid + down_ask
        if denom:
            values.append(((up_bid + down_ask) - (up_ask + down_bid)) / denom)
    return (sum(values) / len(values)) if values else None


def _avg_float(values: Any) -> float | None:
    parsed = [_to_float(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return (sum(parsed) / len(parsed)) if parsed else None


def _max_float(values: Any) -> float | None:
    parsed = [_to_float(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _load_frames_from_abc_tables(conn: Any, *, signal_id: str, max_frames: int = 100) -> tuple[SignalValidationFrame, ...]:
    rows = conn.execute(
        """
        SELECT *
          FROM polymarket_event_path_stats
         WHERE event_key IS NOT NULL
           AND computed_at_utc IS NOT NULL
           AND snapshot_count > 0
         ORDER BY computed_at_utc DESC
         LIMIT ?
        """,
        (max(1, int(max_frames)),),
    ).fetchall()
    frames: list[SignalValidationFrame] = []
    for row in rows:
        row_dict = dict(row)
        computed_at = row_dict.get("computed_at_utc") or row_dict.get("updated_at_utc") or row_dict.get("event_end_time_utc")
        if not computed_at:
            continue
        event_key = row_dict.get("event_key")
        symbol = row_dict.get("symbol")
        profile_state = _latest_profile_state(conn, event_key=event_key)
        market_state = _market_state_from_path_stats(conn, path_stats=row_dict)
        technical_state = _latest_technical_state(conn, symbol=symbol, before_utc=str(computed_at))
        data_blocks: dict[str, Any] = {}
        if technical_state:
            data_blocks["A"] = technical_state
        if profile_state:
            data_blocks["B"] = profile_state
        if market_state:
            data_blocks["C"] = market_state
        if not data_blocks:
            continue
        frame_json = {
            "frame_source": "abc_tables_synthetic",
            "signal_id": signal_id,
            "underlying_state": technical_state.get("underlying_state") if technical_state else None,
            "indicator_state": technical_state.get("indicator_state") if technical_state else None,
            "technical_observer_state": technical_state.get("technical_observer_state") if technical_state else None,
            "profile_state": profile_state,
            "market_state": market_state,
        }
        frames.append(
            SignalValidationFrame(
                frame_key=f"abc:{event_key}:{computed_at}",
                event_key=event_key,
                event_token_key=None,
                decision_at_utc=str(computed_at),
                source_observed_at_utc=str(computed_at),
                data_blocks=data_blocks,
                frame_json=frame_json,
            )
        )
    return tuple(frames)


def _latest_profile_state(conn: Any, *, event_key: str | None) -> dict[str, Any] | None:
    if not event_key:
        return None
    row = conn.execute(
        """
        SELECT *
          FROM profile_distribution_snapshots
         WHERE event_key=?
         ORDER BY computed_at_utc DESC
         LIMIT 1
        """,
        (event_key,),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    components = [
        dict(component)
        for component in conn.execute(
            """
            SELECT handle, grade, trading_style, outcome, net_shares,
                   net_notional_usd, cost_basis_usd, final_weight
              FROM profile_distribution_components
             WHERE distribution_snapshot_key=?
             ORDER BY final_weight DESC
             LIMIT 25
            """,
            (data["distribution_snapshot_key"],),
        ).fetchall()
    ]
    return {
        "event_key": data.get("event_key"),
        "event_slug": data.get("event_slug"),
        "symbol": data.get("symbol"),
        "computed_at_utc": data.get("computed_at_utc"),
        "source_mode": data.get("source_mode"),
        "canonical_method": data.get("canonical_method"),
        "profile_count": data.get("profile_count"),
        "component_count": data.get("component_count"),
        "weights": {
            "up": data.get("up_weight"),
            "down": data.get("down_weight"),
            "up_cost": data.get("up_cost_weight"),
            "down_cost": data.get("down_cost_weight"),
            "up_share": data.get("up_share_weight"),
            "down_share": data.get("down_share_weight"),
            "up_count": data.get("up_count_weight"),
            "down_count": data.get("down_count_weight"),
        },
        "distribution": _json_load(data.get("distribution_json"), {}),
        "blockers": _json_load(data.get("blockers_json"), []),
        "top_components": components,
    }


def _market_state_from_path_stats(conn: Any, *, path_stats: dict[str, Any]) -> dict[str, Any]:
    event_key = path_stats.get("event_key")
    pair = None
    if event_key:
        pair_row = conn.execute(
            """
            SELECT *
              FROM polymarket_updown_pair_snapshots
             WHERE event_key=?
             ORDER BY bucket_timestamp_utc DESC
             LIMIT 1
            """,
            (event_key,),
        ).fetchone()
        pair = dict(pair_row) if pair_row else None
    return {
        "event_key": event_key,
        "event_slug": path_stats.get("event_slug"),
        "symbol": path_stats.get("symbol"),
        "event_start_time_utc": path_stats.get("event_start_time_utc"),
        "event_end_time_utc": path_stats.get("event_end_time_utc"),
        "computed_at_utc": path_stats.get("computed_at_utc"),
        "outcome": path_stats.get("path_direction"),
        "path_direction": path_stats.get("path_direction"),
        "path_efficiency": path_stats.get("path_efficiency"),
        "snapshot_count": path_stats.get("snapshot_count"),
        "up_first_price": path_stats.get("up_first_price"),
        "up_last_price": path_stats.get("up_last_price"),
        "up_min_price": path_stats.get("up_min_price"),
        "up_max_price": path_stats.get("up_max_price"),
        "up_range": path_stats.get("up_range"),
        "up_stddev": path_stats.get("up_stddev"),
        "avg_swing_distance": path_stats.get("avg_swing_distance"),
        "max_swing_distance": path_stats.get("max_swing_distance"),
        "avg_rolling_30s_range": path_stats.get("avg_rolling_30s_range"),
        "max_rolling_30s_range": path_stats.get("max_rolling_30s_range"),
        "avg_rolling_60s_range": path_stats.get("avg_rolling_60s_range"),
        "max_rolling_60s_range": path_stats.get("max_rolling_60s_range"),
        "level_crossing_count": path_stats.get("level_crossing_count"),
        "near_50c_sample_count": path_stats.get("near_50c_sample_count"),
        "rebound_direction_flip_count": path_stats.get("rebound_direction_flip_count"),
        "strong_rebound_touch_count": path_stats.get("strong_rebound_touch_count"),
        "pair_sum_range": path_stats.get("pair_sum_range"),
        "avg_pair_depth_pressure": path_stats.get("avg_pair_depth_pressure"),
        "avg_source_latency_ms": path_stats.get("avg_source_latency_ms"),
        "max_source_latency_ms": path_stats.get("max_source_latency_ms"),
        "event_price_points": _json_load(path_stats.get("event_price_points_json"), {}),
        "pre_event_price_points": _json_load(path_stats.get("pre_event_price_points_json"), {}),
        "level_first_touch_seconds": _json_load(path_stats.get("level_first_touch_seconds_json"), {}),
        "tail_comeback_table": _json_load(path_stats.get("tail_comeback_table_json"), {}),
        "price_bucket_counts": _json_load(path_stats.get("price_bucket_counts_json"), {}),
        "latest_pair_snapshot": pair,
    }


def _latest_technical_state(conn: Any, *, symbol: str | None, before_utc: str) -> dict[str, Any] | None:
    if not symbol:
        return None
    observer_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT provider, interval, observed_at_utc, summary_label, summary_score,
                   buy_count, sell_count, neutral_count, component_count
              FROM external_technical_observer_snapshots
             WHERE symbol=?
               AND observed_at_utc <= ?
             ORDER BY observed_at_utc DESC
             LIMIT 8
            """,
            (symbol, before_utc),
        ).fetchall()
    ]
    if not observer_rows:
        observer_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT provider, interval, observed_at_utc, summary_label, summary_score,
                       buy_count, sell_count, neutral_count, component_count
                  FROM external_technical_observer_snapshots
                 WHERE symbol=?
                 ORDER BY observed_at_utc DESC
                 LIMIT 8
                """,
                (symbol,),
            ).fetchall()
        ]
    indicator_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT interval, indicator_id, computed_at_utc, direction, confidence, signal_value
              FROM indicator_snapshots
             WHERE symbol=?
               AND computed_at_utc <= ?
             ORDER BY computed_at_utc DESC
             LIMIT 8
            """,
            (symbol, before_utc),
        ).fetchall()
    ]
    tick = conn.execute(
        """
        SELECT symbol, source, observed_at_utc, exchange_timestamp_utc, price, bid, ask
          FROM underlying_price_ticks
         WHERE symbol=?
           AND observed_at_utc <= ?
         ORDER BY observed_at_utc DESC
         LIMIT 1
        """,
        (symbol, before_utc),
    ).fetchone()
    if not observer_rows and not indicator_rows and tick is None:
        return None
    return {
        "symbol": symbol,
        "technical_observer_state": observer_rows,
        "indicator_state": indicator_rows,
        "underlying_state": dict(tick) if tick else None,
        "freshness_note": "uses_latest_available_at_or_before_frame_when_available",
    }


def _extract_data_blocks(frame_json: dict[str, Any]) -> dict[str, Any]:
    blocks: dict[str, Any] = {}
    if frame_json.get("underlying_state") or frame_json.get("indicator_state") or frame_json.get("technical_observer_state"):
        blocks["A"] = {
            "underlying_state": frame_json.get("underlying_state"),
            "indicator_state": frame_json.get("indicator_state"),
            "technical_observer_state": frame_json.get("technical_observer_state"),
        }
    if frame_json.get("profile_state"):
        blocks["B"] = frame_json.get("profile_state")
    if frame_json.get("market_state"):
        blocks["C"] = frame_json.get("market_state")
    return blocks


def _expected_direction_for_spec(spec: Any, frame: SignalValidationFrame) -> str | None:
    profile = frame.data_blocks.get("B") or {}
    weights = profile.get("weights") or {}
    market = frame.data_blocks.get("C") or {}
    technical = frame.data_blocks.get("A") or {}
    signal_type = str(getattr(spec, "signal_type", ""))
    sources = set(getattr(spec, "sources", ()) or ())
    if "profiles" in sources:
        up = _to_float(weights.get("up") if weights.get("up") is not None else weights.get("up_cost"))
        down = _to_float(weights.get("down") if weights.get("down") is not None else weights.get("down_cost"))
        if up is not None and down is not None and up != down:
            return "up" if up > down else "down"
    if "optionprice" in sources:
        up_first = _to_float(market.get("up_first_price"))
        up_last = _to_float(market.get("up_last_price"))
        if up_first is not None and up_last is not None and up_first != up_last:
            return "up" if up_last > up_first else "down"
    if "cryptoprice" in sources:
        observer_rows = technical.get("technical_observer_state") or []
        score = sum(_to_float(row.get("summary_score")) or 0.0 for row in observer_rows)
        if score:
            return "up" if score > 0 else "down"
    if signal_type in {"grid_spacing", "grid_count", "grid_type", "liquidity_depth", "stale_order_review"}:
        return _outcome_direction_from_frame(frame)
    return None


def _outcome_direction_from_frame(frame: SignalValidationFrame) -> str | None:
    evaluation = frame.frame_json.get("evaluation") or {}
    evaluation_outcome = str(evaluation.get("outcome_direction") or "").strip().lower()
    if evaluation_outcome in {"up", "down"}:
        return evaluation_outcome
    market = frame.data_blocks.get("C") or {}
    outcome = str(market.get("outcome") or market.get("path_direction") or "").strip().lower()
    if outcome in {"up", "down"}:
        return outcome
    return None


def _hit_for_spec(
    spec: Any,
    frame: SignalValidationFrame,
    *,
    expected_direction: str | None,
    outcome_direction: str | None,
) -> bool | None:
    market = frame.data_blocks.get("C") or {}
    signal_type = str(getattr(spec, "signal_type", ""))
    if signal_type in {"buy_rebound", "support_resistance"}:
        rebound_count = _to_float(market.get("strong_rebound_touch_count")) or 0.0
        flip_count = _to_float(market.get("rebound_direction_flip_count")) or 0.0
        return rebound_count > 0 or flip_count >= 2
    if signal_type == "grid_spacing":
        avg_swing = _to_float(market.get("avg_swing_distance")) or 0.0
        max_rolling = _to_float(market.get("max_rolling_60s_range")) or 0.0
        return avg_swing >= 0.03 or max_rolling >= 0.08
    if signal_type == "grid_count":
        pair = market.get("latest_pair_snapshot") or {}
        up_depth = _to_float(pair.get("up_depth_top3_ask_size")) or 0.0
        down_depth = _to_float(pair.get("down_depth_top3_ask_size")) or 0.0
        return up_depth + down_depth > 0
    if signal_type == "grid_type":
        crossings = _to_float(market.get("level_crossing_count")) or 0.0
        return crossings >= 4
    if signal_type == "liquidity_depth":
        pair = market.get("latest_pair_snapshot") or {}
        spread_values = [
            abs((_to_float(pair.get("up_best_ask")) or 0.0) - (_to_float(pair.get("up_best_bid")) or 0.0)),
            abs((_to_float(pair.get("down_best_ask")) or 0.0) - (_to_float(pair.get("down_best_bid")) or 0.0)),
        ]
        return min(spread_values) <= 0.08 if spread_values else None
    if signal_type == "latency_quality":
        avg_latency = _to_float(market.get("avg_source_latency_ms"))
        max_latency = _to_float(market.get("max_source_latency_ms"))
        return (avg_latency is not None and avg_latency <= 30_000) and (max_latency is None or max_latency <= 120_000)
    if expected_direction and outcome_direction:
        return expected_direction == outcome_direction
    return None


def _forward_return_from_frame(frame: SignalValidationFrame, *, expected_direction: str | None) -> float | None:
    evaluation = frame.frame_json.get("evaluation") or {}
    up_forward = _to_float(evaluation.get("up_forward_return_to_final"))
    if up_forward is not None and expected_direction in {"up", "down"}:
        return up_forward if expected_direction == "up" else -up_forward
    market = frame.data_blocks.get("C") or {}
    up_first = _to_float(market.get("up_first_price"))
    up_last = _to_float(market.get("up_last_price"))
    if up_first is None or up_last is None or expected_direction not in {"up", "down"}:
        return None
    up_return = up_last - up_first
    return up_return if expected_direction == "up" else -up_return


def _observed_value_for_spec(spec: Any, frame: SignalValidationFrame) -> float | None:
    profile = frame.data_blocks.get("B") or {}
    market = frame.data_blocks.get("C") or {}
    weights = profile.get("weights") or {}
    signal_type = str(getattr(spec, "signal_type", ""))
    if signal_type in {"outcome_prediction", "hedge_ratio", "side_start", "final_minute"}:
        up = _to_float(weights.get("up") if weights.get("up") is not None else weights.get("up_cost"))
        down = _to_float(weights.get("down") if weights.get("down") is not None else weights.get("down_cost"))
        if up is not None and down is not None:
            return up - down
    if signal_type in {"buy_rebound", "support_resistance"}:
        return _to_float(market.get("strong_rebound_touch_count"))
    if signal_type in {"grid_spacing", "grid_type"}:
        return _to_float(market.get("avg_swing_distance"))
    if signal_type == "latency_quality":
        return _to_float(market.get("avg_source_latency_ms"))
    return _to_float(market.get("up_range"))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_load(value: Any, fallback: Any) -> Any:
    import json

    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return _parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
