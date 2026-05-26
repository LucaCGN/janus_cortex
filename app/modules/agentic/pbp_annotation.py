from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "pbp_annotation_evidence_v1"
INTENDED_MODEL = "gpt-5.4-nano"


def build_pbp_annotation_evidence(
    *,
    event_id: str,
    live_state: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    source: str = "janus-pbp-annotation",
) -> dict[str, Any]:
    """Build cheap, evidence-only PBP tags for live sleeve/context review.

    This slice intentionally does not emit valuation triggers. It gives the
    runtime a deterministic contract that can later be backed by a nano model.
    """

    live = dict(live_state or {})
    rows = _recent_play_rows(live)
    snapshots = _snapshot_rows(live)
    latest = _latest_snapshot(live, snapshots=snapshots)
    plan_roles = _plan_roles(plan)
    tags: list[dict[str, Any]] = []

    run_tag = _score_run_tag(snapshots=snapshots, latest=latest)
    if run_tag is not None:
        tags.append(run_tag)

    late_tag = _late_game_tag(latest)
    if late_tag is not None:
        tags.append(late_tag)

    blowout_tag = _blowout_tag(latest)
    if blowout_tag is not None:
        tags.append(blowout_tag)

    player_tag = _player_status_text_tag(rows)
    if player_tag is not None:
        tags.append(player_tag)

    quarter_tag = _quarter_boundary_tag(latest)
    if quarter_tag is not None:
        tags.append(quarter_tag)

    status = "ready"
    if _is_pregame(latest):
        status = "pregame_waiting_for_live_pbp"
    elif not rows and not snapshots:
        status = "no_live_pbp_or_scoreboard_rows"
    elif not rows:
        status = "scoreboard_only_no_recent_pbp_rows"

    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "status": status,
        "source_confidence": "db_confirmed" if live else "missing",
        "execution_boundary": "evidence_only",
        "must_not_place_orders": True,
        "emit_trigger": False,
        "intended_model": INTENDED_MODEL,
        "model_tier": "deterministic_fallback",
        "llm_trigger_type": "compression_or_tagging",
        "recent_play_by_play_count": len(rows),
        "snapshot_count": len(snapshots),
        "plan_sleeve_roles": sorted(plan_roles),
        "tag_count": len(tags),
        "tags": tags,
        "signals": [
            {
                "emit_trigger": False,
                "trigger_type": "compression_or_tagging",
                "tag_type": tag["tag_type"],
                "severity": tag["severity"],
                "confidence": tag["confidence"],
                "sleeve_relevance": tag["sleeve_relevance"],
                "reason": tag["reason"],
            }
            for tag in tags
        ],
        "recommended_escalation": _recommended_escalation(tags),
    }


