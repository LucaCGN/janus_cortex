from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.api.db import to_jsonable
from app.modules.agentic.contracts import LiveSignal, StrategyPlan
from app.modules.agentic.signal_aggregation import LiveSignalAggregationDecision
from app.modules.agentic.repository import (
    get_agentic_database_status,
    resolve_catalog_event_strategy_plan_aliases,
    try_persist_strategy_plan,
)
from app.runtime.local_paths import resolve_shared_root


_DATE_TOKEN_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def session_date(value: str | None = None) -> str:
    return value or date.today().isoformat()


def strategy_plan_session_date_for_event(event_id: str, *, fallback: str | None = None) -> str:
    match = _DATE_TOKEN_RE.search(str(event_id or ""))
    if match:
        return match.group(1)
    return session_date(fallback)


def event_id_matches_session_date(event_id: str, day: str | None) -> bool:
    resolved_day = session_date(day)
    match = _DATE_TOKEN_RE.search(str(event_id or ""))
    return match is None or match.group(1) >= resolved_day


def shared_root() -> Path:
    return resolve_shared_root()


def artifacts_root() -> Path:
    return shared_root() / "artifacts"


def reports_root() -> Path:
    return shared_root() / "reports"


def handoffs_root() -> Path:
    return shared_root() / "handoffs"


def strategy_plan_root(day: str | None = None) -> Path:
    return artifacts_root() / "strategy-plans" / session_date(day)


def ops_artifact_root(day: str | None = None) -> Path:
    return artifacts_root() / "ops" / session_date(day)


def live_signal_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "live-signals" / session_date(day)


def live_signal_aggregation_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "live-signal-aggregation" / session_date(day)


def _json_default(value: Any) -> Any:
    return to_jsonable(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=_json_default))
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else {"items": payload}


def write_live_signals(
    signals: list[LiveSignal],
    *,
    day: str | None = None,
    root: Path | None = None,
    source: str = "janus",
) -> dict[str, Any]:
    resolved_day = session_date(day)
    written: list[dict[str, Any]] = []
    for signal in signals:
        payload = signal.model_dump(mode="json")
        event_dir = live_signal_root(resolved_day, root=root) / _safe_name(signal.event_id)
        path = event_dir / f"{signal.signal_id}.json"
        write_json(path, payload)
        append_jsonl(
            live_signal_root(resolved_day, root=root) / "live_signals.jsonl",
            {
                "recorded_at_utc": utc_now().isoformat(),
                "session_date": resolved_day,
                "source": source,
                "signal_id": signal.signal_id,
                "event_id": signal.event_id,
                "signal_type": signal.signal_type,
                "signal_source": signal.source,
                "execution_boundary": signal.execution_boundary,
                "path": str(path),
            },
        )
        written.append(
            {
                "signal_id": signal.signal_id,
                "event_id": signal.event_id,
                "signal_type": signal.signal_type,
                "signal_source": signal.source,
                "path": str(path),
            }
        )
    return {
        "status": "stored",
        "schema_version": "live_signal_artifact_batch_v1",
        "session_date": resolved_day,
        "signal_count": len(signals),
        "signals": written,
        "jsonl_path": str(live_signal_root(resolved_day, root=root) / "live_signals.jsonl"),
    }


def write_live_signal_aggregation_decision(
    decision: LiveSignalAggregationDecision,
    *,
    day: str | None = None,
    root: Path | None = None,
    source: str = "janus",
) -> dict[str, Any]:
    resolved_day = session_date(day)
    event_dir = live_signal_aggregation_root(resolved_day, root=root) / _safe_name(decision.event_id)
    path = event_dir / f"{decision.decision_id}.json"
    payload = decision.model_dump(mode="json")
    write_json(path, payload)
    append_jsonl(
        live_signal_aggregation_root(resolved_day, root=root) / "aggregation_decisions.jsonl",
        {
            "recorded_at_utc": utc_now().isoformat(),
            "session_date": resolved_day,
            "source": source,
            "decision_id": decision.decision_id,
            "event_id": decision.event_id,
            "decision_type": decision.decision_type,
            "selected_signal_count": len(decision.selected_signal_ids),
            "suppressed_signal_count": len(decision.suppressed_signal_ids),
            "blocker_count": len(decision.blocker_artifacts),
            "order_intent_candidate_count": len(decision.order_intent_candidates),
            "path": str(path),
        },
    )
    return {
        "status": "stored",
        "schema_version": "live_signal_aggregation_artifact_v1",
        "session_date": resolved_day,
        "event_id": decision.event_id,
        "decision_id": decision.decision_id,
        "decision_type": decision.decision_type,
        "path": str(path),
        "jsonl_path": str(live_signal_aggregation_root(resolved_day, root=root) / "aggregation_decisions.jsonl"),
    }


