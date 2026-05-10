from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Run one quote-aware StrategyPlanJSON tick with shadow and optional live execution.")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--event-id", action="append", dest="event_ids", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--source", default="codex-live-monitor")
    parser.add_argument("--execute", action="store_true", help="Allow order submission through the audited execute endpoint.")
    parser.add_argument("--live-money", action="store_true", help="Set dry_run=false for the execute pass.")
    parser.add_argument("--max-intents", type=int, default=2)
    parser.add_argument("--orderbook-sample-count", type=int, default=2)
    parser.add_argument("--orderbook-sample-interval-sec", type=float, default=0.5)
    parser.add_argument("--min-size", type=float, default=5.0)
    parser.add_argument("--min-buy-notional-usd", type=float, default=1.0)
    parser.add_argument("--share-precision", type=int, default=3)
    args = parser.parse_args()

    result = run_tick(
        api_root=args.api_root,
        session_date=args.session_date,
        event_ids=args.event_ids,
        account_id=args.account_id,
        source=args.source,
        execute=args.execute,
        live_money=args.live_money,
        max_intents=args.max_intents,
        orderbook_sample_count=args.orderbook_sample_count,
        orderbook_sample_interval_sec=args.orderbook_sample_interval_sec,
        min_size=args.min_size,
        min_buy_notional_usd=args.min_buy_notional_usd,
        share_precision=args.share_precision,
    )
    exit_for_response(result)


def run_tick(
    *,
    api_root: str,
    session_date: str,
    event_ids: list[str],
    account_id: str,
    source: str,
    execute: bool,
    live_money: bool,
    max_intents: int,
    orderbook_sample_count: int,
    orderbook_sample_interval_sec: float,
    min_size: float,
    min_buy_notional_usd: float,
    share_precision: int,
) -> dict[str, Any]:
    integrity = api_json(
        api_root,
        "POST",
        "/v1/ops/integrity-check",
        {
            "session_date": session_date,
            "event_ids": event_ids,
            "account_id": account_id,
            "source": source,
            "notes": "live strategy tick preflight",
        },
    )
    live_monitor = api_json(
        api_root,
        "POST",
        "/v1/ops/live-monitor",
        {
            "session_date": session_date,
            "event_ids": event_ids,
            "account_id": account_id,
            "source": source,
            "execute": execute,
            "notes": "quote-aware live strategy tick",
        },
    )
    events: list[dict[str, Any]] = []
    all_ok = bool(integrity.get("ok", True)) and bool(live_monitor.get("ok", True))
    ready_for_live = bool(((integrity.get("integrity") or {}).get("ready_for_live_minimum_orders")))
    for event_id in event_ids:
        event_result = _run_event_tick(
            api_root=api_root,
            session_date=session_date,
            event_id=event_id,
            account_id=account_id,
            source=source,
            execute=execute,
            live_money=live_money,
            max_intents=max_intents,
            orderbook_sample_count=orderbook_sample_count,
            orderbook_sample_interval_sec=orderbook_sample_interval_sec,
            integrity_ready=ready_for_live,
            min_size=min_size,
            min_buy_notional_usd=min_buy_notional_usd,
            share_precision=share_precision,
        )
        events.append(event_result)
        all_ok = all_ok and bool(event_result.get("ok", True))

    return {
        "ok": all_ok,
        "session_date": session_date,
        "source": source,
        "execute_requested": execute,
        "live_money_requested": live_money,
        "operator_sizing_policy": {
            "mode": "operator_minimum_order",
            "min_size": min_size,
            "min_buy_notional_usd": min_buy_notional_usd,
            "share_precision": share_precision,
        },
        "integrity_ready_for_live_minimum_orders": ready_for_live,
        "integrity_path": integrity.get("path"),
        "live_monitor_path": live_monitor.get("path"),
        "strategy_plan_gate": live_monitor.get("strategy_plan_gate"),
        "events": events,
    }


