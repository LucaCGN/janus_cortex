from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.indicators.compute import IndicatorSnapshot


def summarize_indicator_snapshots(snapshots: list[IndicatorSnapshot]) -> dict[str, Any]:
    if not snapshots:
        return {"direction": "sideways", "confidence": 0.0, "sources": []}
    directional_weight = {"up": 0.0, "down": 0.0, "sideways": 0.0}
    for snapshot in snapshots:
        directional_weight[snapshot.direction] = directional_weight.get(snapshot.direction, 0.0) + snapshot.confidence
    direction = max(directional_weight, key=directional_weight.get)
    total = sum(directional_weight.values())
    confidence = 0.0 if total == 0 else directional_weight[direction] / total
    return {
        "direction": direction,
        "confidence": round(confidence, 6),
        "sources": [snapshot.indicator_id for snapshot in snapshots],
        "weights": directional_weight,
    }


def insert_indicator_snapshot(conn: Any, snapshot: IndicatorSnapshot) -> None:
    snapshot_key = _stable_key(snapshot.indicator_id, snapshot.symbol, snapshot.interval, snapshot.computed_at_utc.isoformat())
    conn.execute(
        """
        INSERT INTO indicator_snapshots(
            snapshot_key, symbol, interval, indicator_id, computed_at_utc, direction,
            confidence, signal_value, components_json, quality_flags_json, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_key) DO NOTHING
        """,
        (
            snapshot_key,
            snapshot.symbol,
            snapshot.interval,
            snapshot.indicator_id,
            snapshot.computed_at_utc.astimezone(UTC).isoformat(),
            snapshot.direction,
            snapshot.confidence,
            snapshot.signal_value,
            json.dumps(snapshot.components, sort_keys=True, default=str),
            json.dumps(snapshot.quality_flags, sort_keys=True, default=str),
            "{}",
            datetime.now(UTC).isoformat(),
        ),
    )


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