def append_pregame_research(
    *,
    day: str | None,
    research_markdown: str | None,
    research_path: str | None,
    source: str,
    event_ids: list[str],
    notes: str | None,
) -> dict[str, Any]:
    resolved_day = session_date(day)
    content = research_markdown or _read_optional_text(research_path)
    if not content:
        return {
            "status": "skipped",
            "reason": "no_research_markdown_or_readable_path",
            "path": str(reports_root() / "daily-live-validation" / f"pregame_research_{resolved_day}.md"),
        }

    path = reports_root() / "daily-live-validation" / f"pregame_research_{resolved_day}.md"
    now = utc_now().isoformat()
    section = "\n".join(
        [
            "",
            f"## Submission - {now}",
            "",
            f"- source: `{source}`",
            f"- event_ids: `{', '.join(event_ids) if event_ids else 'all'}`",
            f"- original_path: `{research_path or ''}`",
            f"- notes: {notes or ''}",
            "",
            content.strip(),
            "",
        ]
    )
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        write_text(path, existing.rstrip() + "\n" + section)
    else:
        write_text(path, f"# Pregame Research - {resolved_day}\n{section}")
    append_jsonl(
        reports_root() / "daily-live-validation" / "pregame_research_submissions.jsonl",
        {
            "timestamp_utc": now,
            "session_date": resolved_day,
            "source": source,
            "event_ids": event_ids,
            "path": str(path),
            "original_path": research_path,
            "char_count": len(content),
        },
    )
    return {"status": "stored", "path": str(path), "char_count": len(content)}


def write_strategy_plan(plan: StrategyPlan, *, day: str | None = None) -> dict[str, Any]:
    resolved_day = day or strategy_plan_session_date_for_event(plan.event_id)
    generated_at = plan.generated_at_utc.astimezone(timezone.utc)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    event_dir = strategy_plan_root(resolved_day) / _safe_name(plan.event_id)
    version_path = event_dir / f"plan_{timestamp}.json"
    current_path = event_dir / "current.json"
    payload = plan.model_dump(mode="json")
    write_json(version_path, payload)
    write_json(current_path, payload)
    append_jsonl(
        strategy_plan_root(resolved_day) / "strategy_plan_versions.jsonl",
        {
            "timestamp_utc": utc_now().isoformat(),
            "event_id": plan.event_id,
            "market_id": plan.market_id,
            "plan_owner": plan.plan_owner,
            "strategy_count": len(plan.active_strategies),
            "path": str(version_path),
        },
    )
    db_persistence = try_persist_strategy_plan(plan)
    return {
        "status": "stored",
        "event_id": plan.event_id,
        "market_id": plan.market_id,
        "strategy_count": len(plan.active_strategies),
        "version_path": str(version_path),
        "current_path": str(current_path),
        "db_persistence": db_persistence,
    }


def load_current_strategy_plan(event_id: str, *, day: str | None = None) -> dict[str, Any] | None:
    resolved_day = day or strategy_plan_session_date_for_event(event_id)
    current_path = strategy_plan_root(resolved_day) / _safe_name(event_id) / "current.json"
    return read_json(current_path)


