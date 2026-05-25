from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.store import append_jsonl, artifacts_root, reports_root, session_date, write_json, write_text


League = Literal["nba", "wnba"]


class SnapshotFeedState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_timestamp_utc: datetime | None = None
    observed_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float | None = Field(default=None, ge=0.0)
    stale: bool = False
    blocker_code: str | None = None


class SnapshotTeamState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["home", "away"]
    team_id: str | None = None
    name: str | None = None
    slug: str | None = None
    score: int | None = None


class SnapshotGameState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    period: int | None = None
    clock: str | None = None
    start_time_utc: datetime | None = None
    home: SnapshotTeamState
    away: SnapshotTeamState


class SnapshotClobOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    token_id: str | None = None
    label: str | None = None
    best_bid: float | None = Field(default=None, ge=0.0, le=1.0)
    best_ask: float | None = Field(default=None, ge=0.0, le=1.0)
    mid_price: float | None = Field(default=None, ge=0.0, le=1.0)
    spread: float | None = Field(default=None, ge=0.0)
    top_bid_depth: float | None = Field(default=None, ge=0.0)
    top_ask_depth: float | None = Field(default=None, ge=0.0)
    minimum_size: float | None = Field(default=None, ge=0.0)
    tick_size: float | None = Field(default=None, ge=0.0)
    source_timestamp_utc: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0.0)
    stale: bool = False
    blocker_code: str | None = None


class SnapshotAccountState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_open_order_count: int = Field(default=0, ge=0)
    event_open_position_count: int = Field(default=0, ge=0)
    pending_intent_count: int = Field(default=0, ge=0)
    submitted_order_count: int = Field(default=0, ge=0)
    current_token_trade_count: int = Field(default=0, ge=0)
    unresolved_inventory: bool = False


class SnapshotRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_plan_ready: bool = False
    current_plan_count: int = Field(default=0, ge=0)
    worker_status: str = "unknown"
    worker_running: bool = False
    execute: bool = False
    live_money: bool = False
    llm_dispatch_enabled: bool = False


class SnapshotEvidenceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_paths: list[str] = Field(default_factory=list)
    blocker_codes: list[str] = Field(default_factory=list)
    source_payload_keys: list[str] = Field(default_factory=list)


class NormalizedLiveSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "normalized_live_snapshot_v1"
    event_id: str
    league: League
    market_id: str | None = None
    market_slug: str | None = None
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    game: SnapshotGameState
    feeds: dict[str, SnapshotFeedState]
    clob: list[SnapshotClobOutcome] = Field(default_factory=list)
    account: SnapshotAccountState = Field(default_factory=SnapshotAccountState)
    runtime: SnapshotRuntimeState = Field(default_factory=SnapshotRuntimeState)
    evidence: SnapshotEvidenceState = Field(default_factory=SnapshotEvidenceState)
    execution_boundary: Literal["evidence_only"] = "evidence_only"


class NormalizedLiveSnapshotReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "normalized_live_snapshot_review_v1"
    session_date: str
    issue: str = "#64"
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trading_boundary: str = "read_only_no_orders_no_worker_starts"
    snapshots: list[NormalizedLiveSnapshot] = Field(default_factory=list)
    parity_fields: list[str] = Field(default_factory=list)
    parity_gaps: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    hard_prohibitions: list[str] = Field(
        default_factory=lambda: [
            "do_not_place_cancel_replace_submit_sign_broadcast_redeem_orders",
            "do_not_start_live_money_workers",
            "do_not_treat_snapshot_artifacts_as_live_order_authority",
        ]
    )


def normalized_live_snapshot_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "normalized-live-snapshots" / session_date(day)


