from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.modules.agentic.store import (
    append_jsonl,
    event_id_matches_session_date,
    read_json,
    strategy_plan_root,
    write_json,
)
from app.runtime.local_paths import repo_root, resolve_shared_root


WORKER_SOURCE = "janus-live-strategy-worker"
DEFAULT_API_ROOT = "http://127.0.0.1:8000"
_CLEARABLE_CONFIG_FIELDS = {
    "max_buy_notional_usd",
    "max_order_buy_notional_usd",
    "event_cap_usd",
    "side_budget_mode",
    "max_same_side_exposure_pct",
}


@dataclass(frozen=True)
class LiveStrategyWorkerConfig:
    session_date: str | None = None
    event_ids: tuple[str, ...] = ()
    account_id: str | None = None
    source: str = WORKER_SOURCE
    api_root: str = DEFAULT_API_ROOT
    enabled: bool = False
    execute: bool = False
    live_money: bool = False
    enable_llm_dispatch: bool = False
    submit_candidate_strategy_plan: bool = False
    interval_seconds: float = 30.0
    timeout_seconds: float = 240.0
    max_intents: int = 2
    orderbook_sample_count: int = 2
    orderbook_sample_interval_sec: float = 0.5
    min_size: float = 5.0
    min_buy_notional_usd: float = 1.0
    order_sizing_mode: str = "operator_minimum_order"
    max_buy_notional_usd: float | None = None
    max_order_buy_notional_usd: float | None = None
    event_cap_usd: float | None = None
    side_budget_mode: str | None = None
    max_same_side_exposure_pct: float | None = None
    share_precision: int = 3
    auto_protect_manual_positions: bool = True
    manual_target_delta_cents: float = 5.0

    @classmethod
    def from_env(cls) -> "LiveStrategyWorkerConfig":
        event_ids = tuple(_split_csv(os.getenv("JANUS_LIVE_STRATEGY_WORKER_EVENT_IDS")))
        return cls(
            session_date=os.getenv("JANUS_LIVE_STRATEGY_WORKER_SESSION_DATE") or None,
            event_ids=event_ids,
            account_id=os.getenv("JANUS_LIVE_STRATEGY_WORKER_ACCOUNT_ID") or None,
            source=os.getenv("JANUS_LIVE_STRATEGY_WORKER_SOURCE") or WORKER_SOURCE,
            api_root=os.getenv("JANUS_LIVE_STRATEGY_WORKER_API_ROOT") or DEFAULT_API_ROOT,
            enabled=_env_bool("JANUS_LIVE_STRATEGY_WORKER_ENABLED", False),
            execute=_env_bool("JANUS_LIVE_STRATEGY_WORKER_EXECUTE", False),
            live_money=_env_bool("JANUS_LIVE_STRATEGY_WORKER_LIVE_MONEY", False),
            enable_llm_dispatch=_env_bool("JANUS_LIVE_STRATEGY_WORKER_ENABLE_LLM_DISPATCH", False),
            submit_candidate_strategy_plan=_env_bool(
                "JANUS_LIVE_STRATEGY_WORKER_SUBMIT_CANDIDATE_STRATEGY_PLAN",
                False,
            ),
            interval_seconds=_env_float("JANUS_LIVE_STRATEGY_WORKER_INTERVAL_SECONDS", 30.0),
            timeout_seconds=_env_float("JANUS_LIVE_STRATEGY_WORKER_TIMEOUT_SECONDS", 240.0),
            max_intents=_env_int("JANUS_LIVE_STRATEGY_WORKER_MAX_INTENTS", 2),
            orderbook_sample_count=_env_int("JANUS_LIVE_STRATEGY_WORKER_ORDERBOOK_SAMPLE_COUNT", 2),
            orderbook_sample_interval_sec=_env_float(
                "JANUS_LIVE_STRATEGY_WORKER_ORDERBOOK_SAMPLE_INTERVAL_SEC",
                0.5,
            ),
            min_size=_env_float("JANUS_LIVE_STRATEGY_WORKER_MIN_SIZE", 5.0),
            min_buy_notional_usd=_env_float("JANUS_LIVE_STRATEGY_WORKER_MIN_BUY_NOTIONAL_USD", 1.0),
            order_sizing_mode=os.getenv("JANUS_LIVE_STRATEGY_WORKER_ORDER_SIZING_MODE") or "operator_minimum_order",
            max_buy_notional_usd=_env_optional_float("JANUS_LIVE_STRATEGY_WORKER_MAX_BUY_NOTIONAL_USD"),
            max_order_buy_notional_usd=_env_optional_float(
                "JANUS_LIVE_STRATEGY_WORKER_MAX_ORDER_BUY_NOTIONAL_USD"
            ),
            event_cap_usd=_env_optional_float("JANUS_LIVE_STRATEGY_WORKER_EVENT_CAP_USD"),
            side_budget_mode=os.getenv("JANUS_LIVE_STRATEGY_WORKER_SIDE_BUDGET_MODE") or None,
            max_same_side_exposure_pct=_env_optional_float(
                "JANUS_LIVE_STRATEGY_WORKER_MAX_SAME_SIDE_EXPOSURE_PCT"
            ),
            share_precision=_env_int("JANUS_LIVE_STRATEGY_WORKER_SHARE_PRECISION", 3),
            auto_protect_manual_positions=_env_bool(
                "JANUS_LIVE_STRATEGY_WORKER_AUTO_PROTECT_MANUAL_POSITIONS",
                True,
            ),
            manual_target_delta_cents=_env_float("JANUS_LIVE_STRATEGY_WORKER_MANUAL_TARGET_DELTA_CENTS", 5.0),
        )

    def with_overrides(self, overrides: dict[str, Any] | None) -> "LiveStrategyWorkerConfig":
        if not overrides:
            return self
        normalized: dict[str, Any] = {}
        for key, value in overrides.items():
            if key not in self.__dataclass_fields__:
                continue
            if value is None:
                if key in _CLEARABLE_CONFIG_FIELDS:
                    normalized[key] = None
                continue
            if key == "event_ids":
                normalized[key] = tuple(str(item).strip() for item in value if str(item).strip())
            else:
                normalized[key] = value
        return replace(self, **normalized)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date,
            "event_ids": list(self.event_ids),
            "account_id_configured": bool(self.account_id),
            "source": self.source,
            "api_root": self.api_root,
            "enabled": self.enabled,
            "execute": self.execute,
            "live_money": self.live_money,
            "enable_llm_dispatch": self.enable_llm_dispatch,
            "submit_candidate_strategy_plan": self.submit_candidate_strategy_plan,
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "max_intents": self.max_intents,
            "orderbook_sample_count": self.orderbook_sample_count,
            "orderbook_sample_interval_sec": self.orderbook_sample_interval_sec,
            "min_size": self.min_size,
            "min_buy_notional_usd": self.min_buy_notional_usd,
            "order_sizing_mode": self.order_sizing_mode,
            "max_buy_notional_usd": self.max_buy_notional_usd,
            "max_order_buy_notional_usd": self.max_order_buy_notional_usd,
            "event_cap_usd": self.event_cap_usd,
            "side_budget_mode": self.side_budget_mode,
            "max_same_side_exposure_pct": self.max_same_side_exposure_pct,
            "share_precision": self.share_precision,
            "auto_protect_manual_positions": self.auto_protect_manual_positions,
            "manual_target_delta_cents": self.manual_target_delta_cents,
        }