def load_current_strategy_plan_for_event(
    event_id: str,
    *,
    day: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    lookup_event_ids = _unique_strings([event_id])
    for lookup_event_id in lookup_event_ids:
        current_plan = load_current_strategy_plan(lookup_event_id, day=day)
        if current_plan is not None:
            return current_plan, lookup_event_id, lookup_event_ids
    lookup_event_ids = _unique_strings([event_id, *resolve_catalog_event_strategy_plan_aliases(event_id)])
    for lookup_event_id in lookup_event_ids[1:]:
        current_plan = load_current_strategy_plan(lookup_event_id, day=day)
        if current_plan is not None:
            return current_plan, lookup_event_id, lookup_event_ids
    return None, None, lookup_event_ids


def record_ops_stage(stage: str, payload: dict[str, Any], *, day: str | None = None) -> dict[str, Any]:
    now = utc_now()
    root = ops_artifact_root(day)
    safe_stage = _safe_name(stage)
    stage_payload = {
        "stage": stage,
        "recorded_at_utc": now.isoformat(),
        **payload,
    }
    path = root / f"{safe_stage}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(path, stage_payload)
    append_jsonl(root / "ops_events.jsonl", {**stage_payload, "path": str(path)})
    return {"status": "recorded", "stage": stage, "path": str(path), "recorded_at_utc": now.isoformat()}


def latest_handoff_statuses() -> dict[str, dict[str, Any]]:
    root = handoffs_root()
    statuses: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return statuses
    for path in sorted(root.glob("*/status.md")):
        lane = path.parent.name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        statuses[lane] = {
            "path": str(path),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "preview": "\n".join(text.splitlines()[:12]),
        }
    return statuses


def build_ops_status() -> dict[str, Any]:
    today = session_date()
    plans: list[Path] = []
    if strategy_plan_root(today).exists():
        for path in sorted(strategy_plan_root(today).glob("*/current.json")):
            payload = read_json(path) or {}
            event_id = str(payload.get("event_id") or path.parent.name)
            if event_id_matches_session_date(event_id, today):
                plans.append(path)
    return {
        "status": "ok",
        "timestamp_utc": utc_now().isoformat(),
        "local_roots": {
            "shared_root": str(shared_root()),
            "artifacts_root": str(artifacts_root()),
            "reports_root": str(reports_root()),
            "handoffs_root": str(handoffs_root()),
        },
        "strategy_plans": {
            "current_plan_count_today": len(plans),
            "current_plan_paths": [str(path) for path in plans],
        },
        "database": get_agentic_database_status(),
        "handoffs": latest_handoff_statuses(),
    }


def build_event_agent_context(event_id: str, *, day: str | None = None) -> dict[str, Any]:
    current_plan, resolved_strategy_plan_event_id, strategy_plan_lookup_event_ids = load_current_strategy_plan_for_event(
        event_id,
        day=day,
    )
    report_dir = reports_root() / "daily-live-validation"
    pregame_path = report_dir / f"pregame_research_{session_date(day)}.md"
    live_plan_path = report_dir / f"live_test_plan_{session_date(day)}.md"
    db_stat_context_trace = build_db_stat_context_trace(
        event_id,
        day=day,
        current_plan=current_plan,
        resolved_strategy_plan_event_id=resolved_strategy_plan_event_id,
    )
    return {
        "event_id": event_id,
        "timestamp_utc": utc_now().isoformat(),
        "strategy_plan_lookup_event_ids": strategy_plan_lookup_event_ids,
        "resolved_strategy_plan_event_id": resolved_strategy_plan_event_id,
        "current_strategy_plan": current_plan,
        "db_stat_context_trace": db_stat_context_trace,
        "db_context_sources": db_stat_context_trace["db_context_sources"],
        "pregame_research": _read_text_preview(pregame_path),
        "live_test_plan": _read_text_preview(live_plan_path),
        "handoffs": latest_handoff_statuses(),
    }


def build_db_stat_context_trace(
    event_id: str,
    *,
    day: str | None = None,
    current_plan: dict[str, Any] | None = None,
    resolved_strategy_plan_event_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or utc_now()
    resolved_day = session_date(day)
    plan = current_plan if isinstance(current_plan, dict) else {}
    context_sources = _plan_db_context_sources(plan)
    database_sources = []
    if not context_sources:
        database_sources = _event_database_context_sources(
            event_id=event_id,
            day=resolved_day,
            generated_at=generated_at,
        )
    event_ids = _unique_strings([event_id, resolved_strategy_plan_event_id or "", str(plan.get("event_id") or "")])
    prior_source = _pregame_prior_db_context_source(
        event_ids=event_ids or [event_id],
        day=resolved_day,
        generated_at=generated_at,
    )
    sources = [*context_sources, *database_sources, prior_source]
    blockers: list[str] = []
    if not context_sources and not database_sources:
        blockers.append("strategy_plan_db_context_sources_missing")
    for source in sources:
        blockers.extend(str(item) for item in source.get("stale_blockers") or [] if str(item).strip())
    included_count = sum(1 for source in sources if source.get("included_in_llm_context") is True)
    return {
        "schema_version": "db_stat_context_trace_v1",
        "event_id": event_id,
        "session_date": resolved_day,
        "generated_at_utc": generated_at.isoformat(),
        "resolved_strategy_plan_event_id": resolved_strategy_plan_event_id,
        "db_context_sources": sources,
        "source_count": len(sources),
        "included_source_count": included_count,
        "stale_source_count": sum(1 for source in sources if source.get("stale") is True),
        "missing_source_count": sum(1 for source in sources if source.get("status") == "missing"),
        "blockers": _unique_strings(blockers),
        "llm_context_inclusion_status": "included" if included_count else "missing_or_stale",
        "liveness_blocking": False,
    }


def _event_database_context_sources(
    *,
    event_id: str,
    day: str,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    parsed = _parse_sports_event_slug(event_id)
    if not parsed:
        return []
    league, away_code, home_code, event_day = parsed
    if league == "wnba":
        return [
            _wnba_database_context_source(
                event_id=event_id,
                away_code=away_code,
                home_code=home_code,
                event_day=event_day or day,
                generated_at=generated_at,
            )
        ]
    if league == "nba":
        return [
            _nba_database_context_source(
                event_id=event_id,
                away_code=away_code,
                home_code=home_code,
                event_day=event_day or day,
                generated_at=generated_at,
            )
        ]
    return []


def _wnba_database_context_source(
    *,
    event_id: str,
    away_code: str,
    home_code: str,
    event_day: str,
    generated_at: datetime,
) -> dict[str, Any]:
    try:
        from app.data.databases.postgres import managed_connection
    except Exception as exc:  # noqa: BLE001
        return _database_context_unavailable_source(event_id, "wnba", "postgres_import_failed", exc)

    try:
        with managed_connection() as connection:
            game = _db_fetch_one(
                connection,
                """
                SELECT game_id, game_date, game_start_time, game_status, game_status_text,
                       home_team_tricode, away_team_tricode, home_score, away_score,
                       updated_at, source
                FROM wnba.wnba_games
                WHERE game_date = %s
                  AND upper(home_team_tricode) = %s
                  AND upper(away_team_tricode) = %s
                ORDER BY updated_at DESC NULLS LAST, game_start_time DESC NULLS LAST
                LIMIT 1;
                """,
                (event_day, home_code, away_code),
            )
            teams = _db_fetch_all(
                connection,
                """
                SELECT team_id, team_tricode, team_city, team_name, updated_at, source
                FROM wnba.wnba_teams
                WHERE upper(team_tricode) IN (%s, %s)
                ORDER BY team_tricode;
                """,
                (away_code, home_code),
            )
            game_id = str(game.get("game_id") or "") if game else ""
            team_boxscore = {"row_count": 0, "team_count": 0, "latest_captured_at": None}
            player_boxscore = {"row_count": 0, "latest_captured_at": None}
            roster_summary = {"row_count": 0, "latest_updated_at": None}
            historical_team_boxscore = {
                "row_count": 0,
                "team_count": 0,
                "game_count": 0,
                "latest_captured_at": None,
            }
            historical_player_boxscore = {
                "row_count": 0,
                "player_count": 0,
                "game_count": 0,
                "latest_captured_at": None,
            }
            players: list[str] = []
            team_codes = [away_code, home_code]
            if game_id:
                team_boxscore = _db_fetch_one(
                    connection,
                    """
                    SELECT count(*)::int AS row_count,
                           count(DISTINCT team_tricode)::int AS team_count,
                           max(captured_at) AS latest_captured_at
                    FROM wnba.wnba_team_boxscore_snapshots
                    WHERE game_id = %s;
                    """,
                    (game_id,),
                ) or team_boxscore
                player_boxscore = _db_fetch_one(
                    connection,
                    """
                    SELECT count(*)::int AS row_count,
                           max(captured_at) AS latest_captured_at
                    FROM wnba.wnba_player_boxscore_snapshots
                    WHERE game_id = %s;
                    """,
                    (game_id,),
                ) or player_boxscore
                player_rows = _db_fetch_all(
                    connection,
                    """
                    SELECT DISTINCT player_name
                    FROM wnba.wnba_player_boxscore_snapshots
                    WHERE game_id = %s
                      AND player_name IS NOT NULL
                    ORDER BY player_name
                    LIMIT 24;
                    """,
                    (game_id,),
                )
                players = _unique_strings([str(row.get("player_name") or "") for row in player_rows])

            roster_summary = _db_fetch_one(
                connection,
                """
                SELECT count(*)::int AS row_count,
                       max(updated_at) AS latest_updated_at
                FROM wnba.wnba_players
                WHERE upper(team_tricode) = ANY(%s);
                """,
                (team_codes,),
            ) or roster_summary
            roster_rows = _db_fetch_all(
                connection,
                """
                SELECT DISTINCT player_name
                FROM wnba.wnba_players
                WHERE upper(team_tricode) = ANY(%s)
                  AND player_name IS NOT NULL
                ORDER BY player_name
                LIMIT 30;
                """,
                (team_codes,),
            )
            historical_team_boxscore = _db_fetch_one(
                connection,
                """
                SELECT count(*)::int AS row_count,
                       count(DISTINCT snapshot.team_tricode)::int AS team_count,
                       count(DISTINCT snapshot.game_id)::int AS game_count,
                       max(snapshot.captured_at) AS latest_captured_at
                FROM wnba.wnba_team_boxscore_snapshots snapshot
                WHERE upper(snapshot.team_tricode) = ANY(%s)
                  AND (%s = '' OR snapshot.game_id <> %s);
                """,
                (team_codes, game_id, game_id),
            ) or historical_team_boxscore
            historical_player_boxscore = _db_fetch_one(
                connection,
                """
                SELECT count(*)::int AS row_count,
                       count(DISTINCT snapshot.player_id)::int AS player_count,
                       count(DISTINCT snapshot.game_id)::int AS game_count,
                       max(snapshot.captured_at) AS latest_captured_at
                FROM wnba.wnba_player_boxscore_snapshots snapshot
                WHERE upper(snapshot.team_tricode) = ANY(%s)
                  AND (%s = '' OR snapshot.game_id <> %s);
                """,
                (team_codes, game_id, game_id),
            ) or historical_player_boxscore
            historical_player_rows = _db_fetch_all(
                connection,
                """
                SELECT snapshot.player_name
                FROM wnba.wnba_player_boxscore_snapshots snapshot
                WHERE upper(snapshot.team_tricode) = ANY(%s)
                  AND (%s = '' OR snapshot.game_id <> %s)
                  AND snapshot.player_name IS NOT NULL
                GROUP BY snapshot.player_name
                ORDER BY count(*) DESC, snapshot.player_name
                LIMIT 30;
                """,
                (team_codes, game_id, game_id),
            )
            if not players:
                players = _unique_strings([str(row.get("player_name") or "") for row in roster_rows])
            if not players:
                players = _unique_strings([str(row.get("player_name") or "") for row in historical_player_rows])
    except Exception as exc:  # noqa: BLE001
        return _database_context_unavailable_source(event_id, "wnba", "postgres_query_failed", exc)

    teams_display = _unique_strings(
        [
            " ".join(str(part or "").strip() for part in (row.get("team_city"), row.get("team_name"))).strip()
            or str(row.get("team_tricode") or "")
            for row in teams
        ]
    )
    source_timestamp = _max_datetime(
        [
            game.get("updated_at") if game else None,
            *[row.get("updated_at") for row in teams],
            team_boxscore.get("latest_captured_at"),
            player_boxscore.get("latest_captured_at"),
            roster_summary.get("latest_updated_at"),
            historical_team_boxscore.get("latest_captured_at"),
            historical_player_boxscore.get("latest_captured_at"),
        ]
    )
    team_count = len(teams)
    team_boxscore_count = int(team_boxscore.get("row_count") or 0)
    player_boxscore_count = int(player_boxscore.get("row_count") or 0)
    roster_count = int(roster_summary.get("row_count") or 0)
    historical_team_boxscore_count = int(historical_team_boxscore.get("row_count") or 0)
    historical_team_game_count = int(historical_team_boxscore.get("game_count") or 0)
    historical_player_boxscore_count = int(historical_player_boxscore.get("row_count") or 0)
    historical_player_game_count = int(historical_player_boxscore.get("game_count") or 0)
    is_pregame = bool(game and str(game.get("game_status") or "") == "1")
    effective_team_stat_count = team_boxscore_count if team_boxscore_count >= 2 else historical_team_boxscore_count
    effective_player_stat_count = player_boxscore_count if player_boxscore_count > 0 else historical_player_boxscore_count
    stale_blockers: list[str] = []
    if not game:
        stale_blockers.append("wnba_game_snapshot_missing")
    if team_count < 2:
        stale_blockers.append("wnba_team_metadata_snapshot_incomplete")
    if is_pregame:
        if roster_count <= 0:
            stale_blockers.append("wnba_roster_snapshot_missing")
        if historical_team_boxscore_count <= 0:
            stale_blockers.append("wnba_historical_team_stats_snapshot_missing")
        if historical_player_boxscore_count <= 0:
            stale_blockers.append("wnba_historical_player_stats_snapshot_missing")
    elif team_boxscore_count < 2:
        stale_blockers.append("wnba_team_boxscore_snapshot_missing")
    if not is_pregame and player_boxscore_count <= 0:
        stale_blockers.append("wnba_player_boxscore_snapshot_missing")
    if source_timestamp is not None and generated_at - source_timestamp > timedelta(hours=24):
        stale_blockers.append("wnba_database_snapshot_older_than_24h")

    included_in_llm_context = bool(
        game
        and team_count >= 2
        and (
            not stale_blockers
            or (is_pregame and effective_team_stat_count > 0 and effective_player_stat_count > 0 and roster_count > 0)
        )
    )
    status = "current" if not stale_blockers else "partial" if included_in_llm_context else "missing"
    source_caveats = [
        f"game_id={game.get('game_id')}" if game else "game_id_unresolved",
        f"game_status={game.get('game_status')}" if game else "game_status_unavailable",
        f"team_metadata_rows={team_count}",
        f"current_game_team_boxscore_rows={team_boxscore_count}",
        f"current_game_player_boxscore_rows={player_boxscore_count}",
        f"roster_rows={roster_count}",
        f"historical_team_boxscore_rows={historical_team_boxscore_count}",
        f"historical_team_boxscore_games={historical_team_game_count}",
        f"historical_player_boxscore_rows={historical_player_boxscore_count}",
        f"historical_player_boxscore_games={historical_player_game_count}",
    ]
    if is_pregame and (team_boxscore_count < 2 or player_boxscore_count <= 0):
        source_caveats.append("current_game_boxscore_not_available_before_tip")
    if is_pregame and effective_player_stat_count > 0:
        source_caveats.append("using_roster_and_historical_boxscore_stats_for_pregame_context")

    return {
        "schema_version": "db_context_source_v1",
        "source_id": "wnba_postgres_game_team_player_snapshot",
        "source_kind": "postgres_sports_database_probe",
        "source_path": "postgres:wnba.wnba_games,wnba.wnba_teams,wnba.wnba_players,wnba.wnba_team_boxscore_snapshots,wnba.wnba_player_boxscore_snapshots",
        "status": status,
        "freshness_status": status,
        "source_timestamp_utc": source_timestamp.isoformat() if source_timestamp is not None else None,
        "expires_at_utc": None,
        "stale": bool(stale_blockers),
        "stale_blockers": _unique_strings(stale_blockers),
        "included_in_llm_context": included_in_llm_context,
        "team_stat_snapshot_count": effective_team_stat_count,
        "player_stat_snapshot_count": effective_player_stat_count,
        "teams": teams_display,
        "players": players,
        "source_caveats": source_caveats,
    }


def _nba_database_context_source(
    *,
    event_id: str,
    away_code: str,
    home_code: str,
    event_day: str,
    generated_at: datetime,
) -> dict[str, Any]:
    try:
        from app.data.databases.postgres import managed_connection
    except Exception as exc:  # noqa: BLE001
        return _database_context_unavailable_source(event_id, "nba", "postgres_import_failed", exc)

    away_slug = _nba_team_slug_fragment(away_code)
    home_slug = _nba_team_slug_fragment(home_code)
    if away_slug is None or home_slug is None:
        return {
            "schema_version": "db_context_source_v1",
            "source_id": "nba_postgres_team_player_stats_snapshot",
            "source_kind": "postgres_sports_database_probe",
            "source_path": None,
            "status": "missing",
            "freshness_status": "missing",
            "source_timestamp_utc": None,
            "expires_at_utc": None,
            "stale": True,
            "stale_blockers": ["nba_event_slug_team_alias_unresolved"],
            "included_in_llm_context": False,
            "team_stat_snapshot_count": None,
            "player_stat_snapshot_count": None,
            "teams": [],
            "players": [],
            "source_caveats": [f"event_slug_codes={away_code}-{home_code}"],
        }

    try:
        with managed_connection() as connection:
            game = _db_fetch_one(
                connection,
                """
                SELECT game_id, game_date, game_start_time, game_status, game_status_text,
                       home_team_id, away_team_id, home_team_slug, away_team_slug,
                       home_score, away_score, updated_at
                FROM nba.nba_games
                WHERE game_date = %s
                  AND lower(home_team_slug) LIKE %s
                  AND lower(away_team_slug) LIKE %s
                ORDER BY updated_at DESC NULLS LAST, game_start_time DESC NULLS LAST
                LIMIT 1;
                """,
                (event_day, f"%{home_slug}%", f"%{away_slug}%"),
            )
            team_ids = [game.get("away_team_id"), game.get("home_team_id")] if game else []
            team_stats = {"row_count": 0, "latest_captured_at": None}
            player_stats = {"row_count": 0, "latest_captured_at": None}
            teams: list[str] = []
            players: list[str] = []
            if team_ids:
                teams = _unique_strings(
                    [
                        str(row.get("team_name") or row.get("team_slug") or "")
                        for row in _db_fetch_all(
                            connection,
                            """
                            SELECT team_id, team_slug, team_name, updated_at
                            FROM nba.nba_teams
                            WHERE team_id = ANY(%s)
                            ORDER BY team_name;
                            """,
                            (team_ids,),
                        )
                    ]
                )
                team_stats = _db_fetch_one(
                    connection,
                    """
                    SELECT count(*)::int AS row_count,
                           max(captured_at) AS latest_captured_at
                    FROM nba.nba_team_stats_snapshots
                    WHERE team_id = ANY(%s)
                      AND season = '2026';
                    """,
                    (team_ids,),
                ) or team_stats
                player_stats = _db_fetch_one(
                    connection,
                    """
                    SELECT count(*)::int AS row_count,
                           max(captured_at) AS latest_captured_at
                    FROM nba.nba_player_stats_snapshots
                    WHERE team_id = ANY(%s)
                      AND season = '2026';
                    """,
                    (team_ids,),
                ) or player_stats
                players = _unique_strings(
                    [
                        str(row.get("player_name") or "")
                        for row in _db_fetch_all(
                            connection,
                            """
                            SELECT DISTINCT player_name
                            FROM nba.nba_player_stats_snapshots
                            WHERE team_id = ANY(%s)
                              AND season = '2026'
                              AND player_name IS NOT NULL
                            ORDER BY player_name
                            LIMIT 24;
                            """,
                            (team_ids,),
                        )
                    ]
                )
    except Exception as exc:  # noqa: BLE001
        return _database_context_unavailable_source(event_id, "nba", "postgres_query_failed", exc)

    source_timestamp = _max_datetime(
        [
            game.get("updated_at") if game else None,
            team_stats.get("latest_captured_at"),
            player_stats.get("latest_captured_at"),
        ]
    )
    team_stat_count = int(team_stats.get("row_count") or 0)
    player_stat_count = int(player_stats.get("row_count") or 0)
    stale_blockers: list[str] = []
    if not game:
        stale_blockers.append("nba_game_snapshot_missing")
    if team_stat_count <= 0:
        stale_blockers.append("nba_team_stats_snapshot_missing")
    if player_stat_count <= 0:
        stale_blockers.append("nba_player_stats_snapshot_missing")
    if source_timestamp is not None and generated_at - source_timestamp > timedelta(hours=24):
        stale_blockers.append("nba_database_snapshot_older_than_24h")

    status = "current" if not stale_blockers else "partial" if game else "missing"
    return {
        "schema_version": "db_context_source_v1",
        "source_id": "nba_postgres_team_player_stats_snapshot",
        "source_kind": "postgres_sports_database_probe",
        "source_path": "postgres:nba.nba_games,nba.nba_team_stats_snapshots,nba.nba_player_stats_snapshots",
        "status": status,
        "freshness_status": status,
        "source_timestamp_utc": source_timestamp.isoformat() if source_timestamp is not None else None,
        "expires_at_utc": None,
        "stale": bool(stale_blockers),
        "stale_blockers": _unique_strings(stale_blockers),
        "included_in_llm_context": bool(game and team_stat_count > 0 and player_stat_count > 0),
        "team_stat_snapshot_count": team_stat_count,
        "player_stat_snapshot_count": player_stat_count,
        "teams": teams,
        "players": players,
        "source_caveats": [
            f"event_slug_codes={away_code}-{home_code}",
            f"game_id={game.get('game_id')}" if game else "game_id_unresolved",
        ],
    }


def _database_context_unavailable_source(
    event_id: str,
    league: str,
    reason: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "schema_version": "db_context_source_v1",
        "source_id": f"{league}_postgres_team_player_stats_snapshot",
        "source_kind": "postgres_sports_database_probe",
        "source_path": None,
        "status": "unavailable",
        "freshness_status": "unavailable",
        "source_timestamp_utc": None,
        "expires_at_utc": None,
        "stale": True,
        "stale_blockers": [reason],
        "included_in_llm_context": False,
        "team_stat_snapshot_count": None,
        "player_stat_snapshot_count": None,
        "teams": [],
        "players": [],
        "source_caveats": [f"{event_id}: {type(exc).__name__}: {exc}"],
    }


def _db_fetch_one(connection: Any, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    rows = _db_fetch_all(connection, query, params)
    return rows[0] if rows else None


def _db_fetch_all(connection: Any, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [item[0] for item in cursor.description or []]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _parse_sports_event_slug(event_id: str) -> tuple[str, str, str, str | None] | None:
    parts = str(event_id or "").lower().split("-")
    if len(parts) < 6 or parts[0] not in {"nba", "wnba"}:
        return None
    year, month, day = parts[-3:]
    if len(year) != 4 or len(month) != 2 or len(day) != 2:
        return None
    away_code = parts[1].upper()
    home_code = parts[2].upper()
    return parts[0], away_code, home_code, f"{year}-{month}-{day}"


def _nba_team_slug_fragment(code: str) -> str | None:
    aliases = {
        "ATL": "atlanta",
        "BKN": "brooklyn",
        "BOS": "boston",
        "CHA": "charlotte",
        "CHI": "chicago",
        "CLE": "cleveland",
        "DAL": "dallas",
        "DEN": "denver",
        "DET": "detroit",
        "GSW": "golden-state",
        "HOU": "houston",
        "IND": "indiana",
        "LAC": "la-clippers",
        "LAL": "la-lakers",
        "MEM": "memphis",
        "MIA": "miami",
        "MIL": "milwaukee",
        "MIN": "minnesota",
        "NOP": "new-orleans",
        "NYK": "new-york",
        "OKC": "oklahoma-city",
        "ORL": "orlando",
        "PHI": "philadelphia",
        "PHX": "phoenix",
        "POR": "portland",
        "SAC": "sacramento",
        "SAS": "san-antonio",
        "TOR": "toronto",
        "UTA": "utah",
        "WAS": "washington",
    }
    return aliases.get(str(code or "").upper())


def _max_datetime(values: list[Any]) -> datetime | None:
    parsed = [_parse_datetime(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    if not parsed:
        return None
    return max(parsed)


def _plan_db_context_sources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    candidates: list[Any] = []
    for section_name in ("context_summary", "explainability"):
        section = plan.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in ("db_context_sources", "db_stat_context_sources", "team_player_stat_context_sources"):
            value = section.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    for key in ("db_context_sources", "db_stat_context_sources", "team_player_stat_context_sources"):
        value = plan.get(key)
        if isinstance(value, list):
            candidates.extend(value)

    sources: list[dict[str, Any]] = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or item.get("freshness_status") or "unknown").strip().lower()
        stale = bool(item.get("stale") is True or status in {"stale", "expired", "missing", "invalid"})
        included = item.get("included_in_llm_context")
        included_bool = bool(included) if included is not None else not stale
        stale_blockers = _string_list(
            item.get("stale_blockers")
            or item.get("blockers")
            or item.get("reason_codes")
            or ([] if not stale else [f"plan_db_context_source_{status or 'stale'}"])
        )
        sources.append(
            {
                "schema_version": "db_context_source_v1",
                "source_id": str(item.get("source_id") or item.get("id") or f"strategy_plan_db_context_{index}"),
                "source_kind": str(item.get("source_kind") or item.get("kind") or "strategy_plan_db_context"),
                "source_path": item.get("source_path") or item.get("path"),
                "status": status or "unknown",
                "freshness_status": status or "unknown",
                "source_timestamp_utc": item.get("source_timestamp_utc")
                or item.get("generated_at_utc")
                or item.get("updated_at_utc"),
                "expires_at_utc": item.get("expires_at_utc"),
                "stale": stale,
                "stale_blockers": stale_blockers,
                "included_in_llm_context": included_bool,
                "team_stat_snapshot_count": item.get("team_stat_snapshot_count"),
                "player_stat_snapshot_count": item.get("player_stat_snapshot_count"),
                "teams": _string_list(item.get("teams")),
                "players": _string_list(item.get("players")),
                "source_caveats": _string_list(item.get("source_caveats")),
            }
        )
    return sources


def _pregame_prior_db_context_source(
    *,
    event_ids: list[str],
    day: str,
    generated_at: datetime,
) -> dict[str, Any]:
    prior_path: Path | None = None
    for candidate_event_id in event_ids:
        current = artifacts_root() / "pregame-priors" / day / _safe_name(candidate_event_id) / "current.json"
        if current.exists():
            prior_path = current
            break
    if prior_path is None:
        return {
            "schema_version": "db_context_source_v1",
            "source_id": "optional_pregame_prior",
            "source_kind": "pregame_research_prior",
            "source_path": None,
            "status": "missing",
            "freshness_status": "missing",
            "source_timestamp_utc": None,
            "expires_at_utc": None,
            "stale": True,
            "stale_blockers": ["pregame_prior_missing"],
            "included_in_llm_context": False,
            "team_stat_snapshot_count": None,
            "player_stat_snapshot_count": None,
            "teams": [],
            "players": [],
            "source_caveats": ["pregame_prior_not_found"],
        }
    payload = read_json(prior_path)
    if not isinstance(payload, dict):
        return {
            "schema_version": "db_context_source_v1",
            "source_id": "optional_pregame_prior",
            "source_kind": "pregame_research_prior",
            "source_path": str(prior_path),
            "status": "invalid",
            "freshness_status": "invalid",
            "source_timestamp_utc": None,
            "expires_at_utc": None,
            "stale": True,
            "stale_blockers": ["pregame_prior_invalid_json"],
            "included_in_llm_context": False,
            "team_stat_snapshot_count": None,
            "player_stat_snapshot_count": None,
            "teams": [],
            "players": [],
            "source_caveats": ["prior_json_unreadable"],
        }

    source_timestamp = _parse_datetime(payload.get("generated_at_utc") or payload.get("created_at_utc"))
    expires_at = _parse_datetime(payload.get("expires_at_utc"))
    blockers: list[str] = []
    status = "current"
    if source_timestamp is None:
        status = "invalid"
        blockers.append("pregame_prior_missing_generated_at")
    if expires_at is not None and expires_at <= generated_at:
        status = "stale"
        blockers.append("pregame_prior_expired")
    source_caveats = _string_list(payload.get("source_caveats"))
    included = status == "current"
    return {
        "schema_version": "db_context_source_v1",
        "source_id": "optional_pregame_prior",
        "source_kind": "pregame_research_prior",
        "source_path": str(prior_path),
        "status": status,
        "freshness_status": status,
        "source_timestamp_utc": source_timestamp.isoformat() if source_timestamp is not None else None,
        "expires_at_utc": expires_at.isoformat() if expires_at is not None else None,
        "stale": status != "current",
        "stale_blockers": blockers,
        "included_in_llm_context": included,
        "team_stat_snapshot_count": payload.get("team_stat_snapshot_count"),
        "player_stat_snapshot_count": payload.get("player_stat_snapshot_count"),
        "teams": _string_list(payload.get("teams")),
        "players": _string_list(payload.get("players")),
        "source_caveats": source_caveats,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _read_text_preview(path: Path, *, max_chars: int = 12000) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "text": ""}
    text = path.read_text(encoding="utf-8")
    return {"path": str(path), "exists": True, "text": text[:max_chars]}


def _read_optional_text(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))[:160] or "unknown"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


__all__ = [
    "append_jsonl",
    "append_pregame_research",
    "build_db_stat_context_trace",
    "build_event_agent_context",
    "build_ops_status",
    "event_id_matches_session_date",
    "load_current_strategy_plan",
    "load_current_strategy_plan_for_event",
    "live_signal_root",
    "live_signal_aggregation_root",
    "record_ops_stage",
    "write_live_signal_aggregation_decision",
    "write_live_signals",
    "write_json",
    "write_strategy_plan",
    "write_text",
]