def build_normalized_live_snapshot(
    *,
    event_id: str,
    league: League,
    game: dict[str, Any],
    orderbooks: dict[str, Any] | None = None,
    direct_clob: dict[str, Any] | None = None,
    strategy_plan_gate: dict[str, Any] | None = None,
    worker: dict[str, Any] | None = None,
    evidence_paths: list[str] | None = None,
    generated_at_utc: datetime | None = None,
    max_feed_age_seconds: float = 90.0,
    max_orderbook_age_seconds: float = 30.0,
) -> NormalizedLiveSnapshot:
    generated_at = _ensure_utc(generated_at_utc) or datetime.now(timezone.utc)
    feeds = _feed_states(game, generated_at=generated_at, max_feed_age_seconds=max_feed_age_seconds)
    clob = [
        _clob_outcome(outcome_id, payload, generated_at=generated_at, max_age_seconds=max_orderbook_age_seconds)
        for outcome_id, payload in (orderbooks or {}).items()
    ]
    account = _account_state(direct_clob or {})
    runtime = _runtime_state(strategy_plan_gate or {}, worker or {})
    blocker_codes = _blocker_codes(
        league=league,
        feeds=feeds,
        clob=clob,
        direct_clob=direct_clob or {},
        runtime=runtime,
    )
    plan = _first_current_plan(strategy_plan_gate or {})
    return NormalizedLiveSnapshot(
        event_id=event_id,
        league=league,
        market_id=_text(plan.get("market_id")) if plan else None,
        market_slug=_text(game.get("market_slug") or game.get("slug")),
        generated_at_utc=generated_at,
        game=SnapshotGameState(
            status=_status_text(game),
            period=_int_value(game.get("period")),
            clock=_text(game.get("game_clock") or game.get("clock")),
            start_time_utc=_parse_utc(game.get("game_start_time") or game.get("start_time_utc")),
            home=SnapshotTeamState(
                side="home",
                team_id=_text(game.get("home_team_id")),
                name=_text(game.get("home_team_name")),
                slug=_text(game.get("home_team_slug")),
                score=_int_value(game.get("home_score")),
            ),
            away=SnapshotTeamState(
                side="away",
                team_id=_text(game.get("away_team_id")),
                name=_text(game.get("away_team_name")),
                slug=_text(game.get("away_team_slug")),
                score=_int_value(game.get("away_score")),
            ),
        ),
        feeds=feeds,
        clob=clob,
        account=account,
        runtime=runtime,
        evidence=SnapshotEvidenceState(
            evidence_paths=_unique_strings(evidence_paths or []),
            blocker_codes=blocker_codes,
            source_payload_keys=sorted(str(key) for key in game.keys()),
        ),
    )


def build_normalized_live_snapshot_review(
    *,
    day: str | None = None,
    snapshots: list[NormalizedLiveSnapshot] | None = None,
) -> NormalizedLiveSnapshotReview:
    resolved_day = session_date(day)
    resolved_snapshots = snapshots if snapshots is not None else sample_snapshot_pair()
    parity_gaps = _parity_gaps(resolved_snapshots)
    return NormalizedLiveSnapshotReview(
        session_date=resolved_day,
        snapshots=resolved_snapshots,
        parity_fields=[
            "event_id",
            "league",
            "game.status",
            "game.period",
            "game.clock",
            "feeds.scoreboard",
            "feeds.play_by_play",
            "feeds.stats",
            "clob[].bid_ask_spread_depth",
            "account.event_scoped_inventory",
            "runtime.strategy_plan_worker_flags",
            "evidence.blocker_codes",
        ],
        parity_gaps=parity_gaps,
        next_actions=_next_actions(parity_gaps),
    )


def write_normalized_live_snapshot_review(
    review: NormalizedLiveSnapshotReview,
    *,
    artifact_root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    timestamp = review.generated_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = normalized_live_snapshot_root(review.session_date, root=artifact_root)
    json_path = root / f"normalized_live_snapshot_review_{timestamp}.json"
    write_json(json_path, review.model_dump(mode="json"))
    append_jsonl(
        root / "normalized_live_snapshot_reviews.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_date": review.session_date,
            "snapshot_count": len(review.snapshots),
            "parity_gap_count": len(review.parity_gaps),
            "path": str(json_path),
        },
    )
    markdown_path = (report_dir or reports_root() / "daily-live-validation") / (
        f"normalized_live_snapshot_review_{timestamp}.md"
    )
    write_text(markdown_path, render_normalized_live_snapshot_review_markdown(review, json_path=str(json_path)))
    return {
        "status": "stored",
        "schema_version": "normalized_live_snapshot_review_write_result_v1",
        "session_date": review.session_date,
        "snapshot_count": len(review.snapshots),
        "parity_gap_count": len(review.parity_gaps),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def render_normalized_live_snapshot_review_markdown(
    review: NormalizedLiveSnapshotReview,
    *,
    json_path: str | None = None,
) -> str:
    lines = [
        f"# Normalized Live Snapshot Review - {review.session_date}",
        "",
        f"- generated_at_utc: `{review.generated_at_utc.isoformat()}`",
        f"- issue: `{review.issue}`",
        f"- trading_boundary: `{review.trading_boundary}`",
        f"- snapshot_count: `{len(review.snapshots)}`",
    ]
    if json_path:
        lines.append(f"- json_artifact: `{json_path}`")
    lines.extend(["", "## Snapshots"])
    for snapshot in review.snapshots:
        lines.append(
            f"- `{snapshot.event_id}` `{snapshot.league}`: status=`{snapshot.game.status}`, "
            f"period=`{snapshot.game.period}`, clob_outcomes=`{len(snapshot.clob)}`, "
            f"blockers=`{','.join(snapshot.evidence.blocker_codes) or 'none'}`"
        )
    lines.extend(["", "## Parity Fields"])
    lines.extend(_bullet_lines(f"`{item}`" for item in review.parity_fields))
    lines.extend(["", "## Parity Gaps"])
    if review.parity_gaps:
        for gap in review.parity_gaps:
            lines.append(
                f"- `{gap['event_id']}` `{gap['league']}` `{gap['blocker_code']}`: {gap['scope']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Next Actions"])
    lines.extend(_bullet_lines(review.next_actions))
    lines.extend(["", "## Hard Prohibitions"])
    lines.extend(_bullet_lines(f"`{item}`" for item in review.hard_prohibitions))
    return "\n".join(lines).rstrip() + "\n"


