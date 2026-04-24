from __future__ import annotations

import json
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from app.api.db import cursor_dict, fetchall_dicts, to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.backtests.controller_vnext import (
    DEFAULT_VNEXT_PROFILE,
    DEFAULT_VNEXT_STOP_MAP,
    apply_stop_overlay,
    build_state_lookup,
    decorate_trade_frame_with_vnext_sizing,
)
from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS, build_backtest_result
from app.data.pipelines.daily.nba.analysis.backtests.llm_experiment import (
    _LLMBudgetState,
    _build_family_profiles,
    _load_llm_cache,
    _resolve_openai_client,
    build_team_profile_context_lookup,
)
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    build_master_router_selection_priors,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.backtests.registry import build_strategy_registry
from app.data.pipelines.daily.nba.analysis.backtests.unified_router import build_unified_router_trade_frame
from app.data.pipelines.daily.nba.analysis.consumer_adapters import load_analysis_consumer_bundle
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisConsumerRequest, BacktestRunRequest, RESEARCH_READY_STATUSES
from app.data.pipelines.daily.nba.analysis.bundle_loader import _build_price_snapshots_for_events
from app.data.pipelines.daily.nba.analysis.mart_game_profiles import derive_game_rows, load_analysis_bundle
from app.data.pipelines.daily.nba.analysis.mart_state_panel import build_state_rows_for_side
from app.data.pipelines.daily.nba.sync_postgres import run_nba_live_game_sync
from app.modules.nba.execution.adapter import (
    OPEN_ORDER_STATUSES,
    cancel_live_order,
    create_live_order,
    fetch_account_summary,
    fetch_latest_orderbook_summary,
    list_active_run_signatures,
    list_latest_positions,
    list_run_orders,
    list_run_trades,
    mirror_account_state,
    resolve_minimum_order_size,
    resolve_trading_account,
)
from app.modules.nba.execution.contracts import LiveRunConfig, build_live_order_metadata, utc_now


LIVE_MASTER_ROUTER_KWARGS = {
    "extra_selection_mode": "same_side_top1",
    "min_core_confidence_for_extras": 0.60,
}
LIVE_UNIFIED_KWARGS = {
    "extra_selection_mode": "same_side_top1",
    "min_core_confidence_for_extras": 0.60,
    "weak_confidence_threshold": 0.64,
    "llm_accept_confidence": 0.60,
    "llm_review_min_confidence": 0.46,
    "skip_weak_when_llm_empty": True,
    "skip_weak_when_llm_low_confidence": True,
    "skip_below_review_min_confidence": True,
}
LIVE_UNIFIED_LLM_LANE = {
    "lane_name": "llm_hybrid_vnext_meta_review_v1",
    "lane_group": "live_controller",
    "lane_mode": "llm_freedom",
    "llm_component_scope": "bc_freedom",
    "allowed_roles": ("core", "extra"),
    "prompt_profile": "compact_anchor",
    "reasoning_effort": "low",
    "include_rationale": False,
    "use_confidence_gate": False,
    "max_selected_candidates": 2,
    "max_core_candidates": 1,
    "max_extra_candidates": 1,
    "require_core_for_extra": True,
}


@dataclass(slots=True)
class ControllerContext:
    selection_sample_name: str
    priors: dict[str, dict[str, Any]]
    family_profiles: dict[str, dict[str, Any]]
    historical_team_context_lookup: dict[str, dict[str, Any]]
    llm_client: Any
    llm_cache_store: Any
    llm_budget_state: _LLMBudgetState


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), ensure_ascii=True, indent=2, default=_json_default), encoding="utf-8")


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(to_jsonable(payload), ensure_ascii=True, default=_json_default) + "\n")


def _append_text(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def _empty_trade_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)


def _matchup_label(game: dict[str, Any]) -> str:
    away = str(game.get("away_team_slug") or game.get("away_team_name") or "Away")
    home = str(game.get("home_team_slug") or game.get("home_team_name") or "Home")
    return f"{away} at {home}"


def _clock_label(game: dict[str, Any], latest_state_row: dict[str, Any] | None) -> str:
    if latest_state_row:
        period_label = str(latest_state_row.get("period_label") or "")
        clock = str(latest_state_row.get("clock") or "")
        if period_label or clock:
            return f"{period_label} {clock}".strip()
    return str(game.get("game_status_text") or game.get("game_clock") or "waiting")


