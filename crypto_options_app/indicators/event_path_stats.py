from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable


PRICE_LEVELS = (0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.90)
PRICE_BUCKETS = (
    "0-10c",
    "10-20c",
    "20-30c",
    "30-40c",
    "40-50c",
    "50-60c",
    "60-70c",
    "70-80c",
    "80-90c",
    "90-100c",
)
EVENT_PATH_OFFSETS_SECONDS = (0, 10, 30, 60, 120, 180, 240, 270, 300)
PRE_EVENT_PATH_OFFSETS_SECONDS = (-900, -600, -300, -120, -60, -30, -10)
TAIL_TOUCH_TARGETS = {
    0.01: (0.05, 0.10, 0.20),
    0.05: (0.10, 0.20, 0.30),
    0.10: (0.20, 0.30, 0.50),
}


@dataclass(frozen=True)
class EventPathStats:
    event_path_stats_key: str
    event_key: str
    event_slug: str | None
    symbol: str | None
    event_start_time_utc: str | None
    event_end_time_utc: str | None
    computed_at_utc: str
    first_snapshot_at_utc: str | None
    last_snapshot_at_utc: str | None
    snapshot_count: int
    up_first_price: float | None
    up_last_price: float | None
    up_min_price: float | None
    up_max_price: float | None
    up_range: float | None
    up_abs_move_sum: float | None
    up_abs_move_per_minute: float | None
    up_stddev: float | None
    event_price_points: dict[str, float | None]
    pre_event_price_points: dict[str, float | None]
    level_first_touch_seconds: dict[str, float | None]
    tail_comeback_table: dict[str, Any]
    path_direction: str | None
    path_efficiency: float | None
    time_to_first_extreme_seconds: float | None
    avg_swing_distance: float | None
    max_swing_distance: float | None
    avg_rolling_30s_range: float | None
    max_rolling_30s_range: float | None
    avg_rolling_60s_range: float | None
    max_rolling_60s_range: float | None
    level_crossing_count: int
    level_crossings: dict[str, int]
    price_bucket_counts: dict[str, int]
    near_50c_sample_count: int
    extreme_sample_count: int
    rebound_direction_flip_count: int
    strong_rebound_touch_count: int
    pair_sum_range: float | None
    avg_pair_depth_pressure: float | None
    avg_source_latency_ms: float | None
    max_source_latency_ms: float | None
    trade_print_count: int
    source: dict[str, Any]

    def as_row(self) -> dict[str, Any]:
        return {
            "event_path_stats_key": self.event_path_stats_key,
            "event_key": self.event_key,
            "event_slug": self.event_slug,
            "symbol": self.symbol,
            "event_start_time_utc": self.event_start_time_utc,
            "event_end_time_utc": self.event_end_time_utc,
            "computed_at_utc": self.computed_at_utc,
            "first_snapshot_at_utc": self.first_snapshot_at_utc,
            "last_snapshot_at_utc": self.last_snapshot_at_utc,
            "snapshot_count": self.snapshot_count,
            "up_first_price": self.up_first_price,
            "up_last_price": self.up_last_price,
            "up_min_price": self.up_min_price,
            "up_max_price": self.up_max_price,
            "up_range": self.up_range,
            "up_abs_move_sum": self.up_abs_move_sum,
            "up_abs_move_per_minute": self.up_abs_move_per_minute,
            "up_stddev": self.up_stddev,
            "event_price_points_json": json.dumps(self.event_price_points, sort_keys=True),
            "pre_event_price_points_json": json.dumps(self.pre_event_price_points, sort_keys=True),
            "level_first_touch_seconds_json": json.dumps(self.level_first_touch_seconds, sort_keys=True),
            "tail_comeback_table_json": json.dumps(self.tail_comeback_table, sort_keys=True),
            "path_direction": self.path_direction,
            "path_efficiency": self.path_efficiency,
            "time_to_first_extreme_seconds": self.time_to_first_extreme_seconds,
            "avg_swing_distance": self.avg_swing_distance,
            "max_swing_distance": self.max_swing_distance,
            "avg_rolling_30s_range": self.avg_rolling_30s_range,
            "max_rolling_30s_range": self.max_rolling_30s_range,
            "avg_rolling_60s_range": self.avg_rolling_60s_range,
            "max_rolling_60s_range": self.max_rolling_60s_range,
            "level_crossing_count": self.level_crossing_count,
            "level_crossings_json": json.dumps(self.level_crossings, sort_keys=True),
            "price_bucket_counts_json": json.dumps(self.price_bucket_counts, sort_keys=True),
            "near_50c_sample_count": self.near_50c_sample_count,
            "extreme_sample_count": self.extreme_sample_count,
            "rebound_direction_flip_count": self.rebound_direction_flip_count,
            "strong_rebound_touch_count": self.strong_rebound_touch_count,
            "pair_sum_range": self.pair_sum_range,
            "avg_pair_depth_pressure": self.avg_pair_depth_pressure,
            "avg_source_latency_ms": self.avg_source_latency_ms,
            "max_source_latency_ms": self.max_source_latency_ms,
            "trade_print_count": self.trade_print_count,
            "source_json": json.dumps(self.source, sort_keys=True, default=str),
        }