def sample_snapshot_pair() -> list[NormalizedLiveSnapshot]:
    generated_at = datetime(2026, 5, 25, 6, 20, tzinfo=timezone.utc)
    nba = build_normalized_live_snapshot(
        event_id="nba-okc-sas-2026-05-24",
        league="nba",
        generated_at_utc=generated_at,
        game={
            "game_status_text": "Final",
            "period": 4,
            "game_clock": "PT00M00.00S",
            "game_start_time": "2026-05-25T00:00:00Z",
            "updated_at": "2026-05-25T02:52:28Z",
            "resolution_source": "nba_cdn_scoreboard_event_slug",
            "home_team_id": "1610612759",
            "home_team_name": "Spurs",
            "home_team_slug": "SAS",
            "home_score": 103,
            "away_team_id": "1610612760",
            "away_team_name": "Thunder",
            "away_team_slug": "OKC",
            "away_score": 82,
            "play_by_play_timestamp_utc": "2026-05-25T02:52:28Z",
            "stats_timestamp_utc": "2026-05-25T02:52:28Z",
        },
        orderbooks={
            "spurs": _sample_orderbook("spurs-token", 0.995, 1.0, "2026-05-25T02:52:59Z"),
            "thunder": _sample_orderbook("thunder-token", 0.0, 0.01, "2026-05-25T02:52:59Z"),
        },
        direct_clob={
            "event_open_order_count": 8,
            "active_event_open_position_count": 1,
            "pending_intents": 0,
            "submitted_orders": 0,
            "current_token_trades": [{"id": "sample-trade"}],
        },
        strategy_plan_gate={"status": "ready", "current_plan_count": 1, "current_plans": [{"market_id": "nba-market"}]},
        worker={"status": "stopped", "running": False, "execute": True, "live_money": True, "enable_llm_dispatch": False},
        evidence_paths=["local/shared/artifacts/live-strategy-worker/2026-05-24/ticks.jsonl"],
    )
    wnba = build_normalized_live_snapshot(
        event_id="wnba-wsh-sea-2026-05-24",
        league="wnba",
        generated_at_utc=generated_at,
        game={
            "game_status_text": "Final",
            "period": 4,
            "clock": "PT00M00.00S",
            "game_start_time": "2026-05-24T22:00:00Z",
            "updated_at": "2026-05-25T02:28:00Z",
            "resolution_source": "wnba_cdn_scoreboard_event_slug",
            "home_team_id": "1611661328",
            "home_team_name": "Seattle Storm",
            "home_team_slug": "SEA",
            "home_score": 97,
            "away_team_id": "1611661322",
            "away_team_name": "Washington Mystics",
            "away_team_slug": "WSH",
            "away_score": 85,
            "play_by_play_timestamp_utc": "2026-05-25T02:28:00Z",
        },
        orderbooks={
            "seattle": _sample_orderbook("seattle-token", 0.95, 0.97, "2026-05-25T02:28:00Z"),
            "washington": _sample_orderbook("washington-token", 0.03, 0.05, "2026-05-25T02:28:00Z"),
        },
        direct_clob={
            "event_open_order_count": 0,
            "active_event_open_position_count": 0,
            "pending_intents": 0,
            "submitted_orders": 0,
            "current_token_trades": [],
        },
        strategy_plan_gate={"status": "ready", "current_plan_count": 1, "current_plans": [{"market_id": "wnba-market"}]},
        worker={"status": "stopped", "running": False, "execute": True, "live_money": True, "enable_llm_dispatch": False},
        evidence_paths=["local/shared/reports/daily-live-validation/postgame_signal_review_2026-05-25T0228Z.md"],
    )
    return [nba, wnba]


