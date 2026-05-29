from __future__ import annotations

import re
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable


SCHEMA_VERSION = "pbp_annotation_evidence_v1"
INTENDED_MODEL = "gpt-5.4-nano"
DEFAULT_OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


def build_pbp_annotation_evidence(
    *,
    event_id: str,
    live_state: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    source: str = "janus-pbp-annotation",
    nano_dispatcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    enable_nano_dispatch: bool = False,
    allow_llm_escalation_triggers: bool = False,
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

    nano_result = _maybe_dispatch_nano(
        event_id=event_id,
        rows=rows,
        latest=latest,
        tags=tags,
        plan_roles=plan_roles,
        dispatcher=nano_dispatcher,
        enabled=enable_nano_dispatch,
    )
    tags = _merge_nano_tags(tags, nano_result)

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
        "model_tier": "nano" if nano_result["status"] == "response_recorded" else "deterministic_fallback",
        "nano_dispatch": nano_result,
        "llm_trigger_type": "compression_or_tagging",
        "call_budget": _pbp_call_budget(),
        "cost_estimate": _pbp_cost_estimate(nano_result),
        "recent_play_by_play_count": len(rows),
        "snapshot_count": len(snapshots),
        "plan_sleeve_roles": sorted(plan_roles),
        "tag_count": len(tags),
        "tags": tags,
        "signals": [
            {
                "emit_trigger": _tag_emits_escalation(tag, allow_llm_escalation_triggers=allow_llm_escalation_triggers),
                "trigger_type": _tag_trigger_type(tag),
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
    if any(tag.get("llm_escalation") == "frontier_review_if_deep_loss_exposure" for tag in tags):
        return "frontier_review_if_deep_loss_exposure"
    if any(tag.get("llm_escalation") == "mini_review_if_strategy_revision_triggered" for tag in tags):
        return "mini_review_if_strategy_revision_triggered"
    if any(tag.get("severity") == "critical" for tag in tags):
        return "frontier_review_if_exposure_exists"
    if any(tag.get("severity") == "elevated" for tag in tags):
        return "mini_review_if_strategy_revision_triggered"
    if tags:
        return "nano_compression_only"
    return "none"


def _maybe_dispatch_nano(
    *,
    event_id: str,
    rows: list[dict[str, Any]],
    latest: dict[str, Any],
    tags: list[dict[str, Any]],
    plan_roles: set[str],
    dispatcher: Callable[[dict[str, Any]], dict[str, Any]] | None,
    enabled: bool,
) -> dict[str, Any]:
    payload = {
        "schema_version": "pbp_nano_dispatch_request_v1",
        "event_id": event_id,
        "model": INTENDED_MODEL,
        "execution_boundary": "evidence_only",
        "must_not_place_orders": True,
        "latest_snapshot": latest,
        "recent_play_by_play": rows[:12],
        "deterministic_tags": tags,
        "plan_sleeve_roles": sorted(plan_roles),
        "call_budget": _pbp_call_budget(),
        "allowed_outputs": [
            "summary",
            "tags",
            "llm_escalation",
            "valuation_signal",
        ],
    }
    if not enabled:
        return {"status": "disabled", "model": INTENDED_MODEL, "request": payload}
    if dispatcher is None:
        return {"status": "dispatcher_unavailable", "model": INTENDED_MODEL, "request": payload}
    try:
        response = dispatcher(payload)
    except Exception as exc:  # pragma: no cover - defensive runtime guard.
        return {
            "status": "dispatcher_error",
            "model": INTENDED_MODEL,
            "error": type(exc).__name__,
            "request": payload,
        }
    if not isinstance(response, dict):
        return {"status": "invalid_response", "model": INTENDED_MODEL, "request": payload}
    return {
        "status": "response_recorded",
        "model": INTENDED_MODEL,
        "request": payload,
        "response": _sanitize_nano_response(response),
    }


def _pbp_call_budget() -> dict[str, Any]:
    try:
        from app.modules.agentic import llm_runtime
    except Exception:  # pragma: no cover - defensive import guard.
        return {
            "schema_version": "pbp_nano_call_budget_v1",
            "status": "llm_runtime_unavailable",
            "model": INTENDED_MODEL,
            "trigger_type": "compression_or_tagging",
            "max_output_tokens": 800,
        }
    pricing = getattr(llm_runtime, "_ESTIMATED_MODEL_COST_PER_MILLION_TOKENS", {}).get(INTENDED_MODEL) or {}
    call_caps = getattr(llm_runtime, "_DEFAULT_TRIGGER_CALL_CAPS", {})
    return {
        "schema_version": "pbp_nano_call_budget_v1",
        "status": "included",
        "model": INTENDED_MODEL,
        "trigger_type": "compression_or_tagging",
        "event_token_budget": getattr(llm_runtime, "DEFAULT_EVENT_TOKEN_BUDGET", None),
        "event_cost_budget_usd": getattr(llm_runtime, "DEFAULT_EVENT_COST_BUDGET_USD", None),
        "trigger_call_cap": call_caps.get("compression_or_tagging"),
        "max_output_tokens": 800,
        "estimated_model_cost_per_million_tokens": dict(pricing),
        "pricing_source": "app.modules.agentic.llm_runtime",
    }


def _pbp_cost_estimate(nano_result: dict[str, Any]) -> dict[str, Any]:
    call_budget = _pbp_call_budget()
    pricing = call_budget.get("estimated_model_cost_per_million_tokens") or {}
    input_tokens = _estimated_json_tokens(nano_result.get("request"))
    output_tokens = _estimated_json_tokens(nano_result.get("response")) if nano_result.get("status") == "response_recorded" else 0
    input_cost = _token_cost_usd(input_tokens, pricing.get("input"))
    output_cost = _token_cost_usd(output_tokens, pricing.get("output"))
    dispatch_performed = nano_result.get("status") == "response_recorded"
    return {
        "schema_version": "pbp_nano_cost_estimate_v1",
        "model": INTENDED_MODEL,
        "dispatch_status": str(nano_result.get("status") or "unknown"),
        "dispatch_performed": dispatch_performed,
        "estimated_input_tokens": input_tokens if dispatch_performed else 0,
        "estimated_output_tokens": output_tokens,
        "estimated_input_tokens_if_dispatched": input_tokens,
        "estimated_cost_usd": round(input_cost + output_cost, 8) if dispatch_performed else 0.0,
        "estimated_cost_usd_if_dispatched": round(input_cost + output_cost, 8),
        "estimation_method": "json_characters_divided_by_4",
        "source_confidence": "estimated",
    }


def _estimated_json_tokens(value: Any) -> int:
    if value in (None, ""):
        return 0
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return max(1, int((len(text) + 3) / 4))


def _token_cost_usd(tokens: int, per_million_tokens: Any) -> float:
    try:
        rate = float(per_million_tokens)
    except (TypeError, ValueError):
        return 0.0
    return (max(0, int(tokens)) / 1_000_000) * rate


def _sanitize_nano_response(response: dict[str, Any]) -> dict[str, Any]:
    tags = response.get("tags") if isinstance(response.get("tags"), list) else []
    clean_tags = [dict(tag) for tag in tags if isinstance(tag, dict)]
    escalation = str(response.get("llm_escalation") or response.get("recommended_escalation") or "none").strip()
    if escalation not in {
        "none",
        "mini_review_if_strategy_revision_triggered",
        "frontier_review_if_deep_loss_exposure",
    }:
        escalation = "none"
    valuation_signal = str(response.get("valuation_signal") or "").strip().lower()
    if valuation_signal not in {"", "undervaluation", "overvaluation"}:
        valuation_signal = ""
    return {
        "summary": str(response.get("summary") or "")[:600],
        "llm_escalation": escalation,
        "valuation_signal": valuation_signal or None,
        "tags": clean_tags[:8],
        "must_not_place_orders": True,
        "execution_boundary": "evidence_only",
    }


def _merge_nano_tags(tags: list[dict[str, Any]], nano_result: dict[str, Any]) -> list[dict[str, Any]]:
    response = nano_result.get("response") if isinstance(nano_result.get("response"), dict) else {}
    nano_tags = response.get("tags") if isinstance(response.get("tags"), list) else []
    merged = list(tags)
    for raw_tag in nano_tags:
        if not isinstance(raw_tag, dict):
            continue
        tag_type = str(raw_tag.get("tag_type") or "nano_context").strip() or "nano_context"
        severity = str(raw_tag.get("severity") or "routine").strip()
        if severity not in {"routine", "elevated", "critical"}:
            severity = "routine"
        merged.append(
            {
                "tag_type": tag_type,
                "severity": severity,
                "confidence": _bounded_confidence(raw_tag.get("confidence"), default=0.5),
                "sleeve_relevance": _clean_sleeve_relevance(raw_tag.get("sleeve_relevance")),
                "reason": str(raw_tag.get("reason") or "Nano PBP context tag.")[:300],
                "evidence": raw_tag.get("evidence") if isinstance(raw_tag.get("evidence"), dict) else {},
                "llm_escalation": response.get("llm_escalation"),
                "valuation_signal": response.get("valuation_signal"),
                "source": "gpt-5.4-nano",
            }
        )
    return merged


def _tag_emits_escalation(tag: dict[str, Any], *, allow_llm_escalation_triggers: bool) -> bool:
    if not allow_llm_escalation_triggers:
        return False
    return str(tag.get("llm_escalation") or "none") in {
        "mini_review_if_strategy_revision_triggered",
        "frontier_review_if_deep_loss_exposure",
    } and str(tag.get("valuation_signal") or "") in {"undervaluation", "overvaluation"}


def _tag_trigger_type(tag: dict[str, Any]) -> str:
    valuation = str(tag.get("valuation_signal") or "").strip().lower()
    if valuation == "undervaluation":
        return "undervaluation"
    if valuation == "overvaluation":
        return "overvaluation"
    return "compression_or_tagging"


def _bounded_confidence(value: Any, *, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(numeric, 0.0), 1.0)


def _clean_sleeve_relevance(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["grid_scalp", "core_hold", "reduce_stop"]
    clean = [str(item).strip() for item in value if str(item or "").strip()]
    return clean[:6] or ["grid_scalp", "core_hold", "reduce_stop"]


def resolve_openai_nano_pbp_dispatcher() -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    """Return a real nano dispatcher when OpenAI runtime config is available."""

    _load_dotenv_if_available()
    if not os.getenv(DEFAULT_OPENAI_API_KEY_ENV):
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    try:
        client = OpenAI()
    except Exception:
        return None

    def _dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        return openai_nano_pbp_dispatcher(payload, client=client)

    return _dispatch


def openai_nano_pbp_dispatcher(payload: dict[str, Any], *, client: Any) -> dict[str, Any]:
    """Call the nano model for compact PBP tags without live-order authority."""

    responses = getattr(client, "responses", None)
    create = getattr(responses, "create", None)
    if not callable(create):
        raise TypeError("OpenAI client does not expose responses.create")
    response = create(
        model=INTENDED_MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "You are Janus' cheap play-by-play tagger. Return only compact JSON. "
                    "You cannot place, cancel, or recommend raw orders. You may tag context, "
                    "suggest mini escalation for strategy review, or frontier escalation only "
                    "for deep loss exposure."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "janus_pbp_nano_annotation",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "summary": {"type": "string"},
                        "llm_escalation": {
                            "type": "string",
                            "enum": [
                                "none",
                                "mini_review_if_strategy_revision_triggered",
                                "frontier_review_if_deep_loss_exposure",
                            ],
                        },
                        "valuation_signal": {
                            "type": ["string", "null"],
                            "enum": ["undervaluation", "overvaluation", None],
                        },
                        "tags": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                                "properties": {
                                    "tag_type": {"type": "string"},
                                    "severity": {"type": "string"},
                                    "confidence": {"type": "number"},
                                    "sleeve_relevance": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "reason": {"type": "string"},
                                    "evidence": {"type": "object"},
                                },
                                "required": ["tag_type", "severity", "confidence", "sleeve_relevance", "reason"],
                            },
                        },
                    },
                    "required": ["summary", "llm_escalation", "valuation_signal", "tags"],
                },
            }
        },
        max_output_tokens=800,
        store=False,
    )
    text = getattr(response, "output_text", None)
    if text is None:
        output = getattr(response, "output", None)
        text = json.dumps(output, default=str) if output is not None else "{}"
    return json.loads(text)


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    try:
        from app.runtime.local_paths import repo_root
    except Exception:
        return
    root = repo_root()
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / ".env.local", override=False)


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


__all__ = [
    "SCHEMA_VERSION",
    "build_pbp_annotation_evidence",
    "openai_nano_pbp_dispatcher",
    "resolve_openai_nano_pbp_dispatcher",
]
