from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from crypto_options_app.pipelines.options.live_micro_executor import (
    CRYPTO_OPTIONS_LIVE_EXECUTION_LEDGER_SCHEMA_VERSION,
    reconcile_live_execution_ledger,
)
from crypto_options_app.pipelines.options.v2_candidates import default_v3_validation_candidate_configs
from crypto_options_app.services.crypto_options.v3_observer import load_observer_snapshot


REPLAY_READINESS_SCHEMA_VERSION = "crypto_options_v3_replay_readiness_v1"
PASSIVE_RECONCILIATION_SCHEMA_VERSION = "crypto_options_v3_passive_reconciliation_v1"


def build_v3_replay_readiness_report(
    run_root: str | Path,
    *,
    now_utc: datetime | None = None,
    min_restart_win_rate: float = 0.60,
    ideal_win_rate: float = 0.70,
    max_worst_case_loss_usd: float = 25.0,
    include_passive_reconciliation_preview: bool = False,
    settlement_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    snapshot = load_observer_snapshot(run_root, now_utc=now_utc)
    components = snapshot.get("components") or []
    bucket_rows = snapshot.get("bucket_rows") or []
    attribution_rows = snapshot.get("source_profile_attribution_rows") or []
    profile_rows = snapshot.get("profile_rows") or []
    passive_preview = (
        build_v3_passive_reconciliation_preview(
            run_root,
            now_utc=now_utc,
            settlement_resolver=settlement_resolver,
        )
        if include_passive_reconciliation_preview
        else None
    )
    blockers: list[str] = []
    warnings: list[str] = []

    active_components = [row for row in components if int(row.get("submitted_buys") or 0) > 0 or row.get("latest_packet_status")]
    if not active_components:
        blockers.append("no_active_component_evidence")

    for row in active_components:
        component_id = str(row.get("component_id") or "unknown")
        win_rate = _float(row.get("win_rate"))
        settled = int(row.get("settled_buys") or 0)
        worst_case = _float(row.get("worst_case_loss_usd")) or 0.0
        open_buys = int(row.get("open_buys") or 0)
        if row.get("disabled"):
            blockers.append(f"{component_id}:component_disabled:{row.get('disabled_reason')}")
        if open_buys > 0:
            blockers.append(f"{component_id}:open_buys_require_reconciliation:{open_buys}")
        if worst_case > max_worst_case_loss_usd:
            blockers.append(f"{component_id}:worst_case_loss_above_restart_limit:{worst_case:.2f}")
        if settled > 0 and win_rate is not None and win_rate < min_restart_win_rate:
            blockers.append(f"{component_id}:win_rate_below_restart_floor:{win_rate:.3f}")
        if settled > 0 and win_rate is not None and win_rate < ideal_win_rate:
            warnings.append(f"{component_id}:win_rate_below_ideal:{win_rate:.3f}")

    if any(str(row.get("profile") or "") == "unattributed" for row in attribution_rows):
        blockers.append("source_profile_attribution_missing_for_existing_rows")

    if not profile_rows:
        warnings.append("profile_universe_not_visible_in_snapshot")
    else:
        strong_profiles = [row for row in profile_rows if str(row.get("grade") or "").upper() in {"S", "S+", "S++"}]
        if len(strong_profiles) < 3:
            blockers.append(f"profile_universe_too_few_s_profiles:{len(strong_profiles)}")
        elif len(strong_profiles) < 10:
            warnings.append(f"profile_universe_low_s_profile_count:{len(strong_profiles)}")
        for row in strong_profiles:
            daily_pnl = _float(row.get("daily_pnl"))
            if daily_pnl is not None and daily_pnl < 0.0:
                blockers.append(f"s_profile_daily_loss:{_profile_label(row)}")

    for row in bucket_rows:
        bucket = str(row.get("bucket") or "")
        win_rate = _float(row.get("win_rate"))
        realized = _float(row.get("realized_pnl_usd")) or 0.0
        open_buys = int(row.get("open_buys") or 0)
        if bucket in {"0.05-0.10", "0.10-0.15", "0.15-0.20", "0.20-0.25"} and (realized < 0.0 or open_buys > 0):
            blockers.append(f"low_bucket_quarantine_required:{bucket}")
        if win_rate is not None and win_rate < min_restart_win_rate and int(row.get("settled_buys") or 0) >= 3:
            warnings.append(f"bucket_win_rate_below_restart_floor:{bucket}:{win_rate:.3f}")

    if passive_preview:
        for row in passive_preview.get("components") or []:
            component_id = str(row.get("component_id") or "unknown")
            post_win_rate = _float(row.get("post_win_rate"))
            post_settled = int(row.get("post_settled_buys") or 0)
            post_open = int(row.get("post_open_buys") or 0)
            post_worst_case = _float(row.get("post_worst_case_loss_usd")) or 0.0
            newly_settled = int(row.get("newly_settled_buys") or 0)
            newly_lost = int(row.get("newly_settled_losses") or 0)
            if post_open > 0:
                blockers.append(f"{component_id}:post_reconcile_open_buys_remain:{post_open}")
            if post_worst_case > max_worst_case_loss_usd:
                blockers.append(f"{component_id}:post_reconcile_worst_case_loss_above_restart_limit:{post_worst_case:.2f}")
            if post_settled > 0 and post_win_rate is not None and post_win_rate < min_restart_win_rate:
                blockers.append(f"{component_id}:post_reconcile_win_rate_below_restart_floor:{post_win_rate:.3f}")
            if newly_settled > 0 and newly_lost == newly_settled:
                warnings.append(f"{component_id}:passive_reconciliation_newly_settled_all_losses:{newly_settled}")

    fresh_iteration_readiness = _fresh_iteration_readiness(
        profile_rows=profile_rows,
        prior_root_blockers=blockers,
    )
    ready = not blockers
    restart_recommendation = _restart_recommendation(ready=ready, blockers=blockers)
    payload = {
        "schema_version": REPLAY_READINESS_SCHEMA_VERSION,
        "generated_at_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "run_root": str(Path(run_root)),
        "ready_for_live_restart": ready,
        "restart_recommendation": restart_recommendation,
        "min_restart_win_rate": min_restart_win_rate,
        "ideal_win_rate": ideal_win_rate,
        "max_worst_case_loss_usd": max_worst_case_loss_usd,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "component_summary": [
            {
                "component_id": row.get("component_id"),
                "submitted_buys": row.get("submitted_buys"),
                "settled_buys": row.get("settled_buys"),
                "open_buys": row.get("open_buys"),
                "wins": row.get("wins"),
                "losses": row.get("losses"),
                "win_rate": row.get("win_rate"),
                "realized_pnl_usd": row.get("realized_pnl_usd"),
                "active_cost_usd": row.get("active_cost_usd"),
                "worst_case_loss_usd": row.get("worst_case_loss_usd"),
                "disabled": row.get("disabled"),
                "disabled_reason": row.get("disabled_reason"),
            }
            for row in active_components
        ],
        "bucket_rows": bucket_rows,
        "source_profile_attribution_rows": attribution_rows,
        "profile_sanity": _profile_sanity_summary(profile_rows),
        "fresh_iteration_readiness": fresh_iteration_readiness,
        "snapshot_warnings": snapshot.get("warnings") or [],
        "manual_orders_allowed": False,
    }
    if passive_preview:
        payload["passive_reconciliation_preview"] = passive_preview
    return payload


def build_v3_passive_reconciliation_preview(
    run_root: str | Path,
    *,
    now_utc: datetime | None = None,
    settlement_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    components = []
    for ledger_path in _component_ledger_paths(run_root):
        before = _load_json_object(ledger_path)
        after = reconcile_live_execution_ledger(
            _ledger_with_schema(before),
            now=now_utc,
            resolver=settlement_resolver,
        )
        before_summary = _ledger_buy_summary(before)
        after_summary = _ledger_buy_summary(after)
        newly_settled = _newly_settled_open_buys(before, after)
        newly_pnl = sum(_float(row.get("realized_pnl_net_usd")) or 0.0 for row in newly_settled)
        newly_wins = sum(1 for row in newly_settled if (_float(row.get("realized_pnl_net_usd")) or 0.0) > 0.0)
        newly_losses = sum(1 for row in newly_settled if (_float(row.get("realized_pnl_net_usd")) or 0.0) < 0.0)
        components.append(
            {
                "component_id": _component_id_from_ledger_path(ledger_path),
                "ledger_path": str(ledger_path),
                "changed": before != after,
                "pre_open_buys": before_summary["open_buys"],
                "post_open_buys": after_summary["open_buys"],
                "newly_settled_buys": len(newly_settled),
                "newly_settled_wins": newly_wins,
                "newly_settled_losses": newly_losses,
                "newly_settled_pnl_usd": round(newly_pnl, 6),
                "post_settled_buys": after_summary["settled_buys"],
                "post_wins": after_summary["wins"],
                "post_losses": after_summary["losses"],
                "post_win_rate": after_summary["win_rate"],
                "post_realized_pnl_usd": after_summary["realized_pnl_usd"],
                "post_active_cost_usd": after_summary["active_cost_usd"],
                "post_worst_case_loss_usd": after_summary["worst_case_loss_usd"],
            }
        )
    return {
        "schema_version": PASSIVE_RECONCILIATION_SCHEMA_VERSION,
        "generated_at_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "run_root": str(Path(run_root)),
        "components": components,
        "manual_orders_allowed": False,
    }


def apply_v3_passive_reconciliation(
    run_root: str | Path,
    *,
    now_utc: datetime | None = None,
    settlement_resolver: Callable[[str], dict[str, Any]] | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for ledger_path in _component_ledger_paths(run_root):
        before = _load_json_object(ledger_path)
        after = reconcile_live_execution_ledger(_ledger_with_schema(before), now=now_utc, resolver=settlement_resolver)
        changed = before != after
        backup_path = None
        if changed:
            if backup:
                backup_path = ledger_path.with_name(f"{ledger_path.name}.bak-{stamp}")
                shutil.copy2(ledger_path, backup_path)
            ledger_path.write_text(json.dumps(after, indent=2, sort_keys=True), encoding="utf-8")
        newly_settled = _newly_settled_open_buys(before, after)
        rows.append(
            {
                "component_id": _component_id_from_ledger_path(ledger_path),
                "ledger_path": str(ledger_path),
                "changed": changed,
                "backup_path": str(backup_path) if backup_path else None,
                "newly_settled_buys": len(newly_settled),
                "newly_settled_pnl_usd": round(sum(_float(row.get("realized_pnl_net_usd")) or 0.0 for row in newly_settled), 6),
            }
        )
    return {
        "schema_version": PASSIVE_RECONCILIATION_SCHEMA_VERSION,
        "generated_at_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "run_root": str(Path(run_root)),
        "applied": True,
        "backup_enabled": bool(backup),
        "components": rows,
        "manual_orders_allowed": False,
    }


def write_v3_replay_readiness_artifacts(payload: dict[str, Any], *, output_dir: str | Path | None = None) -> dict[str, str]:
    root = Path(output_dir) if output_dir is not None else Path(str(payload.get("run_root") or ".")) / "reports"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = root / f"crypto_options_v3_replay_readiness_{stamp}.json"
    md_path = root / f"crypto_options_v3_replay_readiness_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_v3_replay_readiness_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def render_v3_replay_readiness_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options V3 Replay Readiness",
        "",
        f"- Run root: `{payload.get('run_root')}`",
        f"- Ready for live restart: `{payload.get('ready_for_live_restart')}`",
        f"- Recommendation: `{payload.get('restart_recommendation')}`",
        "",
        "## Blockers",
    ]
    blockers = payload.get("blockers") or []
    lines.extend(f"- `{item}`" for item in blockers) if blockers else lines.append("- None")
    lines.extend(["", "## Components", "| Component | Submitted | Settled | Open | WR | PnL | Worst Case | Disabled |", "|---|---:|---:|---:|---:|---:|---:|---|"])
    for row in payload.get("component_summary") or []:
        lines.append(
            "| {component} | {submitted} | {settled} | {open_} | {wr} | {pnl} | {worst} | {disabled} |".format(
                component=row.get("component_id"),
                submitted=row.get("submitted_buys"),
                settled=row.get("settled_buys"),
                open_=row.get("open_buys"),
                wr=_fmt_pct(row.get("win_rate")),
                pnl=_fmt_money(row.get("realized_pnl_usd")),
                worst=_fmt_money(row.get("worst_case_loss_usd")),
                disabled=row.get("disabled_reason") or row.get("disabled"),
            )
        )
    lines.extend(["", "## Bucket Rows", "| Component | Bucket | Submitted | Settled | Open | WR | PnL | Active Cost |", "|---|---|---:|---:|---:|---:|---:|---:|"])
    for row in payload.get("bucket_rows") or []:
        lines.append(
            f"| {row.get('component_id')} | {row.get('bucket')} | {row.get('submitted_buys')} | {row.get('settled_buys')} | {row.get('open_buys')} | {_fmt_pct(row.get('win_rate'))} | {_fmt_money(row.get('realized_pnl_usd'))} | {_fmt_money(row.get('active_cost_usd'))} |"
        )
    applied = payload.get("applied_passive_reconciliation") if isinstance(payload.get("applied_passive_reconciliation"), dict) else None
    if applied:
        lines.extend(["", "## Applied Passive Reconciliation", "| Component | Changed | Newly Settled | Newly Settled PnL | Backup |", "|---|---:|---:|---:|---|"])
        for row in applied.get("components") or []:
            lines.append(
                f"| {row.get('component_id')} | {row.get('changed')} | {row.get('newly_settled_buys')} | {_fmt_money(row.get('newly_settled_pnl_usd'))} | `{row.get('backup_path')}` |"
            )
    preview = payload.get("passive_reconciliation_preview") if isinstance(payload.get("passive_reconciliation_preview"), dict) else None
    if preview:
        lines.extend(["", "## Passive Reconciliation Preview", "| Component | Pre Open | Post Open | Newly Settled | New PnL | Post WR | Post PnL | Worst Case |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
        for row in preview.get("components") or []:
            lines.append(
                f"| {row.get('component_id')} | {row.get('pre_open_buys')} | {row.get('post_open_buys')} | {row.get('newly_settled_buys')} | {_fmt_money(row.get('newly_settled_pnl_usd'))} | {_fmt_pct(row.get('post_win_rate'))} | {_fmt_money(row.get('post_realized_pnl_usd'))} | {_fmt_money(row.get('post_worst_case_loss_usd'))} |"
            )
    fresh = payload.get("fresh_iteration_readiness") if isinstance(payload.get("fresh_iteration_readiness"), dict) else None
    if fresh:
        lines.extend(["", "## Fresh Iteration Readiness", f"- Ready: `{fresh.get('ready_for_fresh_iteration')}`"])
        fresh_blockers = fresh.get("blockers") or []
        if fresh_blockers:
            lines.append("- Blockers:")
            lines.extend(f"  - `{item}`" for item in fresh_blockers)
        else:
            lines.append("- Blockers: None")
        fresh_warnings = fresh.get("warnings") or []
        if fresh_warnings:
            lines.append("- Warnings:")
            lines.extend(f"  - `{item}`" for item in fresh_warnings)
    return "\n".join(lines) + "\n"


def _component_ledger_paths(run_root: str | Path) -> list[Path]:
    ledger_root = Path(run_root) / "v2-ledgers"
    if not ledger_root.exists():
        return []
    return sorted(ledger_root.glob("candidate_ledger_*.json"))


def _fresh_iteration_readiness(*, profile_rows: list[dict[str, Any]], prior_root_blockers: list[str]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    configs = default_v3_validation_candidate_configs(ledger_root="fresh_iteration_probe_ledgers")
    if len(configs) != 1:
        blockers.append(f"fresh_config_unexpected_component_count:{len(configs)}")
    config = configs[0] if configs else {}
    entry_policy = config.get("entry_policy") if isinstance(config.get("entry_policy"), dict) else {}
    promotion_policy = config.get("promotion_policy") if isinstance(config.get("promotion_policy"), dict) else {}
    exposure_policy = config.get("exposure_policy") if isinstance(config.get("exposure_policy"), dict) else {}

    if config.get("validation_blockers"):
        blockers.append("fresh_config_validation_blockers_present")
    if set(entry_policy.get("direct_signal_grades") or []) != {"S++", "S+"}:
        blockers.append("fresh_config_direct_grades_not_elite_only")
    if set(entry_policy.get("confirmation_grades") or []) != {"S", "A"}:
        blockers.append("fresh_config_confirmation_grades_not_s_a_only")
    if set(entry_policy.get("inverse_validator_grades") or []) != {"E", "U"}:
        blockers.append("fresh_config_inverse_validator_grades_not_eu_only")
    if set(entry_policy.get("excluded_signal_grades") or []) != {"B", "C", "D"}:
        blockers.append("fresh_config_bcd_not_excluded")
    if not entry_policy.get("require_direct_signal_grade"):
        blockers.append("fresh_config_missing_direct_signal_requirement")
    if not entry_policy.get("cashout_required"):
        blockers.append("fresh_config_cashout_not_required")
    if not entry_policy.get("cashout_requires_pairable_entry"):
        blockers.append("fresh_config_pairable_cashout_not_required")
    if (_float(entry_policy.get("min_order_notional_usd")) or 0.0) < 5.0:
        blockers.append("fresh_config_min_order_notional_below_5")
    if (_float(entry_policy.get("min_pairable_filled_shares")) or 0.0) < 5.0:
        blockers.append("fresh_config_min_pairable_shares_below_5")
    if (_float(exposure_policy.get("min_order_notional_usd")) or 0.0) < 5.0:
        blockers.append("fresh_exposure_min_order_notional_below_5")

    blocked_buckets = set(entry_policy.get("blocked_entry_buckets") or [])
    quarantined_buckets = set(entry_policy.get("quarantined_entry_buckets") or [])
    experimental_buckets = set(entry_policy.get("experimental_entry_buckets") or [])
    required_blocked = {"0.00-0.05", "0.55-0.70", "0.70-1.00"}
    required_quarantined = {"0.05-0.25"}
    missing_blocked = sorted(required_blocked - blocked_buckets)
    missing_quarantined = sorted(required_quarantined - quarantined_buckets)
    if missing_blocked:
        blockers.append(f"fresh_config_missing_blocked_buckets:{','.join(missing_blocked)}")
    if missing_quarantined:
        blockers.append(f"fresh_config_missing_quarantined_buckets:{','.join(missing_quarantined)}")
    overlap = sorted((blocked_buckets | quarantined_buckets) & experimental_buckets)
    if overlap:
        blockers.append(f"fresh_config_experimental_overlaps_blocked_or_quarantined:{','.join(overlap)}")

    if int(promotion_policy.get("max_submitted_entry_groups") or 0) != 100:
        blockers.append("fresh_config_trade_cap_not_100")
    if (_float(promotion_policy.get("hard_stop_loss_usd")) or 0.0) > 50.0:
        blockers.append("fresh_config_hard_stop_loss_above_50")
    if not promotion_policy.get("tiered_loss_stops"):
        blockers.append("fresh_config_missing_tiered_loss_stops")
    if not promotion_policy.get("profit_giveback_min_peak_pnl_usd"):
        blockers.append("fresh_config_missing_profit_giveback_gate")

    profile_sanity = _profile_sanity_summary(profile_rows)
    grade_counts = profile_sanity.get("grade_counts") or {}
    strong_count = int(grade_counts.get("S") or 0) + int(grade_counts.get("S+") or 0) + int(grade_counts.get("S++") or 0)
    if not profile_rows:
        blockers.append("fresh_profile_universe_not_visible")
    if strong_count < 3:
        blockers.append(f"fresh_profile_universe_too_few_s_profiles:{strong_count}")
    elif strong_count < 10:
        warnings.append(f"fresh_profile_universe_low_s_profile_count:{strong_count}")
    if profile_sanity.get("s_daily_loss_profiles"):
        blockers.append("fresh_profile_s_daily_loss_present")

    stale_root_only = {
        "source_profile_attribution_missing_for_existing_rows",
    }
    inherited_blockers = [blocker for blocker in prior_root_blockers if blocker not in stale_root_only]
    if any("open_buys_require_reconciliation" in blocker or "post_reconcile_open_buys_remain" in blocker for blocker in inherited_blockers):
        blockers.append("fresh_iteration_blocked_until_prior_open_buys_reconciled")
    if any("stale" in blocker for blocker in inherited_blockers):
        blockers.append("fresh_iteration_blocked_by_service_staleness")

    return {
        "ready_for_fresh_iteration": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "candidate_config_mode": "v3-validation-single",
        "candidate_id": config.get("candidate_id"),
        "profile_sanity": profile_sanity,
        "manual_orders_allowed": False,
    }


def _restart_recommendation(*, ready: bool, blockers: list[str]) -> str:
    if ready:
        return "restart_allowed_by_replay_gates"
    if any("open_buys_require_reconciliation" in blocker or "post_reconcile_open_buys_remain" in blocker for blocker in blockers):
        return "do_not_restart_reconcile_first"
    performance_tokens = (
        "component_disabled",
        "win_rate_below_restart_floor",
        "worst_case_loss_above_restart_limit",
        "low_bucket_quarantine_required",
    )
    if any(any(token in blocker for token in performance_tokens) for blocker in blockers):
        return "do_not_restart_current_root_start_new_iteration_after_patch"
    return "do_not_restart_patch_or_reconcile_first"


def _component_id_from_ledger_path(path: Path) -> str:
    stem = path.stem
    prefix = "candidate_ledger_"
    return stem[len(prefix) :] if stem.startswith(prefix) else stem


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _ledger_with_schema(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") == CRYPTO_OPTIONS_LIVE_EXECUTION_LEDGER_SCHEMA_VERSION:
        return payload
    if isinstance(payload.get("entries"), list):
        return {**payload, "schema_version": CRYPTO_OPTIONS_LIVE_EXECUTION_LEDGER_SCHEMA_VERSION}
    return payload


def _newly_settled_open_buys(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_open_keys = {
        _entry_key(row)
        for row in before.get("entries") or []
        if isinstance(row, dict) and _is_submitted_buy(row) and str(row.get("settlement_status") or "") == "open_requires_reconciliation"
    }
    return [
        row
        for row in after.get("entries") or []
        if isinstance(row, dict) and _is_submitted_buy(row) and _entry_key(row) in before_open_keys and _is_settled(row)
    ]


def _ledger_buy_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    buy_rows = [row for row in ledger.get("entries") or [] if isinstance(row, dict) and _is_submitted_buy(row)]
    settled = [row for row in buy_rows if _is_settled(row)]
    open_rows = [row for row in buy_rows if str(row.get("settlement_status") or "") == "open_requires_reconciliation"]
    pnl = sum(_float(row.get("realized_pnl_net_usd")) or 0.0 for row in settled)
    wins = sum(1 for row in settled if (_float(row.get("realized_pnl_net_usd")) or 0.0) > 0.0)
    losses = sum(1 for row in settled if (_float(row.get("realized_pnl_net_usd")) or 0.0) < 0.0)
    active_cost = sum(_entry_cost(row) for row in open_rows)
    return {
        "submitted_buys": len(buy_rows),
        "settled_buys": len(settled),
        "open_buys": len(open_rows),
        "wins": wins,
        "losses": losses,
        "win_rate": (float(wins) / len(settled)) if settled else None,
        "realized_pnl_usd": round(pnl, 6),
        "active_cost_usd": round(active_cost, 6),
        "worst_case_loss_usd": round(max(0.0, -pnl) + active_cost, 6),
    }


def _is_submitted_buy(row: dict[str, Any]) -> bool:
    return str(row.get("side") or "").upper() == "BUY" and str(row.get("status") or "").lower() == "submitted"


def _is_settled(row: dict[str, Any]) -> bool:
    return str(row.get("settlement_status") or "").lower() in {"settled", "closed"} or row.get("realized_pnl_net_usd") is not None


def _entry_key(row: dict[str, Any]) -> str:
    return str(row.get("idempotency_key") or row.get("entry_id") or row.get("ledger_entry_id") or id(row))


def _entry_cost(row: dict[str, Any]) -> float:
    quality = row.get("execution_quality") if isinstance(row.get("execution_quality"), dict) else {}
    for value in (quality.get("filled_notional_usd"), row.get("estimated_total_cost_usd")):
        parsed = _float(value)
        if parsed is not None:
            return parsed
    price = _float(quality.get("realized_price")) or _float(row.get("price")) or 0.0
    shares = _float(quality.get("filled_shares")) or _float(row.get("size")) or 0.0
    return float(price) * float(shares)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _profile_sanity_summary(profile_rows: list[dict[str, Any]]) -> dict[str, Any]:
    grade_counts: dict[str, int] = {}
    s_daily_loss_profiles = []
    for row in profile_rows:
        grade = str(row.get("grade") or "unknown").upper()
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
        if grade in {"S", "S+", "S++"} and (_float(row.get("daily_pnl")) or 0.0) < 0.0:
            s_daily_loss_profiles.append(_profile_label(row))
    return {
        "visible_profile_count": len(profile_rows),
        "grade_counts": dict(sorted(grade_counts.items())),
        "s_daily_loss_profiles": s_daily_loss_profiles,
    }


def _profile_label(row: dict[str, Any]) -> str:
    profile = row.get("profile")
    if isinstance(profile, dict):
        return str(profile.get("name") or profile.get("handle") or profile.get("proxy_wallet") or "unknown")
    return str(profile or "unknown")


def _fmt_pct(value: Any) -> str:
    parsed = _float(value)
    return "n/a" if parsed is None else f"{parsed * 100:.1f}%"


def _fmt_money(value: Any) -> str:
    parsed = _float(value)
    return "n/a" if parsed is None else f"${parsed:.2f}"


__all__ = [
    "PASSIVE_RECONCILIATION_SCHEMA_VERSION",
    "REPLAY_READINESS_SCHEMA_VERSION",
    "apply_v3_passive_reconciliation",
    "build_v3_passive_reconciliation_preview",
    "build_v3_replay_readiness_report",
    "render_v3_replay_readiness_markdown",
    "write_v3_replay_readiness_artifacts",
]