def _feed_states(
    game: dict[str, Any],
    *,
    generated_at: datetime,
    max_feed_age_seconds: float,
) -> dict[str, SnapshotFeedState]:
    scoreboard_ts = _parse_utc(game.get("updated_at") or game.get("source_timestamp_utc"))
    pbp_ts = _parse_utc(game.get("play_by_play_timestamp_utc"))
    stats_ts = _parse_utc(game.get("stats_timestamp_utc"))
    return {
        "scoreboard": _feed_state(
            source=_text(game.get("resolution_source")) or "scoreboard",
            timestamp=scoreboard_ts,
            generated_at=generated_at,
            stale_code="scoreboard_stale_or_missing",
            max_age_seconds=max_feed_age_seconds,
        ),
        "play_by_play": _feed_state(
            source="play_by_play",
            timestamp=pbp_ts,
            generated_at=generated_at,
            stale_code="play_by_play_missing_or_stale",
            max_age_seconds=max_feed_age_seconds,
        ),
        "stats": _feed_state(
            source="stats",
            timestamp=stats_ts,
            generated_at=generated_at,
            stale_code="stats_missing_or_stale",
            max_age_seconds=max_feed_age_seconds,
        ),
    }


def _feed_state(
    *,
    source: str,
    timestamp: datetime | None,
    generated_at: datetime,
    stale_code: str,
    max_age_seconds: float,
) -> SnapshotFeedState:
    age = _age_seconds(timestamp, generated_at)
    stale = timestamp is None or (age is not None and age > max_age_seconds)
    return SnapshotFeedState(
        source=source,
        source_timestamp_utc=timestamp,
        observed_at_utc=generated_at,
        latency_ms=round(age * 1000.0, 3) if age is not None else None,
        stale=stale,
        blocker_code=stale_code if stale else None,
    )


def _clob_outcome(
    outcome_id: str,
    payload: dict[str, Any],
    *,
    generated_at: datetime,
    max_age_seconds: float,
) -> SnapshotClobOutcome:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    ts = _parse_utc(
        snapshot.get("captured_at_utc")
        or snapshot.get("timestamp_utc")
        or snapshot.get("source_timestamp_utc")
        or payload.get("captured_at_utc")
    )
    age = _age_seconds(ts, generated_at)
    bid = _float_value(snapshot.get("best_bid") or snapshot.get("bid"))
    ask = _float_value(snapshot.get("best_ask") or snapshot.get("ask"))
    spread = _float_value(snapshot.get("spread"))
    if spread is None and bid is not None and ask is not None:
        spread = round(max(0.0, ask - bid), 6)
    mid = _float_value(snapshot.get("mid_price"))
    if mid is None and bid is not None and ask is not None:
        mid = round((bid + ask) / 2.0, 6)
    stale = ts is None or (age is not None and age > max_age_seconds)
    return SnapshotClobOutcome(
        outcome_id=str(outcome_id),
        token_id=_text(snapshot.get("token_id") or snapshot.get("asset_id") or payload.get("token_id")),
        label=_text(snapshot.get("outcome_label") or payload.get("label")),
        best_bid=bid,
        best_ask=ask,
        mid_price=mid,
        spread=spread,
        top_bid_depth=_float_value(snapshot.get("bid_depth") or snapshot.get("top_bid_depth")),
        top_ask_depth=_float_value(snapshot.get("ask_depth") or snapshot.get("top_ask_depth")),
        minimum_size=_float_value(snapshot.get("min_order_size") or snapshot.get("minimum_size")),
        tick_size=_float_value(snapshot.get("tick_size")),
        source_timestamp_utc=ts,
        age_seconds=round(age, 6) if age is not None else None,
        stale=stale,
        blocker_code="clob_orderbook_stale_or_missing" if stale else None,
    )


def _account_state(direct_clob: dict[str, Any]) -> SnapshotAccountState:
    event_orders = _int_value(
        direct_clob.get("active_event_open_order_count")
        or direct_clob.get("event_open_order_count")
        or direct_clob.get("open_orders")
    )
    event_positions = _int_value(
        direct_clob.get("active_event_open_position_count")
        or direct_clob.get("event_open_position_count")
        or direct_clob.get("open_positions")
    )
    pending = _int_value(direct_clob.get("pending_intents") or direct_clob.get("pending_buy_intents"))
    submitted = _int_value(direct_clob.get("submitted_orders"))
    trades = direct_clob.get("current_token_trades")
    trade_count = len(trades) if isinstance(trades, list) else _int_value(direct_clob.get("current_token_trade_count"))
    return SnapshotAccountState(
        event_open_order_count=event_orders or 0,
        event_open_position_count=event_positions or 0,
        pending_intent_count=pending or 0,
        submitted_order_count=submitted or 0,
        current_token_trade_count=trade_count or 0,
        unresolved_inventory=bool(direct_clob.get("unresolved_inventory"))
        or bool((event_orders or 0) + (event_positions or 0) + (pending or 0)),
    )