class LiveStrategyWorker:
    def __init__(self, config: LiveStrategyWorkerConfig | None = None) -> None:
        self._config = config or LiveStrategyWorkerConfig.from_env()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._tick_count = 0
        self._consecutive_failures = 0
        self._started_at_utc: str | None = None

    def start_if_env_enabled(self) -> dict[str, Any]:
        self._config = LiveStrategyWorkerConfig.from_env()
        if not self._config.enabled:
            return self.status(extra={"start_skipped_reason": "env_disabled"})
        session_date = self._config.session_date
        today = _brt_session_date()
        if session_date and session_date != today:
            return self.status(
                extra={
                    "start_skipped_reason": "stale_env_session_date",
                    "configured_session_date": session_date,
                    "current_session_date": today,
                }
            )
        return self.start()

    def start(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._config = self._config.with_overrides(overrides)
            if self._thread and self._thread.is_alive():
                return self.status(extra={"start_status": "already_running"})
            start_trust = _live_worker_config_trust(self._config, overrides=overrides, running=False)
            if not start_trust["start_allowed"]:
                return self.status(
                    extra={
                        "start_status": "blocked",
                        "start_blocker_reason": start_trust["stale_config_reason"],
                        "config_trust": start_trust,
                    }
                )
            self._stop_event.clear()
            self._started_at_utc = _utc_now()
            self._thread = threading.Thread(target=self._run_loop, name="janus-live-strategy-worker", daemon=True)
            self._thread.start()
            return self.status(extra={"start_status": "started"})

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            stopped = not (thread and thread.is_alive())
            if stopped:
                self._thread = None
            return self.status(extra={"stop_status": "stopped" if stopped else "stop_pending"})

    def run_once(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            config = self._config.with_overrides(overrides)
        result = self._run_tick(config=config, trigger="manual_tick")
        with self._lock:
            self._last_result = result
            self._tick_count += 1
            if result.get("ok") is False:
                self._consecutive_failures += 1
                self._last_error = str(result.get("error") or result.get("reason") or "tick_failed")
            else:
                self._consecutive_failures = 0
                self._last_error = None
        return result

    def status(self, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            config_trust = _live_worker_config_trust(self._config, overrides=None, running=running)
            payload = {
                "status": "running" if running else "stopped",
                "timestamp_utc": _utc_now(),
                "started_at_utc": self._started_at_utc,
                "worker_thread_alive": running,
                "tick_count": self._tick_count,
                "consecutive_failures": self._consecutive_failures,
                "last_error": self._last_error,
                "config": self._config.safe_dict(),
                "config_trust": config_trust,
                "last_result": self._compact_result(self._last_result),
            }
        if extra:
            payload.update(extra)
        heartbeat = _read_latest_heartbeat()
        if heartbeat:
            payload["latest_heartbeat"] = heartbeat
        return payload

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                config = self._config
            result = self._run_tick(config=config, trigger="scheduled_loop")
            with self._lock:
                self._last_result = result
                self._tick_count += 1
                if result.get("ok") is False:
                    self._consecutive_failures += 1
                    self._last_error = str(result.get("error") or result.get("reason") or "tick_failed")
                else:
                    self._consecutive_failures = 0
                    self._last_error = None
            self._stop_event.wait(max(1.0, float(config.interval_seconds)))

    def _run_tick(self, *, config: LiveStrategyWorkerConfig, trigger: str) -> dict[str, Any]:
        started_at = _utc_now()
        if not self._tick_lock.acquire(blocking=False):
            day = config.session_date or _brt_session_date()
            result = {
                "ok": True,
                "status": "skipped",
                "reason": "live_strategy_worker_tick_in_progress",
                "trigger": trigger,
                "session_date": day,
                "event_ids": list(config.event_ids) or _discover_current_event_ids(day),
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "config": config.safe_dict(),
            }
            _persist_worker_tick(day, result)
            return result
        try:
            return self._run_tick_locked(config=config, trigger=trigger, started_at=started_at)
        finally:
            self._tick_lock.release()

    def _run_tick_locked(
        self,
        *,
        config: LiveStrategyWorkerConfig,
        trigger: str,
        started_at: str,
    ) -> dict[str, Any]:
        day = config.session_date or _brt_session_date()
        event_ids = list(config.event_ids) or _discover_current_event_ids(day)
        if not config.account_id:
            result = {
                "ok": False,
                "status": "blocked",
                "reason": "account_id_required",
                "trigger": trigger,
                "session_date": day,
                "event_ids": event_ids,
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "config": config.safe_dict(),
            }
            _persist_worker_tick(day, result)
            return result
        if not event_ids:
            result = {
                "ok": True,
                "status": "no_op",
                "reason": "no_current_valid_strategy_plans",
                "trigger": trigger,
                "session_date": day,
                "event_ids": [],
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "config": config.safe_dict(),
            }
            _persist_worker_tick(day, result)
            return result

        command = _build_command(config=config, session_date=day, event_ids=event_ids)
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo_root()),
                capture_output=True,
                text=True,
                timeout=max(1.0, float(config.timeout_seconds)),
                check=False,
            )
            parsed_stdout = _parse_json(completed.stdout)
            result = {
                "ok": completed.returncode == 0 and not (isinstance(parsed_stdout, dict) and parsed_stdout.get("ok") is False),
                "status": "completed" if completed.returncode == 0 else "failed",
                "trigger": trigger,
                "session_date": day,
                "event_ids": event_ids,
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "returncode": completed.returncode,
                "command": _safe_command(command),
                "stdout": parsed_stdout if parsed_stdout is not None else _preview(completed.stdout),
                "stderr": _preview(completed.stderr),
                "config": config.safe_dict(),
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "ok": False,
                "status": "timeout",
                "trigger": trigger,
                "session_date": day,
                "event_ids": event_ids,
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "command": _safe_command(command),
                "error": f"live strategy tick exceeded {config.timeout_seconds}s timeout",
                "stdout": _preview(exc.stdout),
                "stderr": _preview(exc.stderr),
                "config": config.safe_dict(),
            }
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "status": "error",
                "trigger": trigger,
                "session_date": day,
                "event_ids": event_ids,
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "error": repr(exc),
                "command": _safe_command(command),
                "config": config.safe_dict(),
            }
        _persist_worker_tick(day, result)
        return result

    @staticmethod
    def _compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not result:
            return None
        return {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "reason": result.get("reason"),
            "session_date": result.get("session_date"),
            "event_ids": result.get("event_ids"),
            "started_at_utc": result.get("started_at_utc"),
            "finished_at_utc": result.get("finished_at_utc"),
            "returncode": result.get("returncode"),
            "error": result.get("error"),
        }


