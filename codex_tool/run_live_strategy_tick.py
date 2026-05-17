from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.contracts import LLMRuntimeTrace
from app.modules.agentic.llm_runtime import (
    build_current_event_inventory_proof,
    build_llm_runtime_trace,
    process_llm_runtime_trace,
)

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


_LOCAL_PENDING_INTENT_STATUSES = {
    "pending_submit",
    "submitted",
    "open",
    "working",
    "pending",
    "partially_filled",
    "partial",
}
_DIRECT_ORDER_ID_FIELDS = ("id", "orderID", "orderId", "order_id", "external_order_id")
_DIRECT_TRADE_ID_FIELDS = ("id", "trade_id", "external_trade_id", "hash", "tx_hash", "transaction_hash")
_DIRECT_TRADE_ORDER_ID_FIELDS = (
    "taker_order_id",
    "takerOrderId",
    "maker_order_id",
    "makerOrderId",
    "order_id",
    "orderID",
    "orderId",
    "external_order_id",
)
_DIRECT_TRADE_TOKEN_FIELDS = ("asset_id", "asset", "token_id")
_DIRECT_ORDER_TOKEN_FIELDS = ("asset_id", "asset", "token_id")
_DIRECT_POSITION_TOKEN_FIELDS = ("asset", "asset_id", "token_id")
_MARKET_TRADE_SOURCE_LATENCY_MS_MAX = 999_999_999.999


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
    parser.add_argument(
        "--auto-protect-manual-positions",
        dest="auto_protect_manual_positions",
        action="store_true",
        help="Place target sells for uncovered direct CLOB positions before evaluating new entries.",
    )
    parser.add_argument(
        "--no-auto-protect-manual-positions",
        dest="auto_protect_manual_positions",
        action="store_false",
        help="Disable automatic target sells for uncovered direct CLOB positions.",
    )
    parser.set_defaults(auto_protect_manual_positions=True)
    parser.add_argument("--manual-target-delta-cents", type=float, default=5.0)
    parser.add_argument(
        "--submit-candidate-strategy-plan",
        action="store_true",
        help="After reviewed operator intervention detection, submit the generated candidate StrategyPlanJSON as the current plan.",
    )
    parser.add_argument(
        "--enable-llm-dispatch",
        action="store_true",
        help="Call the routed internal LLM when runtime triggers exist. Disabled by default; skipped traces are still persisted.",
    )
    parser.add_argument(
        "--llm-runtime-artifact-root",
        default=None,
        help="Override root for LLM runtime trace artifacts. Defaults to local/shared/artifacts/llm-runtime.",
    )
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
        auto_protect_manual_positions=args.auto_protect_manual_positions,
        manual_target_delta_cents=args.manual_target_delta_cents,
        submit_candidate_strategy_plan=args.submit_candidate_strategy_plan,
        enable_llm_dispatch=args.enable_llm_dispatch,
        llm_runtime_artifact_root=args.llm_runtime_artifact_root,
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
    auto_protect_manual_positions: bool,
    manual_target_delta_cents: float,
    submit_candidate_strategy_plan: bool = False,
    enable_llm_dispatch: bool = False,
    llm_runtime_artifact_root: str | None = None,
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
    integrity_snapshot = integrity.get("integrity") if isinstance(integrity.get("integrity"), dict) else {}
    ready_for_live = bool(integrity_snapshot.get("ready_for_live_minimum_orders"))
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
            integrity_snapshot=integrity_snapshot,
            min_size=min_size,
            min_buy_notional_usd=min_buy_notional_usd,
            share_precision=share_precision,
            auto_protect_manual_positions=auto_protect_manual_positions,
            manual_target_delta_cents=manual_target_delta_cents,
            submit_candidate_strategy_plan=submit_candidate_strategy_plan,
            enable_llm_dispatch=enable_llm_dispatch,
            llm_runtime_artifact_root=llm_runtime_artifact_root,
            persist_llm_runtime_trace=True,
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
    integrity_snapshot: dict[str, Any],
    min_size: float,
    min_buy_notional_usd: float,
    share_precision: int,
    auto_protect_manual_positions: bool,
    manual_target_delta_cents: float,
    submit_candidate_strategy_plan: bool = False,
    enable_llm_dispatch: bool = False,
    llm_runtime_artifact_root: str | None = None,
    persist_llm_runtime_trace: bool = False,
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

    outcome_specs: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        entry_rules = strategy.get("entry_rules") or {}
        outcome_id = str(entry_rules.get("outcome_id") or "").strip()
        if outcome_id:
            outcome_specs.append(
                {
                    "outcome_id": outcome_id,
                    "outcome_label": _outcome_label_from_strategy(strategy, entry_rules),
                    "side": str(entry_rules.get("side") or "buy"),
                    "strategy": strategy,
                    "entry_rules": entry_rules,
                    "source": "active_strategy",
                }
            )

    market_outcome_refs: dict[str, dict[str, str]] = {}
    market_outcome_lookup_error: str | None = None
    market_id = str(plan.get("market_id") or "").strip()
    if market_id:
        try:
            market_outcomes = api_json(api_root, "GET", f"/v1/markets/{market_id}/outcomes")
            for row in market_outcomes.get("items") or []:
                if not isinstance(row, dict):
                    continue
                outcome_id = str(row.get("outcome_id") or "").strip()
                if not outcome_id:
                    continue
                ref = {
                    "market_id": str(row.get("market_id") or market_id),
                    "outcome_id": outcome_id,
                    "token_id": str(row.get("token_id") or "").strip(),
                    "outcome_label": str(row.get("outcome_label") or "").strip(),
                }
                market_outcome_refs[outcome_id] = ref
                if ref["token_id"]:
                    market_outcome_refs[ref["token_id"]] = ref
                if any(spec["outcome_id"] == outcome_id for spec in outcome_specs):
                    continue
                outcome_specs.append(
                    {
                        "outcome_id": outcome_id,
                        "outcome_label": ref["outcome_label"] or None,
                        "side": "buy",
                        "strategy": {},
                        "entry_rules": {"side": "buy", "outcome_id": outcome_id, "token_id": ref["token_id"]},
                        "source": "market_outcome_context",
                    }
                )
        except Exception as exc:
            market_outcome_lookup_error = str(exc)

    outcome_states: dict[str, dict[str, Any]] = {}
    orderbook_results: dict[str, Any] = {}
    sampled_outcomes: list[dict[str, Any]] = []
    for spec in outcome_specs:
        strategy = spec.get("strategy") if isinstance(spec.get("strategy"), dict) else {}
        entry_rules = spec.get("entry_rules") if isinstance(spec.get("entry_rules"), dict) else {}
        outcome_id = str(spec.get("outcome_id") or "")
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
        outcome_label = str(spec.get("outcome_label") or "").strip() or _outcome_label_from_strategy(strategy, entry_rules)
        if not outcome_label:
            try:
                outcome_detail = api_json(api_root, "GET", f"/v1/outcomes/{outcome_id}")
                outcome_label = str(outcome_detail.get("outcome_label") or "").strip() or None
            except Exception:
                outcome_label = None
        sampled_outcomes.append(
            {
                "outcome_id": outcome_id,
                "outcome_label": outcome_label,
                "token_id": str(entry_rules.get("token_id") or market_outcome_refs.get(outcome_id, {}).get("token_id") or ""),
                "source": spec.get("source"),
            }
        )
        orderbook_results[outcome_id] = latest
        outcome_states[outcome_id] = _state_from_orderbook(
            latest,
            side=str(entry_rules.get("side") or "buy"),
            outcome_label=outcome_label,
            game=game,
            live_state=live_state,
        )

    watch_persistence = _persist_orderbook_watch_ticks(
        api_root=api_root,
        event_id=event_id,
        plan=plan,
        orderbooks=orderbook_results,
        outcome_lookup_override=market_outcome_refs,
        source=source,
        game=game,
        cadence_ms=max(0, int(orderbook_sample_interval_sec * 1000)),
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
    direct_clob = integrity_snapshot.get("direct_clob") if isinstance(integrity_snapshot, dict) else None
    if not isinstance(direct_clob, dict):
        integrity = context.get("integrity") or {}
        direct_clob = integrity.get("direct_clob") if isinstance(integrity, dict) else None
    direct_clob_state = direct_clob if isinstance(direct_clob, dict) else {}
    event_direct_clob_state = _event_scoped_direct_clob(direct_clob_state, plan) if direct_clob_state else {}
    if event_direct_clob_state:
        portfolio_state["direct_clob_global_open_orders"] = event_direct_clob_state.get("global_open_order_count")
        portfolio_state["direct_clob_global_open_positions"] = event_direct_clob_state.get("global_open_position_count")
        portfolio_state["direct_clob_event_token_count"] = len(event_direct_clob_state.get("event_token_ids") or [])
        portfolio_state["open_orders"] = event_direct_clob_state.get(
            "event_open_order_count",
            len(((event_direct_clob_state.get("open_orders") or {}).get("orders") or [])),
        )
        portfolio_state["open_positions"] = event_direct_clob_state.get(
            "event_open_position_count",
            len(((event_direct_clob_state.get("open_positions") or {}).get("positions") or [])),
        )

    pending_intents = _pending_intent_summary(
        api_root=api_root,
        account_id=account_id,
        event_id=event_id,
        plan=plan,
        direct_clob=event_direct_clob_state,
    )
    known_order_ids = _known_portfolio_order_external_ids(
        api_root=api_root,
        account_id=account_id,
        event_id=event_id,
        plan=plan,
    )
    portfolio_state["pending_intents"] = pending_intents["pending_intent_count"]
    portfolio_state["pending_buy_intents"] = pending_intents["pending_buy_intent_count"]
    portfolio_state["pending_intents_side"] = "buy"
    portfolio_state["pending_intent_orders"] = pending_intents["orders"]
    portfolio_state["pending_intent_source"] = pending_intents["source"]
    if not pending_intents["ok"]:
        portfolio_state["pending_intents_unavailable"] = True
        portfolio_state["pending_intents_error"] = pending_intents.get("error")

    direct_trade_persistence = _persist_direct_trade_watch_observations(
        api_root=api_root,
        event_id=event_id,
        plan=plan,
        direct_clob=event_direct_clob_state,
        source=source,
    )
    portfolio_state["direct_clob_trade_observation_count"] = direct_trade_persistence["trade_count"]
    if direct_trade_persistence.get("ok") is False:
        portfolio_state["direct_clob_trade_persistence_failed"] = True

    player_status_shocks = _player_status_shocks_from_live_state(live_state, plan=plan, game=game)
    revision_required_shocks = [
        shock for shock in player_status_shocks if shock.get("requires_strategy_plan_revision") is not False
    ]
    market_state = {
        "outcome_states": outcome_states,
        "sampled_outcomes": sampled_outcomes,
        "game": game,
        "live_state": _compact_live_state(live_state),
        "player_status_shocks": player_status_shocks,
        "player_status_shock_count": len(revision_required_shocks),
    }
    if market_outcome_lookup_error:
        market_state["market_outcome_lookup_error"] = market_outcome_lookup_error
    operator_reaction = _auto_protect_direct_positions(
        api_root=api_root,
        account_id=account_id,
        event_id=event_id,
        plan=plan,
        direct_clob=event_direct_clob_state,
        execute=execute,
        live_money=live_money,
        integrity_ready=integrity_ready,
        source=source,
        min_size=min_size,
        target_delta_cents=manual_target_delta_cents,
        enabled=auto_protect_manual_positions,
        known_external_order_ids=set(known_order_ids["external_order_ids"]) if known_order_ids["ok"] else None,
        outcome_states=outcome_states,
    )
    operator_reaction["known_order_lookup"] = known_order_ids
    operator_reaction["direct_trade_persistence"] = direct_trade_persistence
    operator_reaction["candidate_strategy_plan_submission"] = _submit_candidate_strategy_plan(
        api_root=api_root,
        event_id=event_id,
        operator_reaction=operator_reaction,
        enabled=submit_candidate_strategy_plan,
    )
    llm_portfolio_state = dict(portfolio_state)
    if operator_reaction.get("submitted_orders"):
        llm_portfolio_state["submitted_orders"] = operator_reaction["submitted_orders"]
    current_event_inventory_proof = build_current_event_inventory_proof(
        direct_clob_truth=event_direct_clob_state,
        portfolio_state=llm_portfolio_state,
    )
    portfolio_state["current_event_inventory_proof"] = current_event_inventory_proof
    llm_portfolio_state["current_event_inventory_proof"] = current_event_inventory_proof
    market_state["current_event_inventory_proof"] = current_event_inventory_proof
    llm_runtime_trace = build_llm_runtime_trace(
        event_id=event_id,
        market_id=str(plan.get("market_id") or "") or None,
        session_date=session_date,
        current_plan=plan,
        event_context=context,
        live_state=live_state,
        direct_clob_truth=event_direct_clob_state,
        orderbook_state={
            "outcome_states": outcome_states,
            "orderbooks": _summarize_orderbooks(orderbook_results),
        },
        portfolio_state=llm_portfolio_state,
        operator_interventions=_operator_reaction_revision_events(operator_reaction),
        strategy_decisions=[],
        pbp_shocks=player_status_shocks,
        ml_pbp_evidence={"status": "placeholder_schema_ready", "signals": []},
        source=f"{source}:llm_runtime_detector",
    )
    llm_runtime_persistence: dict[str, Any] = {
        "enabled": False,
        "ok": True,
        "status": "not_persisted",
        "reason": "persistence_disabled_for_direct_call",
    }
    if persist_llm_runtime_trace:
        llm_runtime_trace, llm_runtime_persistence = process_llm_runtime_trace(
            llm_runtime_trace,
            dispatch_enabled=enable_llm_dispatch,
            artifact_root=llm_runtime_artifact_root,
            session_date=session_date,
        )
    market_state["llm_runtime_trigger_count"] = llm_runtime_trace.trigger_count
    market_state["llm_runtime_triggers"] = [
        {
            "trigger_id": trigger.trigger_id,
            "trigger_type": trigger.trigger_type,
            "severity": trigger.severity,
            "reason": trigger.reason,
            "selected_model": llm_runtime_trace.model_routing.selected_model,
        }
        for trigger in llm_runtime_trace.triggers
    ]
    market_state["llm_runtime_status"] = _llm_runtime_status_summary(
        llm_runtime_trace,
        persistence=llm_runtime_persistence,
        dispatch_enabled=enable_llm_dispatch,
    )
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
        if direct_trade_persistence.get("ok") is False:
            live_blocked_by.append("direct_clob_trade_persistence_failed")
        llm_blocker = _llm_runtime_live_blocker(llm_runtime_trace)
        if llm_blocker:
            live_blocked_by.append(llm_blocker)
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

    sleeve_runtime_status = _sleeve_runtime_status(live if live is not None else shadow)
    candidate_submission = operator_reaction.get("candidate_strategy_plan_submission") or {}
    candidate_submission_failed = bool(submit_candidate_strategy_plan) and candidate_submission.get("ok") is False
    direct_trade_persistence_failed = direct_trade_persistence.get("ok") is False
    return {
        "ok": not candidate_submission_failed and not direct_trade_persistence_failed,
        "event_id": event_id,
        "game": game,
        "market_id": plan.get("market_id"),
        "active_strategy_count": len(plan.get("active_strategies") or []),
        "market_state": market_state,
        "portfolio_state": portfolio_state,
        "shadow_evaluation": shadow,
        "live_execution": live,
        "live_blocked_by": live_blocked_by,
        "sleeve_states": sleeve_runtime_status["items"],
        "strategy_sleeve_status": sleeve_runtime_status,
        "operator_reaction": operator_reaction,
        "llm_runtime_trace": llm_runtime_trace.model_dump(mode="json"),
        "llm_runtime_persistence": llm_runtime_persistence,
        "llm_runtime_status": market_state["llm_runtime_status"],
        "orderbook_results": _summarize_orderbooks(orderbook_results),
        "watch_persistence": watch_persistence,
    }


def _sleeve_runtime_status(evaluation: dict[str, Any] | None) -> dict[str, Any]:
    raw_states = evaluation.get("sleeve_states") if isinstance(evaluation, dict) else None
    states = [dict(item) for item in raw_states or [] if isinstance(item, dict)]
    status_counts: dict[str, int] = {}
    for state in states:
        status = str(state.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "status": "recorded" if states else "not_recorded",
        "sleeve_count": len(states),
        "status_counts": status_counts,
        "blocked_sleeve_count": status_counts.get("blocked", 0),
        "intent_sleeve_count": status_counts.get("intent_created", 0),
        "items": states,
    }


def _llm_runtime_status_summary(
    trace: LLMRuntimeTrace,
    *,
    persistence: dict[str, Any],
    dispatch_enabled: bool,
) -> dict[str, Any]:
    response = trace.revision_response
    return {
        "trace_id": trace.trace_id,
        "event_id": trace.event_id,
        "status": trace.status,
        "trigger_count": trace.trigger_count,
        "trigger_types": [trigger.trigger_type for trigger in trace.triggers],
        "selected_model": trace.model_routing.selected_model,
        "selected_tier": trace.model_routing.selected_tier,
        "dispatch_enabled": dispatch_enabled,
        "response_status": response.status if response else None,
        "skipped_reason": response.skipped_reason if response else None,
        "persisted": bool(persistence.get("ok")) and persistence.get("status") == "persisted",
        "artifact_path": persistence.get("path"),
        "live_blocker": _llm_runtime_live_blocker(trace),
    }


def _llm_runtime_live_blocker(trace: LLMRuntimeTrace) -> str | None:
    if not any(trigger.requires_revision for trigger in trace.triggers):
        return None
    response = trace.revision_response
    if response is None or response.status != "response_recorded":
        return "llm_revision_unavailable"
    return "llm_revision_review_required"


def _submit_candidate_strategy_plan(
    *,
    api_root: str,
    event_id: str,
    operator_reaction: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "submitted": False, "reason": "review_flag_required"}
    candidate = operator_reaction.get("candidate_strategy_plan")
    if not isinstance(candidate, dict):
        return {"enabled": True, "submitted": False, "reason": "candidate_strategy_plan_missing"}
    response = api_json(api_root, "POST", f"/v1/events/{event_id}/strategy-plan", candidate)
    ok = bool(response.get("ok", True))
    return {
        "enabled": True,
        "submitted": ok,
        "ok": ok,
        "response": response,
    }


def _operator_reaction_revision_events(operator_reaction: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("position_reactions", "order_reactions", "trade_reactions"):
        value = operator_reaction.get(key)
        if isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, dict))
    return rows