def _runtime_state(strategy_plan_gate: dict[str, Any], worker: dict[str, Any]) -> SnapshotRuntimeState:
    status = str(worker.get("status") or ("running" if worker.get("running") else "unknown"))
    return SnapshotRuntimeState(
        strategy_plan_ready=strategy_plan_gate.get("status") == "ready"
        or bool(strategy_plan_gate.get("ready_for_strategy_evaluation")),
        current_plan_count=_int_value(strategy_plan_gate.get("current_plan_count")) or 0,
        worker_status=status,
        worker_running=bool(worker.get("running") or status == "running"),
        execute=bool(worker.get("execute")),
        live_money=bool(worker.get("live_money")),
        llm_dispatch_enabled=bool(worker.get("enable_llm_dispatch")),
    )


def _blocker_codes(
    *,
    league: League,
    feeds: dict[str, SnapshotFeedState],
    clob: list[SnapshotClobOutcome],
    direct_clob: dict[str, Any],
    runtime: SnapshotRuntimeState,
) -> list[str]:
    codes = [feed.blocker_code for feed in feeds.values() if feed.blocker_code]
    codes.extend(item.blocker_code for item in clob if item.blocker_code)
    if not clob:
        codes.append("clob_orderbook_missing")
    if not direct_clob:
        codes.append("direct_clob_event_inventory_missing")
    if not runtime.strategy_plan_ready:
        codes.append("strategy_plan_not_ready")
    if not runtime.worker_running:
        codes.append("live_strategy_worker_not_running")
    return [f"{league}_execution:{code}" for code in _unique_strings([str(code) for code in codes if code])]


def _parity_gaps(snapshots: list[NormalizedLiveSnapshot]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for code in snapshot.evidence.blocker_codes:
            gaps.append(
                {
                    "event_id": snapshot.event_id,
                    "league": snapshot.league,
                    "blocker_code": code,
                    "scope": f"{snapshot.league} execution only; other league snapshots remain independently usable",
                }
            )
    return gaps


def _next_actions(parity_gaps: list[dict[str, Any]]) -> list[str]:
    actions = [
        "Wire this normalized snapshot object into the live tick output before live-worker adoption.",
        "Keep league-specific feed gaps as scoped blocker codes instead of global runtime failures.",
        "Use #70/#71 replay and project-chief artifacts to decide which fields need stricter validation.",
    ]
    if parity_gaps:
        actions.insert(0, "Backfill or classify stale/missing feed fields before treating the affected league as executable.")
    return actions


def _sample_orderbook(token_id: str, bid: float, ask: float, captured_at_utc: str) -> dict[str, Any]:
    return {
        "snapshot": {
            "token_id": token_id,
            "best_bid": bid,
            "best_ask": ask,
            "spread": round(max(0.0, ask - bid), 6),
            "bid_depth": 5.0,
            "ask_depth": 5.0,
            "min_order_size": 5.0,
            "tick_size": 0.001,
            "captured_at_utc": captured_at_utc,
        }
    }


def _first_current_plan(strategy_plan_gate: dict[str, Any]) -> dict[str, Any]:
    plans = strategy_plan_gate.get("current_plans")
    if isinstance(plans, list) and plans and isinstance(plans[0], dict):
        return plans[0]
    return {}


def _status_text(game: dict[str, Any]) -> str:
    return _text(game.get("game_status_text") or game.get("status") or game.get("game_status")) or "unknown"


def _parse_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_seconds(timestamp: datetime | None, generated_at: datetime) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (generated_at - timestamp).total_seconds())


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _bullet_lines(items: Any) -> list[str]:
    rendered = [f"- {item}" for item in items if item]
    return rendered or ["- none"]


__all__ = [
    "NormalizedLiveSnapshot",
    "NormalizedLiveSnapshotReview",
    "SnapshotAccountState",
    "SnapshotClobOutcome",
    "SnapshotEvidenceState",
    "SnapshotFeedState",
    "SnapshotGameState",
    "SnapshotRuntimeState",
    "SnapshotTeamState",
    "build_normalized_live_snapshot",
    "build_normalized_live_snapshot_review",
    "normalized_live_snapshot_root",
    "render_normalized_live_snapshot_review_markdown",
    "sample_snapshot_pair",
    "write_normalized_live_snapshot_review",
]