_WORKER: LiveStrategyWorker | None = None


def get_live_strategy_worker() -> LiveStrategyWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = LiveStrategyWorker()
    return _WORKER


def build_live_strategy_worker_readiness(
    *,
    session_date: str | None,
    event_ids: list[str] | None,
    strategy_plan_gate: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    day = session_date or _brt_session_date()
    worker_status = get_live_strategy_worker().status()
    config = worker_status.get("config") if isinstance(worker_status.get("config"), dict) else {}
    config_trust = worker_status.get("config_trust") if isinstance(worker_status.get("config_trust"), dict) else {}
    expected_event_ids = _expected_worker_event_ids(day, event_ids or [], strategy_plan_gate)
    explicit_event_ids = _normalized_unique_values(event_ids or [])
    gate_ready = (strategy_plan_gate or {}).get("ready_for_strategy_evaluation")
    gate_status = (strategy_plan_gate or {}).get("status")
    worker_required = bool(
        expected_event_ids
        and (
            gate_ready is True
            or strategy_plan_gate is None
            or (not explicit_event_ids and gate_status == "not_required")
        )
    )
    heartbeat = _read_heartbeat(day)
    interval_seconds = _safe_float(config.get("interval_seconds"), 30.0)
    max_age_seconds = max(90.0, interval_seconds * 3.0)
    now = now_utc or datetime.now(timezone.utc)
    heartbeat_at = _heartbeat_timestamp(heartbeat)
    heartbeat_age_seconds = (now - heartbeat_at).total_seconds() if heartbeat_at is not None else None
    heartbeat_event_ids = _normalized_unique_values(heartbeat.get("event_ids") if heartbeat else [])
    missing_heartbeat_event_ids = [event_id for event_id in expected_event_ids if event_id not in heartbeat_event_ids]
    heartbeat_fresh = heartbeat_age_seconds is not None and heartbeat_age_seconds <= max_age_seconds
    worker_thread_alive = bool(worker_status.get("worker_thread_alive"))

    if not worker_required:
        status_text = "not_required"
        blocker_reason = None
        ready_for_live_execution = True
    elif not worker_thread_alive:
        status_text = "blocked"
        blocker_reason = "live_strategy_worker_not_running"
        ready_for_live_execution = False
    elif heartbeat is None:
        status_text = "blocked"
        blocker_reason = "live_strategy_worker_heartbeat_missing"
        ready_for_live_execution = False
    elif not heartbeat_fresh:
        status_text = "blocked"
        blocker_reason = "live_strategy_worker_heartbeat_stale"
        ready_for_live_execution = False
    elif missing_heartbeat_event_ids:
        status_text = "blocked"
        blocker_reason = "live_strategy_worker_event_mismatch"
        ready_for_live_execution = False
    else:
        status_text = "ready"
        blocker_reason = None
        ready_for_live_execution = True

    return {
        "schema_version": "live_strategy_worker_monitor_v1",
        "status": status_text,
        "blocker_reason": blocker_reason,
        "worker_required": worker_required,
        "ready_for_live_execution": ready_for_live_execution,
        "health_only_not_executor": True,
        "session_date": day,
        "expected_event_ids": expected_event_ids,
        "worker_thread_alive": worker_thread_alive,
        "tick_count": worker_status.get("tick_count"),
        "consecutive_failures": worker_status.get("consecutive_failures"),
        "last_error": worker_status.get("last_error"),
        "heartbeat_present": heartbeat is not None,
        "heartbeat_fresh": heartbeat_fresh,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_max_age_seconds": max_age_seconds,
        "heartbeat_event_ids": heartbeat_event_ids,
        "missing_heartbeat_event_ids": missing_heartbeat_event_ids,
        "latest_heartbeat": heartbeat,
        "config_trust": config_trust,
        "worker_status": {
            "status": worker_status.get("status"),
            "started_at_utc": worker_status.get("started_at_utc"),
            "last_result": worker_status.get("last_result"),
            "config": config,
            "config_trust": config_trust,
        },
    }


def _live_worker_config_trust(
    config: LiveStrategyWorkerConfig,
    *,
    overrides: dict[str, Any] | None,
    running: bool,
) -> dict[str, Any]:
    safe_config = config.safe_dict()
    day = config.session_date or _brt_session_date()
    current_day = _brt_session_date()
    event_ids = list(config.event_ids)
    explicit_start_payload = overrides is not None and any(
        key in overrides for key in ("session_date", "event_ids", "account_id", "execute", "live_money")
    )
    explicit_event_scope_payload = overrides is not None and "event_ids" in overrides and bool(event_ids)
    explicit_account_payload = overrides is not None and "account_id" in overrides and bool(config.account_id)
    trusted_env_autostart = bool(config.enabled and config.session_date and config.event_ids)
    event_scope_matches_config_date = all(event_id_matches_session_date(event_id, day) for event_id in event_ids)
    event_scope_current = day == current_day and event_scope_matches_config_date
    stale_config_reason: str | None = None

    if config.session_date and config.session_date != current_day:
        stale_config_reason = "stale_config_session_date"
    elif event_ids and not event_scope_current:
        stale_config_reason = "stale_config_event_scope"
    elif not running and not explicit_start_payload and not trusted_env_autostart:
        stale_config_reason = "explicit_start_payload_required"
    elif not running and not explicit_event_scope_payload and not trusted_env_autostart:
        stale_config_reason = "explicit_event_scope_required_for_restart"
    elif (config.execute or config.live_money) and not event_ids:
        stale_config_reason = "explicit_event_scope_required_for_live_money_start"
    elif (config.execute or config.live_money) and not config.account_id:
        stale_config_reason = "explicit_account_required_for_live_money_start"

    start_allowed = stale_config_reason is None
    if running:
        config_trust_status = "trusted_running_config" if start_allowed else "running_with_stale_config"
    elif start_allowed:
        config_trust_status = "trusted_current_start_config"
    else:
        config_trust_status = "fail_closed_stale_or_implicit_config"

    return {
        "schema_version": "live_strategy_worker_config_trust_v1",
        "schema_contract": _live_worker_config_trust_schema_contract(),
        "config_trust_status": config_trust_status,
        "start_allowed": start_allowed,
        "restart_allowed": start_allowed,
        "explicit_start_required": not trusted_env_autostart,
        "explicit_start_payload": explicit_start_payload,
        "explicit_event_scope_payload": explicit_event_scope_payload,
        "explicit_account_payload": explicit_account_payload,
        "trusted_env_autostart": trusted_env_autostart,
        "stale_config_reason": stale_config_reason,
        "current_session_date": current_day,
        "configured_session_date": config.session_date,
        "effective_session_date": day,
        "effective_event_ids": event_ids,
        "event_scope_current": event_scope_current,
        "execute": safe_config["execute"],
        "live_money": safe_config["live_money"],
    }


def _live_worker_config_trust_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "live_strategy_worker_config_trust_contract_v1",
        "primary_schema": "live_strategy_worker_config_trust_v1",
        "execution_authority": False,
        "required_sections": [
            "config_trust_status",
            "start_allowed",
            "restart_allowed",
            "explicit_start_required",
            "explicit_start_payload",
            "explicit_event_scope_payload",
            "explicit_account_payload",
            "trusted_env_autostart",
            "stale_config_reason",
            "current_session_date",
            "configured_session_date",
            "effective_session_date",
            "effective_event_ids",
            "event_scope_current",
            "execute",
            "live_money",
        ],
        "consumer_notes": [
            "start_allowed and restart_allowed are read-only safety evidence, not execution authority.",
            "A false explicit_event_scope_payload means API callers must provide current event_ids before live-money work.",
            "Live-money starts also require explicit_account_payload or trusted env autostart with an account.",
        ],
    }


