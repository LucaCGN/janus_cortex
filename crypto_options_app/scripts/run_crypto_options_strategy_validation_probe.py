from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.db.connection import connect, count_rows  # noqa: E402
from crypto_options_app.strategies.registry import all_strategy_specs, get_strategy  # noqa: E402
from crypto_options_app.trading.live_candidates import verify_live_market_candidate  # noqa: E402
from crypto_options_app.trading.live_preflight import LiveEnvironmentFlags, TrustedCashBalanceSnapshot  # noqa: E402
from crypto_options_app.workers.live_minimal_validator import run_minimal_supervised_live_validation_batch  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded child-process strategy-validation probe with scoped live flags and a fake submitter."
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--strategy-ids", nargs="+", default=None)
    parser.add_argument("--token-id", default="probe-token-up")
    parser.add_argument("--event-key", default="btc-updown-5m-probe")
    parser.add_argument("--event-token-key", default="btc-updown-5m-probe:up")
    parser.add_argument("--event-slug", default="btc-updown-5m-probe")
    parser.add_argument("--outcome", default="Up")
    parser.add_argument("--best-bid", type=float, default=0.50)
    parser.add_argument("--best-ask", type=float, default=0.51)
    parser.add_argument("--ask-size", type=float, default=25.0)
    parser.add_argument("--depth-top3-ask-size", type=float, default=40.0)
    parser.add_argument("--quote-age-seconds", type=float, default=1.0)
    parser.add_argument("--validation-budget-cap-usd", type=float, default=50.0)
    parser.add_argument("--validation-budget-spent-usd", type=float, default=0.0)
    parser.add_argument("--cash-balance-hard-stop-usd", type=float, default=100.0)
    parser.add_argument("--trusted-cash-balance-usd", type=float, default=None)
    parser.add_argument("--trusted-cash-balance-source", default="strategy-validation-probe")
    parser.add_argument("--scoped-live-execute", action="store_true")
    parser.add_argument("--scoped-live-approved", action="store_true")
    parser.add_argument("--scoped-live-risk-ack", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _verified_candidate_from_args(args: argparse.Namespace):
    now_utc = datetime.now(UTC)
    observed_at = now_utc - timedelta(seconds=max(0.0, float(args.quote_age_seconds)))
    verification = verify_live_market_candidate(
        {
            "token_id": args.token_id,
            "event_key": args.event_key,
            "event_token_key": args.event_token_key,
            "event_slug": args.event_slug,
            "outcome": args.outcome,
            "best_bid": args.best_bid,
            "best_ask": args.best_ask,
            "spread": max(0.0, float(args.best_ask) - float(args.best_bid)),
            "ask_size": args.ask_size,
            "depth_top3_ask_size": args.depth_top3_ask_size,
            "observed_at_utc": observed_at.isoformat(),
            "event_end_time_utc": (now_utc + timedelta(minutes=10)).isoformat(),
            "source": "strategy_validation_probe",
        },
        now_utc=now_utc,
    )
    return verification


def _fake_submitter(order_request: dict[str, object]) -> dict[str, object]:
    app_order_key = str(order_request.get("app_order_key") or "probe-order")
    price = float(order_request.get("price") or 0.51)
    size = float(order_request.get("size") or 1.0)
    return {
        "success": True,
        "status": "submitted",
        "raw": {"orderID": f"probe:{app_order_key}"},
        "remote_order": {
            "id": f"probe:{app_order_key}",
            "status": "FILLED",
            "filledSize": str(size),
            "price": str(price),
        },
        "order_request": order_request,
    }


def _selected_strategy_ids(args: argparse.Namespace) -> tuple[str, ...]:
    if args.strategy_ids:
        return tuple(args.strategy_ids)
    return tuple(spec.strategy_id for spec in all_strategy_specs())


def main() -> int:
    args = build_parser().parse_args()
    run_id = args.run_id or f"strategy-validation-probe-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    verification = _verified_candidate_from_args(args)
    flags = LiveEnvironmentFlags(
        live_execute=bool(args.scoped_live_execute),
        execution_approved=bool(args.scoped_live_approved),
        risk_acknowledged=bool(args.scoped_live_risk_ack),
    )
    cash_balance_snapshot = (
        None
        if args.trusted_cash_balance_usd is None
        else TrustedCashBalanceSnapshot(
            balance_usd=float(args.trusted_cash_balance_usd),
            source=str(args.trusted_cash_balance_source),
        )
    )
    strategy_ids = _selected_strategy_ids(args)
    result = run_minimal_supervised_live_validation_batch(
        run_id=run_id,
        candidate_verifications=(verification,),
        credentials_ready=True,
        env_flags=flags,
        specs=tuple(get_strategy(strategy_id) for strategy_id in strategy_ids),
        operator="codex-automation",
        reason="Child-process strategy-validation probe with fake submitter",
        submitter=_fake_submitter,
        persist_db_path=Path(args.db_path),
        cash_balance_snapshot=cash_balance_snapshot,
        validation_budget_spent_usd=float(args.validation_budget_spent_usd),
        validation_budget_cap_usd=float(args.validation_budget_cap_usd),
        cash_balance_hard_stop_usd=float(args.cash_balance_hard_stop_usd),
    )
    with connect(Path(args.db_path)) as conn:
        db_counts = {
            "strategy_validation_runs": count_rows(conn, "strategy_validation_runs"),
            "validation_budget_ledger": count_rows(conn, "validation_budget_ledger"),
            "strategy_candidates": count_rows(conn, "strategy_candidates"),
            "orders": count_rows(conn, "orders"),
            "fills": count_rows(conn, "fills"),
            "positions": count_rows(conn, "positions"),
        }
    payload = {
        "schema_version": "crypto_options_strategy_validation_probe_v1",
        "run_id": run_id,
        "verified_candidate": verification.verified,
        "candidate_blockers": list(verification.blockers),
        "scoped_live_flags": {
            "live_execute": flags.live_execute,
            "execution_approved": flags.execution_approved,
            "risk_acknowledged": flags.risk_acknowledged,
        },
        "strategy_ids": list(strategy_ids),
        "result_count": len(result.results),
        "live_submission_attempted": result.live_submission_attempted,
        "manual_orders_avoided": result.manual_orders_avoided,
        "blocked_result_count": sum(1 for item in result.results if item.status == "blocked"),
        "statuses": [item.status for item in result.results],
        "blockers_by_result": [list(item.blockers) for item in result.results],
        "db_counts": db_counts,
        "trusted_cash_balance_usd": args.trusted_cash_balance_usd,
        "validation_budget_cap_usd": float(args.validation_budget_cap_usd),
        "validation_budget_spent_usd": float(args.validation_budget_spent_usd),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"run_id={run_id}")
        print(f"verified_candidate={verification.verified}")
        print(f"result_count={len(result.results)}")
        print(f"blocked_result_count={payload['blocked_result_count']}")
        print(f"live_submission_attempted={result.live_submission_attempted}")
        print(f"db_counts={json.dumps(db_counts, sort_keys=True)}")
    return 0 if verification.verified and payload["blocked_result_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