def _recent_play_rows(live_state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = live_state.get("recent_play_by_play")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _snapshot_rows(live_state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = live_state.get("live_snapshots")
    if not isinstance(rows, list):
        latest = live_state.get("latest_snapshot")
        return [dict(latest)] if isinstance(latest, dict) else []
    clean = [dict(row) for row in rows if isinstance(row, dict)]
    latest = live_state.get("latest_snapshot")
    if isinstance(latest, dict) and latest not in clean:
        clean.insert(0, dict(latest))
    return clean


def _latest_snapshot(live_state: dict[str, Any], *, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    latest = live_state.get("latest_snapshot")
    if isinstance(latest, dict):
        return dict(latest)
    return dict(snapshots[0]) if snapshots else {}


def _is_pregame(latest: dict[str, Any]) -> bool:
    status = str(latest.get("game_status") or latest.get("gameStatus") or "").strip()
    status_text = str(latest.get("game_status_text") or latest.get("status_text") or latest.get("status") or "").lower()
    return status == "1" or "pm et" in status_text or "pregame" in status_text


def _score_run_tag(*, snapshots: list[dict[str, Any]], latest: dict[str, Any]) -> dict[str, Any] | None:
    if len(snapshots) < 2:
        return None
    latest_home = _number(latest.get("home_score") or latest.get("homeScore"))
    latest_away = _number(latest.get("away_score") or latest.get("awayScore"))
    if latest_home is None or latest_away is None:
        return None
    latest_total = latest_home + latest_away
    best: dict[str, Any] | None = None
    for row in snapshots[1:16]:
        home = _number(row.get("home_score") or row.get("homeScore"))
        away = _number(row.get("away_score") or row.get("awayScore"))
        if home is None or away is None:
            continue
        total_delta = latest_total - (home + away)
        if total_delta <= 0:
            continue
        home_delta = latest_home - home
        away_delta = latest_away - away
        swing = abs(home_delta - away_delta)
        if total_delta >= 8 and swing >= 6:
            leader = "home" if home_delta > away_delta else "away"
            best = {
                "tag_type": "score_run",
                "severity": "elevated" if swing >= 10 else "routine",
                "confidence": 0.72 if swing >= 10 else 0.64,
                "sleeve_relevance": ["grid_scalp", "core_hold"],
                "reason": f"{leader} side run detected from scoreboard snapshots: {home_delta:.0f}-{away_delta:.0f}.",
                "evidence": {
                    "home_delta": home_delta,
                    "away_delta": away_delta,
                    "total_delta": total_delta,
                    "swing": swing,
                    "leader": leader,
                },
            }
            break
    return best


def _late_game_tag(latest: dict[str, Any]) -> dict[str, Any] | None:
    period = _period(latest)
    clock_seconds = _clock_seconds(latest)
    margin = _score_margin(latest)
    if period < 4 or clock_seconds is None or margin is None:
        return None
    if clock_seconds <= 360 and abs(margin) <= 8:
        return {
            "tag_type": "late_game_uncertainty",
            "severity": "elevated" if abs(margin) <= 4 else "routine",
            "confidence": 0.7,
            "sleeve_relevance": ["grid_scalp", "core_hold", "reduce_stop"],
            "reason": f"Q{period} late window with margin {margin:+.0f} and {clock_seconds:.0f}s remaining.",
            "evidence": {"period": period, "clock_remaining_seconds": clock_seconds, "score_margin": margin},
        }
    return None


def _blowout_tag(latest: dict[str, Any]) -> dict[str, Any] | None:
    period = _period(latest)
    clock_seconds = _clock_seconds(latest)
    margin = _score_margin(latest)
    if period < 3 or margin is None:
        return None
    if abs(margin) >= 18:
        return {
            "tag_type": "blowout_low_price_context",
            "severity": "routine",
            "confidence": 0.62,
            "sleeve_relevance": ["ultra_low_rebound", "reduce_stop"],
            "reason": f"Large Q{period}+ margin {margin:+.0f}; ultra-low behavior should be constrained and evidence-tracked.",
            "evidence": {"period": period, "clock_remaining_seconds": clock_seconds, "score_margin": margin},
        }
    return None


def _quarter_boundary_tag(latest: dict[str, Any]) -> dict[str, Any] | None:
    period = _period(latest)
    clock_seconds = _clock_seconds(latest)
    if period <= 0 or clock_seconds is None:
        return None
    if clock_seconds <= 75:
        return {
            "tag_type": "quarter_boundary_context",
            "severity": "routine",
            "confidence": 0.68,
            "sleeve_relevance": ["grid_scalp", "core_hold"],
            "reason": f"Q{period} boundary approaching with {clock_seconds:.0f}s remaining.",
            "evidence": {"period": period, "clock_remaining_seconds": clock_seconds},
        }
    return None


def _player_status_text_tag(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    keywords = ("injury", "injured", "foul", "fouled out", "technical", "bench", "substitution", "ejected")
    hits: list[str] = []
    for row in rows[:12]:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        text = " ".join(
            str(value)
            for value in (
                row.get("description"),
                row.get("text"),
                payload.get("description"),
                payload.get("actionType"),
                payload.get("subType"),
            )
            if value not in (None, "")
        ).lower()
        if any(keyword in text for keyword in keywords):
            hits.append(text[:180])
    if not hits:
        return None
    return {
        "tag_type": "player_status_text_watch",
        "severity": "elevated",
        "confidence": 0.55,
        "sleeve_relevance": ["core_hold", "reduce_stop", "grid_scalp"],
        "reason": "Recent PBP text contains player-status or rotation keywords; higher layer should review if exposure exists.",
        "evidence": {"matched_rows": hits[:3]},
    }


def _recommended_escalation(tags: list[dict[str, Any]]) -> str:
    if any(tag.get("severity") == "critical" for tag in tags):
        return "frontier_review_if_exposure_exists"
    if any(tag.get("severity") == "elevated" for tag in tags):
        return "mini_review_if_strategy_revision_triggered"
    if tags:
        return "nano_compression_only"
    return "none"


def _plan_roles(plan: dict[str, Any] | None) -> set[str]:
    if not isinstance(plan, dict):
        return set()
    roles: set[str] = set()
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        role = str(strategy.get("sleeve_role") or strategy.get("entry_rules", {}).get("sleeve_role") or "").strip()
        if role:
            roles.add(role)
    return roles


def _period(latest: dict[str, Any]) -> int:
    value = latest.get("period") or latest.get("game_period")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clock_seconds(latest: dict[str, Any]) -> float | None:
    value = latest.get("clock") or latest.get("game_clock") or latest.get("gameClock")
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    match = re.search(r"PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
    if match:
        minutes = float(match.group(1) or 0)
        seconds = float(match.group(2) or 0)
        return minutes * 60 + seconds
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        return float(match.group(1)) * 60 + float(match.group(2))
    return None


def _score_margin(latest: dict[str, Any]) -> float | None:
    home = _number(latest.get("home_score") or latest.get("homeScore"))
    away = _number(latest.get("away_score") or latest.get("awayScore"))
    if home is None or away is None:
        return None
    return home - away


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["SCHEMA_VERSION", "build_pbp_annotation_evidence"]