def _build_command(
    *,
    config: LiveStrategyWorkerConfig,
    session_date: str,
    event_ids: list[str],
) -> list[str]:
    command = [
        sys.executable,
        str(repo_root() / "codex_tool" / "run_live_strategy_tick.py"),
        "--api-root",
        config.api_root,
        "--session-date",
        session_date,
        "--account-id",
        config.account_id or "",
        "--source",
        config.source,
        "--max-intents",
        str(config.max_intents),
        "--orderbook-sample-count",
        str(config.orderbook_sample_count),
        "--orderbook-sample-interval-sec",
        str(config.orderbook_sample_interval_sec),
        "--min-size",
        str(config.min_size),
        "--min-buy-notional-usd",
        str(config.min_buy_notional_usd),
        "--order-sizing-mode",
        str(config.order_sizing_mode),
        "--manual-target-delta-cents",
        str(config.manual_target_delta_cents),
    ]
    if config.max_buy_notional_usd is not None:
        command.extend(["--max-buy-notional-usd", str(config.max_buy_notional_usd)])
    if config.max_order_buy_notional_usd is not None:
        command.extend(["--max-order-buy-notional-usd", str(config.max_order_buy_notional_usd)])
    if config.event_cap_usd is not None:
        command.extend(["--event-cap-usd", str(config.event_cap_usd)])
    if config.side_budget_mode:
        command.extend(["--side-budget-mode", str(config.side_budget_mode)])
    if config.max_same_side_exposure_pct is not None:
        command.extend(["--max-same-side-exposure-pct", str(config.max_same_side_exposure_pct)])
    command.extend(["--share-precision", str(config.share_precision)])
    for event_id in event_ids:
        command.extend(["--event-id", event_id])
    if config.execute:
        command.append("--execute")
    if config.live_money:
        command.append("--live-money")
    if config.enable_llm_dispatch:
        command.append("--enable-llm-dispatch")
    if config.submit_candidate_strategy_plan:
        command.append("--submit-candidate-strategy-plan")
    if config.auto_protect_manual_positions:
        command.append("--auto-protect-manual-positions")
    else:
        command.append("--no-auto-protect-manual-positions")
    return command