def compute_event_path_stats(
    *,
    event: dict[str, Any],
    pair_snapshots: Iterable[dict[str, Any]],
    profile_context_snapshots: Iterable[dict[str, Any]] | None = None,
    crypto_context_snapshots: Iterable[dict[str, Any]] | None = None,
    trade_print_count: int = 0,
    computed_at_utc: datetime | None = None,
) -> EventPathStats | None:
    rows = [dict(row) for row in pair_snapshots]
    values = [_optional_float(row.get("up_mid_price")) for row in rows]
    up_values = [value for value in values if value is not None]
    if not up_values:
        return None

    computed_at = (computed_at_utc or datetime.now(UTC)).astimezone(UTC).isoformat()
    timestamps = [str(row.get("bucket_timestamp_utc")) for row in rows if row.get("bucket_timestamp_utc")]
    first_snapshot = min(timestamps) if timestamps else None
    last_snapshot = max(timestamps) if timestamps else None
    coverage_seconds = _coverage_seconds(first_snapshot, last_snapshot)
    diffs = [up_values[index] - up_values[index - 1] for index in range(1, len(up_values))]
    abs_move_sum = sum(abs(diff) for diff in diffs)
    mean = sum(up_values) / len(up_values)
    variance = sum((value - mean) ** 2 for value in up_values) / len(up_values)
    event_start = _parse_optional_datetime(event.get("event_start_time_utc"))
    event_end = _parse_optional_datetime(event.get("event_end_time_utc"))
    points = _path_points(rows)
    down_points = _outcome_path_points(rows, "down_mid_price")
    swings = _swing_distances(points)
    rolling_30s_ranges = _rolling_ranges(points, window_seconds=30)
    rolling_60s_ranges = _rolling_ranges(points, window_seconds=60)
    level_crossings = _level_crossings(up_values)
    bucket_counts = _bucket_counts(up_values)
    pair_sums = [
        up + down
        for up, down in (
            (_optional_float(row.get("up_mid_price")), _optional_float(row.get("down_mid_price")))
            for row in rows
        )
        if up is not None and down is not None
    ]
    pressures = [_depth_pressure(row) for row in rows]
    latencies = [_non_negative_latency_ms(row.get("source_latency_ms")) for row in rows]
    latencies = [latency for latency in latencies if latency is not None]
    return EventPathStats(
        event_path_stats_key=f"event_path_stats:{event.get('event_key')}",
        event_key=str(event.get("event_key")),
        event_slug=event.get("event_slug"),
        symbol=event.get("symbol"),
        event_start_time_utc=event.get("event_start_time_utc"),
        event_end_time_utc=event.get("event_end_time_utc"),
        computed_at_utc=computed_at,
        first_snapshot_at_utc=first_snapshot,
        last_snapshot_at_utc=last_snapshot,
        snapshot_count=len(rows),
        up_first_price=up_values[0],
        up_last_price=up_values[-1],
        up_min_price=min(up_values),
        up_max_price=max(up_values),
        up_range=max(up_values) - min(up_values),
        up_abs_move_sum=abs_move_sum,
        up_abs_move_per_minute=abs_move_sum / coverage_seconds * 60.0,
        up_stddev=math.sqrt(variance),
        event_price_points=_price_points_at_offsets(points, event_start, EVENT_PATH_OFFSETS_SECONDS),
        pre_event_price_points=_price_points_at_offsets(points, event_start, PRE_EVENT_PATH_OFFSETS_SECONDS),
        level_first_touch_seconds=_level_first_touch_seconds(points, event_start),
        tail_comeback_table=compute_tail_comeback_table(
            up_points=points,
            down_points=down_points,
            event_start=event_start,
            event_end=event_end,
            pair_snapshots=rows,
            profile_context_snapshots=list(profile_context_snapshots or []),
            crypto_context_snapshots=list(crypto_context_snapshots or []),
        ),
        path_direction=_path_direction(up_values),
        path_efficiency=_path_efficiency(up_values, abs_move_sum),
        time_to_first_extreme_seconds=_time_to_first_extreme_seconds(points, event_start),
        avg_swing_distance=None if not swings else sum(swings) / len(swings),
        max_swing_distance=None if not swings else max(swings),
        avg_rolling_30s_range=None if not rolling_30s_ranges else sum(rolling_30s_ranges) / len(rolling_30s_ranges),
        max_rolling_30s_range=None if not rolling_30s_ranges else max(rolling_30s_ranges),
        avg_rolling_60s_range=None if not rolling_60s_ranges else sum(rolling_60s_ranges) / len(rolling_60s_ranges),
        max_rolling_60s_range=None if not rolling_60s_ranges else max(rolling_60s_ranges),
        level_crossing_count=sum(level_crossings.values()),
        level_crossings=level_crossings,
        price_bucket_counts=bucket_counts,
        near_50c_sample_count=sum(1 for value in up_values if 0.45 <= value <= 0.55),
        extreme_sample_count=sum(1 for value in up_values if value <= 0.10 or value >= 0.90),
        rebound_direction_flip_count=_direction_flips(diffs),
        strong_rebound_touch_count=_strong_rebound_touches(up_values),
        pair_sum_range=None if not pair_sums else max(pair_sums) - min(pair_sums),
        avg_pair_depth_pressure=None if not pressures else sum(pressures) / len(pressures),
        avg_source_latency_ms=None if not latencies else sum(latencies) / len(latencies),
        max_source_latency_ms=None if not latencies else max(latencies),
        trade_print_count=trade_print_count,
        source={
            "price_levels": [round(level, 4) for level in PRICE_LEVELS],
            "price_buckets": PRICE_BUCKETS,
            "event_path_offsets_seconds": EVENT_PATH_OFFSETS_SECONDS,
            "pre_event_path_offsets_seconds": PRE_EVENT_PATH_OFFSETS_SECONDS,
            "path_efficiency_definition": "abs(last-first)/sum(abs(delta)); 1 means direct path, near 0 means chop",
            "swing_distance_definition": "distance between alternating local extrema after filtering moves below 1c",
            "rolling_range_definition": "high-low range inside trailing time windows",
            "rebound_definition": "local extremum followed by at least 5c reversal within 8 samples",
            "tail_comeback_definition": "for each side, first underdog touch at 1c/5c/10c and later rebound to configured target cents",
        },
    )


def refresh_event_path_stats_for_event(conn: Any, *, event_key: str) -> EventPathStats | None:
    event_row = conn.execute(
        """
        SELECT event_key, event_slug, symbol, event_start_time_utc, event_end_time_utc
        FROM events
        WHERE event_key = ?
        LIMIT 1
        """,
        (event_key,),
    ).fetchone()
    if event_row is None:
        return None
    snapshot_rows = conn.execute(
        """
        SELECT *
        FROM polymarket_updown_pair_snapshots
        WHERE event_key = ?
        ORDER BY bucket_timestamp_utc
        """,
        (event_key,),
    ).fetchall()
    trade_count = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM polymarket_trade_prints
        WHERE token_id IN (SELECT token_id FROM event_tokens WHERE event_key = ?)
        """,
        (event_key,),
    ).fetchone()["c"]
    stats = compute_event_path_stats(
        event=dict(event_row),
        pair_snapshots=[dict(row) for row in snapshot_rows],
        profile_context_snapshots=_load_profile_context_snapshots(
            conn,
            event_key=str(event_row["event_key"]),
            event_slug=str(event_row["event_slug"] or ""),
        ),
        crypto_context_snapshots=_load_crypto_context_snapshots(
            conn,
            symbol=str(event_row["symbol"] or ""),
        ),
        trade_print_count=int(trade_count),
    )
    if stats is None:
        return None
    upsert_event_path_stats(conn, stats)
    return stats


def upsert_event_path_stats(conn: Any, stats: EventPathStats) -> None:
    row = stats.as_row()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO polymarket_event_path_stats(
            event_path_stats_key, event_key, event_slug, symbol, event_start_time_utc, event_end_time_utc,
            computed_at_utc, first_snapshot_at_utc, last_snapshot_at_utc, snapshot_count,
            up_first_price, up_last_price, up_min_price, up_max_price, up_range,
            up_abs_move_sum, up_abs_move_per_minute, up_stddev,
            event_price_points_json, pre_event_price_points_json, level_first_touch_seconds_json,
            tail_comeback_table_json,
            path_direction, path_efficiency, time_to_first_extreme_seconds,
            avg_swing_distance, max_swing_distance,
            avg_rolling_30s_range, max_rolling_30s_range,
            avg_rolling_60s_range, max_rolling_60s_range,
            level_crossing_count,
            level_crossings_json, price_bucket_counts_json, near_50c_sample_count,
            extreme_sample_count, rebound_direction_flip_count, strong_rebound_touch_count,
            pair_sum_range, avg_pair_depth_pressure, avg_source_latency_ms, max_source_latency_ms,
            trade_print_count, source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (
            :event_path_stats_key, :event_key, :event_slug, :symbol, :event_start_time_utc, :event_end_time_utc,
            :computed_at_utc, :first_snapshot_at_utc, :last_snapshot_at_utc, :snapshot_count,
            :up_first_price, :up_last_price, :up_min_price, :up_max_price, :up_range,
            :up_abs_move_sum, :up_abs_move_per_minute, :up_stddev,
            :event_price_points_json, :pre_event_price_points_json, :level_first_touch_seconds_json,
            :tail_comeback_table_json,
            :path_direction, :path_efficiency, :time_to_first_extreme_seconds,
            :avg_swing_distance, :max_swing_distance,
            :avg_rolling_30s_range, :max_rolling_30s_range,
            :avg_rolling_60s_range, :max_rolling_60s_range,
            :level_crossing_count,
            :level_crossings_json, :price_bucket_counts_json, :near_50c_sample_count,
            :extreme_sample_count, :rebound_direction_flip_count, :strong_rebound_touch_count,
            :pair_sum_range, :avg_pair_depth_pressure, :avg_source_latency_ms, :max_source_latency_ms,
            :trade_print_count, :source_json, :inserted_at_utc, :updated_at_utc
        )
        ON CONFLICT(event_path_stats_key) DO UPDATE SET
            computed_at_utc=excluded.computed_at_utc,
            first_snapshot_at_utc=excluded.first_snapshot_at_utc,
            last_snapshot_at_utc=excluded.last_snapshot_at_utc,
            snapshot_count=excluded.snapshot_count,
            up_first_price=excluded.up_first_price,
            up_last_price=excluded.up_last_price,
            up_min_price=excluded.up_min_price,
            up_max_price=excluded.up_max_price,
            up_range=excluded.up_range,
            up_abs_move_sum=excluded.up_abs_move_sum,
            up_abs_move_per_minute=excluded.up_abs_move_per_minute,
            up_stddev=excluded.up_stddev,
            event_price_points_json=excluded.event_price_points_json,
            pre_event_price_points_json=excluded.pre_event_price_points_json,
            level_first_touch_seconds_json=excluded.level_first_touch_seconds_json,
            tail_comeback_table_json=excluded.tail_comeback_table_json,
            path_direction=excluded.path_direction,
            path_efficiency=excluded.path_efficiency,
            time_to_first_extreme_seconds=excluded.time_to_first_extreme_seconds,
            avg_swing_distance=excluded.avg_swing_distance,
            max_swing_distance=excluded.max_swing_distance,
            avg_rolling_30s_range=excluded.avg_rolling_30s_range,
            max_rolling_30s_range=excluded.max_rolling_30s_range,
            avg_rolling_60s_range=excluded.avg_rolling_60s_range,
            max_rolling_60s_range=excluded.max_rolling_60s_range,
            level_crossing_count=excluded.level_crossing_count,
            level_crossings_json=excluded.level_crossings_json,
            price_bucket_counts_json=excluded.price_bucket_counts_json,
            near_50c_sample_count=excluded.near_50c_sample_count,
            extreme_sample_count=excluded.extreme_sample_count,
            rebound_direction_flip_count=excluded.rebound_direction_flip_count,
            strong_rebound_touch_count=excluded.strong_rebound_touch_count,
            pair_sum_range=excluded.pair_sum_range,
            avg_pair_depth_pressure=excluded.avg_pair_depth_pressure,
            avg_source_latency_ms=excluded.avg_source_latency_ms,
            max_source_latency_ms=excluded.max_source_latency_ms,
            trade_print_count=excluded.trade_print_count,
            source_json=excluded.source_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        {**row, "inserted_at_utc": now, "updated_at_utc": now},
    )


def latest_completed_event_path_stats(conn: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM polymarket_event_path_stats
        WHERE event_end_time_utc IS NOT NULL
          AND event_end_time_utc <= ?
        ORDER BY event_end_time_utc DESC, symbol
        LIMIT ?
        """,
        (datetime.now(UTC).isoformat(), max(0, int(limit))),
    ).fetchall()
    return [_decode_stats_json(dict(row)) for row in rows]