def _run_event_tick(
    *,
    api_root: str,
    session_date: str,
    event_id: str,
    account_id: str,
    source: str,
    execute: bool,
    live_money: bool,
    max_intents: int,
    orderbook_sample_count: int,
    orderbook_sample_interval_sec: float,
    integrity_ready: bool,
    min_size: float,
    min_buy_notional_usd: float,
    share_precision: int,
) -> dict[str, Any]:
    context = api_json(
        api_root,
        "GET",
        f"/v1/events/{event_id}/agent-context",
        query={"session_date": session_date},
    )
    plan = context.get("current_strategy_plan") or {}
    if not plan:
        return {"ok": False, "event_id": event_id, "reason": "missing_current_strategy_plan", "context": context}

    game = _resolve_game(api_root, event_id, session_date)
    live_state: dict[str, Any] = {}
    if game.get("game_id"):
        api_json(
            api_root,
            "POST",
            f"/v1/sync/nba/live/{game['game_id']}",
            {"include_live_snapshots": True, "include_play_by_play": True},
        )
        live_state = api_json(api_root, "GET", f"/v1/nba/games/{game['game_id']}/live")

    outcome_states: dict[str, dict[str, Any]] = {}
    orderbook_results: dict[str, Any] = {}
    for strategy in plan.get("active_strategies") or []:
        entry_rules = strategy.get("entry_rules") or {}
        outcome_id = str(entry_rules.get("outcome_id") or "")
        if not outcome_id or outcome_id in outcome_states:
            continue
        api_json(
            api_root,
            "POST",
            "/v1/sync/polymarket/orderbook",
            {
                "outcome_id": outcome_id,
                "sample_count": orderbook_sample_count,
                "sample_interval_sec": orderbook_sample_interval_sec,
                "max_levels_per_side": 10,
            },
        )
        latest = api_json(api_root, "GET", f"/v1/outcomes/{outcome_id}/orderbook/latest")
        orderbook_results[outcome_id] = latest
        outcome_states[outcome_id] = _state_from_orderbook(
            latest,
            side=str(entry_rules.get("side") or "buy"),
            outcome_label=strategy.get("side"),
            game=game,
            live_state=live_state,
        )

    portfolio_state = {
        "open_orders": _direct_count(context, "direct_open_order_count"),
        "open_positions": _direct_count(context, "direct_open_position_count"),
        "operator_sizing_policy": {
            "mode": "operator_minimum_order",
            "min_size": min_size,
            "min_buy_notional_usd": min_buy_notional_usd,
            "share_precision": share_precision,
        },
    }
    integrity = context.get("integrity") or {}
    direct_clob = integrity.get("direct_clob") if isinstance(integrity, dict) else None
    if isinstance(direct_clob, dict):
        portfolio_state["open_orders"] = direct_clob.get("open_order_count", portfolio_state["open_orders"])
        portfolio_state["open_positions"] = len(((direct_clob.get("open_positions") or {}).get("positions") or []))

    market_state = {
        "outcome_states": outcome_states,
        "game": game,
        "live_state": _compact_live_state(live_state),
    }
    shadow = api_json(
        api_root,
        "POST",
        f"/v1/events/{event_id}/strategy-plan/evaluate",
        {
            "session_date": session_date,
            "account_id": account_id,
            "dry_run": True,
            "execute": False,
            "market_state": market_state,
            "portfolio_state": portfolio_state,
            "source": f"{source}:shadow",
            "max_intents": max_intents,
        },
    )
    live: dict[str, Any] | None = None
    live_blocked_by: list[str] = []
    if execute:
        if not live_money:
            live_blocked_by.append("live_money_flag_required")
        if not integrity_ready:
            live_blocked_by.append("integrity_not_ready_for_live_minimum_orders")
        if not live_blocked_by:
            live = api_json(
                api_root,
                "POST",
                f"/v1/events/{event_id}/strategy-plan/execute",
                {
                    "session_date": session_date,
                    "account_id": account_id,
                    "dry_run": False,
                    "execute": True,
                    "market_state": market_state,
                    "portfolio_state": portfolio_state,
                    "source": f"{source}:live",
                    "max_intents": max_intents,
                },
            )

    return {
        "ok": True,
        "event_id": event_id,
        "game": game,
        "market_id": plan.get("market_id"),
        "active_strategy_count": len(plan.get("active_strategies") or []),
        "market_state": market_state,
        "portfolio_state": portfolio_state,
        "shadow_evaluation": shadow,
        "live_execution": live,
        "live_blocked_by": live_blocked_by,
        "orderbook_results": _summarize_orderbooks(orderbook_results),
    }


