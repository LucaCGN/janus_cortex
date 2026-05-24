"""Live activation preflight for Janus sports and portfolio-manager scopes."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from codex_tools.janus.client import DEFAULT_API_ROOT, api_json, base_parser, exit_for_response
from codex_tools.janus.worker import get_live_strategy_worker_status

LIVE_ACTIVATION_PREFLIGHT_SCHEMA_VERSION = "janus_live_activation_preflight_v1"
NO_EXECUTION_STATEMENT = (
    "No order, cancel, replace, prepare, submit, sign, broadcast, redeem, worker start, "
    "service start, or live-money action was attempted."
)

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


@dataclass(frozen=True)
class ActivationCheck:
    name: str
    passed: bool
    required: bool
    value: Any
    blocker: str | None = None


@dataclass(frozen=True)
class ActivationScopeResult:
    scope: str
    mode: str
    ready: bool
    status: str
    checks: list[ActivationCheck]
    blockers: list[str]
    config: dict[str, Any]
    runtime: dict[str, Any]
    next_commands: dict[str, str]


@dataclass(frozen=True)
class LiveActivationPreflight:
    schema_version: str
    scope: str
    mode: str
    ready: bool
    status: str
    sports_live: ActivationScopeResult | None
    portfolio_manager: ActivationScopeResult | None
    blockers: list[str]
    next_commands: dict[str, str]
    execution_statement: str


def parse_env_file(path: str | Path | None) -> dict[str, str]:
    """Parse a simple dotenv file without expanding values or requiring dependencies."""
    if path is None:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {env_path}")
    parsed: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            parsed[key] = value
    return parsed


def merged_env(env_file: str | Path | None = None, overrides: dict[str, str | None] | None = None) -> dict[str, str]:
    """Return dotenv values overlaid by process env and explicit CLI overrides."""
    merged: dict[str, str] = parse_env_file(env_file)
    for key, value in os.environ.items():
        merged[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            merged[key] = str(value)
    return merged


def env_bool(env: dict[str, str], key: str, *, default: bool = False) -> bool:
    value = str(env.get(key, "")).strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def env_int(env: dict[str, str], key: str, *, default: int | None = None) -> int | None:
    value = str(env.get(key, "")).strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(env: dict[str, str], key: str, *, default: float | None = None) -> float | None:
    value = str(env.get(key, "")).strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_list(env: dict[str, str], key: str) -> list[str]:
    raw = str(env.get(key, "")).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def build_sports_live_config(env: dict[str, str]) -> dict[str, Any]:
    event_ids = env_list(env, "JANUS_LIVE_TEST_EVENT_IDS")
    single_event_id = str(env.get("JANUS_LIVE_TEST_EVENT_ID", "")).strip()
    if single_event_id and single_event_id not in event_ids:
        event_ids.append(single_event_id)
    return {
        "enabled": env_bool(env, "JANUS_LIVE_TEST_ENABLED"),
        "mode": str(env.get("JANUS_LIVE_ACTIVATION_MODE", "rehearsal")).strip().lower() or "rehearsal",
        "session_date": str(env.get("JANUS_LIVE_TEST_SESSION_DATE", "")).strip() or None,
        "event_ids": event_ids,
        "account_id": str(env.get("JANUS_LIVE_TEST_ACCOUNT_ID", "")).strip() or None,
        "execute": env_bool(env, "JANUS_LIVE_TEST_EXECUTE"),
        "live_money": env_bool(env, "JANUS_LIVE_TEST_LIVE_MONEY"),
        "enable_llm_dispatch": env_bool(env, "JANUS_LIVE_TEST_ENABLE_LLM_DISPATCH"),
        "submit_candidate_strategy_plan": env_bool(env, "JANUS_LIVE_TEST_SUBMIT_CANDIDATE_STRATEGY_PLAN"),
        "codex_reviewed_fallback_enabled": env_bool(env, "JANUS_LIVE_TEST_CODEX_REVIEWED_FALLBACK_ENABLED"),
        "max_intents": env_int(env, "JANUS_LIVE_TEST_MAX_INTENTS", default=0),
        "min_size": env_float(env, "JANUS_LIVE_TEST_MIN_SIZE", default=5.0),
        "min_buy_notional_usd": env_float(env, "JANUS_LIVE_TEST_MIN_BUY_NOTIONAL_USD", default=1.0),
        "max_buy_notional_usd": env_float(env, "JANUS_LIVE_TEST_MAX_BUY_NOTIONAL_USD", default=10.0),
        "source": str(env.get("JANUS_LIVE_TEST_SOURCE", "codex-live-activation-preflight")).strip(),
    }


def build_portfolio_manager_config(env: dict[str, str]) -> dict[str, Any]:
    return {
        "enabled": env_bool(env, "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED"),
        "mode": str(env.get("JANUS_PORTFOLIO_MANAGER_ACTIVATION_MODE", "rehearsal")).strip().lower() or "rehearsal",
        "account_id": str(env.get("JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID", "")).strip() or None,
        "execution_approved": env_bool(env, "JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED"),
        "reviewed_by": str(env.get("JANUS_PORTFOLIO_MANAGER_REVIEWED_BY", "")).strip() or None,
        "reason": str(env.get("JANUS_PORTFOLIO_MANAGER_REASON", "")).strip() or None,
        "max_initial_notional_usd": env_float(env, "JANUS_PORTFOLIO_MANAGER_MAX_INITIAL_NOTIONAL_USD", default=5.0),
        "target_notional_usd": env_float(env, "JANUS_PORTFOLIO_MANAGER_TARGET_NOTIONAL_USD", default=1.0),
        "kill_switch_clear": env_bool(env, "JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR"),
        "direct_truth_max_age_seconds": env_int(
            env,
            "JANUS_PORTFOLIO_MANAGER_DIRECT_TRUTH_MAX_AGE_SECONDS",
            default=60,
        ),
    }


def evaluate_sports_live_activation(
    config: dict[str, Any],
    *,
    worker_status: dict[str, Any] | None = None,
    janus_status: dict[str, Any] | None = None,
    require_runtime: bool = False,
) -> ActivationScopeResult:
    checks: list[ActivationCheck] = []

    def add(name: str, passed: bool, value: Any, *, required: bool = True, blocker: str | None = None) -> None:
        checks.append(ActivationCheck(name, bool(passed), required, value, None if passed else blocker or name))

    mode = str(config.get("mode") or "rehearsal")
    live_mode = mode == "live"
    add("live_test_enabled", bool(config.get("enabled")), config.get("enabled"), blocker="JANUS_LIVE_TEST_ENABLED must be true")
    add("session_date_present", bool(config.get("session_date")), config.get("session_date"), blocker="JANUS_LIVE_TEST_SESSION_DATE missing")
    add("event_ids_present", bool(config.get("event_ids")), config.get("event_ids"), blocker="JANUS_LIVE_TEST_EVENT_ID(S) missing")
    add("account_id_present", bool(config.get("account_id")), _redact(config.get("account_id")), blocker="JANUS_LIVE_TEST_ACCOUNT_ID missing")
    add("max_intents_positive", (config.get("max_intents") or 0) > 0, config.get("max_intents"), blocker="JANUS_LIVE_TEST_MAX_INTENTS must be > 0")
    add("minimum_size_policy", (config.get("min_size") or 0) >= 5, config.get("min_size"), blocker="JANUS_LIVE_TEST_MIN_SIZE must be >= 5")
    add(
        "minimum_notional_policy",
        (config.get("min_buy_notional_usd") or 0) >= 1,
        config.get("min_buy_notional_usd"),
        blocker="JANUS_LIVE_TEST_MIN_BUY_NOTIONAL_USD must be >= 1",
    )
    add(
        "maximum_notional_policy",
        (config.get("max_buy_notional_usd") or 0) >= (config.get("min_buy_notional_usd") or 0),
        config.get("max_buy_notional_usd"),
        blocker="JANUS_LIVE_TEST_MAX_BUY_NOTIONAL_USD must be >= JANUS_LIVE_TEST_MIN_BUY_NOTIONAL_USD",
    )
    add(
        "execute_flag_live_mode",
        bool(config.get("execute")) if live_mode else not bool(config.get("execute")),
        config.get("execute"),
        blocker="JANUS_LIVE_TEST_EXECUTE must match activation mode",
    )
    add(
        "live_money_flag_live_mode",
        bool(config.get("live_money")) if live_mode else not bool(config.get("live_money")),
        config.get("live_money"),
        blocker="JANUS_LIVE_TEST_LIVE_MONEY must match activation mode",
    )
    add(
        "revision_path_enabled",
        bool(config.get("enable_llm_dispatch") or config.get("codex_reviewed_fallback_enabled")),
        {
            "enable_llm_dispatch": config.get("enable_llm_dispatch"),
            "codex_reviewed_fallback_enabled": config.get("codex_reviewed_fallback_enabled"),
        },
        blocker="Enable LLM dispatch or Codex reviewed fallback before live testing",
    )

    runtime: dict[str, Any] = {
        "janus_status_checked": janus_status is not None,
        "worker_status_checked": worker_status is not None,
    }
    if require_runtime:
        add("janus_status_available", bool(janus_status and janus_status.get("ok", True)), _compact_status(janus_status), blocker="Janus status endpoint unavailable")
        add("worker_status_available", bool(worker_status and worker_status.get("ok", True)), _compact_status(worker_status), blocker="Live worker status endpoint unavailable")

    if worker_status:
        worker_flat = _flatten_worker_status(worker_status)
        runtime["worker"] = worker_flat
        add("worker_running", worker_flat.get("running"), worker_flat.get("status"), blocker="live strategy worker is not running")
        add("worker_enabled", worker_flat.get("enabled"), worker_flat.get("enabled"), blocker="worker enabled=false")
        add(
            "worker_execute_matches",
            bool(worker_flat.get("execute")) == bool(config.get("execute")),
            {"worker": worker_flat.get("execute"), "config": config.get("execute")},
            blocker="worker execute flag does not match activation config",
        )
        add(
            "worker_live_money_matches",
            bool(worker_flat.get("live_money")) == bool(config.get("live_money")),
            {"worker": worker_flat.get("live_money"), "config": config.get("live_money")},
            blocker="worker live_money flag does not match activation config",
        )
        add(
            "worker_account_configured",
            worker_flat.get("account_id_configured"),
            worker_flat.get("account_id_configured"),
            blocker="worker account_id_configured=false",
        )
        if config.get("event_ids"):
            configured_events = set(worker_flat.get("event_ids") or [])
            wanted_events = set(config.get("event_ids") or [])
            add(
                "worker_event_scope_matches",
                wanted_events.issubset(configured_events) if configured_events else False,
                {"worker_event_ids": sorted(configured_events), "wanted_event_ids": sorted(wanted_events)},
                blocker="worker event scope does not include target event",
            )

    blockers = [check.blocker for check in checks if check.required and not check.passed and check.blocker]
    ready = not blockers
    return ActivationScopeResult(
        scope="sports-live",
        mode=mode,
        ready=ready,
        status="ready_for_live_activation" if ready else "blocked",
        checks=checks,
        blockers=blockers,
        config=_redacted_config(config),
        runtime=runtime,
        next_commands=build_sports_live_next_commands(config),
    )


def evaluate_portfolio_manager_activation(config: dict[str, Any]) -> ActivationScopeResult:
    checks: list[ActivationCheck] = []

    def add(name: str, passed: bool, value: Any, *, blocker: str) -> None:
        checks.append(ActivationCheck(name, bool(passed), True, value, None if passed else blocker))

    mode = str(config.get("mode") or "rehearsal")
    live_mode = mode == "live"
    account_id = str(config.get("account_id") or "").strip()
    add(
        "order_management_runtime_enabled",
        bool(config.get("enabled")) if live_mode else True,
        config.get("enabled"),
        blocker="JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED must be true for live mode",
    )
    add(
        "account_id_present",
        bool(account_id),
        _redact(config.get("account_id")),
        blocker="JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID missing",
    )
    if account_id:
        add(
            "account_id_uuid_shape",
            _is_uuid_text(account_id),
            _redact(account_id),
            blocker="JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID must be a Janus portfolio account UUID, not a wallet address",
        )
    add(
        "execution_approved",
        bool(config.get("execution_approved")) if live_mode else True,
        config.get("execution_approved"),
        blocker="JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED must be true for live mode",
    )
    add(
        "reviewer_metadata_present",
        bool(config.get("reviewed_by") and config.get("reason")) if live_mode else True,
        {"reviewed_by": config.get("reviewed_by"), "reason_present": bool(config.get("reason"))},
        blocker="JANUS_PORTFOLIO_MANAGER_REVIEWED_BY and JANUS_PORTFOLIO_MANAGER_REASON required for live mode",
    )
    add(
        "kill_switch_clear",
        bool(config.get("kill_switch_clear")) if live_mode else True,
        config.get("kill_switch_clear"),
        blocker="JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR must be true for live mode",
    )
    add(
        "micro_risk_cap_present",
        (config.get("max_initial_notional_usd") or 0) <= 5 and (config.get("target_notional_usd") or 0) <= 1,
        {
            "max_initial_notional_usd": config.get("max_initial_notional_usd"),
            "target_notional_usd": config.get("target_notional_usd"),
        },
        blocker="portfolio-manager micro-risk caps must remain <= $5 initial and <= $1 target notional",
    )
    add(
        "direct_truth_freshness_policy",
        (config.get("direct_truth_max_age_seconds") or 999999) <= 60,
        config.get("direct_truth_max_age_seconds"),
        blocker="direct truth max age must be <= 60 seconds for live mode",
    )

    blockers = [check.blocker for check in checks if not check.passed and check.blocker]
    ready = not blockers
    return ActivationScopeResult(
        scope="portfolio-manager",
        mode=mode,
        ready=ready,
        status="ready_for_live_activation" if ready else "blocked",
        checks=checks,
        blockers=blockers,
        config=_redacted_config(config),
        runtime={},
        next_commands=build_portfolio_manager_next_commands(config),
    )


def build_sports_live_next_commands(config: dict[str, Any]) -> dict[str, str]:
    base = [
        "python",
        "codex_tool\\start_live_strategy_worker.py",
        "--session-date",
        str(config.get("session_date") or "<YYYY-MM-DD>"),
    ]
    for event_id in config.get("event_ids") or ["<EVENT_ID>"]:
        base.extend(["--event-id", str(event_id)])
    base.extend(
        [
            "--account-id",
            str(config.get("account_id") or "<ACCOUNT_ID>"),
            "--max-intents",
            str(config.get("max_intents") or 2),
            "--min-size",
            str(config.get("min_size") or 5),
            "--min-buy-notional-usd",
            str(config.get("min_buy_notional_usd") or 1),
            "--max-buy-notional-usd",
            str(config.get("max_buy_notional_usd") or 10),
        ]
    )
    rehearsal = list(base)
    rehearsal.extend(["--source", str(config.get("source") or "codex-live-activation-preflight")])
    live = list(rehearsal)
    live.extend(["--execute", "--live-money"])
    if config.get("enable_llm_dispatch"):
        live.append("--enable-llm-dispatch")
    if config.get("submit_candidate_strategy_plan"):
        live.append("--submit-candidate-strategy-plan")
    return {
        "status": "python codex_tool\\live_strategy_worker_status.py",
        "start_rehearsal_worker": " ".join(rehearsal),
        "start_live_worker": " ".join(live),
        "dry_tick": _build_worker_tick_command(config, execute=False),
        "live_tick": _build_worker_tick_command(config, execute=True),
    }


def build_portfolio_manager_next_commands(config: dict[str, Any]) -> dict[str, str]:
    reviewed_by = config.get("reviewed_by") or "<REVIEWER>"
    reason = config.get("reason") or "<REASON>"
    account_id = config.get("account_id") or "<ACCOUNT_UUID_NOT_WALLET>"
    return {
        "dry_run_order": (
            "python -m codex_tools.polymarket portfolio-manager-order "
            "--action-plan-json <ACTION_PLAN_JSON> --requested-order-json <REQUESTED_ORDER_JSON> "
            f"--account-id {account_id}"
        ),
        "live_order": (
            "python -m codex_tools.polymarket portfolio-manager-order "
            "--action-plan-json <ACTION_PLAN_JSON> --requested-order-json <REQUESTED_ORDER_JSON> "
            f"--account-id {account_id} --execute --execution-approved --reviewed-by {reviewed_by} --reason \"{reason}\""
        ),
        "required_env": (
            "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED=true; "
            "JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED=true; "
            "JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR=true"
        ),
    }


def build_live_activation_preflight(
    *,
    scope: str,
    env: dict[str, str],
    worker_status: dict[str, Any] | None = None,
    janus_status: dict[str, Any] | None = None,
    require_runtime: bool = False,
) -> LiveActivationPreflight:
    sports = None
    portfolio = None
    if scope in {"sports-live", "both"}:
        sports = evaluate_sports_live_activation(
            build_sports_live_config(env),
            worker_status=worker_status,
            janus_status=janus_status,
            require_runtime=require_runtime,
        )
    if scope in {"portfolio-manager", "both"}:
        portfolio = evaluate_portfolio_manager_activation(build_portfolio_manager_config(env))

    blockers: list[str] = []
    next_commands: dict[str, str] = {}
    for result in (sports, portfolio):
        if result is None:
            continue
        blockers.extend([f"{result.scope}: {blocker}" for blocker in result.blockers])
        next_commands.update({f"{result.scope}.{key}": value for key, value in result.next_commands.items()})
    ready = not blockers
    return LiveActivationPreflight(
        schema_version=LIVE_ACTIVATION_PREFLIGHT_SCHEMA_VERSION,
        scope=scope,
        mode="mixed" if scope == "both" else (sports or portfolio).mode if (sports or portfolio) else "unknown",
        ready=ready,
        status="ready_for_live_activation" if ready else "blocked",
        sports_live=sports,
        portfolio_manager=portfolio,
        blockers=blockers,
        next_commands=next_commands,
        execution_statement=NO_EXECUTION_STATEMENT,
    )


def build_live_activation_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--scope", choices=["sports-live", "portfolio-manager", "both"], default="both")
    parser.add_argument("--env-file")
    parser.add_argument("--mode", choices=["rehearsal", "live"])
    parser.add_argument("--session-date")
    parser.add_argument("--event-id", action="append", dest="event_ids", default=[])
    parser.add_argument("--account-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--live-money", action="store_true")
    parser.add_argument("--enable-llm-dispatch", action="store_true")
    parser.add_argument("--codex-reviewed-fallback-enabled", action="store_true")
    parser.add_argument("--max-intents", type=int)
    parser.add_argument("--probe-api", action="store_true", help="Check Janus status and live-worker status endpoints.")
    parser.add_argument("--require-ready", action="store_true", help="Exit nonzero unless every selected scope is ready.")
    parser.add_argument("--require-runtime", action="store_true", help="Require probed Janus/worker runtime readiness.")
    return parser


def build_env_overrides(args: Namespace) -> dict[str, str | None]:
    event_ids = ",".join(args.event_ids) if args.event_ids else None
    return {
        "JANUS_LIVE_ACTIVATION_MODE": args.mode,
        "JANUS_LIVE_TEST_SESSION_DATE": args.session_date,
        "JANUS_LIVE_TEST_EVENT_IDS": event_ids,
        "JANUS_LIVE_TEST_ACCOUNT_ID": args.account_id,
        "JANUS_LIVE_TEST_EXECUTE": "true" if args.execute else None,
        "JANUS_LIVE_TEST_LIVE_MONEY": "true" if args.live_money else None,
        "JANUS_LIVE_TEST_ENABLE_LLM_DISPATCH": "true" if args.enable_llm_dispatch else None,
        "JANUS_LIVE_TEST_CODEX_REVIEWED_FALLBACK_ENABLED": "true" if args.codex_reviewed_fallback_enabled else None,
        "JANUS_LIVE_TEST_MAX_INTENTS": str(args.max_intents) if args.max_intents is not None else None,
        "JANUS_PORTFOLIO_MANAGER_ACTIVATION_MODE": args.mode,
        "JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID": args.account_id,
    }


def main_for_live_activation_preflight(description: str) -> None:
    args = build_live_activation_parser(description).parse_args()
    env = merged_env(args.env_file, build_env_overrides(args))
    janus_status = None
    worker_status = None
    if args.probe_api:
        janus_status = api_json(args.api_root, "GET", "/v1/ops/status")
        worker_status = get_live_strategy_worker_status(args.api_root)
    report = build_live_activation_preflight(
        scope=args.scope,
        env=env,
        worker_status=worker_status,
        janus_status=janus_status,
        require_runtime=args.require_runtime or args.probe_api,
    )
    response = asdict(report)
    response["ok"] = report.ready or not args.require_ready
    exit_for_response(response)


def _build_worker_tick_command(config: dict[str, Any], *, execute: bool) -> str:
    parts = [
        "python",
        "codex_tool\\run_live_strategy_worker_tick.py",
        "--session-date",
        str(config.get("session_date") or "<YYYY-MM-DD>"),
    ]
    for event_id in config.get("event_ids") or ["<EVENT_ID>"]:
        parts.extend(["--event-id", str(event_id)])
    parts.extend(
        [
            "--account-id",
            str(config.get("account_id") or "<ACCOUNT_ID>"),
            "--max-intents",
            str(config.get("max_intents") or 2),
            "--min-size",
            str(config.get("min_size") or 5),
            "--min-buy-notional-usd",
            str(config.get("min_buy_notional_usd") or 1),
            "--max-buy-notional-usd",
            str(config.get("max_buy_notional_usd") or 10),
        ]
    )
    if execute:
        parts.extend(["--execute", "--live-money"])
    if config.get("enable_llm_dispatch"):
        parts.append("--enable-llm-dispatch")
    return " ".join(parts)


def _flatten_worker_status(worker_status: dict[str, Any]) -> dict[str, Any]:
    config = worker_status.get("config") if isinstance(worker_status.get("config"), dict) else {}
    worker = worker_status.get("worker") if isinstance(worker_status.get("worker"), dict) else {}
    status = worker_status.get("status") or worker.get("status") or config.get("status")
    event_ids = worker_status.get("event_ids") or worker.get("event_ids") or config.get("event_ids") or []
    return {
        "status": status,
        "running": str(status or "").lower() in {"running", "active", "ok"} or bool(worker_status.get("running")),
        "enabled": _nested_bool(worker_status, worker, config, key="enabled"),
        "execute": _nested_bool(worker_status, worker, config, key="execute"),
        "live_money": _nested_bool(worker_status, worker, config, key="live_money"),
        "account_id_configured": _nested_bool(worker_status, worker, config, key="account_id_configured"),
        "enable_llm_dispatch": _nested_bool(worker_status, worker, config, key="enable_llm_dispatch"),
        "event_ids": [str(item) for item in event_ids if str(item).strip()],
    }


def _nested_bool(*payloads: dict[str, Any], key: str) -> bool:
    for payload in payloads:
        if key in payload:
            return bool(payload.get(key))
    return False


def _is_uuid_text(value: str) -> bool:
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _redact(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _redacted_config(config: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(config)
    if "account_id" in redacted:
        redacted["account_id"] = _redact(redacted.get("account_id"))
    return redacted


def _compact_status(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "ok": payload.get("ok", True),
        "status": payload.get("status"),
        "error": payload.get("error"),
        "status_code": payload.get("status_code"),
    }


__all__ = [
    "LIVE_ACTIVATION_PREFLIGHT_SCHEMA_VERSION",
    "ActivationCheck",
    "ActivationScopeResult",
    "LiveActivationPreflight",
    "build_env_overrides",
    "build_live_activation_parser",
    "build_live_activation_preflight",
    "build_portfolio_manager_config",
    "build_sports_live_config",
    "env_bool",
    "evaluate_portfolio_manager_activation",
    "evaluate_sports_live_activation",
    "main_for_live_activation_preflight",
    "merged_env",
    "parse_env_file",
]