def _discover_current_event_ids(day: str) -> list[str]:
    root = strategy_plan_root(day)
    if not root.exists():
        return []
    now = datetime.now(timezone.utc)
    event_ids: list[str] = []
    for path in sorted(root.glob("*/current.json")):
        payload = read_json(path) or {}
        event_id = str(payload.get("event_id") or path.parent.name).strip()
        if not event_id:
            continue
        if not event_id_matches_session_date(event_id, day):
            continue
        valid_until = _parse_dt(payload.get("valid_until_utc"))
        if valid_until is not None and valid_until <= now:
            continue
        event_ids.append(event_id)
    return list(dict.fromkeys(event_ids))


def _persist_worker_tick(day: str, result: dict[str, Any]) -> None:
    root = resolve_shared_root() / "artifacts" / "live-strategy-worker" / day
    heartbeat = {
        "schema_version": "live_strategy_worker_heartbeat_v1",
        "updated_at_utc": _utc_now(),
        "status": result.get("status"),
        "ok": result.get("ok"),
        "reason": result.get("reason"),
        "event_ids": result.get("event_ids") or [],
        "session_date": day,
        "source": WORKER_SOURCE,
        "last_tick_started_at_utc": result.get("started_at_utc"),
        "last_tick_finished_at_utc": result.get("finished_at_utc"),
        "returncode": result.get("returncode"),
        "error": result.get("error"),
    }
    write_json(root / "heartbeat.json", heartbeat)
    append_jsonl(root / "ticks.jsonl", result)