def _state_from_orderbook(
    latest: dict[str, Any],
    *,
    side: str,
    outcome_label: str | None,
    game: dict[str, Any],
    live_state: dict[str, Any],
) -> dict[str, Any]:
    snapshot = latest.get("snapshot") or {}
    best_bid = _float(snapshot.get("best_bid"))
    best_ask = _float(snapshot.get("best_ask"))
    spread = _float(snapshot.get("spread"))
    price = best_ask if side.lower() == "buy" else best_bid
    captured_at = _parse_dt(snapshot.get("captured_at"))
    scoreboard = _scoreboard_state(outcome_label, game=game, live_state=live_state)
    state = {
        "price": price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_cents": round(spread * 100.0, 6) if spread is not None else None,
        "orderbook_age_seconds": _age_seconds(captured_at),
        "captured_at_utc": snapshot.get("captured_at"),
    }
    state.update(scoreboard)
    return state


def _scoreboard_state(outcome_label: str | None, *, game: dict[str, Any], live_state: dict[str, Any]) -> dict[str, Any]:
    latest = live_state.get("latest_snapshot") or {}
    latest_payload = latest.get("payload_json") if isinstance(latest.get("payload_json"), dict) else {}
    if not latest:
        return {
            "score_gap": None,
            "scoreboard_age_seconds": None,
            "game_status": game.get("game_status"),
            "period": game.get("period"),
            "game_clock": game.get("game_clock"),
        }
    home_score = _float(latest.get("home_score"))
    away_score = _float(latest.get("away_score"))
    label = str(outcome_label or "").strip().lower()
    home_names = {str(game.get("home_team_name") or "").lower(), str(game.get("home_team_slug") or "").lower()}
    away_names = {str(game.get("away_team_name") or "").lower(), str(game.get("away_team_slug") or "").lower()}
    score_gap = None
    if home_score is not None and away_score is not None:
        if label in home_names:
            score_gap = home_score - away_score
        elif label in away_names:
            score_gap = away_score - home_score
    snapshot_time = _parse_dt(latest.get("captured_at") or latest.get("updated_at") or latest.get("snapshot_time"))
    return {
        "score_gap": score_gap,
        "scoreboard_age_seconds": _age_seconds(snapshot_time),
        "game_status": latest.get("game_status") or latest_payload.get("game_status") or game.get("game_status"),
        "period": latest.get("period") or game.get("period"),
        "game_clock": latest.get("game_clock") or latest.get("clock") or latest_payload.get("game_clock") or game.get("game_clock"),
        "home_score": home_score,
        "away_score": away_score,
    }


def _resolve_game(api_root: str, event_id: str, session_date: str) -> dict[str, Any]:
    games = api_json(api_root, "GET", "/v1/nba/games", query={"limit": 1000})
    items = games.get("items") or []
    parsed = _parse_event_id(event_id)
    if not parsed:
        return {"event_id": event_id, "resolved": False, "reason": "event_id_not_parseable"}
    team_a, team_b, date = parsed
    for item in items:
        expected_date = date or session_date
        if item.get("game_date") != expected_date:
            continue
        slugs = {str(item.get("home_team_slug") or "").lower(), str(item.get("away_team_slug") or "").lower()}
        if {team_a, team_b} == slugs:
            return {**item, "resolved": True}
    return {"event_id": event_id, "resolved": False, "reason": "game_not_found", "parsed": parsed}


def _parse_event_id(event_id: str) -> tuple[str, str, str] | None:
    parts = event_id.lower().split("-")
    if len(parts) < 6 or parts[0] != "nba":
        return None
    return parts[1], parts[2], "-".join(parts[-3:])


def _compact_live_state(live_state: dict[str, Any]) -> dict[str, Any]:
    latest = live_state.get("latest_snapshot") or {}
    return {
        "game_id": live_state.get("game_id"),
        "latest_snapshot": latest,
        "recent_play_by_play_count": len(live_state.get("recent_play_by_play") or []),
        "sync_summary": live_state.get("sync_summary"),
    }


def _direct_count(context: dict[str, Any], key: str) -> int:
    value = context.get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summarize_orderbooks(orderbooks: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for outcome_id, payload in orderbooks.items():
        snapshot = payload.get("snapshot") or {}
        summary[outcome_id] = {
            "best_bid": snapshot.get("best_bid"),
            "best_ask": snapshot.get("best_ask"),
            "spread": snapshot.get("spread"),
            "captured_at": snapshot.get("captured_at"),
            "levels_count": payload.get("levels_count"),
        }
    return summary


def _age_seconds(value: datetime | None) -> float | None:
    if value is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - value).total_seconds())


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