def _position_target_price_from_plan(
    *,
    plan: dict[str, Any],
    token_id: str,
    outcome_id: str,
    avg_price: float,
    default_target_delta_cents: float,
    outcome_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = _position_strategy_from_plan(
        plan=plan,
        token_id=token_id,
        outcome_id=outcome_id,
        outcome_state=outcome_state,
    )
    if strategy is not None:
        exit_rules = strategy.get("exit_rules") if isinstance(strategy.get("exit_rules"), dict) else {}
        sleeve = _strategy_sleeve_metadata(strategy)
        scaled_target = _scaled_micro_grid_target_price(avg_price, exit_rules)
        if scaled_target is not None:
            scaled_target = _normalize_direct_sell_limit_price(scaled_target)
            return {
                "target_price": scaled_target,
                "target_policy": "micro_grid_scaled",
                "target_delta_cents": round((scaled_target - avg_price) * 100.0, 4),
                "strategy_id": strategy.get("strategy_id"),
                "strategy_family": strategy.get("family"),
                "sleeve": sleeve,
                "sleeve_id": sleeve.get("sleeve_id"),
                "sleeve_group": sleeve.get("sleeve_group"),
                "sleeve_role": sleeve.get("sleeve_role"),
                "exit_rules": exit_rules,
            }
        explicit_target = _float(exit_rules.get("target_price"))
        if explicit_target is not None:
            target_price = _normalize_direct_sell_limit_price(explicit_target)
            return {
                "target_price": target_price,
                "target_policy": "explicit_target_price",
                "target_delta_cents": round((target_price - avg_price) * 100.0, 4),
                "strategy_id": strategy.get("strategy_id"),
                "strategy_family": strategy.get("family"),
                "sleeve": sleeve,
                "sleeve_id": sleeve.get("sleeve_id"),
                "sleeve_group": sleeve.get("sleeve_group"),
                "sleeve_role": sleeve.get("sleeve_role"),
                "exit_rules": exit_rules,
            }
    fallback_sleeve = _strategy_sleeve_metadata(strategy)
    fallback_target = _normalize_direct_sell_limit_price(avg_price + default_target_delta_cents / 100.0)
    return {
        "target_price": fallback_target,
        "target_policy": "fallback_fixed_delta",
        "target_delta_cents": default_target_delta_cents,
        "strategy_id": strategy.get("strategy_id") if strategy else None,
        "strategy_family": strategy.get("family") if strategy else None,
        "sleeve": fallback_sleeve,
        "sleeve_id": fallback_sleeve.get("sleeve_id"),
        "sleeve_group": fallback_sleeve.get("sleeve_group"),
        "sleeve_role": fallback_sleeve.get("sleeve_role"),
    }


def _normalize_direct_sell_limit_price(price: float) -> float:
    """Normalize protective target sells to CLOB tick sizes without lowering target."""

    bounded = min(0.95, max(0.01, float(price)))
    if bounded < 0.10:
        return round(math.ceil((bounded - 1e-12) * 100.0) / 100.0, 2)
    return round(bounded, 4)


def _strategy_matches_token(entry_rules: dict[str, Any], *, token_id: str, outcome_id: str) -> bool:
    entry_token = str(entry_rules.get("token_id") or entry_rules.get("asset_id") or "").strip()
    entry_outcome = str(entry_rules.get("outcome_id") or "").strip()
    return bool((entry_token and entry_token == token_id) or (entry_outcome and entry_outcome == outcome_id))


def _strategy_sleeve_metadata(strategy: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(strategy, dict):
        return {}
    entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
    metadata: dict[str, Any] = {}
    for key in ("sleeve_id", "sleeve_group", "sleeve_role"):
        value = strategy.get(key) or entry_rules.get(key)
        if str(value or "").strip():
            metadata[key] = value
    return metadata


def _scaled_micro_grid_target_price(entry_price: float, rules: dict[str, Any]) -> float | None:
    if entry_price <= 0.0:
        return None
    target_policy = str(rules.get("target_policy") or rules.get("target_mode") or "").strip().lower()
    min_target_cents = _float(
        rules.get("min_target_cents")
        or rules.get("minimum_target_cents")
        or rules.get("target_floor_cents")
    )
    target_return_fraction = _float(
        rules.get("target_return_fraction")
        or rules.get("target_gain_fraction")
        or rules.get("target_return")
    )
    target_return_percent = _float(rules.get("target_return_percent") or rules.get("target_gain_percent"))
    if target_return_fraction is None and target_return_percent is not None:
        target_return_fraction = target_return_percent / 100.0
    if (
        target_policy not in {"micro_grid_scaled", "scaled_micro_grid", "price_scaled_micro_grid"}
        and min_target_cents is None
        and target_return_fraction is None
    ):
        return None
    min_move = (min_target_cents or 0.0) / 100.0
    fraction_move = entry_price * (target_return_fraction or 0.0)
    target_move = max(min_move, fraction_move)
    if target_move <= 0.0:
        return None
    return round(min(0.95, max(0.01, entry_price + target_move)), 4)


def _auto_protect_direct_positions(
    *,
    api_root: str,
    account_id: str,
    event_id: str,
    plan: dict[str, Any],
    direct_clob: dict[str, Any],
    execute: bool,
    live_money: bool,
    integrity_ready: bool,
    source: str,
    min_size: float,
    target_delta_cents: float,
    enabled: bool,
    known_external_order_ids: set[str] | None = None,
    outcome_states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reaction: dict[str, Any] = {
        "enabled": enabled,
        "checked": False,
        "submitted_orders": [],
        "recommended_orders": [],
        "blocked_by": [],
        "covered_positions": [],
        "order_reactions": [],
        "trade_reactions": [],
        "position_reactions": [],
        "adverse_position_reviews": [],
        "revision_requests": [],
        "candidate_strategy_plan_required": False,
        "intervention_records": [],
    }
    if not enabled:
        return reaction
    reaction["checked"] = True
    if not execute:
        reaction["blocked_by"].append("execute_flag_required")
    if not live_money:
        reaction["blocked_by"].append("live_money_flag_required")
    if not integrity_ready:
        reaction["blocked_by"].append("integrity_not_ready_for_live_minimum_orders")

    positions = ((direct_clob.get("open_positions") or {}).get("positions") or [])
    open_orders = ((direct_clob.get("open_orders") or {}).get("orders") or [])
    direct_trades = _direct_trade_rows(direct_clob, include_market_observations=False)
    reaction["direct_trade_count"] = len(direct_trades)
    plan_outcomes = _plan_outcome_lookup(plan)
    reaction["order_reactions"].extend(
        _unknown_direct_order_reactions(
            event_id=event_id,
            open_orders=open_orders,
            plan=plan,
            plan_outcomes=plan_outcomes,
            known_external_order_ids=known_external_order_ids,
        )
    )
    reaction["revision_requests"].extend(
        item["revision_request"] for item in reaction["order_reactions"] if isinstance(item.get("revision_request"), dict)
    )
    reaction["trade_reactions"].extend(
        _unknown_direct_trade_reactions(
            event_id=event_id,
            trades=direct_trades,
            plan=plan,
            plan_outcomes=plan_outcomes,
            known_external_order_ids=known_external_order_ids,
        )
    )
    reaction["revision_requests"].extend(
        item["revision_request"] for item in reaction["trade_reactions"] if isinstance(item.get("revision_request"), dict)
    )
    for position in positions:
        event_slug = str(position.get("event_slug") or position.get("slug") or "")
        if event_slug and event_slug != event_id:
            continue
        token_id = str(position.get("asset") or position.get("token_id") or "").strip()
        if not token_id:
            reaction["blocked_by"].append("position_token_missing")
            continue
        position_size = _float(position.get("size")) or 0.0
        open_sell_size = _open_sell_size_for_token(open_orders, token_id)
        uncovered_size = max(0.0, position_size - open_sell_size)
        outcome_ref = plan_outcomes.get(token_id)
        avg_price = _position_avg_price(position, position_size)
        outcome_state = (outcome_states or {}).get(str(outcome_ref["outcome_id"])) if outcome_ref is not None else {}
        if not isinstance(outcome_state, dict):
            outcome_state = {}
        matched_strategy = (
            _position_strategy_from_plan(
                plan=plan,
                token_id=token_id,
                outcome_id=str(outcome_ref["outcome_id"]),
                outcome_state=outcome_state,
            )
            if outcome_ref is not None
            else None
        )
        sleeve = _strategy_sleeve_metadata(matched_strategy)
        if outcome_ref is not None and avg_price is not None:
            adverse_review = _position_adverse_review(
                event_id=event_id,
                plan=plan,
                position=position,
                token_id=token_id,
                outcome_id=str(outcome_ref["outcome_id"]),
                outcome_state=outcome_state,
                position_size=position_size,
                avg_price=avg_price,
                open_sell_size=open_sell_size,
                uncovered_size=uncovered_size,
            )
            if adverse_review is not None:
                reaction["adverse_position_reviews"].append(adverse_review)
                reaction["position_reactions"].append(adverse_review)
                reaction["revision_requests"].append(adverse_review["revision_request"])
        if uncovered_size <= 1e-9:
            reaction["covered_positions"].append({"token_id": token_id, "position_size": position_size, "open_sell_size": open_sell_size})
            continue
        position_reaction = {
            "action": "adopt_operator_position",
            "token_id": token_id,
            "outcome_label": position.get("outcome"),
            "position_size": position_size,
            "open_sell_size": open_sell_size,
            "uncovered_size": uncovered_size,
            "no_new_entry": True,
            "requires_strategy_plan_revision": True,
            "revision_request": {
                "reason": "operator_intervention_detected",
                "event_id": event_id,
                "token_id": token_id,
                "outcome_label": position.get("outcome"),
                "matched_strategy_id": matched_strategy.get("strategy_id") if matched_strategy else None,
                "matched_strategy_family": matched_strategy.get("family") if matched_strategy else None,
                "position_management_only": True,
                "disable_new_entries": True,
                "required_context": [
                    "direct_clob_truth",
                    "latest_scoreboard",
                    "recent_play_by_play",
                    "current_orderbook",
                    "existing_targets",
                ],
                "current_position": {
                    "size": position_size,
                    "avg_price": _float(position.get("avg_price")),
                    "open_sell_size": open_sell_size,
                    "uncovered_size": uncovered_size,
                },
            },
        }
        if sleeve:
            position_reaction["sleeve"] = dict(sleeve)
            position_reaction.update(sleeve)
            position_reaction["revision_request"]["sleeve"] = dict(sleeve)
            position_reaction["revision_request"].update(sleeve)
        reaction["position_reactions"].append(position_reaction)
        reaction["revision_requests"].append(position_reaction["revision_request"])
        if uncovered_size < min_size:
            reaction["recommended_orders"].append(
                {
                    "reason": "uncovered_size_below_minimum",
                    "token_id": token_id,
                    "uncovered_size": uncovered_size,
                    "minimum_size": min_size,
                }
            )
            continue
        if outcome_ref is None:
            reaction["recommended_orders"].append(
                {
                    "reason": "outcome_mapping_missing",
                    "token_id": token_id,
                    "uncovered_size": uncovered_size,
                }
            )
            continue
        if avg_price is None:
            reaction["recommended_orders"].append(
                {
                    "reason": "position_price_missing",
                    "token_id": token_id,
                    "uncovered_size": uncovered_size,
                }
            )
            continue
        target_resolution = _position_target_price_from_plan(
            plan=plan,
            token_id=token_id,
            outcome_id=str(outcome_ref["outcome_id"]),
            avg_price=avg_price,
            default_target_delta_cents=target_delta_cents,
            outcome_state=outcome_state,
        )
        target_price = target_resolution["target_price"]
        order_payload = {
            "account_id": account_id,
            "market_id": str(outcome_ref["market_id"]),
            "outcome_id": str(outcome_ref["outcome_id"]),
            "side": "sell",
            "order_type": "limit",
            "time_in_force": "gtc",
            "limit_price": target_price,
            "size": round(uncovered_size, 6),
            "metadata_json": {
                "source": source,
                "event_id": event_id,
                "reason": "automatic target for uncovered direct CLOB position",
                "reaction_type": "operator_intervention_target",
                "reaction_owner": "janus_internal_reactor_v0",
                "outcome_label": position.get("outcome"),
                "entry_avg_price": avg_price,
                "target_delta_cents": target_resolution.get("target_delta_cents"),
                "target_policy": target_resolution.get("target_policy"),
                "matched_strategy_id": target_resolution.get("strategy_id"),
                "matched_strategy_family": target_resolution.get("strategy_family"),
                "matched_sleeve_id": target_resolution.get("sleeve_id"),
                "matched_sleeve_group": target_resolution.get("sleeve_group"),
                "matched_sleeve_role": target_resolution.get("sleeve_role"),
                "sleeve": target_resolution.get("sleeve") or {},
                "position_size": position_size,
                "open_sell_size": open_sell_size,
                "no_new_entry_until_revision": True,
                "revision_request": position_reaction["revision_request"],
            },
            "dry_run": False,
        }
        reaction["recommended_orders"].append({k: v for k, v in order_payload.items() if k != "metadata_json"})
        if reaction["blocked_by"]:
            continue
        submitted = api_json(api_root, "POST", "/v1/portfolio/orders", order_payload)
        reaction["submitted_orders"].append(submitted)
        external_order_ids = [
            str(submitted.get(key))
            for key in ("external_order_id", "order_id", "id")
            if str(submitted.get(key) or "").strip()
        ]
        intervention = api_json(
            api_root,
            "POST",
            "/v1/operator/interventions/reconcile",
            {
                "account_id": account_id,
                "event_id": event_id,
                "market_id": str(outcome_ref["market_id"]),
                "action": "scan",
                "external_order_ids": external_order_ids,
                "manual_reason": "auto-protected uncovered direct CLOB position",
                "target_status": "target_order_submitted" if submitted.get("ok", True) else "target_order_submit_failed",
                "stop_status": "not_configured_review_required",
                "hedge_status": "opposite_side_disabled_without_profit_lock",
                "protective_order_status": submitted.get("status"),
                "expected_close_path": f"target_sell_{target_price}",
                "metadata": {
                    "token_id": token_id,
                    "outcome_id": str(outcome_ref["outcome_id"]),
                    "target_order": submitted,
                    "position": position,
                    "reaction": position_reaction,
                    "target_resolution": target_resolution,
                    "recommended_order": {k: v for k, v in order_payload.items() if k != "metadata_json"},
                },
                "notes": "Live monitor auto-protection for operator/manual or otherwise uncovered position.",
            },
        )
        reaction["intervention_records"].append(intervention)
    candidate_plan = _operator_position_strategy_plan_candidate(
        event_id=event_id,
        plan=plan,
        position_reactions=reaction["position_reactions"],
        order_reactions=reaction["order_reactions"],
        trade_reactions=reaction["trade_reactions"],
        source=source,
    )
    reaction["candidate_strategy_plan_required"] = bool(reaction["revision_requests"])
    if candidate_plan is not None:
        reaction["candidate_strategy_plan"] = candidate_plan
    return reaction


def _position_avg_price(position: dict[str, Any], position_size: float) -> float | None:
    avg_price = _float(position.get("avg_price"))
    if avg_price is not None:
        return avg_price
    current_value = _float(position.get("current_value"))
    return current_value / position_size if current_value is not None and position_size > 0 else None


def _position_adverse_review(
    *,
    event_id: str,
    plan: dict[str, Any],
    position: dict[str, Any],
    token_id: str,
    outcome_id: str,
    outcome_state: dict[str, Any],
    position_size: float,
    avg_price: float,
    open_sell_size: float,
    uncovered_size: float,
) -> dict[str, Any] | None:
    strategy = _position_strategy_from_plan(
        plan=plan,
        token_id=token_id,
        outcome_id=outcome_id,
        outcome_state=outcome_state,
    )
    if strategy is None:
        return None
    stop_rules = strategy.get("stop_rules") if isinstance(strategy.get("stop_rules"), dict) else {}
    if not stop_rules:
        return None
    sleeve = _strategy_sleeve_metadata(strategy)

    current_bid = _float(outcome_state.get("best_bid"))
    current_ask = _float(outcome_state.get("best_ask"))
    current_price = current_bid if current_bid is not None else _float(outcome_state.get("price"))
    score_gap = _float(outcome_state.get("score_gap"))
    captured_at_utc = outcome_state.get("captured_at_utc") or outcome_state.get("captured_at")
    triggered_rules: list[dict[str, Any]] = []

    stop_price = _float(stop_rules.get("stop_price") or stop_rules.get("stop_review_price") or stop_rules.get("review_below_price"))
    if stop_price is not None and current_price is not None and current_price <= stop_price:
        triggered_rules.append(
            {
                "rule": "stop_price",
                "threshold": stop_price,
                "current_exit_price": current_price,
            }
        )

    max_adverse_cents = _float(stop_rules.get("max_adverse_cents") or stop_rules.get("review_after_adverse_cents"))
    adverse_cents = round((avg_price - current_price) * 100.0, 4) if current_price is not None else None
    if max_adverse_cents is not None and adverse_cents is not None and adverse_cents >= max_adverse_cents:
        triggered_rules.append(
            {
                "rule": "max_adverse_cents",
                "threshold_cents": max_adverse_cents,
                "adverse_cents": adverse_cents,
            }
        )

    score_gap_threshold = _float(
        stop_rules.get("stop_review_if_score_gap_exceeds")
        or stop_rules.get("review_if_score_gap_exceeds")
        or stop_rules.get("max_adverse_score_gap")
    )
    if score_gap_threshold is not None and score_gap is not None and score_gap <= -abs(score_gap_threshold):
        triggered_rules.append(
            {
                "rule": "adverse_score_gap",
                "threshold_points": abs(score_gap_threshold),
                "score_gap": score_gap,
            }
        )

    if not triggered_rules:
        return None

    return {
        "action": "position_adverse_move",
        "event_id": event_id,
        "token_id": token_id,
        "outcome_id": outcome_id,
        "outcome_label": position.get("outcome") or strategy.get("side"),
        "strategy_id": strategy.get("strategy_id"),
        "strategy_family": strategy.get("family"),
        "sleeve": sleeve,
        "sleeve_id": sleeve.get("sleeve_id"),
        "sleeve_group": sleeve.get("sleeve_group"),
        "sleeve_role": sleeve.get("sleeve_role"),
        "position_size": position_size,
        "open_sell_size": open_sell_size,
        "uncovered_size": uncovered_size,
        "avg_price": avg_price,
        "current_exit_bid": current_bid,
        "current_buy_ask": current_ask,
        "current_price": current_price,
        "captured_at_utc": captured_at_utc,
        "score_gap": score_gap,
        "triggered_rules": triggered_rules,
        "no_new_entry": False,
        "requires_strategy_plan_revision": True,
        "revision_request": {
            "reason": "position_adverse_move",
            "event_id": event_id,
            "token_id": token_id,
            "outcome_id": outcome_id,
            "outcome_label": position.get("outcome") or strategy.get("side"),
            "strategy_id": strategy.get("strategy_id"),
            "strategy_family": strategy.get("family"),
            "sleeve": sleeve,
            "sleeve_id": sleeve.get("sleeve_id"),
            "sleeve_group": sleeve.get("sleeve_group"),
            "sleeve_role": sleeve.get("sleeve_role"),
            "required_context": [
                "direct_clob_truth",
                "latest_scoreboard",
                "recent_play_by_play",
                "current_orderbook",
                "existing_targets",
                "opposite_side_orderbook",
            ],
            "decision_options_to_compare": [
                "hold_existing_target",
                "cancel_replace_lower_target",
                "opposite_side_hedge_or_continuation",
                "add_down_same_side_micro_grid",
                "marketable_stop_or_reduce_only_if_virtual_dead_or_garbage_time",
            ],
            "current_position": {
                "size": position_size,
                "avg_price": avg_price,
                "open_sell_size": open_sell_size,
                "uncovered_size": uncovered_size,
                "current_exit_bid": current_bid,
                "current_buy_ask": current_ask,
                "score_gap": score_gap,
                "captured_at_utc": captured_at_utc,
                "triggered_rules": triggered_rules,
            },
        },
    }


def _position_strategy_from_plan(
    *,
    plan: dict[str, Any],
    token_id: str,
    outcome_id: str,
    outcome_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    matches: list[tuple[tuple[bool, int, int], dict[str, Any]]] = []
    for index, strategy in enumerate(strategy for strategy in plan.get("active_strategies") or [] if isinstance(strategy, dict)):
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        if _strategy_matches_token(entry_rules, token_id=token_id, outcome_id=outcome_id):
            shadow_only = bool((strategy.get("shadow_flags") or {}).get("shadow_only"))
            matches.append(((shadow_only, _strategy_period_rank(entry_rules, outcome_state), -index), strategy))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _strategy_period_rank(entry_rules: dict[str, Any], outcome_state: dict[str, Any] | None) -> int:
    period = _int((outcome_state or {}).get("period"))
    allowed_periods = entry_rules.get("allowed_periods")
    min_period = _int(entry_rules.get("min_period"))
    max_period = _int(entry_rules.get("max_period"))
    has_period_gate = bool(allowed_periods) or min_period is not None or max_period is not None
    if period is None or not has_period_gate:
        return 1
    if isinstance(allowed_periods, list):
        normalized_allowed = {_int(value) for value in allowed_periods}
        normalized_allowed.discard(None)
        if normalized_allowed and period not in normalized_allowed:
            return 2
    if min_period is not None and period < min_period:
        return 2
    if max_period is not None and period > max_period:
        return 2
    return 0


def _operator_position_strategy_plan_candidate(
    *,
    event_id: str,
    plan: dict[str, Any],
    position_reactions: list[dict[str, Any]],
    order_reactions: list[dict[str, Any]],
    trade_reactions: list[dict[str, Any]],
    source: str,
) -> dict[str, Any] | None:
    if not position_reactions and not order_reactions and not trade_reactions:
        return None
    market_id = str(plan.get("market_id") or "").strip()
    if not market_id:
        return None

    now = datetime.now(timezone.utc)
    outcome_lookup = _plan_outcome_lookup(plan)
    active_strategies: list[dict[str, Any]] = []
    portfolio_reconciliation: list[dict[str, Any]] = []
    for index, reaction in enumerate(position_reactions, start=1):
        token_id = str(reaction.get("token_id") or "").strip()
        if not token_id:
            continue
        outcome_ref = outcome_lookup.get(token_id) or {}
        outcome_id = str(outcome_ref.get("outcome_id") or "").strip()
        outcome_label = str(reaction.get("outcome_label") or outcome_id or token_id).strip()
        strategy_suffix = _safe_slug(outcome_label or token_id)
        revision_request = reaction.get("revision_request") if isinstance(reaction.get("revision_request"), dict) else {}
        current_position = revision_request.get("current_position") if isinstance(revision_request, dict) else {}
        if not isinstance(current_position, dict):
            current_position = {}
        position_size = _float(reaction.get("position_size"))
        avg_price = _float(current_position.get("avg_price"))
        open_sell_size = _float(reaction.get("open_sell_size")) or 0.0
        uncovered_size = _float(reaction.get("uncovered_size")) or 0.0
        sleeve = {key: reaction.get(key) for key in ("sleeve_id", "sleeve_group", "sleeve_role") if reaction.get(key) is not None}
        active_strategies.append(
            {
                "strategy_id": f"operator-position-management-{strategy_suffix}-{index}",
                "family": "operator_position_management",
                "side": outcome_label or "operator_position",
                "budget_usd": 0.0,
                "max_positions": 0,
                "entry_rules": {
                    "entry_disabled": True,
                    "no_new_entry": True,
                    "side": "buy",
                    "order_type": "limit",
                    "market_id": str(outcome_ref.get("market_id") or market_id),
                    "outcome_id": outcome_id,
                    "token_id": token_id,
                    "price": avg_price,
                    "size": 0.0,
                    "max_open_positions": 0,
                    "reason": "operator_position_adopted_position_management_only",
                    "revision_request_reason": revision_request.get("reason"),
                },
                "exit_rules": {
                    "position_management_only": True,
                    "current_position_size": position_size,
                    "current_position_avg_price": avg_price,
                    "covered_by_open_sell_size": open_sell_size,
                    "uncovered_size": uncovered_size,
                    "target_required": uncovered_size > 1e-9,
                    "do_not_hold_to_final_without_fresh_revision": True,
                },
                "stop_rules": {
                    "manual_or_llm_review_required": True,
                    "stop_order_autonomy": "disabled_until_revision",
                },
                "hedge_rules": {
                    "opposite_side_only_if_locks_profit_or_reduces_marked_loss": True,
                    "new_bilateral_inventory_disabled": True,
                },
                "revision_triggers": [
                    {"type": "operator_intervention_detected", "required": True},
                    {"type": "target_fill_or_cancel", "required": True},
                    {"type": "player_status_shock", "required": True},
                    {"type": "quarter_end", "required": True},
                    {"type": "score_gap_6_plus", "required": True},
                    {"type": "clob_move_without_scoreboard_driver", "required": True},
                ],
                "shadow_flags": {
                    "shadow_only": True,
                    "execution_mode": "position_management_only",
                    "requires_review_before_live": True,
                },
            }
        )
        if sleeve:
            active_strategies[-1].update(sleeve)
        portfolio_reconciliation.append(
            {
                "action": "adopt",
                "reason": "operator_intervention_detected",
                "token_id": token_id,
                "outcome_id": outcome_id,
                "outcome_label": outcome_label,
                "position_size": position_size,
                "open_sell_size": open_sell_size,
                "uncovered_size": uncovered_size,
                "no_new_entry": True,
                "requires_strategy_plan_revision": True,
            }
        )
        if sleeve:
            portfolio_reconciliation[-1].update(sleeve)

    for index, reaction in enumerate(order_reactions, start=1):
        token_id = str(reaction.get("token_id") or "").strip()
        if not token_id:
            continue
        outcome_ref = outcome_lookup.get(token_id) or {}
        outcome_id = str(outcome_ref.get("outcome_id") or "").strip()
        outcome_label = str(reaction.get("outcome_label") or outcome_id or token_id).strip()
        order_side = str(reaction.get("order_side") or "").lower()
        strategy_suffix = _safe_slug(outcome_label or token_id)
        direct_order = reaction.get("direct_order") if isinstance(reaction.get("direct_order"), dict) else {}
        sleeve = {key: reaction.get(key) for key in ("sleeve_id", "sleeve_group", "sleeve_role") if reaction.get(key) is not None}
        active_strategies.append(
            {
                "strategy_id": f"operator-open-order-management-{strategy_suffix}-{index}",
                "family": "operator_order_management",
                "side": outcome_label or "operator_order",
                "budget_usd": 0.0,
                "max_positions": 0,
                "entry_rules": {
                    "entry_disabled": True,
                    "no_new_entry": True,
                    "side": "buy",
                    "order_type": "limit",
                    "market_id": str(outcome_ref.get("market_id") or market_id),
                    "outcome_id": outcome_id,
                    "token_id": token_id,
                    "price": _float(direct_order.get("price")),
                    "size": 0.0,
                    "max_open_positions": 0,
                    "reason": "unknown_direct_clob_order_adopted_order_management_only",
                    "revision_request_reason": "unknown_direct_clob_order_detected",
                },
                "exit_rules": {
                    "order_management_only": True,
                    "direct_order_id": reaction.get("direct_order_id"),
                    "direct_order_side": order_side,
                    "direct_order_status": reaction.get("order_status"),
                    "direct_order_size": _float(direct_order.get("size")),
                    "direct_order_price": _float(direct_order.get("price")),
                    "cancel_replace_review_required": True,
                },
                "stop_rules": {"manual_or_llm_review_required": True},
                "hedge_rules": {
                    "opposite_side_only_if_locks_profit_or_reduces_marked_loss": True,
                    "new_bilateral_inventory_disabled": True,
                },
                "revision_triggers": [
                    {"type": "unknown_direct_clob_order_detected", "required": True},
                    {"type": "order_fill_cancel_or_expire", "required": True},
                    {"type": "player_status_shock", "required": True},
                    {"type": "clob_move_without_scoreboard_driver", "required": True},
                ],
                "shadow_flags": {
                    "shadow_only": True,
                    "execution_mode": "order_management_only",
                    "requires_review_before_live": True,
                },
            }
        )
        if sleeve:
            active_strategies[-1].update(sleeve)
        portfolio_reconciliation.append(
            {
                "action": "adopt_open_order",
                "reason": "unknown_direct_clob_order_detected",
                "direct_order_id": reaction.get("direct_order_id"),
                "token_id": token_id,
                "outcome_id": outcome_id,
                "outcome_label": outcome_label,
                "order_side": order_side,
                "order_status": reaction.get("order_status"),
                "no_new_entry": True,
                "requires_strategy_plan_revision": True,
            }
        )
        if sleeve:
            portfolio_reconciliation[-1].update(sleeve)

    for index, reaction in enumerate(trade_reactions, start=1):
        token_id = str(reaction.get("token_id") or "").strip()
        if not token_id:
            continue
        outcome_ref = outcome_lookup.get(token_id) or {}
        outcome_id = str(outcome_ref.get("outcome_id") or "").strip()
        outcome_label = str(reaction.get("outcome_label") or outcome_id or token_id).strip()
        trade_side = str(reaction.get("trade_side") or "").lower()
        strategy_suffix = _safe_slug(outcome_label or token_id)
        direct_trade = reaction.get("direct_trade") if isinstance(reaction.get("direct_trade"), dict) else {}
        direct_order_ids = [str(item) for item in reaction.get("direct_order_ids") or [] if str(item).strip()]
        sleeve = {key: reaction.get(key) for key in ("sleeve_id", "sleeve_group", "sleeve_role") if reaction.get(key) is not None}
        active_strategies.append(
            {
                "strategy_id": f"operator-trade-management-{strategy_suffix}-{index}",
                "family": "operator_trade_management",
                "side": outcome_label or "operator_trade",
                "budget_usd": 0.0,
                "max_positions": 0,
                "entry_rules": {
                    "entry_disabled": True,
                    "no_new_entry": True,
                    "side": "buy",
                    "order_type": "limit",
                    "market_id": str(outcome_ref.get("market_id") or market_id),
                    "outcome_id": outcome_id,
                    "token_id": token_id,
                    "price": _float(direct_trade.get("price")),
                    "size": 0.0,
                    "max_open_positions": 0,
                    "reason": "unknown_direct_clob_trade_adopted_trade_management_only",
                    "revision_request_reason": "unknown_direct_clob_trade_detected",
                },
                "exit_rules": {
                    "trade_management_only": True,
                    "direct_trade_id": reaction.get("direct_trade_id"),
                    "direct_order_ids": direct_order_ids,
                    "direct_trade_side": trade_side,
                    "direct_trade_size": _float(direct_trade.get("size")),
                    "direct_trade_price": _float(direct_trade.get("price")),
                    "estimated_cashflow_usd": reaction.get("estimated_cashflow_usd"),
                    "settlement_link_required": True,
                    "final_pnl_review_required": True,
                },
                "stop_rules": {"manual_or_llm_review_required": True},
                "hedge_rules": {
                    "opposite_side_only_if_locks_profit_or_reduces_marked_loss": True,
                    "new_bilateral_inventory_disabled": True,
                },
                "revision_triggers": [
                    {"type": "unknown_direct_clob_trade_detected", "required": True},
                    {"type": "settlement_or_offset_fill", "required": True},
                    {"type": "player_status_shock", "required": True},
                    {"type": "clob_move_without_scoreboard_driver", "required": True},
                ],
                "shadow_flags": {
                    "shadow_only": True,
                    "execution_mode": "trade_management_only",
                    "requires_review_before_live": True,
                },
            }
        )
        if sleeve:
            active_strategies[-1].update(sleeve)
        portfolio_reconciliation.append(
            {
                "action": "adopt_trade_fill",
                "reason": "unknown_direct_clob_trade_detected",
                "direct_trade_id": reaction.get("direct_trade_id"),
                "direct_order_ids": direct_order_ids,
                "token_id": token_id,
                "outcome_id": outcome_id,
                "outcome_label": outcome_label,
                "trade_side": trade_side,
                "trade_size": _float(direct_trade.get("size")),
                "trade_price": _float(direct_trade.get("price")),
                "estimated_cashflow_usd": reaction.get("estimated_cashflow_usd"),
                "no_new_entry": True,
                "requires_strategy_plan_revision": True,
                "final_pnl_review_required": True,
            }
        )
        if sleeve:
            portfolio_reconciliation[-1].update(sleeve)

    if not active_strategies:
        return None
    previous_strategy_ids = [
        str(strategy.get("strategy_id"))
        for strategy in plan.get("active_strategies") or []
        if str(strategy.get("strategy_id") or "").strip()
    ]
    return {
        "schema_version": "strategy_plan_v1",
        "event_id": event_id,
        "market_id": market_id,
        "generated_at_utc": now.isoformat(),
        "valid_until_utc": (now + timedelta(minutes=30)).isoformat(),
        "plan_owner": "system",
        "context_summary": {
            "source": source,
            "revision_owner": "janus_internal_reactor_v0",
            "revision_source": "operator_position_reaction",
            "previous_plan_owner": plan.get("plan_owner"),
            "previous_strategy_ids": previous_strategy_ids,
            "direct_position_count": len(position_reactions),
            "unknown_direct_order_count": len(order_reactions),
            "unknown_direct_trade_count": len(trade_reactions),
            "no_new_entry": True,
            "position_management_only": bool(position_reactions),
            "order_management_only": bool(order_reactions),
            "trade_management_only": bool(trade_reactions),
        },
        "active_strategies": active_strategies,
        "trigger_conditions": [
            {
                "type": "operator_intervention_detected",
                "requires_strategy_plan_revision": True,
                "disable_new_entries": True,
            }
        ],
        "portfolio_reconciliation": portfolio_reconciliation,
        "explainability": {
            "summary": "Candidate plan adopts detected direct CLOB positions, orders, and trades, then disables fresh buys until reviewed.",
            "invalidates_previous_entry_families": True,
            "requires_operator_or_llm_review": True,
        },
    }


def _unknown_direct_order_reactions(
    *,
    event_id: str,
    open_orders: list[dict[str, Any]],
    plan: dict[str, Any],
    plan_outcomes: dict[str, dict[str, str]],
    known_external_order_ids: set[str] | None,
) -> list[dict[str, Any]]:
    if known_external_order_ids is None:
        return []
    known_ids = {str(item).strip().lower() for item in known_external_order_ids if str(item).strip()}
    reactions: list[dict[str, Any]] = []
    for order in open_orders:
        if not isinstance(order, dict):
            continue
        event_slug = str(order.get("event_slug") or order.get("slug") or "").strip()
        if event_slug and event_slug != event_id:
            continue
        token_id = str(order.get("token_id") or order.get("asset_id") or order.get("asset") or "").strip()
        if not token_id or token_id not in plan_outcomes:
            continue
        direct_order_id = _direct_order_external_id(order)
        if not direct_order_id:
            continue
        if direct_order_id.lower() in known_ids:
            continue
        order_side = str(order.get("side") or "").strip().lower()
        outcome_ref = plan_outcomes.get(token_id) or {}
        outcome_id = str(outcome_ref.get("outcome_id") or "")
        strategy = _position_strategy_from_plan(plan=plan, token_id=token_id, outcome_id=outcome_id)
        sleeve = _strategy_sleeve_metadata(strategy)
        outcome_label = str(order.get("outcome") or order.get("outcome_label") or outcome_ref.get("outcome_id") or "").strip()
        revision_request = {
            "reason": "unknown_direct_clob_order_detected",
            "event_id": event_id,
            "direct_order_id": direct_order_id,
            "token_id": token_id,
            "outcome_id": outcome_id or outcome_ref.get("outcome_id"),
            "outcome_label": outcome_label or None,
            "matched_strategy_id": strategy.get("strategy_id") if strategy else None,
            "matched_strategy_family": strategy.get("family") if strategy else None,
            "order_management_only": True,
            "disable_new_entries": True,
            "required_context": [
                "direct_clob_truth",
                "latest_scoreboard",
                "recent_play_by_play",
                "current_orderbook",
                "local_portfolio_orders",
            ],
            "current_order": {
                "side": order_side,
                "status": order.get("status"),
                "price": _float(order.get("price")),
                "size": _float(order.get("size")),
                "filled_size": _float(order.get("filled_size") or order.get("filled")),
            },
        }
        if sleeve:
            revision_request["sleeve"] = dict(sleeve)
            revision_request.update(sleeve)
        reactions.append(
            {
                "action": "adopt_operator_open_order",
                "direct_order_id": direct_order_id,
                "token_id": token_id,
                "outcome_id": outcome_id or outcome_ref.get("outcome_id"),
                "outcome_label": outcome_label or None,
                "matched_strategy_id": strategy.get("strategy_id") if strategy else None,
                "matched_strategy_family": strategy.get("family") if strategy else None,
                "order_side": order_side,
                "order_status": order.get("status"),
                "no_new_entry": True,
                "requires_strategy_plan_revision": True,
                "revision_request": revision_request,
                "direct_order": order,
            }
        )
        if sleeve:
            reactions[-1]["sleeve"] = dict(sleeve)
            reactions[-1].update(sleeve)
    return reactions


def _unknown_direct_trade_reactions(
    *,
    event_id: str,
    trades: list[dict[str, Any]],
    plan: dict[str, Any],
    plan_outcomes: dict[str, dict[str, str]],
    known_external_order_ids: set[str] | None,
) -> list[dict[str, Any]]:
    if known_external_order_ids is None:
        return []
    known_ids = {str(item).strip().lower() for item in known_external_order_ids if str(item).strip()}
    reactions: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        event_slug = str(trade.get("event_slug") or trade.get("slug") or "").strip()
        if event_slug and event_slug != event_id:
            continue
        token_id = _direct_trade_token_id(trade)
        if not token_id or token_id not in plan_outcomes:
            continue
        direct_order_ids = _direct_trade_order_ids(trade)
        direct_trade_id = _direct_trade_external_id(trade)
        if not direct_trade_id and not direct_order_ids:
            continue
        if direct_order_ids and direct_order_ids.intersection(known_ids):
            continue
        trade_side = str(trade.get("side") or "").strip().lower()
        price = _float(trade.get("price"))
        size = _float(trade.get("size"))
        fee = _float(trade.get("fee"))
        estimated_cashflow = _estimated_trade_cashflow(side=trade_side, price=price, size=size, fee=fee)
        outcome_ref = plan_outcomes.get(token_id) or {}
        outcome_id = str(outcome_ref.get("outcome_id") or "")
        strategy = _position_strategy_from_plan(plan=plan, token_id=token_id, outcome_id=outcome_id)
        sleeve = _strategy_sleeve_metadata(strategy)
        outcome_label = str(trade.get("outcome") or trade.get("outcome_label") or outcome_ref.get("outcome_id") or "").strip()
        revision_request = {
            "reason": "unknown_direct_clob_trade_detected",
            "event_id": event_id,
            "direct_trade_id": direct_trade_id,
            "direct_order_ids": sorted(direct_order_ids),
            "token_id": token_id,
            "outcome_id": outcome_id or outcome_ref.get("outcome_id"),
            "outcome_label": outcome_label or None,
            "matched_strategy_id": strategy.get("strategy_id") if strategy else None,
            "matched_strategy_family": strategy.get("family") if strategy else None,
            "trade_management_only": True,
            "disable_new_entries": True,
            "required_context": [
                "direct_clob_truth",
                "local_portfolio_orders",
                "local_portfolio_trades",
                "latest_scoreboard",
                "recent_play_by_play",
                "current_orderbook",
                "settlement_status",
            ],
            "current_trade": {
                "side": trade_side or None,
                "price": price,
                "size": size,
                "fee": fee,
                "estimated_cashflow_usd": estimated_cashflow,
            },
        }
        if sleeve:
            revision_request["sleeve"] = dict(sleeve)
            revision_request.update(sleeve)
        reactions.append(
            {
                "action": "adopt_operator_trade",
                "direct_trade_id": direct_trade_id,
                "direct_order_ids": sorted(direct_order_ids),
                "token_id": token_id,
                "outcome_id": outcome_id or outcome_ref.get("outcome_id"),
                "outcome_label": outcome_label or None,
                "matched_strategy_id": strategy.get("strategy_id") if strategy else None,
                "matched_strategy_family": strategy.get("family") if strategy else None,
                "trade_side": trade_side,
                "estimated_cashflow_usd": estimated_cashflow,
                "no_new_entry": True,
                "requires_strategy_plan_revision": True,
                "revision_request": revision_request,
                "direct_trade": trade,
            }
        )
        if sleeve:
            reactions[-1]["sleeve"] = dict(sleeve)
            reactions[-1].update(sleeve)
    return reactions


def _persist_direct_trade_watch_observations(
    *,
    api_root: str,
    event_id: str,
    plan: dict[str, Any],
    direct_clob: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    trades = _direct_trade_rows(direct_clob)
    if not trades:
        return {"ok": True, "skipped": True, "reason": "no_direct_clob_trades", "trade_count": 0}
    outcome_lookup = _plan_outcome_lookup(plan)
    observations = [
        observation
        for trade in trades
        if (
            observation := _direct_trade_watch_observation(
                event_id=event_id,
                trade=trade,
                outcome_lookup=outcome_lookup,
                source=source,
            )
        )
        is not None
    ]
    if not observations:
        return {"ok": True, "skipped": True, "reason": "no_plan_token_trades", "trade_count": 0}
    result = api_json(
        api_root,
        "POST",
        "/v1/watchlists/trades",
        {
            "source": f"{source}:direct_clob_trade_observation",
            "trades": observations,
        },
    )
    persistence = result.get("db_persistence") if isinstance(result.get("db_persistence"), dict) else {}
    ok = bool(result.get("ok", True)) and bool(persistence.get("ok", True))
    return {
        "ok": ok,
        "skipped": False,
        "trade_count": len(observations),
        "result": result,
    }


def _direct_trade_watch_observation(
    *,
    event_id: str,
    trade: dict[str, Any],
    outcome_lookup: dict[str, dict[str, str]],
    source: str,
) -> dict[str, Any] | None:
    token_id = _direct_trade_token_id(trade)
    if not token_id:
        return None
    outcome_ref = outcome_lookup.get(token_id)
    if outcome_ref is None:
        return None
    observed_at = datetime.now(timezone.utc)
    trade_time = _direct_trade_time_utc(trade)
    order_ids = sorted(_direct_trade_order_ids(trade))
    source_latency_ms = _direct_trade_source_latency_ms(observed_at=observed_at, trade_time=trade_time)
    return {
        "event_key": event_id,
        "market_id": outcome_ref.get("market_id"),
        "outcome_id": outcome_ref.get("outcome_id"),
        "token_id": token_id,
        "external_trade_id": _direct_trade_external_id(trade),
        "trade_time_utc": trade_time.isoformat() if trade_time is not None else observed_at.isoformat(),
        "observed_at_utc": observed_at.isoformat(),
        "side": str(trade.get("side") or "").strip().lower() or None,
        "price": _float(trade.get("price")),
        "size": _float(trade.get("size")),
        "source_latency_ms": source_latency_ms,
        "raw": {
            "source": source,
            "capture_owner": "run_live_strategy_tick",
            "event_id": event_id,
            "direct_order_ids": order_ids,
            "direct_trade": trade,
        },
    }


def _direct_trade_source_latency_ms(*, observed_at: datetime, trade_time: datetime | None) -> float | None:
    if trade_time is None:
        return None
    latency_ms = max(0.0, (observed_at - trade_time).total_seconds() * 1000.0)
    if not math.isfinite(latency_ms):
        return None
    if latency_ms > _MARKET_TRADE_SOURCE_LATENCY_MS_MAX:
        return None
    return latency_ms


def _safe_slug(value: str) -> str:
    normalized = str(value or "").strip().lower()
    chars = [ch if ch.isalnum() else "-" for ch in normalized]
    slug = "-".join(part for part in "".join(chars).split("-") if part)
    return (slug or "position")[:48]


def _persist_orderbook_watch_ticks(
    *,
    api_root: str,
    event_id: str,
    plan: dict[str, Any],
    orderbooks: dict[str, Any],
    outcome_lookup_override: dict[str, dict[str, str]] | None = None,
    source: str,
    game: dict[str, Any],
    cadence_ms: int | None,
) -> dict[str, Any]:
    if not orderbooks:
        return {"ok": True, "skipped": True, "reason": "no_orderbooks_sampled", "tick_count": 0}
    watch_session_key = _watch_session_key_for_event(event_id)
    session = api_json(
        api_root,
        "POST",
        "/v1/watchlists/sessions",
        {
            "watch_session_id": watch_session_key,
            "event_key": event_id,
            "category": "nba",
            "passive_only": True,
            "cadence_ms": cadence_ms,
            "reason": "live_strategy_tick_orderbook_capture",
            "metadata": {
                "source": source,
                "event_id": event_id,
                "game_id": game.get("game_id"),
                "market_id": plan.get("market_id"),
                "capture_owner": "run_live_strategy_tick",
            },
        },
    )
    db_persistence = session.get("db_persistence") if isinstance(session.get("db_persistence"), dict) else {}
    watch_session_id = db_persistence.get("watch_session_id")
    outcome_lookup = _plan_outcome_lookup(plan)
    if outcome_lookup_override:
        outcome_lookup.update(outcome_lookup_override)
    ticks = [
        tick
        for outcome_id, payload in orderbooks.items()
        if (
            tick := _orderbook_watch_tick_payload(
                event_id=event_id,
                outcome_id=outcome_id,
                payload=payload,
                outcome_lookup=outcome_lookup,
                source=source,
                watch_session_key=watch_session_key,
                watch_session_id=watch_session_id,
            )
        )
        is not None
    ]
    tick_result: dict[str, Any] = {"ok": True, "tick_count": 0, "skipped": True}
    if ticks:
        tick_result = api_json(
            api_root,
            "POST",
            "/v1/watchlists/orderbook-ticks",
            {
                "source": f"{source}:strategy_tick_orderbook",
                "ticks": ticks,
            },
        )
    session_ok = bool(session.get("ok", True)) and bool(db_persistence.get("ok", True))
    ticks_ok = bool(tick_result.get("ok", True)) and bool((tick_result.get("db_persistence") or {}).get("ok", True))
    return {
        "ok": session_ok and ticks_ok,
        "watch_session_key": watch_session_key,
        "watch_session_id": watch_session_id,
        "tick_count": len(ticks),
        "session": session,
        "orderbook_ticks": tick_result,
    }


def _orderbook_watch_tick_payload(
    *,
    event_id: str,
    outcome_id: str,
    payload: dict[str, Any],
    outcome_lookup: dict[str, dict[str, str]],
    source: str,
    watch_session_key: str,
    watch_session_id: str | None,
) -> dict[str, Any] | None:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    best_bid = _float(snapshot.get("best_bid"))
    best_ask = _float(snapshot.get("best_ask"))
    if best_bid is None and best_ask is None:
        return None
    spread = _float(snapshot.get("spread"))
    if spread is None and best_bid is not None and best_ask is not None:
        spread = round(max(0.0, best_ask - best_bid), 6)
    mid_price = _float(snapshot.get("mid_price"))
    if mid_price is None and best_bid is not None and best_ask is not None:
        mid_price = round((best_bid + best_ask) / 2.0, 6)
    source_at = _parse_dt(snapshot.get("captured_at"))
    captured_at = datetime.now(timezone.utc)
    outcome_ref = outcome_lookup.get(str(outcome_id)) or {}
    return {
        "event_key": event_id,
        "market_id": outcome_ref.get("market_id") or snapshot.get("market_id"),
        "outcome_id": outcome_id,
        "token_id": outcome_ref.get("token_id") or snapshot.get("token_id"),
        "captured_at_utc": captured_at.isoformat(),
        "source_timestamp_utc": source_at.isoformat() if source_at is not None else None,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "mid_price": mid_price,
        "bid_depth": _float(snapshot.get("bid_depth")),
        "ask_depth": _float(snapshot.get("ask_depth")),
        "source_latency_ms": max(0.0, (captured_at - source_at).total_seconds() * 1000.0) if source_at is not None else None,
        "ingest_latency_ms": 0.0,
        "levels": {
            "bids": payload.get("bids") or [],
            "asks": payload.get("asks") or [],
            "levels_count": payload.get("levels_count"),
        },
        "raw": {
            "source": source,
            "capture_owner": "run_live_strategy_tick",
            "event_id": event_id,
            "watch_session_key": watch_session_key,
            "watch_session_id": watch_session_id,
            "orderbook_snapshot_id": snapshot.get("orderbook_snapshot_id"),
            "outcome_ref": outcome_ref,
            "latest": payload,
        },
    }


def _pending_intent_summary(
    *,
    api_root: str,
    account_id: str,
    event_id: str,
    plan: dict[str, Any],
    direct_clob: dict[str, Any],
) -> dict[str, Any]:
    market_id = str(plan.get("market_id") or "").strip()
    source = "/v1/portfolio/orders"
    payload = api_json(
        api_root,
        "GET",
        source,
        query={
            "account_id": account_id,
            "market_id": market_id or None,
            "side": "buy",
            "limit": 5000,
        },
        timeout=60,
    )
    if payload.get("ok") is False:
        return {
            "ok": False,
            "source": source,
            "pending_intent_count": 0,
            "pending_buy_intent_count": 0,
            "orders": [],
            "error": payload.get("error") or payload.get("status_code") or "portfolio_order_query_failed",
        }

    direct_open_order_ids = _direct_open_order_external_ids(direct_clob)
    direct_filled_order_ids = _direct_filled_order_external_ids(direct_clob)
    pending_orders: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        event_slug = str(item.get("event_slug") or "").strip()
        if event_slug and event_slug != event_id:
            continue
        side = str(item.get("side") or "").strip().lower()
        if side != "buy":
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in _LOCAL_PENDING_INTENT_STATUSES:
            continue
        external_order_id = str(item.get("external_order_id") or "").strip()
        if external_order_id and external_order_id.lower() in direct_filled_order_ids:
            continue
        if external_order_id and external_order_id.lower() in direct_open_order_ids:
            continue
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        pending_orders.append(
            {
                "order_id": item.get("order_id"),
                "external_order_id": external_order_id or None,
                "client_order_id": item.get("client_order_id"),
                "event_slug": event_slug or None,
                "market_id": item.get("market_id"),
                "outcome_id": item.get("outcome_id"),
                "side": side,
                "status": status,
                "size": item.get("size"),
                "limit_price": item.get("limit_price"),
                "strategy_id": metadata.get("strategy_id"),
                "strategy_family": metadata.get("strategy_family"),
                "signal_id": metadata.get("signal_id"),
                "source": "portfolio.orders",
            }
        )
    return {
        "ok": True,
        "source": source,
        "pending_intent_count": len(pending_orders),
        "pending_buy_intent_count": len(pending_orders),
        "orders": pending_orders,
    }


def _known_portfolio_order_external_ids(
    *,
    api_root: str,
    account_id: str,
    event_id: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    market_id = str(plan.get("market_id") or "").strip()
    source = "/v1/portfolio/orders"
    payload = api_json(
        api_root,
        "GET",
        source,
        query={
            "account_id": account_id,
            "market_id": market_id or None,
            "limit": 5000,
        },
        timeout=60,
    )
    if payload.get("ok") is False:
        return {
            "ok": False,
            "source": source,
            "external_order_ids": [],
            "error": payload.get("error") or payload.get("status_code") or "portfolio_order_query_failed",
        }
    ids: set[str] = set()
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        event_slug = str(item.get("event_slug") or "").strip()
        if event_slug and event_slug != event_id:
            continue
        external_order_id = str(item.get("external_order_id") or "").strip().lower()
        if external_order_id:
            ids.add(external_order_id)
    return {"ok": True, "source": source, "external_order_ids": sorted(ids), "known_order_count": len(ids)}


def _direct_open_order_external_ids(direct_clob: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for order in ((direct_clob.get("open_orders") or {}).get("orders") or []):
        if not isinstance(order, dict):
            continue
        value = _direct_order_external_id(order)
        if value:
            ids.add(value.lower())
    return ids


def _direct_filled_order_external_ids(direct_clob: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for trade in _direct_trade_rows(direct_clob):
        ids.update(_direct_trade_order_ids(trade))
    return ids


def _direct_trade_rows(direct_clob: dict[str, Any], *, include_market_observations: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    keys = ("current_token_trades", "trades", "market_trades", "direct_trades") if include_market_observations else ("trades", "direct_trades")
    for key in keys:
        for trade in _direct_trade_section_rows(direct_clob.get(key)):
            if not isinstance(trade, dict):
                continue
            stable_key = _direct_trade_stable_key(trade)
            if stable_key in seen:
                continue
            seen.add(stable_key)
            rows.append(trade)
    return rows


def _direct_trade_section_rows(section: Any) -> list[dict[str, Any]]:
    if isinstance(section, list):
        return [_direct_item_to_dict(item) for item in section]
    if isinstance(section, dict):
        for key in ("trades", "items", "data", "results"):
            nested = section.get(key)
            if isinstance(nested, list):
                return [_direct_item_to_dict(item) for item in nested]
    return []


def _direct_item_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump") and callable(item.model_dump):
        return dict(item.model_dump())
    if hasattr(item, "dict") and callable(item.dict):
        return dict(item.dict())
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    return {"value": str(item)}


def _direct_trade_stable_key(trade: dict[str, Any]) -> str:
    trade_id = _direct_trade_external_id(trade)
    if trade_id:
        return f"id:{trade_id.lower()}"
    order_ids = ",".join(sorted(_direct_trade_order_ids(trade)))
    return "|".join(
        [
            "fields",
            order_ids,
            _direct_trade_token_id(trade) or "",
            str(trade.get("side") or "").strip().lower(),
            str(trade.get("price") or "").strip(),
            str(trade.get("size") or "").strip(),
            str(trade.get("timestamp") or trade.get("trade_time") or trade.get("created_at") or "").strip(),
        ]
    )


def _direct_trade_external_id(trade: dict[str, Any]) -> str | None:
    for field in _DIRECT_TRADE_ID_FIELDS:
        value = str(trade.get(field) or "").strip()
        if value:
            return value
    return None


def _direct_trade_order_ids(trade: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for field in _DIRECT_TRADE_ORDER_ID_FIELDS:
        value = str(trade.get(field) or "").strip()
        if value:
            ids.add(value.lower())
    return ids


def _direct_trade_token_id(trade: dict[str, Any]) -> str | None:
    for field in _DIRECT_TRADE_TOKEN_FIELDS:
        value = str(trade.get(field) or "").strip()
        if value:
            return value
    return None


def _direct_trade_time_utc(trade: dict[str, Any]) -> datetime | None:
    for field in ("trade_time_utc", "trade_time", "timestamp", "created_at", "createdAt"):
        value = trade.get(field)
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            timestamp = float(value)
            if timestamp <= 0:
                continue
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            try:
                return datetime.fromtimestamp(timestamp, timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
    return None


def _estimated_trade_cashflow(
    *,
    side: str,
    price: float | None,
    size: float | None,
    fee: float | None,
) -> float | None:
    if price is None or size is None:
        return None
    notional = price * size
    fee_value = fee or 0.0
    if side.strip().lower() == "sell":
        return round(notional - fee_value, 6)
    if side.strip().lower() == "buy":
        return round(-notional - fee_value, 6)
    return None


def _direct_order_external_id(order: dict[str, Any]) -> str | None:
    for field in _DIRECT_ORDER_ID_FIELDS:
        value = str(order.get(field) or "").strip()
        if value:
            return value
    return None


def _plan_token_ids(plan: dict[str, Any]) -> set[str]:
    token_ids: set[str] = set()
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        token_id = str(entry_rules.get("token_id") or "").strip()
        if token_id:
            token_ids.add(token_id)
    return token_ids


def _direct_order_token_id(order: dict[str, Any]) -> str | None:
    for field in _DIRECT_ORDER_TOKEN_FIELDS:
        value = str(order.get(field) or "").strip()
        if value:
            return value
    return None


def _direct_position_token_id(position: dict[str, Any]) -> str | None:
    for field in _DIRECT_POSITION_TOKEN_FIELDS:
        value = str(position.get(field) or "").strip()
        if value:
            return value
    return None


def _event_scoped_direct_clob(direct_clob: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Return direct CLOB truth filtered to the current event's plan tokens.

    Integrity checks are account-scoped, but a live event tick must not let an
    unrelated open futures position suppress a current-game strategy.
    """
    token_ids = _plan_token_ids(plan)
    if not token_ids:
        return dict(direct_clob)

    open_orders_section = direct_clob.get("open_orders") if isinstance(direct_clob.get("open_orders"), dict) else {}
    raw_orders = [item for item in (open_orders_section.get("orders") or []) if isinstance(item, dict)]
    scoped_orders = [order for order in raw_orders if _direct_order_token_id(order) in token_ids]

    open_positions_section = (
        direct_clob.get("open_positions") if isinstance(direct_clob.get("open_positions"), dict) else {}
    )
    raw_positions = [item for item in (open_positions_section.get("positions") or []) if isinstance(item, dict)]
    scoped_positions = [position for position in raw_positions if _direct_position_token_id(position) in token_ids]

    scoped_account_trades = [
        trade
        for trade in _direct_trade_rows(direct_clob, include_market_observations=False)
        if _direct_trade_token_id(trade) in token_ids
    ]
    scoped_market_trades = [
        trade
        for trade in _direct_trade_rows(direct_clob, include_market_observations=True)
        if _direct_trade_token_id(trade) in token_ids
    ]

    scoped = dict(direct_clob)
    scoped["open_orders"] = dict(open_orders_section)
    scoped["open_orders"]["orders"] = scoped_orders
    scoped["open_orders"]["event_scoped"] = True
    scoped["open_positions"] = dict(open_positions_section)
    scoped["open_positions"]["positions"] = scoped_positions
    scoped["open_positions"]["event_scoped"] = True
    scoped["open_order_count"] = len(scoped_orders)
    scoped["open_position_count"] = len(scoped_positions)
    scoped["event_open_order_count"] = len(scoped_orders)
    scoped["event_open_position_count"] = len(scoped_positions)
    scoped["global_open_order_count"] = direct_clob.get("open_order_count", len(raw_orders))
    scoped["global_open_position_count"] = len(raw_positions)
    scoped["event_token_ids"] = sorted(token_ids)

    current_token_trades = direct_clob.get("current_token_trades")
    if isinstance(current_token_trades, dict):
        scoped["current_token_trades"] = dict(current_token_trades)
    else:
        scoped["current_token_trades"] = {}
    scoped["current_token_trades"]["trades"] = scoped_market_trades
    scoped["current_token_trades"]["trade_count"] = len(scoped_market_trades)
    scoped["current_token_trades"]["token_ids"] = sorted(token_ids)
    scoped["current_token_trade_count"] = len(scoped_market_trades)
    if "trades" in scoped:
        scoped["trades"] = scoped_account_trades
    if "market_trades" in scoped:
        scoped["market_trades"] = scoped_market_trades
    if "direct_trades" in scoped:
        scoped["direct_trades"] = scoped_account_trades
    return scoped


def _plan_outcome_lookup(plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    market_id = str(plan.get("market_id") or "")
    for strategy in plan.get("active_strategies") or []:
        entry_rules = strategy.get("entry_rules") or {}
        token_id = str(entry_rules.get("token_id") or "").strip()
        outcome_id = str(entry_rules.get("outcome_id") or "").strip()
        strategy_market_id = str(entry_rules.get("market_id") or market_id).strip()
        if token_id and outcome_id and strategy_market_id:
            ref = {"market_id": strategy_market_id, "outcome_id": outcome_id, "token_id": token_id}
            lookup[token_id] = ref
            lookup[outcome_id] = ref
    return lookup


def _watch_session_key_for_event(event_key: str) -> str:
    normalized = str(event_key or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in normalized)
    safe = safe.strip("-")[:180]
    return f"watch-{safe or 'unknown'}"


def _open_sell_size_for_token(open_orders: list[dict[str, Any]], token_id: str) -> float:
    total = 0.0
    for order in open_orders:
        side = str(order.get("side") or "").lower()
        status = str(order.get("status") or "").lower()
        order_token = str(order.get("token_id") or order.get("asset_id") or "").strip()
        if side == "sell" and order_token == token_id and status in {"live", "open", "submitted"}:
            total += _float(order.get("size")) or 0.0
    return total


def _outcome_label_from_strategy(strategy: dict[str, Any], entry_rules: dict[str, Any]) -> str | None:
    for key in ("outcome_label", "team_label", "team", "target_outcome"):
        value = entry_rules.get(key) or strategy.get(key)
        if isinstance(value, str) and value.strip().lower() not in {"buy", "sell", "yes", "no"}:
            return value.strip()
    return None


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
    captured_age_seconds = _age_seconds(snapshot_time)
    stall_seconds = _scoreboard_stall_seconds(live_state, latest=latest)
    pause_context = _scoreboard_pause_context(live_state, latest=latest)
    scoreboard_age_seconds = captured_age_seconds
    stall_suppressed_reason = None
    if stall_seconds is not None and pause_context.get("active"):
        stall_suppressed_reason = pause_context.get("reason") or "clock_stopped_dead_ball"
    elif stall_seconds is not None:
        scoreboard_age_seconds = max(scoreboard_age_seconds or 0.0, stall_seconds)
    return {
        "score_gap": score_gap,
        "scoreboard_age_seconds": scoreboard_age_seconds,
        "scoreboard_captured_age_seconds": captured_age_seconds,
        "scoreboard_stall_seconds": stall_seconds,
        "scoreboard_stall_suppressed_reason": stall_suppressed_reason,
        "scoreboard_pause_context": pause_context,
        "game_status": latest.get("game_status") or latest_payload.get("game_status") or game.get("game_status"),
        "period": latest.get("period") or game.get("period"),
        "game_clock": latest.get("game_clock") or latest.get("clock") or latest_payload.get("game_clock") or game.get("game_clock"),
        "home_score": home_score,
        "away_score": away_score,
    }


def _scoreboard_stall_seconds(live_state: dict[str, Any], *, latest: dict[str, Any]) -> float | None:
    """Return how long the visible game clock has been frozen in the local feed.

    NBA CDN snapshots can keep a fresh poll timestamp while the period/clock stays
    frozen. Trading gates should treat that as stale scoreboard evidence.
    """
    rows = live_state.get("live_snapshots") or live_state.get("snapshots") or []
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    latest_key = _scoreboard_progress_key(latest)
    if latest_key is None:
        return None
    latest_time = _parse_dt(latest.get("captured_at") or latest.get("updated_at") or latest.get("snapshot_time"))
    if latest_time is None:
        return None
    oldest_matching_time = latest_time
    matching_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _scoreboard_progress_key(row) != latest_key:
            break
        row_time = _parse_dt(row.get("captured_at") or row.get("updated_at") or row.get("snapshot_time"))
        if row_time is None:
            continue
        matching_count += 1
        if row_time < oldest_matching_time:
            oldest_matching_time = row_time
    if matching_count < 2:
        return None
    return max(0.0, (latest_time - oldest_matching_time).total_seconds())


def _scoreboard_pause_context(live_state: dict[str, Any], *, latest: dict[str, Any]) -> dict[str, Any]:
    """Detect legitimate dead-ball pauses so clock stalls do not masquerade as stale feeds."""
    latest_key = _scoreboard_progress_key(latest)
    if latest_key is None:
        return {"active": False}
    rows = live_state.get("recent_play_by_play") or live_state.get("play_by_play") or []
    if not isinstance(rows, list):
        return {"active": False}
    latest_snapshot_time = _parse_dt(latest.get("captured_at") or latest.get("updated_at") or latest.get("snapshot_time"))
    pause_action_types = {"timeout", "period", "substitution", "foul", "freethrow", "jumpball", "review", "violation"}
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        if _scoreboard_progress_key(row) != latest_key:
            continue
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        action_type = str(payload.get("actionType") or row.get("action_type") or "").strip().lower()
        description = str(row.get("description") or payload.get("description") or "").strip().lower()
        subtype = str(payload.get("subType") or row.get("sub_type") or "").strip().lower()
        is_pause = (
            action_type in pause_action_types
            or "timeout" in description
            or "period end" in description
            or "official review" in description
        )
        if not is_pause:
            continue
        event_time = _parse_dt(payload.get("timeActual") or payload.get("edited") or row.get("updated_at"))
        event_age_seconds = None
        if event_time is not None and latest_snapshot_time is not None:
            event_age_seconds = max(0.0, (latest_snapshot_time - event_time).total_seconds())
        # NBA timeouts/reviews/substitution dead balls can legitimately hold the visible clock for minutes.
        if event_age_seconds is not None and event_age_seconds > 360:
            return {
                "active": False,
                "reason": "pause_event_too_old",
                "action_type": action_type,
                "description": row.get("description") or payload.get("description"),
                "event_age_seconds": event_age_seconds,
            }
        return {
            "active": True,
            "reason": f"dead_ball_{action_type or subtype or 'pause'}",
            "action_type": action_type,
            "subtype": subtype,
            "description": row.get("description") or payload.get("description"),
            "event_age_seconds": event_age_seconds,
        }
    return {"active": False}


def _scoreboard_progress_key(snapshot: dict[str, Any]) -> tuple[Any, str] | None:
    payload = snapshot.get("payload_json") if isinstance(snapshot.get("payload_json"), dict) else {}
    period = snapshot.get("period") or payload.get("period")
    clock = snapshot.get("game_clock") or snapshot.get("clock") or payload.get("game_clock") or payload.get("clock")
    if period is None or not clock:
        return None
    return period, str(clock)


def _player_status_shocks_from_live_state(
    live_state: dict[str, Any],
    *,
    plan: dict[str, Any] | None = None,
    game: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = live_state.get("recent_play_by_play") or live_state.get("play_by_play") or []
    if not isinstance(rows, list):
        return []

    watched_tokens = _watched_player_tokens(plan or {})
    active_player_names = _active_player_names_from_live_state(live_state)
    shocks: list[dict[str, Any]] = []
    seen: set[tuple[Any, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        text = _normalize_text(" ".join(_iter_text_values(row, include_keys=False)))
        player_name = _player_name_from_pbp_row(row, payload)
        watched_player = _player_is_watched(player_name, text, watched_tokens)
        period = _int(row.get("period") or payload.get("period"))
        tags = _player_status_shock_tags(row=row, payload=payload, text=text, period=period, watched_player=watched_player)
        if not tags:
            continue

        normalized_player = _normalize_text(player_name)
        if normalized_player and normalized_player in active_player_names and any(
            tag in tags for tag in ("ejection", "injury", "sub_out_star")
        ):
            tags = _unique([*tags, "status_conflict", "feed_status_conflict"])

        requires_revision = _shock_requires_revision(tags, watched_player=watched_player)
        key = (row.get("event_index") or row.get("action_id"), normalized_player, "|".join(tags))
        if key in seen:
            continue
        seen.add(key)
        shocks.append(
            {
                "shock_type": "player_status_shock",
                "source": "play_by_play",
                "game_id": live_state.get("game_id") or (game or {}).get("game_id"),
                "event_index": row.get("event_index"),
                "action_id": row.get("action_id"),
                "period": row.get("period") or payload.get("period"),
                "clock": row.get("clock") or payload.get("clock"),
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "description": row.get("description") or payload.get("description") or payload.get("actionDescription"),
                "player_name": player_name,
                "team": row.get("team") or payload.get("teamTricode") or payload.get("teamAbbreviation"),
                "tags": tags,
                "role_weight": 1.0 if watched_player else 0.7,
                "watched_player": watched_player,
                "requires_strategy_plan_revision": requires_revision,
            }
        )
    return shocks


def _player_status_shock_tags(
    *,
    row: dict[str, Any],
    payload: dict[str, Any],
    text: str,
    period: int | None,
    watched_player: bool,
) -> list[str]:
    tags: list[str] = []
    if any(value in text for value in ("eject", "ejected", "ejection", "disqualified")):
        tags.append("ejection")
    if "flagrant" in text:
        if any(value in text for value in ("type 2", "type ii", "flagrant 2", "flagrant two", "flagrant2")):
            tags.append("flagrant_type_2")
        else:
            tags.append("flagrant")
    if "technical" in text:
        tags.append("technical")
    if any(value in text for value in ("injury", "injured", "hurt", "limp", "left game", "will not return")):
        tags.append("injury")
    # Routine rotation substitutions are not player-status shocks. They become
    # revision-worthy only when paired with injury/ejection/foul-threshold
    # evidence, otherwise Q1/Q2 starter rests would freeze live trading.

    foul_count = _first_int(row, payload, ("person_fouls", "personFouls", "personal_fouls", "fouls", "foul_count"))
    if "foul" in text and (
        (foul_count is not None and foul_count >= 4 and (period is None or period < 4))
        or "fourth personal" in text
        or "4th personal" in text
        or "fifth personal" in text
        or "5th personal" in text
    ):
        tags.append("foul_count_threshold")
    return _unique(tags)


def _shock_requires_revision(tags: list[str], *, watched_player: bool) -> bool:
    critical = {
        "ejection",
        "flagrant_type_2",
        "injury",
        "sub_out_star",
        "foul_count_threshold",
        "status_conflict",
        "feed_status_conflict",
    }
    if any(tag in critical for tag in tags):
        return True
    return watched_player and "technical" in tags


def _watched_player_tokens(plan: dict[str, Any]) -> set[str]:
    text = _normalize_text(" ".join(_iter_text_values(plan, include_keys=True)))
    return {token for token in text.split() if len(token) >= 4}


def _player_is_watched(player_name: str | None, text: str, watched_tokens: set[str]) -> bool:
    if not watched_tokens:
        return False
    player_tokens = {token for token in _normalize_text(player_name).split() if len(token) >= 4}
    if player_tokens and player_tokens.intersection(watched_tokens):
        return True
    return any(token in text for token in watched_tokens if token in {"wembanyama", "edwards", "embiid", "anunoby"})


def _player_name_from_pbp_row(row: dict[str, Any], payload: dict[str, Any]) -> str | None:
    value = _first_value(
        row,
        payload,
        (
            "player_name",
            "playerName",
            "playerNameI",
            "person_name",
            "personName",
            "athlete_name",
            "athleteName",
            "displayName",
            "name",
        ),
    )
    return str(value).strip() if value not in (None, "") else None


def _active_player_names_from_live_state(live_state: dict[str, Any]) -> set[str]:
    latest = live_state.get("latest_snapshot") if isinstance(live_state.get("latest_snapshot"), dict) else {}
    payload = latest.get("payload_json") if isinstance(latest.get("payload_json"), dict) else latest
    active_names: set[str] = set()
    for node in _iter_dict_nodes(payload):
        name = _first_value(
            node,
            {},
            ("player_name", "playerName", "playerNameI", "person_name", "personName", "displayName", "name"),
        )
        status = _first_value(node, {}, ("status", "availability", "availability_status", "injury_status"))
        normalized_status = _normalize_text(status)
        if name not in (None, "") and normalized_status in {"active", "available", "ok", "playing"}:
            active_names.add(_normalize_text(name))
    return active_names


def _iter_dict_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dict_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dict_nodes(item)


def _iter_text_values(value: Any, *, include_keys: bool):
    if isinstance(value, dict):
        for key, item in value.items():
            if include_keys:
                yield str(key)
            yield from _iter_text_values(item, include_keys=include_keys)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_text_values(item, include_keys=include_keys)
    elif value not in (None, ""):
        yield str(value)


def _first_value(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for mapping in (primary, secondary):
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return None


def _first_int(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for mapping in (primary, secondary):
        for key in keys:
            value = _int(mapping.get(key))
            if value is not None:
                return value
    return None


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    raw = "".join(character.lower() if str(character).isalnum() else " " for character in str(value or ""))
    return " ".join(raw.split())


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


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