def _decode_stats_json(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "event_price_points_json",
        "pre_event_price_points_json",
        "level_first_touch_seconds_json",
        "tail_comeback_table_json",
        "level_crossings_json",
        "price_bucket_counts_json",
        "source_json",
    ):
        try:
            row[key.removesuffix("_json")] = json.loads(row.get(key) or "{}")
        except json.JSONDecodeError:
            row[key.removesuffix("_json")] = {}
    row["avg_source_latency_ms"] = _non_negative_latency_ms(row.get("avg_source_latency_ms"))
    row["max_source_latency_ms"] = _non_negative_latency_ms(row.get("max_source_latency_ms"))
    return row


def _coverage_seconds(first_snapshot: str | None, last_snapshot: str | None) -> float:
    if not first_snapshot or not last_snapshot:
        return 1.0
    first = _parse_datetime(first_snapshot)
    last = _parse_datetime(last_snapshot)
    return max(1.0, (last - first).total_seconds())


def _level_crossings(values: list[float]) -> dict[str, int]:
    crossings: dict[str, int] = {}
    for level in PRICE_LEVELS:
        key = f"{int(level * 100)}c"
        crossings[key] = sum(
            1
            for index in range(1, len(values))
            if (values[index - 1] < level <= values[index])
            or (values[index - 1] > level >= values[index])
        )
    return crossings


def _bucket_counts(values: list[float]) -> dict[str, int]:
    counts = {bucket: 0 for bucket in PRICE_BUCKETS}
    for value in values:
        index = min(9, max(0, int(value * 10)))
        counts[PRICE_BUCKETS[index]] += 1
    return counts