def _read_latest_heartbeat() -> dict[str, Any] | None:
    root = resolve_shared_root() / "artifacts" / "live-strategy-worker"
    if not root.exists():
        return None
    candidates = sorted(root.glob("*/heartbeat.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return read_json(candidates[0])


def _read_heartbeat(day: str) -> dict[str, Any] | None:
    return read_json(resolve_shared_root() / "artifacts" / "live-strategy-worker" / day / "heartbeat.json")


def _expected_worker_event_ids(
    day: str,
    event_ids: list[str],
    strategy_plan_gate: dict[str, Any] | None,
) -> list[str]:
    explicit = _normalized_unique_values(event_ids)
    if explicit:
        return explicit
    gate_plans = (strategy_plan_gate or {}).get("current_plans") or []
    from_gate = _normalized_unique_values(
        [item.get("event_id") for item in gate_plans if isinstance(item, dict)]
    )
    if from_gate:
        return from_gate
    return _discover_current_event_ids(day)


def _heartbeat_timestamp(heartbeat: dict[str, Any] | None) -> datetime | None:
    if not heartbeat:
        return None
    return _parse_dt(
        heartbeat.get("last_tick_finished_at_utc")
        or heartbeat.get("updated_at_utc")
        or heartbeat.get("last_tick_started_at_utc")
    )


def _normalized_unique_values(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            normalized.append(text)
            seen.add(text)
    return normalized


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


def _brt_session_date() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


def _env_optional_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_json(value: str | bytes | None) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _preview(value: str | bytes | None, *, max_chars: int = 4000) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    return (value or "")[:max_chars]


def _safe_command(command: list[str]) -> list[str]:
    safe: list[str] = []
    skip_next = False
    for index, item in enumerate(command):
        if skip_next:
            safe.append("<redacted>")
            skip_next = False
            continue
        safe.append(item)
        if item == "--account-id" and index + 1 < len(command):
            skip_next = True
    return safe


__all__ = [
    "DEFAULT_API_ROOT",
    "LiveStrategyWorker",
    "LiveStrategyWorkerConfig",
    "WORKER_SOURCE",
    "build_live_strategy_worker_readiness",
    "get_live_strategy_worker",
]
