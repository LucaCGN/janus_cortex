"""Compatibility bridge for the legacy quote-aware live strategy tick.

The orchestration still lives in ``codex_tool.run_live_strategy_tick`` because
it is a large execution-sensitive path. This module owns the target namespace
CLI surface and keeps the legacy entrypoint explicit while the orchestration is
migrated in smaller reviewed slices.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from importlib import import_module
from typing import Any, Callable

from codex_tools.janus.client import base_parser, exit_for_response

LIVE_STRATEGY_TICK_COMPATIBILITY_STATE = "legacy_quote_aware_orchestration"
LIVE_STRATEGY_TICK_LEGACY_MODULE = "codex_tool.run_live_strategy_tick"
LIVE_STRATEGY_TICK_EXECUTION_SENSITIVE_FLAGS = (
    "--execute",
    "--live-money",
    "--auto-protect-manual-positions",
    "--submit-candidate-strategy-plan",
    "--enable-llm-dispatch",
)


def build_live_strategy_tick_parser(description: str) -> ArgumentParser:
    """Build the parser for the quote-aware live strategy tick compatibility CLI."""
    parser = base_parser(description)
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
    parser.add_argument("--max-buy-notional-usd", type=float, default=None)
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
    return parser


def build_live_strategy_tick_kwargs(args: Namespace) -> dict[str, Any]:
    """Translate parsed CLI args into legacy ``run_tick`` keyword arguments."""
    return {
        "api_root": args.api_root,
        "session_date": args.session_date,
        "event_ids": args.event_ids,
        "account_id": args.account_id,
        "source": args.source,
        "execute": args.execute,
        "live_money": args.live_money,
        "max_intents": args.max_intents,
        "orderbook_sample_count": args.orderbook_sample_count,
        "orderbook_sample_interval_sec": args.orderbook_sample_interval_sec,
        "min_size": args.min_size,
        "min_buy_notional_usd": args.min_buy_notional_usd,
        "max_buy_notional_usd": args.max_buy_notional_usd,
        "share_precision": args.share_precision,
        "auto_protect_manual_positions": args.auto_protect_manual_positions,
        "manual_target_delta_cents": args.manual_target_delta_cents,
        "submit_candidate_strategy_plan": args.submit_candidate_strategy_plan,
        "enable_llm_dispatch": args.enable_llm_dispatch,
        "llm_runtime_artifact_root": args.llm_runtime_artifact_root,
    }


def describe_live_strategy_tick_compatibility() -> dict[str, Any]:
    """Return the current migration status for the execution-sensitive live tick path."""
    return {
        "state": LIVE_STRATEGY_TICK_COMPATIBILITY_STATE,
        "legacy_module": LIVE_STRATEGY_TICK_LEGACY_MODULE,
        "target_namespace": "codex_tools.janus.live_strategy_tick",
        "execution_sensitive_flags": list(LIVE_STRATEGY_TICK_EXECUTION_SENSITIVE_FLAGS),
        "order_path_boundary": "legacy orchestration can call Janus portfolio/order endpoints only when invoked with execution flags",
        "migration_note": "parser and compatibility metadata live in the target namespace; orchestration remains legacy pending a higher-risk reviewed slice",
    }


def load_legacy_run_tick() -> Callable[..., dict[str, Any]]:
    """Load the legacy orchestration lazily to avoid import-time circular coupling."""
    module = import_module(LIVE_STRATEGY_TICK_LEGACY_MODULE)
    return module.run_tick


def run_legacy_live_strategy_tick(**kwargs: Any) -> dict[str, Any]:
    """Run the legacy quote-aware orchestration with target-namespace CLI kwargs."""
    return load_legacy_run_tick()(**kwargs)


def main_for_live_strategy_tick(description: str) -> None:
    """Parse args with the target namespace and run the legacy orchestration."""
    args = build_live_strategy_tick_parser(description).parse_args()
    exit_for_response(run_legacy_live_strategy_tick(**build_live_strategy_tick_kwargs(args)))