def _path_points(rows: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    return _outcome_path_points(rows, "up_mid_price")


def _outcome_path_points(rows: list[dict[str, Any]], price_key: str) -> list[tuple[datetime, float]]:
    points: list[tuple[datetime, float]] = []
    for row in rows:
        value = _optional_float(row.get(price_key))
        timestamp = _parse_optional_datetime(row.get("bucket_timestamp_utc"))
        if value is not None and timestamp is not None:
            points.append((timestamp, value))
    points.sort(key=lambda item: item[0])
    return points


def compute_tail_comeback_table(
    *,
    up_points: list[tuple[datetime, float]],
    down_points: list[tuple[datetime, float]],
    event_start: datetime | None,
    event_end: datetime | None,
    pair_snapshots: list[dict[str, Any]] | None = None,
    profile_context_snapshots: list[dict[str, Any]] | None = None,
    crypto_context_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    depth_points = _depth_pressure_points(pair_snapshots or [])
    execution_context_points = _execution_context_points(pair_snapshots or [])
    profile_contexts = _profile_context_points(profile_context_snapshots or [])
    crypto_contexts = _crypto_context_points(crypto_context_snapshots or [])
    sides = {
        "up": _tail_comebacks_for_side(
            up_points,
            event_start=event_start,
            event_end=event_end,
            depth_points=depth_points,
            execution_context_points=execution_context_points,
            profile_contexts=profile_contexts,
            crypto_contexts=crypto_contexts,
        ),
        "down": _tail_comebacks_for_side(
            down_points,
            event_start=event_start,
            event_end=event_end,
            depth_points=depth_points,
            execution_context_points=execution_context_points,
            profile_contexts=profile_contexts,
            crypto_contexts=crypto_contexts,
        ),
    }
    bucket_counts: dict[str, dict[str, dict[str, int]]] = {}
    context_counts: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    microstructure_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]] = {}
    execution_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]] = {}
    crypto_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]] = {}
    crypto_distance_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]] = {}
    crypto_distance_execution_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]]] = {}
    profile_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]] = {}
    profile_price_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]] = {}
    profile_price_crypto_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]]] = {}
    profile_price_execution_context_counts: dict[str, dict[str, dict[str, dict[str, dict[str, dict[str, int]]]]]] = {}
    for side_payload in sides.values():
        for touch_key, touch_payload in side_payload.items():
            if not touch_payload.get("touched"):
                continue
            bucket = str(touch_payload.get("time_remaining_bucket") or "unknown")
            volatility_bucket = str(touch_payload.get("volatility_bucket") or "unknown")
            depth_pressure_bucket = str(touch_payload.get("depth_pressure_bucket") or "unknown")
            execution_context_bucket = str(touch_payload.get("execution_context_bucket") or "unknown")
            crypto_context_bucket = str(touch_payload.get("crypto_context_bucket") or "unknown")
            crypto_distance_bucket = str(touch_payload.get("crypto_distance_bucket") or "unknown")
            profile_context_bucket = str(touch_payload.get("profile_context_bucket") or "unknown")
            profile_price_context_bucket = str(touch_payload.get("profile_price_context_bucket") or "unknown")
            profile_price_crypto_context_bucket = str(
                touch_payload.get("profile_price_crypto_context_bucket") or "unknown|unknown"
            )
            profile_price_execution_context_bucket = str(
                touch_payload.get("profile_price_execution_context_bucket") or "unknown|unknown"
            )
            crypto_distance_execution_context_bucket = str(
                touch_payload.get("crypto_distance_execution_context_bucket") or "unknown|unknown"
            )
            profile_price_crypto_profile_bucket, _, profile_price_crypto_distance_bucket = (
                profile_price_crypto_context_bucket.partition("||")
            )
            profile_price_execution_profile_bucket, _, profile_price_execution_bucket = (
                profile_price_execution_context_bucket.partition("||")
            )
            crypto_distance_execution_crypto_bucket, _, crypto_distance_execution_bucket = (
                crypto_distance_execution_context_bucket.partition("||")
            )
            first_target_key = str(touch_payload.get("first_target_key") or "")
            if not first_target_key:
                continue
            bucket_counts.setdefault(touch_key, {}).setdefault(bucket, {"touched": 0, "reached": 0})
            bucket_counts[touch_key][bucket]["touched"] += 1
            context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {"touched": 0, "reached": 0},
            )
            context_counts[touch_key][bucket][volatility_bucket]["touched"] += 1
            microstructure_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(depth_pressure_bucket, {"touched": 0, "reached": 0})
            microstructure_context_counts[touch_key][bucket][volatility_bucket][depth_pressure_bucket]["touched"] += 1
            execution_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(execution_context_bucket, {"touched": 0, "reached": 0})
            execution_context_counts[touch_key][bucket][volatility_bucket][execution_context_bucket]["touched"] += 1
            crypto_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(crypto_context_bucket, {"touched": 0, "reached": 0})
            crypto_context_counts[touch_key][bucket][volatility_bucket][crypto_context_bucket]["touched"] += 1
            crypto_distance_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(crypto_distance_bucket, {"touched": 0, "reached": 0})
            crypto_distance_context_counts[touch_key][bucket][volatility_bucket][crypto_distance_bucket]["touched"] += 1
            crypto_distance_execution_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(crypto_distance_execution_crypto_bucket, {}).setdefault(
                crypto_distance_execution_bucket,
                {"touched": 0, "reached": 0},
            )
            crypto_distance_execution_context_counts[touch_key][bucket][volatility_bucket][
                crypto_distance_execution_crypto_bucket
            ][crypto_distance_execution_bucket]["touched"] += 1
            profile_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(profile_context_bucket, {"touched": 0, "reached": 0})
            profile_context_counts[touch_key][bucket][volatility_bucket][profile_context_bucket]["touched"] += 1
            profile_price_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(profile_price_context_bucket, {"touched": 0, "reached": 0})
            profile_price_context_counts[touch_key][bucket][volatility_bucket][profile_price_context_bucket]["touched"] += 1
            profile_price_crypto_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(profile_price_crypto_profile_bucket, {}).setdefault(
                profile_price_crypto_distance_bucket,
                {"touched": 0, "reached": 0},
            )
            profile_price_crypto_context_counts[touch_key][bucket][volatility_bucket][profile_price_crypto_profile_bucket][
                profile_price_crypto_distance_bucket
            ]["touched"] += 1
            profile_price_execution_context_counts.setdefault(touch_key, {}).setdefault(bucket, {}).setdefault(
                volatility_bucket,
                {},
            ).setdefault(profile_price_execution_profile_bucket, {}).setdefault(
                profile_price_execution_bucket,
                {"touched": 0, "reached": 0},
            )
            profile_price_execution_context_counts[touch_key][bucket][volatility_bucket][
                profile_price_execution_profile_bucket
            ][profile_price_execution_bucket]["touched"] += 1
            if touch_payload.get(first_target_key):
                bucket_counts[touch_key][bucket]["reached"] += 1
                context_counts[touch_key][bucket][volatility_bucket]["reached"] += 1
                microstructure_context_counts[touch_key][bucket][volatility_bucket][depth_pressure_bucket]["reached"] += 1
                execution_context_counts[touch_key][bucket][volatility_bucket][execution_context_bucket]["reached"] += 1
                crypto_context_counts[touch_key][bucket][volatility_bucket][crypto_context_bucket]["reached"] += 1
                crypto_distance_context_counts[touch_key][bucket][volatility_bucket][crypto_distance_bucket]["reached"] += 1
                crypto_distance_execution_context_counts[touch_key][bucket][volatility_bucket][
                    crypto_distance_execution_crypto_bucket
                ][crypto_distance_execution_bucket]["reached"] += 1
                profile_context_counts[touch_key][bucket][volatility_bucket][profile_context_bucket]["reached"] += 1
                profile_price_context_counts[touch_key][bucket][volatility_bucket][profile_price_context_bucket]["reached"] += 1
                profile_price_crypto_context_counts[touch_key][bucket][volatility_bucket][
                    profile_price_crypto_profile_bucket
                ][profile_price_crypto_distance_bucket]["reached"] += 1
                profile_price_execution_context_counts[touch_key][bucket][volatility_bucket][
                    profile_price_execution_profile_bucket
                ][profile_price_execution_bucket]["reached"] += 1
    bucket_probabilities = {
        touch_key: {
            bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
            for bucket, counts in buckets.items()
        }
        for touch_key, buckets in bucket_counts.items()
    }
    context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                for volatility_bucket, counts in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in context_counts.items()
    }
    microstructure_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    depth_pressure_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                    for depth_pressure_bucket, counts in depth_buckets.items()
                }
                for volatility_bucket, depth_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in microstructure_context_counts.items()
    }
    execution_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    execution_context_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                    for execution_context_bucket, counts in execution_buckets.items()
                }
                for volatility_bucket, execution_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in execution_context_counts.items()
    }
    crypto_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    crypto_context_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                    for crypto_context_bucket, counts in crypto_buckets.items()
                }
                for volatility_bucket, crypto_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in crypto_context_counts.items()
    }
    crypto_distance_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    crypto_distance_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                    for crypto_distance_bucket, counts in crypto_distance_buckets.items()
                }
                for volatility_bucket, crypto_distance_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in crypto_distance_context_counts.items()
    }
    crypto_distance_execution_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    crypto_distance_bucket: {
                        execution_context_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                        for execution_context_bucket, counts in execution_buckets.items()
                    }
                    for crypto_distance_bucket, execution_buckets in crypto_distance_buckets.items()
                }
                for volatility_bucket, crypto_distance_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in crypto_distance_execution_context_counts.items()
    }
    profile_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    profile_context_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                    for profile_context_bucket, counts in profile_buckets.items()
                }
                for volatility_bucket, profile_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in profile_context_counts.items()
    }
    profile_price_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    profile_price_context_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                    for profile_price_context_bucket, counts in profile_price_buckets.items()
                }
                for volatility_bucket, profile_price_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in profile_price_context_counts.items()
    }
    profile_price_crypto_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    profile_price_context_bucket: {
                        crypto_distance_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                        for crypto_distance_bucket, counts in crypto_distance_buckets.items()
                    }
                    for profile_price_context_bucket, crypto_distance_buckets in profile_price_buckets.items()
                }
                for volatility_bucket, profile_price_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in profile_price_crypto_context_counts.items()
    }
    profile_price_execution_context_probabilities = {
        touch_key: {
            bucket: {
                volatility_bucket: {
                    profile_price_context_bucket: {
                        execution_context_bucket: (counts["reached"] / counts["touched"] if counts["touched"] else 0.0)
                        for execution_context_bucket, counts in execution_buckets.items()
                    }
                    for profile_price_context_bucket, execution_buckets in profile_price_buckets.items()
                }
                for volatility_bucket, profile_price_buckets in volatility_buckets.items()
            }
            for bucket, volatility_buckets in buckets.items()
        }
        for touch_key, buckets in profile_price_execution_context_counts.items()
    }
    return {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "touch_targets": {
            f"{int(touch * 100)}c": [f"{int(target * 100)}c" for target in targets]
            for touch, targets in TAIL_TOUCH_TARGETS.items()
        },
        "bucket_probabilities": bucket_probabilities,
        "context_probabilities": context_probabilities,
        "microstructure_context_probabilities": microstructure_context_probabilities,
        "execution_context_probabilities": execution_context_probabilities,
        "crypto_context_probabilities": crypto_context_probabilities,
        "crypto_distance_context_probabilities": crypto_distance_context_probabilities,
        "crypto_distance_execution_context_probabilities": crypto_distance_execution_context_probabilities,
        "profile_context_probabilities": profile_context_probabilities,
        "profile_price_context_probabilities": profile_price_context_probabilities,
        "profile_price_crypto_context_probabilities": profile_price_crypto_context_probabilities,
        "profile_price_execution_context_probabilities": profile_price_execution_context_probabilities,
        "observability": {
            "context_counts": context_counts,
            "microstructure_context_counts": microstructure_context_counts,
            "execution_context_counts": execution_context_counts,
            "crypto_context_counts": crypto_context_counts,
            "crypto_distance_context_counts": crypto_distance_context_counts,
            "crypto_distance_execution_context_counts": crypto_distance_execution_context_counts,
            "profile_context_counts": profile_context_counts,
            "profile_price_context_counts": profile_price_context_counts,
            "profile_price_crypto_context_counts": profile_price_crypto_context_counts,
            "profile_price_execution_context_counts": profile_price_execution_context_counts,
            "context_dimensions": ["time_remaining_bucket", "volatility_bucket"],
            "microstructure_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "depth_pressure_bucket",
            ],
            "execution_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "spread_bucket",
                "liquidity_bucket",
                "slippage_bucket",
            ],
            "crypto_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "crypto_context_bucket",
            ],
            "crypto_distance_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "crypto_distance_bucket",
            ],
            "crypto_distance_execution_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "crypto_distance_bucket",
                "execution_context_bucket",
            ],
            "profile_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "profile_context_bucket",
            ],
            "profile_price_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "profile_price_context_bucket",
            ],
            "profile_price_crypto_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "profile_price_context_bucket",
                "crypto_distance_bucket",
            ],
            "profile_price_execution_context_dimensions": [
                "time_remaining_bucket",
                "volatility_bucket",
                "profile_price_context_bucket",
                "execution_context_bucket",
            ],
        },
        "sides": sides,
    }