def _side_outcome_map(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected_market = bundle.get("selected_market") or {}
    mapping: dict[str, dict[str, Any]] = {}
    for item in selected_market.get("series", []):
        side = str(item.get("side") or "").strip()
        if side in {"home", "away"}:
            mapping[side] = item
    return mapping


def _overlay_live_orderbook_ticks(bundle: dict[str, Any], *, account: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    selected_market = bundle.get("selected_market") or {}
    market_id = str(selected_market.get("market_id") or "")
    if not market_id or not account:
        return {}
    try:
        creds = resolve_trading_account_credentials(account)
    except Exception:
        return {}

    orderbooks_by_side: dict[str, dict[str, Any]] = {}
    for series_item in selected_market.get("series") or []:
        side = str(series_item.get("side") or "").strip()
        token_id = str(series_item.get("token_id") or "")
        if side not in {"home", "away"} or not token_id:
            continue
        try:
            orderbook = fetch_latest_orderbook_summary(
                creds=creds,
                market_id=market_id,
                token_id=token_id,
            )
        except Exception:
            continue
        orderbooks_by_side[side] = orderbook
        best_bid = orderbook.get("best_bid")
        best_ask = orderbook.get("best_ask")
        if best_bid is None or best_ask is None:
            continue
        mid_price = round((float(best_bid) + float(best_ask)) / 2.0, 6)
        timestamp = _parse_datetime(orderbook.get("timestamp")) or utc_now()
        ticks = list(series_item.get("ticks") or [])
        latest_tick = ticks[-1] if ticks else None
        latest_ts = _parse_datetime((latest_tick or {}).get("ts"))
        latest_price = _safe_float((latest_tick or {}).get("price"))
        if latest_ts is not None and latest_ts >= timestamp and latest_price is not None and abs(latest_price - mid_price) < 1e-9:
            continue
        ticks.append(
            {
                "outcome_id": series_item.get("outcome_id"),
                "ts": timestamp,
                "source": "live_orderbook_mid",
                "price": Decimal(str(mid_price)),
                "bid": Decimal(str(float(best_bid))),
                "ask": Decimal(str(float(best_ask))),
                "volume": None,
                "liquidity": None,
            }
        )
        series_item["ticks"] = ticks
    return orderbooks_by_side


def _infer_live_coverage_status(bundle: dict[str, Any]) -> tuple[str, str]:
    feature_snapshot = bundle.get("feature_snapshot") or {}
    coverage_status = str(feature_snapshot.get("coverage_status") or "").strip()

    selected_market = bundle.get("selected_market") or {}
    series = selected_market.get("series") or []
    side_map = _side_outcome_map(bundle)
    has_both_sides = all(side in side_map for side in ("home", "away"))
    has_ticks_for_both = has_both_sides and all(bool((side_map[side].get("ticks") or [])) for side in ("home", "away"))
    play_by_play = bundle.get("play_by_play") or {}
    timed_items = [
        item
        for item in (play_by_play.get("items") or [])
        if _parse_datetime(item.get("time_actual")) is not None
    ]

    inferred_coverage_status = "missing_feature_snapshot"
    inferred_classification = "descriptive_only"
    if has_ticks_for_both and timed_items:
        inferred_coverage_status = "covered_partial"
        inferred_classification = "research_ready"
    elif has_ticks_for_both and series:
        inferred_coverage_status = "pregame_only"
    elif series:
        inferred_coverage_status = "covered_partial"

    if coverage_status in RESEARCH_READY_STATUSES:
        return coverage_status, "research_ready"
    if inferred_coverage_status in RESEARCH_READY_STATUSES:
        return inferred_coverage_status, inferred_classification
    if coverage_status and coverage_status not in {"missing_feature_snapshot", "no_matching_event", "debug_only"}:
        return coverage_status, "research_ready" if coverage_status in RESEARCH_READY_STATUSES else "descriptive_only"
    return inferred_coverage_status, inferred_classification


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None or value == "":
        return {}
    try:
        payload = json.loads(str(value))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _controller_is_unified(name: str) -> bool:
    return str(name or "").strip().startswith("controller_vnext_unified_v1")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_id_for_trade(record: dict[str, Any]) -> str:
    return (
        f"{str(record.get('source_strategy_family') or record.get('strategy_family') or '')}"
        f"|{str(record.get('game_id') or '')}"
        f"|{str(record.get('team_side') or '')}"
        f"|{int(record.get('entry_state_index') or 0)}"
    )


def _extract_stop_price(record: dict[str, Any]) -> float | None:
    metadata = _parse_json_dict(record.get("entry_metadata_json")) or dict(record.get("entry_metadata") or {})
    for key in ("stop_price", "exit_threshold"):
        value = metadata.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _is_recent_signal(record: dict[str, Any], latest_state: dict[str, Any]) -> bool:
    entry_at = _parse_datetime(record.get("entry_at"))
    event_at = _parse_datetime(latest_state.get("event_at"))
    if entry_at is None or event_at is None:
        return False
    return (event_at - entry_at).total_seconds() <= 60.0


def _evaluate_exit_reason(position_state: dict[str, Any], current_state: dict[str, Any]) -> tuple[str | None, bool]:
    family = str(position_state.get("strategy_family") or "")
    metadata = dict(position_state.get("entry_metadata") or {})
    current_price = float(current_state.get("team_price") or 0.0)
    period_label = str(current_state.get("period_label") or "")
    seconds_to_game_end = float(current_state.get("seconds_to_game_end") or 0.0)
    target_price = metadata.get("target_price")
    stop_price = position_state.get("stop_price") if position_state.get("stop_price") is not None else metadata.get("stop_price")
    exit_threshold = metadata.get("exit_threshold")

    if family in {"winner_definition", "inversion"} and exit_threshold is not None and current_price <= float(exit_threshold):
        return "threshold_break", True
    if family in {"underdog_liftoff", "q1_repricing", "q4_clutch"}:
        if target_price is not None and current_price >= float(target_price):
            return "target_hit", False
        if stop_price is not None and current_price <= float(stop_price):
            return "stop_hit", True
    if family == "q1_repricing" and period_label != "Q1":
        return "end_of_q1", False
    if seconds_to_game_end <= 0.0:
        return "game_end", False
    return None, False


def resolve_trading_account_credentials(account: dict[str, Any]) -> Any:
    from app.modules.nba.execution.adapter import build_live_creds

    return build_live_creds(account)


def _load_regular_season_trade_frames() -> tuple[dict[str, pd.DataFrame], str]:
    bundle = load_analysis_consumer_bundle(
        AnalysisConsumerRequest(
            season="2025-26",
            season_phase="regular_season",
            analysis_version="v1_0_1",
        )
    )
    artifacts = bundle.backtest_payload.get("artifacts") or {}
    families = [*DEFAULT_MASTER_ROUTER_CORE_FAMILIES, *DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES]
    trade_frames = {
        family: pd.read_csv(str(artifacts[f"{family}_csv"]))
        for family in families
        if artifacts.get(f"{family}_csv")
    }
    return trade_frames, "regular_season_full_sample"


def _load_team_profile_context_lookup() -> dict[str, dict[str, Any]]:
    with managed_connection() as connection:
        with cursor_dict(connection) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM nba.nba_analysis_team_season_profiles
                WHERE season = %s AND season_phase = %s AND analysis_version = %s
                ORDER BY computed_at DESC;
                """,
                ("2025-26", "regular_season", "v1_0_1"),
            )
            rows = fetchall_dicts(cursor)
    return build_team_profile_context_lookup(pd.DataFrame(rows))


def build_controller_context(run_root: Path) -> ControllerContext:
    trade_frames, selection_sample_name = _load_regular_season_trade_frames()
    selection_result = SimpleNamespace(trade_frames=trade_frames)
    registry = build_strategy_registry()
    priors = build_master_router_selection_priors(
        selection_result,
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    )
    family_profiles = _build_family_profiles(
        selection_result,
        registry=registry,
        strategy_families=[*DEFAULT_MASTER_ROUTER_CORE_FAMILIES, *DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES],
        core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    )
    return ControllerContext(
        selection_sample_name=selection_sample_name,
        priors=priors,
        family_profiles=family_profiles,
        historical_team_context_lookup=_load_team_profile_context_lookup(),
        llm_client=_resolve_openai_client(),
        llm_cache_store=_load_llm_cache(run_root / "llm_router_cache.json"),
        llm_budget_state=_LLMBudgetState(spent_usd=0.0),
    )


class LiveRunWorker:
    def __init__(self, config: LiveRunConfig) -> None:
        self.config = config
        self.run_root = config.run_root()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.controller_context = build_controller_context(self.run_root)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.status = "created"
        self.entries_enabled = bool(config.entries_enabled)
        self.last_error: str | None = None
        self.account: dict[str, Any] | None = None
        self.run_started_at: datetime | None = None
        self.last_heartbeat_at: datetime | None = None
        self.game_cards: dict[str, dict[str, Any]] = {}
        self.active_orders: dict[str, dict[str, Any]] = {}
        self.active_positions: dict[str, dict[str, Any]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=400)
        self.fill_metrics: list[dict[str, Any]] = []
        self._seen_trade_ids: set[str] = set()
        self.cycle_count = 0
        self.last_cycle_started_at: datetime | None = None
        self.last_cycle_completed_at: datetime | None = None
        self.last_cycle_duration_seconds: float | None = None
        self.last_successful_cycle_at: datetime | None = None
        self.last_traceback: str | None = None
        self._load_recovery_snapshot()
        _write_json(self.run_root / "run_config.json", self.config.model_dump())

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"live-run-{self.config.run_id}",
                daemon=True,
            )
            self._thread.start()

    def request_stop(self) -> None:
        self._stop_event.set()

    def pause_entries(self) -> None:
        with self._lock:
            self.entries_enabled = False
        self._record_event("warn", "Entries paused", "Operator paused new entries.")
        self._persist_recovery_snapshot()

    def resume_entries(self) -> None:
        with self._lock:
            self.entries_enabled = True
        self._record_event("info", "Entries resumed", "Operator resumed new entries.")
        self._persist_recovery_snapshot()

    def summary_snapshot(self) -> dict[str, Any]:
        account_summary = None
        if self.account:
            with managed_connection() as connection:
                account_summary = fetch_account_summary(connection, account_id=str(self.account["account_id"]))
        current_bankroll = float(account_summary.get("equity_usd") or 0.0) if account_summary else None
        starting_bankroll = float(account_summary.get("cash_usd") or current_bankroll or 0.0) if account_summary else None
        with self._lock:
            return {
                "run_id": self.config.run_id,
                "status": self.status,
                "controller_name": self.config.controller_name,
                "fallback_controller_name": self.config.fallback_controller_name,
                "execution_profile_version": self.config.execution_profile_version,
                "active_games": len(self.config.game_ids),
                "open_orders": len(self.active_orders),
                "open_positions": len(self.active_positions),
                "entries_enabled": self.entries_enabled,
                "dry_run": self.config.dry_run,
                "current_bankroll": current_bankroll,
                "starting_bankroll": starting_bankroll,
                "drawdown_pct": None,
                "drawdown_amount": None,
                "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
                "last_successful_cycle_at": self.last_successful_cycle_at.isoformat() if self.last_successful_cycle_at else None,
                "last_cycle_started_at": self.last_cycle_started_at.isoformat() if self.last_cycle_started_at else None,
                "last_cycle_completed_at": self.last_cycle_completed_at.isoformat() if self.last_cycle_completed_at else None,
                "last_cycle_duration_seconds": self.last_cycle_duration_seconds,
                "cycle_count": self.cycle_count,
                "last_error": self.last_error,
                "last_traceback": self.last_traceback,
                "run_root": str(self.run_root),
                "log_paths": {
                    "heartbeat": str(self.run_root / "heartbeat.json"),
                    "decisions": str(self.run_root / "decisions.jsonl"),
                    "controller_trace": str(self.run_root / "controller_trace.jsonl"),
                    "events": str(self.run_root / "executor_events.jsonl"),
                    "runtime_log": str(self.run_root / "runtime.log"),
                    "recovery_snapshot": str(self.run_root / "recovery_snapshot.json"),
                    "last_error": str(self.run_root / "last_error.txt"),
                },
                "games": list(self.game_cards.values()),
            }

    def game_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.game_cards.values())

    def order_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "game": value.get("matchup"),
                    "market": value.get("market_question") or value.get("market_id"),
                    "type": value.get("type"),
                    "price": f"${float(value.get('price') or 0.0):.2f}" if value.get("price") is not None else "-",
                    "qty": f"{float(value.get('size') or 0.0):.2f} shares",
                    "status": value.get("status"),
                    "age": value.get("age"),
                }
                for value in sorted(self.active_orders.values(), key=lambda item: item.get("submitted_at") or "", reverse=True)
            ]

    def position_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "game": value.get("matchup"),
                    "market": value.get("market_question") or value.get("market_id"),
                    "side": value.get("side"),
                    "size": f"{float(value.get('size') or 0.0):.2f} shares",
                    "entry": f"${float(value.get('entry_price') or 0.0):.2f}",
                    "mark": f"${float(value.get('mark_price') or 0.0):.2f}" if value.get("mark_price") is not None else "-",
                    "pnl": value.get("pnl_text") or "-",
                    "status": value.get("status") or "open",
                }
                for value in sorted(self.active_positions.values(), key=lambda item: item.get("entry_at") or "", reverse=True)
            ]

    def event_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)[-50:]

    def fills_summary(self) -> list[dict[str, Any]]:
        with self._lock:
            attempted = len([event for event in self.events if event.get("title") in {"Entry submitted", "Exit submitted", "Stop submitted"}])
            fill_rate = (len(self.fill_metrics) / attempted) * 100.0 if attempted else 0.0
            avg_slippage = None
            median_delay = None
            if self.fill_metrics:
                avg_slippage = sum(abs(float(item.get("slippage_vs_signal_cents") or 0.0)) for item in self.fill_metrics) / len(self.fill_metrics)
                delays = sorted(float(item.get("fill_delay_seconds") or 0.0) for item in self.fill_metrics)
                median_delay = delays[len(delays) // 2]
            stop_hits = sum(1 for item in self.fill_metrics if bool(item.get("stop_triggered_flag")))
            return [
                {"label": "Fill rate", "value": round(fill_rate, 2), "suffix": "%"},
                {"label": "Avg slippage", "value": round(avg_slippage, 3) if avg_slippage is not None else None, "suffix": "c", "warn": bool(avg_slippage and avg_slippage > 1.0)},
                {"label": "Median delay", "value": round(median_delay, 2) if median_delay is not None else None, "suffix": "s"},
                {"label": "Stop hits", "value": stop_hits, "suffix": ""},
            ]

    def _run_loop(self) -> None:
        self.status = "starting"
        self.run_started_at = utc_now()
        self._record_event("info", "Live run starting", f"Booting live run {self.config.run_id}.")
        while not self._stop_event.is_set():
            cycle_started = utc_now()
            self.last_cycle_started_at = cycle_started
            self.cycle_count += 1
            self._write_runtime_log(f"[{cycle_started.isoformat()}] cycle_start index={self.cycle_count} entries_enabled={self.entries_enabled}")
            try:
                with managed_connection() as connection:
                    self.account = resolve_trading_account(connection, account_id=self.config.account_id)
                    mirror_account_state(connection, account=self.account)
                    snapshot = self._build_cycle_snapshot(connection)
                    self._reconcile_runtime_state(connection)
                    self._process_open_positions(connection, snapshot=snapshot)
                    self._process_open_orders(connection, snapshot=snapshot)
                    if self.entries_enabled:
                        self._process_new_entries(connection, snapshot=snapshot)
                    self._refresh_game_cards(snapshot)
                    mirror_account_state(connection, account=self.account)
                self.status = "running"
                self.last_error = None
                self.last_traceback = None
                self.last_successful_cycle_at = utc_now()
            except Exception as exc:  # noqa: BLE001
                self.status = "error"
                self.last_error = str(exc)
                self.last_traceback = traceback.format_exc()
                self._write_error_trace(self.last_traceback)
                self._record_event("error", "Cycle failed", str(exc), details={"traceback_file": str(self.run_root / "last_error.txt")})
            finally:
                cycle_finished = utc_now()
                self.last_cycle_completed_at = cycle_finished
                self.last_cycle_duration_seconds = max(0.0, (cycle_finished - cycle_started).total_seconds())
                self._write_runtime_log(
                    f"[{cycle_finished.isoformat()}] cycle_end index={self.cycle_count} status={self.status} "
                    f"duration_s={self.last_cycle_duration_seconds:.3f} error={self.last_error or ''}"
                )
                self.last_heartbeat_at = cycle_started
                self._write_heartbeat()
                self._persist_recovery_snapshot()

            if self._all_games_finished():
                self.status = "completed"
                self._record_event("info", "Live run completed", "All configured games are final.")
                self._write_heartbeat()
                self._persist_recovery_snapshot()
                return

            sleep_seconds = self.config.poll_interval_idle_sec
            if any(str(card.get("state_label") or "").lower() not in {"pregame", "final", "skip"} for card in self.game_cards.values()):
                sleep_seconds = self.config.poll_interval_live_sec
            if self._stop_event.wait(sleep_seconds):
                break

        self.status = "stopped"
        self._record_event("warn", "Live run stopped", "Operator stop requested.")
        self._write_heartbeat()
        self._persist_recovery_snapshot()

    def _build_cycle_snapshot(self, connection: Any) -> dict[str, Any]:
        state_rows: list[dict[str, Any]] = []
        bundles: dict[str, dict[str, Any]] = {}
        diagnostics_by_game: dict[str, dict[str, Any]] = {}

        for game_id in self.config.game_ids:
            run_nba_live_game_sync(game_id=str(game_id), include_live_snapshots=True, include_play_by_play=True)
            bundle = load_analysis_bundle(connection, game_id=str(game_id))
            if bundle is None:
                diagnostics_by_game[str(game_id)] = {"error": "game_not_found"}
                continue
            live_orderbooks = _overlay_live_orderbook_ticks(bundle, account=self.account)
            selected_market = bundle.get("selected_market") or {}
            series_by_outcome = {
                str(item.get("outcome_id")): list(item.get("ticks") or [])
                for item in selected_market.get("series") or []
                if item.get("outcome_id")
            }
            if bundle.get("play_by_play") and series_by_outcome:
                _build_price_snapshots_for_events(
                    bundle["play_by_play"].get("items") or [],
                    series_by_outcome,
                )
            coverage_status, classification = _infer_live_coverage_status(bundle)
            universe_row = pd.Series(
                {
                    "season": str(bundle["game"].get("season") or "2025-26"),
                    "season_phase": str(bundle["game"].get("season_phase") or "playoffs"),
                    "coverage_status": coverage_status,
                    "classification": classification,
                    "research_ready_flag": classification == "research_ready",
                }
            )
            game_rows, per_game_state_rows, diagnostics = derive_game_rows(
                universe_row=universe_row,
                bundle=bundle,
                analysis_version="v1_0_1",
                computed_at=utc_now(),
                build_state_rows_for_side=build_state_rows_for_side,
            )
            bundle["derived_game_rows"] = game_rows
            bundle["live_orderbooks"] = live_orderbooks
            bundles[str(game_id)] = bundle
            diagnostics_by_game[str(game_id)] = diagnostics
            state_rows.extend(per_game_state_rows)

        state_df = pd.DataFrame(state_rows)
        if state_df.empty:
            empty_result = SimpleNamespace(
                state_df=state_df,
                trade_frames={family: _empty_trade_frame() for family in build_strategy_registry()},
            )
            return {
                "bundles": bundles,
                "diagnostics_by_game": diagnostics_by_game,
                "state_df": state_df,
                "sample_result": empty_result,
                "deterministic_trades": _empty_trade_frame(),
                "deterministic_decisions": pd.DataFrame(),
                "unified_trades": _empty_trade_frame(),
                "unified_decisions": pd.DataFrame(),
            }

        request = BacktestRunRequest(
            season="2025-26",
            season_phase="playoffs",
            portfolio_game_limit=None,
            slippage_cents=0,
            llm_enable=True,
            llm_model="gpt-5.4",
            llm_max_budget_usd=2.0,
        )
        sample_result = build_backtest_result(state_df, request)
        deterministic_trades, deterministic_decisions = build_master_router_trade_frame(
            sample_result,
            sample_name="live_current",
            selection_sample_name=self.controller_context.selection_sample_name,
            priors=self.controller_context.priors,
            core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
            extra_strategy_families=DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
            **LIVE_MASTER_ROUTER_KWARGS,
        )
        unified_trades, unified_decisions, _ = build_unified_router_trade_frame(
            sample_result,
            sample_name="live_current",
            selection_sample_name=self.controller_context.selection_sample_name,
            priors=self.controller_context.priors,
            family_profiles=self.controller_context.family_profiles,
            core_strategy_families=DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
            extra_strategy_families=DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
            llm_lane=LIVE_UNIFIED_LLM_LANE,
            request=request,
            client=self.controller_context.llm_client,
            budget_state=self.controller_context.llm_budget_state,
            cache_store=self.controller_context.llm_cache_store,
            historical_team_context_lookup=self.controller_context.historical_team_context_lookup,
            **LIVE_UNIFIED_KWARGS,
        )
        state_lookup = build_state_lookup(state_df)
        deterministic_trades = decorate_trade_frame_with_vnext_sizing(
            apply_stop_overlay(deterministic_trades, state_lookup=state_lookup, stop_map=DEFAULT_VNEXT_STOP_MAP),
            profile=DEFAULT_VNEXT_PROFILE,
        )
        unified_trades = decorate_trade_frame_with_vnext_sizing(
            apply_stop_overlay(unified_trades, state_lookup=state_lookup, stop_map=DEFAULT_VNEXT_STOP_MAP),
            profile=DEFAULT_VNEXT_PROFILE,
        )
        self._append_controller_trace(
            "cycle_snapshot",
            payload={
                "game_ids": list(self.config.game_ids),
                "state_row_count": int(len(state_df)),
                "deterministic_trade_count": int(len(deterministic_trades)),
                "deterministic_decision_count": int(len(deterministic_decisions)),
                "unified_trade_count": int(len(unified_trades)),
                "unified_decision_count": int(len(unified_decisions)),
                "diagnostics_by_game": diagnostics_by_game,
            },
        )
        self._persist_decisions(
            (unified_decisions if _controller_is_unified(self.config.controller_name) else deterministic_decisions).to_dict(orient="records")
        )
        return {
            "bundles": bundles,
            "diagnostics_by_game": diagnostics_by_game,
            "state_df": state_df,
            "sample_result": sample_result,
            "deterministic_trades": deterministic_trades,
            "deterministic_decisions": deterministic_decisions,
            "unified_trades": unified_trades,
            "unified_decisions": unified_decisions,
        }

    def _refresh_game_cards(self, snapshot: dict[str, Any]) -> None:
        cards: dict[str, dict[str, Any]] = {}
        trade_frame = snapshot["unified_trades"] if _controller_is_unified(self.config.controller_name) else snapshot["deterministic_trades"]
        decisions_frame = snapshot["unified_decisions"] if _controller_is_unified(self.config.controller_name) else snapshot["deterministic_decisions"]
        decision_lookup = {
            str(row["game_id"]): row
            for row in decisions_frame.to_dict(orient="records")
        } if decisions_frame is not None and not decisions_frame.empty else {}
        trade_lookup: dict[str, list[dict[str, Any]]] = {}
        if trade_frame is not None and not trade_frame.empty:
            for record in trade_frame.to_dict(orient="records"):
                trade_lookup.setdefault(str(record.get("game_id") or ""), []).append(record)

        for game_id in self.config.game_ids:
            bundle = snapshot["bundles"].get(str(game_id))
            diagnostics = snapshot["diagnostics_by_game"].get(str(game_id)) or {}
            if not bundle:
                cards[str(game_id)] = {
                    "game_id": str(game_id),
                    "matchup": str(game_id),
                    "clock": "unavailable",
                    "controller_name": self.config.controller_name,
                    "strategy_family": "skip",
                    "selected_action": "wait",
                    "selected_confidence": None,
                    "state_label": "error",
                    "note": diagnostics.get("error") or "Game bundle unavailable.",
                    "open_order_count": 0,
                    "open_position_count": 0,
                    "best_bid": None,
                    "best_ask": None,
                    "stop_price": None,
                    "realized_pnl": None,
                    "fill_state": "none",
                }
                continue

            game = bundle["game"]
            decision = decision_lookup.get(str(game_id)) or {}
            trades = trade_lookup.get(str(game_id)) or []
            selected_trade = trades[0] if trades else None
            latest_state_row = None
            if snapshot["state_df"] is not None and not snapshot["state_df"].empty:
                side = str((selected_trade or {}).get("team_side") or decision.get("selected_team_side") or "")
                if side:
                    latest_state_row = self._latest_state_row(snapshot["state_df"], game_id=str(game_id), team_side=side)
            orderbook = None
            if selected_trade is not None and self.account:
                outcome_map = _side_outcome_map(bundle)
                outcome_meta = outcome_map.get(str(selected_trade.get("team_side") or ""))
                if outcome_meta is not None:
                    try:
                        orderbook = fetch_latest_orderbook_summary(
                            creds=resolve_trading_account_credentials(self.account),
                            market_id=str(bundle.get("selected_market", {}).get("market_id") or ""),
                            token_id=str(outcome_meta.get("token_id") or ""),
                        )
                    except Exception:
                        orderbook = None
            active_orders = [item for item in self.active_orders.values() if str(item.get("game_id")) == str(game_id)]
            active_positions = [item for item in self.active_positions.values() if str(item.get("game_id")) == str(game_id)]
            fill_state = "filled" if active_positions else "working" if active_orders else "none"
            stop_price = active_positions[0].get("stop_price") if active_positions else _extract_stop_price(selected_trade or {})
            candidate_is_stale = bool(
                selected_trade is not None
                and latest_state_row is not None
                and int(latest_state_row.get("state_index") or -1) > int((selected_trade or {}).get("entry_state_index") or -1)
                and not _is_recent_signal(selected_trade or {}, latest_state_row)
            )

            state_label = "pregame" if int(game.get("game_status") or 0) == 1 else "monitoring"
            if int(game.get("game_status") or 0) == 3:
                state_label = "final"
            if decision.get("final_source") == "skip_weak_game" or decision.get("selected_core_family") is None:
                state_label = "skip" if state_label != "final" else state_label
            if candidate_is_stale and state_label not in {"final", "pregame"}:
                state_label = "stale signal"
            if active_orders:
                state_label = "entry queued"
            if active_positions:
                state_label = "open position"

            card_payload = {
                "game_id": str(game_id),
                "matchup": _matchup_label(game),
                "clock": _clock_label(game, latest_state_row),
                "controller_name": self.config.controller_name,
                "strategy_family": str((selected_trade or {}).get("source_strategy_family") or decision.get("selected_core_family") or "skip"),
                "selected_action": (
                    "hold"
                    if active_positions
                    else "buy limit"
                    if selected_trade is not None and not candidate_is_stale
                    else "wait"
                ),
                "selected_confidence": decision.get("selected_confidence") or (selected_trade or {}).get("unified_router_default_confidence"),
                "state_label": state_label,
                "note": (
                    "entry_signal_stale"
                    if candidate_is_stale and not active_orders and not active_positions
                    else self._latest_event_message_for_game(str(game_id))
                    or str(decision.get("final_selection_reason") or diagnostics.get("coverage_status") or "Monitoring live state.")
                ),
                "open_order_count": len(active_orders),
                "open_position_count": len(active_positions),
                "best_bid": (orderbook or {}).get("best_bid"),
                "best_ask": (orderbook or {}).get("best_ask"),
                "stop_price": stop_price,
                "realized_pnl": active_positions[0].get("realized_pnl") if active_positions else None,
                "fill_state": fill_state,
            }
            cards[str(game_id)] = card_payload
            self._append_controller_trace(
                "game_card",
                game_id=str(game_id),
                payload={
                    "matchup": _matchup_label(game),
                    "coverage_status": diagnostics.get("coverage_status"),
                    "latest_state": self._state_trace_row(latest_state_row),
                    "decision": self._decision_trace_row(decision),
                    "selected_trade": self._trade_trace_row(selected_trade),
                    "candidate_is_stale": candidate_is_stale,
                    "orderbook": {
                        "best_bid": (orderbook or {}).get("best_bid"),
                        "best_ask": (orderbook or {}).get("best_ask"),
                        "spread_cents": (orderbook or {}).get("spread_cents"),
                    },
                    "active_order_count": len(active_orders),
                    "active_position_count": len(active_positions),
                    "card": card_payload,
                },
            )
        with self._lock:
            self.game_cards = cards

    def _reconcile_runtime_state(self, connection: Any) -> None:
        run_orders = list_run_orders(connection, run_id=self.config.run_id)
        run_trades = list_run_trades(connection, run_id=self.config.run_id)
        latest_positions = list_latest_positions(connection, account_id=str(self.account["account_id"])) if self.account else []
        trade_by_order: dict[str, list[dict[str, Any]]] = {}
        for trade in run_trades:
            trade_by_order.setdefault(str(trade.get("order_id") or ""), []).append(trade)

        stale_order_keys: list[str] = []
        with self._lock:
            for signal_id, order_state in self.active_orders.items():
                order_row = next((row for row in run_orders if str(row.get("order_id")) == str(order_state.get("order_id"))), None)
                if order_row is None:
                    stale_order_keys.append(signal_id)
                    continue
                order_state["status"] = str(order_row.get("status") or order_state.get("status") or "")
                order_state["age"] = _format_age(order_state.get("submitted_at"))
                if str(order_state["status"]).lower() not in OPEN_ORDER_STATUSES:
                    stale_order_keys.append(signal_id)
                for trade in trade_by_order.get(str(order_state.get("order_id") or ""), []):
                    trade_id = str(trade.get("trade_id") or "")
                    if not trade_id or trade_id in self._seen_trade_ids:
                        continue
                    self._seen_trade_ids.add(trade_id)
                    self._handle_trade_fill(order_state, trade)
                    stale_order_keys.append(signal_id)
            for key in stale_order_keys:
                self.active_orders.pop(key, None)

            for position_state in self.active_positions.values():
                latest_row = next(
                    (row for row in latest_positions if str(row.get("outcome_id")) == str(position_state.get("outcome_id"))),
                    None,
                )
                if latest_row is None:
                    continue
                position_state["mark_price"] = latest_row.get("current_price")
                if latest_row.get("current_price") is not None:
                    entry_price = float(position_state.get("entry_price") or 0.0)
                    size = float(position_state.get("size") or 0.0)
                    pnl_amount = (float(latest_row.get("current_price") or 0.0) - entry_price) * size
                    position_state["pnl_text"] = f"{pnl_amount:+.2f}"

    def _process_open_orders(self, connection: Any, *, snapshot: dict[str, Any]) -> None:
        now = utc_now()
        active_orders = list(self.active_orders.values())
        for order_state in active_orders:
            submitted_at = _parse_datetime(order_state.get("submitted_at")) or now
            age = max(0.0, (now - submitted_at).total_seconds())
            pending_action = str(order_state.get("pending_action") or "")
            if pending_action == "entry" and age >= 15.0:
                cancel_live_order(connection, account=self.account, order_id=str(order_state["order_id"]), dry_run=self.config.dry_run, reason="entry_timeout")
                self._record_event("warn", "Entry canceled", f"Canceled stale entry after {age:.1f}s.", game_id=str(order_state.get("game_id") or ""))
            elif pending_action == "exit" and age >= 10.0 and not bool(order_state.get("market_retry_done")):
                cancel_live_order(connection, account=self.account, order_id=str(order_state["order_id"]), dry_run=self.config.dry_run, reason="exit_timeout")
                self._record_event("warn", "Exit retry", "Limit exit timed out; retrying with aggressive limit.", game_id=str(order_state.get("game_id") or ""))
                order_state["market_retry_done"] = True
                self._submit_exit_order(
                    connection,
                    position_state=order_state,
                    order_policy="market_emulated_aggressive_limit",
                    stop_triggered_flag=bool(order_state.get("stop_triggered_flag")),
                    aggressive=True,
                )

    def _process_open_positions(self, connection: Any, *, snapshot: dict[str, Any]) -> None:
        active_positions = list(self.active_positions.values())
        for position_state in active_positions:
            current_state = self._latest_state_row(
                snapshot["state_df"],
                game_id=str(position_state.get("game_id") or ""),
                team_side=str(position_state.get("team_side") or ""),
            )
            if current_state is None:
                self._append_controller_trace(
                    "exit_gate",
                    game_id=str(position_state.get("game_id") or ""),
                    payload={
                        "result": "hold",
                        "reason": "no_current_state",
                        "position": self._position_trace_row(position_state),
                    },
                )
                continue
            exit_reason, stop_triggered = _evaluate_exit_reason(position_state, current_state)
            if not exit_reason:
                self._append_controller_trace(
                    "exit_gate",
                    game_id=str(position_state.get("game_id") or ""),
                    payload={
                        "result": "hold",
                        "reason": "no_exit_signal",
                        "position": self._position_trace_row(position_state),
                        "current_state": self._state_trace_row(current_state),
                    },
                )
                continue
            self._append_controller_trace(
                "exit_gate",
                game_id=str(position_state.get("game_id") or ""),
                payload={
                    "result": "submit_exit",
                    "reason": exit_reason,
                    "stop_triggered": stop_triggered,
                    "position": self._position_trace_row(position_state),
                    "current_state": self._state_trace_row(current_state),
                },
            )
            self._submit_exit_order(
                connection,
                position_state={**position_state, "exit_reason": exit_reason},
                order_policy="market_emulated_aggressive_limit" if stop_triggered else "limit_then_market_emulated",
                stop_triggered_flag=stop_triggered,
                aggressive=stop_triggered,
            )

    def _process_new_entries(self, connection: Any, *, snapshot: dict[str, Any]) -> None:
        trade_frame = snapshot["unified_trades"] if _controller_is_unified(self.config.controller_name) else snapshot["deterministic_trades"]
        decisions_frame = snapshot["unified_decisions"] if _controller_is_unified(self.config.controller_name) else snapshot["deterministic_decisions"]
        if trade_frame is None or trade_frame.empty:
            return
        active_signatures = list_active_run_signatures(
            connection,
            run_id=self.config.run_id,
            execution_profile_version=self.config.execution_profile_version,
        )
        active_position_signatures = {
            (str(value.get("game_id") or ""), str(value.get("outcome_id") or ""), str(value.get("side") or "buy"))
            for value in self.active_positions.values()
        }
        decision_lookup = {
            str(row.get("game_id") or ""): row
            for row in decisions_frame.to_dict(orient="records")
        } if decisions_frame is not None and not decisions_frame.empty else {}

        for record in trade_frame.to_dict(orient="records"):
            game_id = str(record.get("game_id") or "")
            team_side = str(record.get("team_side") or "")
            signal_id = _signal_id_for_trade(record)
            if signal_id in self.active_orders or signal_id in self.active_positions:
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "already_active_signal",
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            bundle = snapshot["bundles"].get(game_id)
            if not bundle:
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "bundle_unavailable",
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            latest_state = self._latest_state_row(snapshot["state_df"], game_id=game_id, team_side=team_side)
            if latest_state is None:
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "latest_state_unavailable",
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            entry_state_index = int(record.get("entry_state_index") or -1)
            latest_state_index = int(latest_state.get("state_index") or -1)
            if latest_state_index < entry_state_index:
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "signal_in_future",
                        "trade": self._trade_trace_row(record),
                        "latest_state": self._state_trace_row(latest_state),
                    },
                )
                continue
            if latest_state_index > entry_state_index and not _is_recent_signal(record, latest_state):
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "stale_signal",
                        "trade": self._trade_trace_row(record),
                        "latest_state": self._state_trace_row(latest_state),
                    },
                )
                continue
            outcome_map = _side_outcome_map(bundle)
            outcome_meta = outcome_map.get(team_side)
            if outcome_meta is None:
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "outcome_meta_unavailable",
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            signature = (game_id, str(outcome_meta.get("outcome_id") or ""), "buy")
            if signature in active_signatures or signature in active_position_signatures:
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "active_signature_exists",
                        "signature": signature,
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            if any(str(value.get("game_id") or "") == game_id for value in self.active_positions.values()):
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "game_position_exists",
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            orderbook = fetch_latest_orderbook_summary(
                creds=resolve_trading_account_credentials(self.account),
                market_id=str(bundle.get("selected_market", {}).get("market_id") or ""),
                token_id=str(outcome_meta.get("token_id") or ""),
            )
            best_bid = orderbook.get("best_bid")
            best_ask = orderbook.get("best_ask")
            spread_cents = orderbook.get("spread_cents")
            if best_bid is None or best_ask is None:
                self._record_event(
                    "warn",
                    "Entry skipped",
                    "Orderbook unavailable for entry.",
                    game_id=game_id,
                    details={
                        "signal_id": signal_id,
                        "team_side": team_side,
                        "market_id": str(bundle.get("selected_market", {}).get("market_id") or ""),
                        "outcome_id": str(outcome_meta.get("outcome_id") or ""),
                    },
                )
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "orderbook_unavailable",
                        "trade": self._trade_trace_row(record),
                    },
                )
                continue
            if spread_cents is not None and float(spread_cents) > 2.0:
                self._record_event(
                    "warn",
                    "Entry skipped",
                    f"Spread {float(spread_cents):.2f}c exceeded threshold.",
                    game_id=game_id,
                    details={
                        "signal_id": signal_id,
                        "team_side": team_side,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "spread_cents": spread_cents,
                    },
                )
                self._append_controller_trace(
                    "entry_gate",
                    game_id=game_id,
                    payload={
                        "signal_id": signal_id,
                        "result": "skip",
                        "reason": "spread_too_wide",
                        "trade": self._trade_trace_row(record),
                        "orderbook": {
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "spread_cents": spread_cents,
                        },
                    },
                )
                continue
            entry_price = float(best_ask)
            size = resolve_minimum_order_size(entry_price)
            metadata = build_live_order_metadata(
                config=self.config,
                controller_name=self.config.controller_name,
                controller_source="primary" if _controller_is_unified(self.config.controller_name) else "deterministic",
                game_id=game_id,
                market_id=str(bundle.get("selected_market", {}).get("market_id") or ""),
                outcome_id=str(outcome_meta.get("outcome_id") or ""),
                strategy_family=str(record.get("source_strategy_family") or record.get("strategy_family") or ""),
                signal_id=signal_id,
                signal_price=float(record.get("entry_price") or latest_state.get("team_price") or entry_price),
                signal_timestamp=record.get("entry_at"),
                entry_reason=str(decision_lookup.get(game_id, {}).get("final_selection_reason") or "live_signal"),
                stop_price=_extract_stop_price(record),
                order_policy="limit_best_ask",
                extra={
                    "team_side": team_side,
                    "team_slug": record.get("team_slug"),
                    "opponent_team_slug": record.get("opponent_team_slug"),
                    "entry_metadata": _parse_json_dict(record.get("entry_metadata_json")),
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread_cents": spread_cents,
                },
            )
            placed = create_live_order(
                connection,
                account=self.account,
                market_id=str(bundle.get("selected_market", {}).get("market_id") or ""),
                outcome_id=str(outcome_meta.get("outcome_id") or ""),
                token_id=str(outcome_meta.get("token_id") or ""),
                side="buy",
                size=size,
                price=entry_price,
                order_type="limit",
                metadata_json=metadata,
                dry_run=self.config.dry_run,
            )
            self._append_controller_trace(
                "entry_gate",
                game_id=game_id,
                payload={
                    "signal_id": signal_id,
                    "result": "submit_entry",
                    "trade": self._trade_trace_row(record),
                    "latest_state": self._state_trace_row(latest_state),
                    "orderbook": {
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "spread_cents": spread_cents,
                    },
                    "submitted": {
                        "price": entry_price,
                        "size": size,
                        "order_policy": "limit_best_ask",
                        "order_id": placed.get("order_id"),
                        "external_order_id": placed.get("external_order_id"),
                        "dry_run": self.config.dry_run,
                    },
                },
            )
            with self._lock:
                self.active_orders[signal_id] = {
                    "signal_id": signal_id,
                    "order_id": placed["order_id"],
                    "external_order_id": placed.get("external_order_id"),
                    "status": placed["status"],
                    "pending_action": "entry",
                    "submitted_at": utc_now().isoformat(),
                    "game_id": game_id,
                    "matchup": _matchup_label(bundle["game"]),
                    "market_id": str(bundle.get("selected_market", {}).get("market_id") or ""),
                    "market_question": bundle.get("selected_market", {}).get("question"),
                    "outcome_id": str(outcome_meta.get("outcome_id") or ""),
                    "token_id": str(outcome_meta.get("token_id") or ""),
                    "side": "buy",
                    "team_side": team_side,
                    "strategy_family": str(record.get("source_strategy_family") or record.get("strategy_family") or ""),
                    "size": size,
                    "price": entry_price,
                    "signal_price": float(record.get("entry_price") or entry_price),
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "stop_price": _extract_stop_price(record),
                    "entry_metadata": _parse_json_dict(record.get("entry_metadata_json")),
                    "type": "limit buy",
                    "age": "00:00",
                }
            self._record_event(
                "info",
                "Entry submitted",
                f"{_matchup_label(bundle['game'])} {metadata['strategy_family']} at {entry_price:.3f}.",
                game_id=game_id,
                details={
                    "signal_id": signal_id,
                    "team_side": team_side,
                    "market_id": str(bundle.get("selected_market", {}).get("market_id") or ""),
                    "outcome_id": str(outcome_meta.get("outcome_id") or ""),
                    "token_id": str(outcome_meta.get("token_id") or ""),
                    "size": size,
                    "price": entry_price,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread_cents": spread_cents,
                    "order_policy": "limit_best_ask",
                },
            )

    def _submit_exit_order(
        self,
        connection: Any,
        *,
        position_state: dict[str, Any],
        order_policy: str,
        stop_triggered_flag: bool,
        aggressive: bool,
    ) -> None:
        signal_id = str(position_state.get("signal_id") or "")
        if signal_id in self.active_orders:
            return
        orderbook = fetch_latest_orderbook_summary(
            creds=resolve_trading_account_credentials(self.account),
            market_id=str(position_state.get("market_id") or ""),
            token_id=str(position_state.get("token_id") or ""),
        )
        best_bid = orderbook.get("best_bid")
        if best_bid is None:
            self._record_event(
                "error",
                "Exit pricing unavailable",
                "Could not price exit order from current orderbook.",
                game_id=str(position_state.get("game_id") or ""),
                details={
                    "signal_id": signal_id,
                    "market_id": str(position_state.get("market_id") or ""),
                    "outcome_id": str(position_state.get("outcome_id") or ""),
                    "team_side": position_state.get("team_side"),
                    "order_policy": order_policy,
                },
            )
            return
        price = max(0.01, float(best_bid))
        metadata = build_live_order_metadata(
            config=self.config,
            controller_name=self.config.controller_name,
            controller_source="exit_manager",
            game_id=str(position_state.get("game_id") or ""),
            market_id=str(position_state.get("market_id") or ""),
            outcome_id=str(position_state.get("outcome_id") or ""),
            strategy_family=str(position_state.get("strategy_family") or ""),
            signal_id=signal_id,
            signal_price=float(position_state.get("signal_price") or position_state.get("entry_price") or 0.0),
            signal_timestamp=position_state.get("entry_at"),
            entry_reason=str(position_state.get("exit_reason") or "live_exit"),
            stop_price=float(position_state.get("stop_price") or 0.0) if position_state.get("stop_price") is not None else None,
            order_policy=order_policy,
            extra={"team_side": position_state.get("team_side"), "stop_triggered_flag": stop_triggered_flag},
        )
        placed = create_live_order(
            connection,
            account=self.account,
            market_id=str(position_state.get("market_id") or ""),
            outcome_id=str(position_state.get("outcome_id") or ""),
            token_id=str(position_state.get("token_id") or ""),
            side="sell",
            size=float(position_state.get("size") or 0.0),
            price=price,
            order_type="market" if aggressive else "limit",
            metadata_json=metadata,
            dry_run=self.config.dry_run,
        )
        with self._lock:
            self.active_orders[signal_id] = {
                **position_state,
                "order_id": placed["order_id"],
                "external_order_id": placed.get("external_order_id"),
                "status": placed["status"],
                "pending_action": "exit",
                "submitted_at": utc_now().isoformat(),
                "price": price,
                "best_bid": best_bid,
                "type": "market sell" if aggressive else "limit sell",
                "age": "00:00",
                "stop_triggered_flag": stop_triggered_flag,
                "market_retry_done": aggressive,
            }
        self._record_event(
            "warn" if stop_triggered_flag else "info",
            "Stop submitted" if stop_triggered_flag else "Exit submitted",
            f"{position_state.get('matchup')} {position_state.get('strategy_family')} exit at {price:.3f}.",
            game_id=str(position_state.get("game_id") or ""),
            details={
                "signal_id": signal_id,
                "market_id": str(position_state.get("market_id") or ""),
                "outcome_id": str(position_state.get("outcome_id") or ""),
                "size": float(position_state.get("size") or 0.0),
                "price": price,
                "best_bid": best_bid,
                "order_policy": order_policy,
                "stop_triggered_flag": stop_triggered_flag,
                "aggressive": aggressive,
                "exit_reason": position_state.get("exit_reason"),
            },
        )

    def _handle_trade_fill(self, order_state: dict[str, Any], trade: dict[str, Any]) -> None:
        side = str(order_state.get("side") or "").lower()
        fill_price = float(trade.get("price") or 0.0)
        size = float(trade.get("size") or order_state.get("size") or 0.0)
        submitted_at = _parse_datetime(order_state.get("submitted_at")) or utc_now()
        trade_time = _parse_datetime(trade.get("trade_time")) or utc_now()
        signal_price = float(order_state.get("signal_price") or 0.0)
        best_quote = float(order_state.get("best_ask") or 0.0) if side == "buy" else float(order_state.get("best_bid") or 0.0)
        slippage_signal = ((fill_price - signal_price) * 100.0) if side == "buy" else ((signal_price - fill_price) * 100.0)
        slippage_quote = ((fill_price - best_quote) * 100.0) if side == "buy" and best_quote else ((best_quote - fill_price) * 100.0 if best_quote else None)
        self.fill_metrics.append(
            {
                "order_id": order_state.get("order_id"),
                "trade_id": trade.get("trade_id"),
                "signal_id": order_state.get("signal_id"),
                "game_id": order_state.get("game_id"),
                "fill_delay_seconds": max(0.0, (trade_time - submitted_at).total_seconds()),
                "slippage_vs_signal_cents": round(slippage_signal, 4),
                "slippage_vs_best_quote_cents": round(slippage_quote, 4) if slippage_quote is not None else None,
                "stop_triggered_flag": bool(order_state.get("stop_triggered_flag")),
            }
        )
        if side == "buy":
            with self._lock:
                self.active_positions[str(order_state["signal_id"])] = {
                    **order_state,
                    "status": "open",
                    "entry_price": fill_price,
                    "entry_at": trade_time.isoformat(),
                    "size": size,
                    "realized_pnl": None,
                    "pnl_text": "+0.00",
                }
            self._record_event(
                "info",
                "Position opened",
                f"{order_state.get('matchup')} {order_state.get('strategy_family')} filled at {fill_price:.3f}.",
                game_id=str(order_state.get("game_id") or ""),
                details={
                    "signal_id": order_state.get("signal_id"),
                    "trade_id": trade.get("trade_id"),
                    "fill_delay_seconds": max(0.0, (trade_time - submitted_at).total_seconds()),
                    "slippage_vs_signal_cents": round(slippage_signal, 4),
                    "slippage_vs_best_quote_cents": round(slippage_quote, 4) if slippage_quote is not None else None,
                },
            )
            return
        existing = self.active_positions.pop(str(order_state.get("signal_id")), None)
        if existing:
            entry_price = float(existing.get("entry_price") or 0.0)
            pnl_amount = (fill_price - entry_price) * size
            self._record_event(
                "info",
                "Position closed",
                f"{order_state.get('matchup')} exit filled at {fill_price:.3f}, pnl {pnl_amount:+.2f}.",
                game_id=str(order_state.get("game_id") or ""),
                details={
                    "signal_id": order_state.get("signal_id"),
                    "trade_id": trade.get("trade_id"),
                    "fill_delay_seconds": max(0.0, (trade_time - submitted_at).total_seconds()),
                    "slippage_vs_signal_cents": round(slippage_signal, 4),
                    "slippage_vs_best_quote_cents": round(slippage_quote, 4) if slippage_quote is not None else None,
                    "realized_pnl": round(pnl_amount, 4),
                    "stop_triggered_flag": bool(order_state.get("stop_triggered_flag")),
                },
            )

    def _latest_state_row(self, state_df: pd.DataFrame, *, game_id: str, team_side: str) -> dict[str, Any] | None:
        if state_df is None or state_df.empty:
            return None
        rows = state_df[
            (state_df["game_id"].astype(str) == str(game_id))
            & (state_df["team_side"].astype(str) == str(team_side))
        ]
        if rows.empty:
            return None
        return rows.sort_values("state_index", kind="mergesort").iloc[-1].to_dict()

    def _all_games_finished(self) -> bool:
        with self._lock:
            return bool(self.game_cards) and all(str(card.get("state_label") or "").lower() == "final" for card in self.game_cards.values())

    def _record_event(
        self,
        level: str,
        title: str,
        message: str,
        *,
        game_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "timestamp": utc_now().isoformat(),
            "time": utc_now().strftime("%H:%M:%S"),
            "level": level,
            "title": title,
            "message": message,
            "game_id": game_id,
            "details": to_jsonable(details or {}),
        }
        with self._lock:
            self.events.append(event)
        _append_jsonl(self.run_root / "executor_events.jsonl", event)
        self._write_runtime_log(
            f"[{event['timestamp']}] event level={level} title={title} game_id={game_id or ''} message={message}"
        )

    def _persist_decisions(self, decision_rows: list[dict[str, Any]]) -> None:
        for row in decision_rows:
            _append_jsonl(self.run_root / "decisions.jsonl", row)

    def _write_heartbeat(self) -> None:
        _write_json(
            self.run_root / "heartbeat.json",
            {
                "run_id": self.config.run_id,
                "status": self.status,
                "entries_enabled": self.entries_enabled,
                "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
                "last_error": self.last_error,
                "last_traceback": self.last_traceback,
                "last_successful_cycle_at": self.last_successful_cycle_at.isoformat() if self.last_successful_cycle_at else None,
                "last_cycle_started_at": self.last_cycle_started_at.isoformat() if self.last_cycle_started_at else None,
                "last_cycle_completed_at": self.last_cycle_completed_at.isoformat() if self.last_cycle_completed_at else None,
                "last_cycle_duration_seconds": self.last_cycle_duration_seconds,
                "cycle_count": self.cycle_count,
            },
        )

    def _persist_recovery_snapshot(self) -> None:
        with self._lock:
            payload = {
                "status": self.status,
                "entries_enabled": self.entries_enabled,
                "last_error": self.last_error,
                "last_traceback": self.last_traceback,
                "account": self.account,
                "active_orders": self.active_orders,
                "active_positions": self.active_positions,
                "fill_metrics": self.fill_metrics,
                "cycle_count": self.cycle_count,
                "last_cycle_started_at": self.last_cycle_started_at,
                "last_cycle_completed_at": self.last_cycle_completed_at,
                "last_cycle_duration_seconds": self.last_cycle_duration_seconds,
                "last_successful_cycle_at": self.last_successful_cycle_at,
            }
        _write_json(self.run_root / "recovery_snapshot.json", payload)

    def _load_recovery_snapshot(self) -> None:
        path = self.run_root / "recovery_snapshot.json"
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.entries_enabled = bool(payload.get("entries_enabled", self.entries_enabled))
        self.active_orders = {str(key): value for key, value in dict(payload.get("active_orders") or {}).items()}
        self.active_positions = {str(key): value for key, value in dict(payload.get("active_positions") or {}).items()}
        self.fill_metrics = list(payload.get("fill_metrics") or [])
        self.last_traceback = payload.get("last_traceback")
        self.cycle_count = int(payload.get("cycle_count") or 0)
        self.last_cycle_started_at = _parse_datetime(payload.get("last_cycle_started_at"))
        self.last_cycle_completed_at = _parse_datetime(payload.get("last_cycle_completed_at"))
        duration_value = payload.get("last_cycle_duration_seconds")
        self.last_cycle_duration_seconds = float(duration_value) if duration_value is not None else None
        self.last_successful_cycle_at = _parse_datetime(payload.get("last_successful_cycle_at"))
        if isinstance(payload.get("account"), dict):
            self.account = payload["account"]

    def _latest_event_message_for_game(self, game_id: str) -> str | None:
        with self._lock:
            for event in reversed(self.events):
                if str(event.get("game_id") or "") == str(game_id):
                    return str(event.get("message") or "")
        return None

    def _append_controller_trace(
        self,
        stage: str,
        *,
        game_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "timestamp": utc_now().isoformat(),
            "run_id": self.config.run_id,
            "cycle_count": self.cycle_count,
            "stage": stage,
            "game_id": game_id,
            "payload": payload or {},
        }
        _append_jsonl(self.run_root / "controller_trace.jsonl", row)

    def _state_trace_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "state_index": row.get("state_index"),
            "period_label": row.get("period_label"),
            "clock": row.get("clock"),
            "event_at": row.get("event_at"),
            "team_side": row.get("team_side"),
            "team_price": row.get("team_price"),
            "score_diff": row.get("score_diff"),
            "net_points_last_5_events": row.get("net_points_last_5_events"),
            "seconds_to_game_end": row.get("seconds_to_game_end"),
            "market_regime": row.get("market_regime"),
        }

    def _decision_trace_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "selected_core_family": row.get("selected_core_family"),
            "selected_team_side": row.get("selected_team_side"),
            "selected_confidence": row.get("selected_confidence"),
            "final_source": row.get("final_source"),
            "final_selection_reason": row.get("final_selection_reason"),
            "llm_evaluated_flag": row.get("llm_evaluated_flag"),
            "llm_action": row.get("llm_action"),
            "llm_confidence": row.get("llm_confidence"),
            "final_selected_trade_count": row.get("final_selected_trade_count"),
            "weak_game_flag": row.get("weak_game_flag"),
        }

    def _trade_trace_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "source_strategy_family": row.get("source_strategy_family") or row.get("strategy_family"),
            "team_side": row.get("team_side"),
            "team_slug": row.get("team_slug"),
            "opponent_team_slug": row.get("opponent_team_slug"),
            "entry_state_index": row.get("entry_state_index"),
            "entry_at": row.get("entry_at"),
            "entry_price": row.get("entry_price"),
            "stop_price": _extract_stop_price(row),
            "signal_id": _signal_id_for_trade(row),
        }

    def _position_trace_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "signal_id": row.get("signal_id"),
            "strategy_family": row.get("strategy_family"),
            "team_side": row.get("team_side"),
            "entry_price": row.get("entry_price"),
            "stop_price": row.get("stop_price"),
            "size": row.get("size"),
            "entry_at": row.get("entry_at"),
            "exit_reason": row.get("exit_reason"),
        }

    def _write_runtime_log(self, line: str) -> None:
        _append_text(self.run_root / "runtime.log", line)

    def _write_error_trace(self, traceback_text: str) -> None:
        path = self.run_root / "last_error.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback_text, encoding="utf-8")


def _format_age(value: Any) -> str:
    submitted_at = _parse_datetime(value)
    if submitted_at is None:
        return "-"
    seconds = max(0, int((utc_now() - submitted_at).total_seconds()))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


__all__ = ["LiveRunWorker", "build_controller_context"]
