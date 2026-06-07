from __future__ import annotations

from crypto_options_app.workers.comparison_runner import BundleComparisonRunResult


def render_bundle_comparison_report(
    *,
    dry_run_result: BundleComparisonRunResult,
    supervised_live_gate_result: BundleComparisonRunResult | None = None,
    generated_at_utc: str | None = None,
) -> str:
    generated = generated_at_utc or dry_run_result.generated_at_utc.isoformat()
    lines: list[str] = [
        "# Crypto Options App Bundle Comparison Execution",
        "",
        f"Generated UTC: {generated}",
        "",
        "## Scope",
        "",
        "- Mode executed: dry_run/shadow structural comparison",
        "- Live order path: not invoked",
        "- Manual orders avoided: yes",
        "",
        "## Dry-Run Result",
        "",
        f"- Run id: {dry_run_result.run_id}",
        f"- Groups: {len(dry_run_result.group_results)}",
        f"- Candidates: {_candidate_count(dry_run_result)}",
        f"- Remaining blockers: {len(dry_run_result.remaining_blockers)}",
        f"- Dry-run/shadow comparison may begin: {_yes_no(dry_run_result.dry_run_shadow_comparison_may_begin)}",
        "",
        "## Bundle Groups",
        "",
    ]
    for group in dry_run_result.group_results:
        lines.extend(
            [
                f"### {group.group_id}",
                "",
                f"- Status: {group.status}",
                "",
                "| Candidate | Status | Volume | First sizing | Cap | Live orders | Blockers |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for candidate in group.candidate_results:
            cap = f"50 trades" if candidate.high_volume_trade_cap else f"{candidate.low_volume_event_target} events"
            lines.append(
                "| "
                + " | ".join(
                    (
                        candidate.strategy_id,
                        candidate.status,
                        candidate.volume_class,
                        candidate.first_run_sizing,
                        f"{cap}; max {candidate.hard_event_cap} events / {candidate.hard_time_limit_seconds // 60} min",
                        "enabled" if candidate.orders_allowed or candidate.live_trading_authorized else "not invoked",
                        ", ".join(candidate.blockers) if candidate.blockers else "none",
                    )
                )
                + " |"
            )
        lines.append("")
    if supervised_live_gate_result is not None:
        lines.extend(
            [
                "## Supervised-Live Gate Check",
                "",
                f"- Run id: {supervised_live_gate_result.run_id}",
                f"- Remaining blockers: {len(supervised_live_gate_result.remaining_blockers)}",
                f"- Supervised-live comparison may begin: {_yes_no(False)}",
                "",
                "Observed blockers:",
                "",
            ]
        )
        for blocker in sorted(set(supervised_live_gate_result.remaining_blockers)):
            lines.append(f"- {blocker}")
        lines.append("")
    lines.extend(
        [
            "## Readiness Statement",
            "",
            "Dry-run/shadow strategy bundle comparison completed for component validation.",
            "",
            "Actual supervised-live strategy comparison must not begin until executor boundary configuration, ledger gate, risk gate, reconciliation gate, execution approval, and live risk acknowledgement are explicitly enabled through the supervised runtime.",
            "",
            "No live order path was invoked.",
        ]
    )
    return "\n".join(lines) + "\n"


def _candidate_count(result: BundleComparisonRunResult) -> int:
    return sum(len(group.candidate_results) for group in result.group_results)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