def _tail_comebacks_for_side(
    points: list[tuple[datetime, float]],
    *,
    event_start: datetime | None,
    event_end: datetime | None,
    depth_points: list[tuple[datetime, float]] | None = None,
    execution_context_points: list[tuple[datetime, dict[str, float]]] | None = None,
    profile_contexts: list[dict[str, Any]] | None = None,
    crypto_contexts: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    post_start_points = [
        (timestamp, value)
        for timestamp, value in points
        if event_start is None or timestamp >= event_start
    ]
    payload: dict[str, dict[str, Any]] = {}
    for touch, targets in TAIL_TOUCH_TARGETS.items():
        touch_key = f"touched_{int(touch * 100)}c"
        first_touch_index: int | None = None
        for index, (_timestamp, value) in enumerate(post_start_points):
            if value <= touch + 1e-9:
                first_touch_index = index
                break
        if first_touch_index is None:
            payload[touch_key] = {
                "touched": False,
                "first_touch_seconds": None,
                "time_remaining_seconds": None,
                "time_remaining_bucket": None,
                "max_after_touch": None,
                "first_target_key": f"reached_{int(targets[0] * 100)}c",
            }
            for target in targets:
                payload[touch_key][f"reached_{int(target * 100)}c"] = False
                payload[touch_key][f"time_to_{int(target * 100)}c_seconds"] = None
            continue
        touch_time, _touch_value = post_start_points[first_touch_index]
        future = post_start_points[first_touch_index:]
        max_after_touch = max(value for _timestamp, value in future)
        elapsed = None if event_start is None else max(0.0, (touch_time - event_start).total_seconds())
        remaining = None if event_end is None else max(0.0, (event_end - touch_time).total_seconds())
        pre_touch_range = _pre_touch_range(points=post_start_points, touch_index=first_touch_index)
        depth_pressure_score = _pre_touch_depth_pressure(depth_points=depth_points or [], touch_time=touch_time)
        execution_context = _pre_touch_execution_context(
            execution_context_points=execution_context_points or [],
            touch_time=touch_time,
        )
        profile_context = _profile_context_at_or_before(profile_contexts or [], touch_time=touch_time)
        crypto_context = _crypto_context_at_or_before(crypto_contexts or [], touch_time=touch_time)
        item: dict[str, Any] = {
            "touched": True,
            "first_touch_seconds": elapsed,
            "time_remaining_seconds": remaining,
            "time_remaining_bucket": None if remaining is None else _time_remaining_bucket(remaining),
            "pre_touch_range": pre_touch_range,
            "volatility_bucket": _volatility_bucket(pre_touch_range),
            "depth_pressure_score": depth_pressure_score,
            "depth_pressure_bucket": _depth_pressure_bucket(depth_pressure_score),
            "avg_spread_before_touch": execution_context.get("avg_spread_before_touch"),
            "spread_bucket": execution_context.get("spread_bucket"),
            "avg_depth_before_touch": execution_context.get("avg_depth_before_touch"),
            "liquidity_bucket": execution_context.get("liquidity_bucket"),
            "slippage_proxy_before_touch": execution_context.get("slippage_proxy_before_touch"),
            "slippage_bucket": execution_context.get("slippage_bucket"),
            "execution_context_bucket": execution_context.get("execution_context_bucket"),
            "crypto_context_bucket": crypto_context.get("crypto_context_bucket"),
            "crypto_symbol": crypto_context.get("symbol"),
            "crypto_distance_bucket": crypto_context.get("crypto_distance_bucket"),
            "crypto_summary_score": crypto_context.get("summary_score"),
            "crypto_context_snapshot_at_utc": crypto_context.get("completed_at_utc"),
            "profile_context_bucket": profile_context.get("profile_context_bucket"),
            "profile_price_context_bucket": profile_context.get("profile_price_context_bucket"),
            "profile_price_crypto_context_bucket": _profile_price_crypto_context_bucket(
                profile_context.get("profile_price_context_bucket"),
                crypto_context.get("crypto_distance_bucket"),
            ),
            "profile_price_execution_context_bucket": _profile_price_execution_context_bucket(
                profile_context.get("profile_price_context_bucket"),
                execution_context.get("execution_context_bucket"),
            ),
            "crypto_distance_execution_context_bucket": _crypto_distance_execution_context_bucket(
                crypto_context.get("crypto_distance_bucket"),
                execution_context.get("execution_context_bucket"),
            ),
            "profile_context_label": profile_context.get("profile_context_label"),
            "profile_tilt_side": profile_context.get("profile_tilt_side"),
            "profile_price_side": profile_context.get("profile_price_side"),
            "profile_price_delta": profile_context.get("profile_price_delta"),
            "profile_price_delta_bucket": profile_context.get("profile_price_delta_bucket"),
            "profile_context_snapshot_at_utc": profile_context.get("computed_at_utc"),
            "max_after_touch": max_after_touch,
            "first_target_key": f"reached_{int(targets[0] * 100)}c",
        }
        for target in targets:
            target_key = f"reached_{int(target * 100)}c"
            target_time_key = f"time_to_{int(target * 100)}c_seconds"
            hit_time = next((timestamp for timestamp, value in future if value >= target - 1e-9), None)
            item[target_key] = hit_time is not None
            item[target_time_key] = None if hit_time is None else max(0.0, (hit_time - touch_time).total_seconds())
        payload[touch_key] = item
    return payload


def _time_remaining_bucket(time_remaining_seconds: float) -> str:
    if time_remaining_seconds < 60.0:
        return "lt60"
    if time_remaining_seconds <= 180.0:
        return "60_180"
    return "gt180"


def _pre_touch_range(*, points: list[tuple[datetime, float]], touch_index: int, lookback_points: int = 4) -> float:
    if touch_index < 0 or not points:
        return 0.0
    start_index = max(0, touch_index - max(1, lookback_points) + 1)
    window = [value for _timestamp, value in points[start_index : touch_index + 1]]
    if not window:
        return 0.0
    return max(window) - min(window)


def _volatility_bucket(pre_touch_range: float) -> str:
    if pre_touch_range < 0.04:
        return "calm"
    if pre_touch_range < 0.12:
        return "active"
    return "violent"


def _depth_pressure_points(rows: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    points: list[tuple[datetime, float]] = []
    for row in rows:
        timestamp = row.get("bucket_timestamp_utc")
        if not timestamp:
            continue
        points.append((_parse_datetime(timestamp), _depth_pressure(row)))
    return points


def _pre_touch_depth_pressure(
    *,
    depth_points: list[tuple[datetime, float]],
    touch_time: datetime,
    lookback_points: int = 4,
) -> float:
    eligible = [value for timestamp, value in depth_points if timestamp <= touch_time]
    if not eligible:
        return 0.0
    window = eligible[-max(1, lookback_points) :]
    return sum(window) / len(window)


def _depth_pressure_bucket(depth_pressure_score: float) -> str:
    score = float(depth_pressure_score)
    if score <= -0.2:
        return "down_supportive"
    if score >= 0.2:
        return "up_supportive"
    return "balanced"


def _execution_context_points(rows: list[dict[str, Any]]) -> list[tuple[datetime, dict[str, float]]]:
    points: list[tuple[datetime, dict[str, float]]] = []
    for row in rows:
        timestamp = row.get("bucket_timestamp_utc")
        if not timestamp:
            continue
        points.append(
            (
                _parse_datetime(timestamp),
                {
                    "spread": _pair_spread(row),
                    "liquidity_depth": _pair_liquidity_depth(row),
                },
            )
        )
    return points


def _pair_spread(row: dict[str, Any]) -> float:
    spreads = [
        _spread(_optional_float(row.get("up_best_bid")), _optional_float(row.get("up_best_ask"))),
        _spread(_optional_float(row.get("down_best_bid")), _optional_float(row.get("down_best_ask"))),
    ]
    valid = [spread for spread in spreads if spread is not None]
    if not valid:
        return 0.0
    return sum(valid) / len(valid)


def _spread(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is None or best_ask is None:
        return None
    return max(0.0, best_ask - best_bid)


def _pair_liquidity_depth(row: dict[str, Any]) -> float:
    return sum(
        max(0.0, _optional_float(row.get(key)) or 0.0)
        for key in (
            "up_depth_top3_bid_size",
            "up_depth_top3_ask_size",
            "down_depth_top3_bid_size",
            "down_depth_top3_ask_size",
        )
    )


def _pre_touch_execution_context(
    *,
    execution_context_points: list[tuple[datetime, dict[str, float]]],
    touch_time: datetime,
    lookback_points: int = 4,
) -> dict[str, Any]:
    eligible = [payload for timestamp, payload in execution_context_points if timestamp <= touch_time]
    if not eligible:
        return {
            "avg_spread_before_touch": 0.0,
            "spread_bucket": "tight",
            "avg_depth_before_touch": 0.0,
            "liquidity_bucket": "shallow",
            "slippage_proxy_before_touch": 0.0,
            "slippage_bucket": "low",
            "execution_context_bucket": "tight|shallow|low",
        }
    window = eligible[-max(1, lookback_points) :]
    avg_spread = sum(max(0.0, float(payload.get("spread") or 0.0)) for payload in window) / len(window)
    avg_depth = sum(max(0.0, float(payload.get("liquidity_depth") or 0.0)) for payload in window) / len(window)
    slippage_proxy = avg_spread / max(avg_depth, 1.0)
    spread_bucket = _spread_bucket(avg_spread)
    liquidity_bucket = _liquidity_bucket(avg_depth)
    slippage_bucket = _slippage_bucket(slippage_proxy)
    return {
        "avg_spread_before_touch": avg_spread,
        "spread_bucket": spread_bucket,
        "avg_depth_before_touch": avg_depth,
        "liquidity_bucket": liquidity_bucket,
        "slippage_proxy_before_touch": slippage_proxy,
        "slippage_bucket": slippage_bucket,
        "execution_context_bucket": f"{spread_bucket}|{liquidity_bucket}|{slippage_bucket}",
    }


def _spread_bucket(avg_spread: float) -> str:
    if avg_spread < 0.025:
        return "tight"
    if avg_spread < 0.05:
        return "normal"
    return "wide"


def _liquidity_bucket(avg_depth: float) -> str:
    if avg_depth < 20.0:
        return "shallow"
    if avg_depth < 40.0:
        return "medium"
    return "deep"


def _slippage_bucket(slippage_proxy: float) -> str:
    if slippage_proxy < 0.001:
        return "low"
    if slippage_proxy < 0.003:
        return "moderate"
    return "elevated"


def _load_crypto_context_snapshots(conn: Any, *, symbol: str) -> list[dict[str, Any]]:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol or not _table_exists(conn, "external_technical_observer_snapshots"):
        return []
    rows = conn.execute(
        """
        SELECT provider, symbol, interval, completed_at_utc, summary_label, summary_score
          FROM external_technical_observer_snapshots
         WHERE symbol = ?
         ORDER BY completed_at_utc ASC
        """,
        (normalized_symbol,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_profile_context_snapshots(conn: Any, *, event_key: str, event_slug: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "profile_distribution_snapshots"):
        return []
    rows = conn.execute(
        """
        SELECT distribution_snapshot_key, event_key, event_slug, computed_at_utc, distribution_json
          FROM profile_distribution_snapshots
         WHERE (? != '' AND event_key = ?)
            OR (? != '' AND event_slug = ?)
         ORDER BY computed_at_utc ASC
        """,
        (event_key, event_key, event_slug, event_slug),
    ).fetchall()
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        try:
            distribution = json.loads(payload.get("distribution_json") or "{}")
        except json.JSONDecodeError:
            distribution = {}
        snapshots.append(
            {
                "distribution_snapshot_key": payload.get("distribution_snapshot_key"),
                "computed_at_utc": payload.get("computed_at_utc"),
                "distribution": distribution,
                "component_breakdown": _profile_distribution_component_breakdown(
                    conn,
                    distribution_snapshot_key=str(payload.get("distribution_snapshot_key") or ""),
                ),
            }
        )
    return snapshots


def _profile_distribution_component_breakdown(conn: Any, *, distribution_snapshot_key: str) -> dict[str, Any]:
    if not distribution_snapshot_key or not _table_exists(conn, "profile_distribution_components"):
        return {"by_grade": {}, "by_style": {}, "by_grade_style": {}}
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(grade, ''), 'unknown') AS grade,
            COALESCE(NULLIF(trading_style, ''), 'unknown') AS trading_style,
            COALESCE(NULLIF(outcome, ''), 'unknown') AS outcome,
            COUNT(*) AS component_count,
            COUNT(DISTINCT profile_key) AS profile_count,
            SUM(net_shares * final_weight) AS weighted_shares,
            SUM(cost_basis_usd * final_weight) AS weighted_cost
          FROM profile_distribution_components
         WHERE distribution_snapshot_key = ?
         GROUP BY grade, trading_style, outcome
        """,
        (distribution_snapshot_key,),
    ).fetchall()
    raw: dict[str, dict[str, dict[str, Any]]] = {"by_grade": {}, "by_style": {}, "by_grade_style": {}}
    for row in rows:
        payload = dict(row)
        outcome = _normalize_profile_outcome(payload.get("outcome"))
        if outcome not in {"up", "down"}:
            continue
        grade = str(payload.get("grade") or "unknown")
        style = str(payload.get("trading_style") or "unknown")
        _accumulate_breakdown_bucket(raw["by_grade"].setdefault(grade, {}), outcome, payload)
        _accumulate_breakdown_bucket(raw["by_style"].setdefault(style, {}), outcome, payload)
        _accumulate_breakdown_bucket(raw["by_grade_style"].setdefault(f"{grade} / {style}", {}), outcome, payload)
    return {
        group: {
            label: _breakdown_row(label, sides)
            for label, sides in sorted(labels.items())
        }
        for group, labels in raw.items()
    }


def _profile_context_points(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        computed_at = snapshot.get("computed_at_utc")
        if not computed_at:
            continue
        payload = dict(snapshot)
        payload["computed_at"] = _parse_datetime(str(computed_at))
        points.append(payload)
    points.sort(key=lambda item: item["computed_at"])
    return points


def _crypto_context_points(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        completed_at = snapshot.get("completed_at_utc")
        if not completed_at:
            continue
        payload = dict(snapshot)
        payload["completed_at"] = _parse_datetime(str(completed_at))
        points.append(payload)
    points.sort(key=lambda item: item["completed_at"])
    return points


def _profile_context_at_or_before(profile_contexts: list[dict[str, Any]], *, touch_time: datetime) -> dict[str, Any]:
    eligible = [item for item in profile_contexts if item.get("computed_at") and item["computed_at"] <= touch_time]
    if not eligible:
        return {}
    snapshot = eligible[-1]
    distribution = snapshot.get("distribution") if isinstance(snapshot.get("distribution"), dict) else {}
    prices = (
        distribution.get("reconstructed_profile_prices")
        if isinstance(distribution.get("reconstructed_profile_prices"), dict)
        else {}
    )
    breakdown = snapshot.get("component_breakdown") if isinstance(snapshot.get("component_breakdown"), dict) else {}
    dominant_label, dominant_payload = _dominant_profile_group(breakdown)
    profile_tilt_side = _pressure_side(
        dominant_payload.get("pressure_delta") if isinstance(dominant_payload, dict) else None,
        positive="up",
        negative="down",
    )
    profile_price_side = _price_side(prices.get("up"), prices.get("down"))
    profile_price_delta = _price_delta(prices.get("up"), prices.get("down"))
    profile_price_delta_bucket = _profile_price_delta_bucket(profile_price_delta)
    return {
        "computed_at_utc": snapshot.get("computed_at_utc"),
        "profile_context_label": dominant_label,
        "profile_tilt_side": profile_tilt_side,
        "profile_price_side": profile_price_side,
        "profile_price_delta": profile_price_delta,
        "profile_price_delta_bucket": profile_price_delta_bucket,
        "profile_context_bucket": _profile_context_bucket(dominant_label, profile_tilt_side, profile_price_side),
        "profile_price_context_bucket": _profile_price_context_bucket(
            dominant_label,
            profile_tilt_side,
            profile_price_delta_bucket,
        ),
    }


def _crypto_context_at_or_before(crypto_contexts: list[dict[str, Any]], *, touch_time: datetime) -> dict[str, Any]:
    eligible = [item for item in crypto_contexts if item.get("completed_at") and item["completed_at"] <= touch_time]
    if not eligible:
        return {}
    snapshot = eligible[-1]
    summary_score = _optional_float(snapshot.get("summary_score"))
    distance_bucket = _crypto_distance_bucket(summary_score)
    return {
        "symbol": str(snapshot.get("symbol") or "").strip().upper() or None,
        "completed_at_utc": snapshot.get("completed_at_utc"),
        "summary_score": summary_score,
        "crypto_distance_bucket": distance_bucket,
        "crypto_context_bucket": _crypto_context_bucket(
            symbol=str(snapshot.get("symbol") or ""),
            distance_bucket=distance_bucket,
        ),
    }


def _dominant_profile_group(breakdown: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    groups = breakdown.get("by_grade_style") if isinstance(breakdown.get("by_grade_style"), dict) else {}
    if not groups:
        return "aggregate", {}
    best_label = "aggregate"
    best_payload: dict[str, Any] = {}
    best_weight = -1.0
    for label, payload in groups.items():
        if not isinstance(payload, dict):
            continue
        up_ratio = _optional_float(payload.get("up_pressure_ratio")) or 0.0
        down_ratio = _optional_float(payload.get("down_pressure_ratio")) or 0.0
        weight = max(up_ratio, down_ratio)
        if weight > best_weight:
            best_weight = weight
            best_label = _normalize_profile_group_label(str(label))
            best_payload = payload
    return best_label, best_payload


def _normalize_profile_group_label(label: str) -> str:
    parts = [part.strip().lower().replace("+", "plus").replace(" ", "_") for part in label.split("/") if part.strip()]
    return "_".join(parts) if parts else "aggregate"


def _profile_context_bucket(label: str, tilt_side: str, price_side: str) -> str:
    return f"{label}|{tilt_side}|{price_side}"


def _profile_price_context_bucket(label: str, tilt_side: str, price_delta_bucket: str) -> str:
    return f"{label}|{tilt_side}|{price_delta_bucket}"


def _profile_price_crypto_context_bucket(profile_price_context_bucket: Any, crypto_distance_bucket: Any) -> str:
    profile_bucket = str(profile_price_context_bucket or "unknown")
    crypto_bucket = str(crypto_distance_bucket or "unknown")
    return f"{profile_bucket}||{crypto_bucket}"


def _profile_price_execution_context_bucket(profile_price_context_bucket: Any, execution_context_bucket: Any) -> str:
    profile_bucket = str(profile_price_context_bucket or "unknown")
    execution_bucket = str(execution_context_bucket or "unknown")
    return f"{profile_bucket}||{execution_bucket}"


def _crypto_distance_execution_context_bucket(crypto_distance_bucket: Any, execution_context_bucket: Any) -> str:
    crypto_bucket = str(crypto_distance_bucket or "unknown")
    execution_bucket = str(execution_context_bucket or "unknown")
    return f"{crypto_bucket}||{execution_bucket}"


def _crypto_context_bucket(*, symbol: str, distance_bucket: str) -> str:
    normalized_symbol = (symbol or "unknown").strip().lower() or "unknown"
    return f"{normalized_symbol}|{distance_bucket}"


def _crypto_distance_bucket(summary_score: float | None) -> str:
    score = 0.0 if summary_score is None else max(-1.0, min(1.0, float(summary_score)))
    if score <= -0.45:
        return "far_down"
    if score <= -0.15:
        return "moderate_down"
    if score < 0.15:
        return "neutral"
    if score < 0.45:
        return "moderate_up"
    return "far_up"


def _pressure_side(value: Any, *, positive: str, negative: str) -> str:
    score = _optional_float(value)
    if score is None:
        return "balanced"
    if score >= 0.1:
        return positive
    if score <= -0.1:
        return negative
    return "balanced"


def _price_side(up_price: Any, down_price: Any) -> str:
    up = _optional_float(up_price)
    down = _optional_float(down_price)
    if up is None or down is None:
        return "balanced"
    if up - down >= 0.05:
        return "up"
    if down - up >= 0.05:
        return "down"
    return "balanced"


def _price_delta(up_price: Any, down_price: Any) -> float | None:
    up = _optional_float(up_price)
    down = _optional_float(down_price)
    if up is None or down is None:
        return None
    return up - down


def _profile_price_delta_bucket(price_delta: float | None) -> str:
    if price_delta is None:
        return "balanced_narrow"
    delta = float(price_delta)
    abs_delta = abs(delta)
    side = "up" if delta >= 0.0 else "down"
    if abs_delta < 0.05:
        return "balanced_narrow"
    if abs_delta < 0.20:
        return f"{side}_lean"
    if abs_delta < 0.40:
        return f"{side}_strong"
    return f"{side}_extreme"


def _price_points_at_offsets(
    points: list[tuple[datetime, float]],
    event_start: datetime | None,
    offsets_seconds: tuple[int, ...],
) -> dict[str, float | None]:
    if not points or event_start is None:
        return {f"{offset:+d}s" if offset < 0 else f"{offset}s": None for offset in offsets_seconds}
    return {
        (f"{offset:+d}s" if offset < 0 else f"{offset}s"): _interpolated_price_at(points, event_start.timestamp() + offset)
        for offset in offsets_seconds
    }


def _interpolated_price_at(points: list[tuple[datetime, float]], target_timestamp: float) -> float | None:
    if not points:
        return None
    first_timestamp = points[0][0].timestamp()
    last_timestamp = points[-1][0].timestamp()
    if target_timestamp < first_timestamp or target_timestamp > last_timestamp:
        return None
    previous_time, previous_value = points[0]
    if target_timestamp == previous_time.timestamp():
        return previous_value
    for current_time, current_value in points[1:]:
        current_timestamp = current_time.timestamp()
        previous_timestamp = previous_time.timestamp()
        if target_timestamp == current_timestamp:
            return current_value
        if previous_timestamp <= target_timestamp <= current_timestamp:
            span = max(current_timestamp - previous_timestamp, 1e-9)
            ratio = (target_timestamp - previous_timestamp) / span
            return previous_value + (current_value - previous_value) * ratio
        previous_time, previous_value = current_time, current_value
    return points[-1][1]


def _level_first_touch_seconds(points: list[tuple[datetime, float]], event_start: datetime | None) -> dict[str, float | None]:
    touches: dict[str, float | None] = {f"{int(level * 100)}c": None for level in PRICE_LEVELS}
    if not points or event_start is None:
        return touches
    for level in PRICE_LEVELS:
        key = f"{int(level * 100)}c"
        for timestamp, value in points:
            if timestamp < event_start:
                continue
            if value >= level:
                touches[key] = max(0.0, (timestamp - event_start).total_seconds())
                break
    return touches


def _path_direction(values: list[float]) -> str | None:
    if len(values) < 2:
        return None
    delta = values[-1] - values[0]
    if abs(delta) < 0.01:
        return "flat"
    return "up" if delta > 0 else "down"


def _path_efficiency(values: list[float], abs_move_sum: float) -> float | None:
    if len(values) < 2 or abs_move_sum <= 0:
        return None
    return abs(values[-1] - values[0]) / abs_move_sum


def _time_to_first_extreme_seconds(points: list[tuple[datetime, float]], event_start: datetime | None) -> float | None:
    if not points or event_start is None:
        return None
    for timestamp, value in points:
        if timestamp < event_start:
            continue
        if value <= 0.10 or value >= 0.90:
            return max(0.0, (timestamp - event_start).total_seconds())
    return None


def _swing_distances(points: list[tuple[datetime, float]], *, min_turn_move: float = 0.01) -> list[float]:
    if len(points) < 3:
        return []
    extrema: list[float] = [points[0][1]]
    direction = 0
    candidate = points[0][1]
    for _timestamp, value in points[1:]:
        diff = value - candidate
        if abs(diff) < min_turn_move:
            continue
        new_direction = 1 if diff > 0 else -1
        if direction == 0:
            direction = new_direction
            candidate = value
            continue
        if new_direction == direction:
            candidate = value
            continue
        extrema.append(candidate)
        direction = new_direction
        candidate = value
    extrema.append(candidate)
    return [abs(extrema[index] - extrema[index - 1]) for index in range(1, len(extrema))]


def _rolling_ranges(points: list[tuple[datetime, float]], *, window_seconds: int) -> list[float]:
    if len(points) < 2:
        return []
    ranges: list[float] = []
    left = 0
    for right, (timestamp, _value) in enumerate(points):
        while left < right and (timestamp - points[left][0]).total_seconds() > window_seconds:
            left += 1
        window_values = [value for _time, value in points[left : right + 1]]
        if len(window_values) >= 2:
            ranges.append(max(window_values) - min(window_values))
    return ranges


def _direction_flips(diffs: list[float]) -> int:
    signs = [1 if diff > 0 else -1 for diff in diffs if abs(diff) >= 0.005]
    return sum(1 for index in range(1, len(signs)) if signs[index] != signs[index - 1])


def _strong_rebound_touches(values: list[float]) -> int:
    touches = 0
    for index in range(1, max(1, len(values) - 8)):
        previous = values[index - 1]
        current = values[index]
        next_value = values[index + 1]
        future = values[index + 1 : index + 9]
        if current > previous and current >= next_value and current - min(future) >= 0.05:
            touches += 1
        elif current < previous and current <= next_value and max(future) - current >= 0.05:
            touches += 1
    return touches


def _depth_pressure(row: dict[str, Any]) -> float:
    up_bid = _optional_float(row.get("up_depth_top3_bid_size")) or 0.0
    up_ask = _optional_float(row.get("up_depth_top3_ask_size")) or 0.0
    down_bid = _optional_float(row.get("down_depth_top3_bid_size")) or 0.0
    down_ask = _optional_float(row.get("down_depth_top3_ask_size")) or 0.0
    up_pressure = (up_bid - up_ask) / (up_bid + up_ask) if up_bid + up_ask else 0.0
    down_pressure = (down_bid - down_ask) / (down_bid + down_ask) if down_bid + down_ask else 0.0
    return up_pressure - down_pressure


def _normalize_profile_outcome(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("up") or text in {"yes", "long"}:
        return "up"
    if text.startswith("down") or text in {"no", "short"}:
        return "down"
    return "unknown"


def _accumulate_breakdown_bucket(target: dict[str, Any], outcome: str, row: dict[str, Any]) -> None:
    bucket = target.setdefault(
        outcome,
        {
            "component_count": 0,
            "profile_count": 0,
            "weighted_shares": 0.0,
            "weighted_cost": 0.0,
        },
    )
    bucket["component_count"] += int(row.get("component_count") or 0)
    bucket["profile_count"] += int(row.get("profile_count") or 0)
    bucket["weighted_shares"] += float(row.get("weighted_shares") or 0.0)
    bucket["weighted_cost"] += float(row.get("weighted_cost") or 0.0)


def _breakdown_row(label: str, sides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    up = sides.get("up", {})
    down = sides.get("down", {})
    up_cost = float(up.get("weighted_cost") or 0.0)
    down_cost = float(down.get("weighted_cost") or 0.0)
    up_shares = float(up.get("weighted_shares") or 0.0)
    down_shares = float(down.get("weighted_shares") or 0.0)
    total_cost = up_cost + down_cost
    up_ratio = up_cost / total_cost if total_cost > 0 else None
    down_ratio = down_cost / total_cost if total_cost > 0 else None
    up_price = _weighted_profile_price(up_cost, up_shares)
    down_price = _weighted_profile_price(down_cost, down_shares)
    return {
        "label": label,
        "component_count": int(up.get("component_count") or 0) + int(down.get("component_count") or 0),
        "profile_count": int(up.get("profile_count") or 0) + int(down.get("profile_count") or 0),
        "up_pressure_ratio": up_ratio,
        "down_pressure_ratio": down_ratio,
        "pressure_delta": None if up_ratio is None or down_ratio is None else up_ratio - down_ratio,
        "up_reconstructed_profile_price": up_price,
        "down_reconstructed_profile_price": down_price,
        "reconstructed_profile_pair_sum": None if up_price is None or down_price is None else up_price + down_price,
    }


def _weighted_profile_price(cost: float, shares: float) -> float | None:
    if abs(shares) <= 1e-9:
        return None
    return cost / abs(shares)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _non_negative_latency_ms(value: Any) -> float | None:
    latency = _optional_float(value)
    if latency is None:
        return None
    return max(0.0, latency)


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return _parse_datetime(value)
    except (TypeError, ValueError):
        return None
