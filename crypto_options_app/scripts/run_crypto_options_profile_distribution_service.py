from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.data_services.profile_distribution_service import (  # noqa: E402
    ProfileDistributionConfig,
    capture_top_profile_distributions_once,
)
from crypto_options_app.db.errors import is_transient_database_error  # noqa: E402
from crypto_options_app.scripts._status_io import write_json_atomically  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute top-profile Up/Down distribution signals.")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--active-profile-pool", default=None)
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--allowed-grades", nargs="+", default=["S++", "S+", "S"])
    parser.add_argument("--max-profiles", type=int, default=120)
    parser.add_argument("--lookback-minutes", type=int, default=5)
    parser.add_argument("--lookahead-minutes", type=int, default=15)
    parser.add_argument("--target-refresh-seconds", type=int, default=30)
    parser.add_argument("--max-source-age-seconds", type=int, default=90)
    parser.add_argument("--canonical-method", default="cost_weighted")
    parser.add_argument("--include-external-fetch", action="store_true")
    parser.add_argument("--external-activity-limit", type=int, default=100)
    parser.add_argument("--external-position-limit", type=int, default=100)
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def _config(args: argparse.Namespace) -> ProfileDistributionConfig:
    defaults = ProfileDistributionConfig()
    return ProfileDistributionConfig(
        db_path=Path(args.db_path) if args.db_path else defaults.db_path,
        active_profile_pool_path=Path(args.active_profile_pool) if args.active_profile_pool else defaults.active_profile_pool_path,
        symbols=tuple(str(symbol).upper() for symbol in args.symbols),
        allowed_grades=tuple(str(grade).upper() for grade in args.allowed_grades),
        max_profiles=max(1, int(args.max_profiles)),
        lookback_minutes=max(0, int(args.lookback_minutes)),
        lookahead_minutes=max(0, int(args.lookahead_minutes)),
        target_refresh_seconds=max(1, int(args.target_refresh_seconds)),
        max_source_age_seconds=max(1, int(args.max_source_age_seconds)),
        canonical_method=str(args.canonical_method),
        include_external_fetch=bool(args.include_external_fetch),
        external_activity_limit=max(1, int(args.external_activity_limit)),
        external_position_limit=max(1, int(args.external_position_limit)),
        max_concurrency=max(1, int(args.max_concurrency)),
        timeout_seconds=max(1.0, float(args.timeout_seconds)),
    )


def _summary_dict(summary) -> dict:
    payload = {
        "generated_at_utc": summary.generated_at_utc,
        "status": summary.status,
        "db_path": summary.db_path,
        "event_count": summary.event_count,
        "snapshot_rows_inserted": summary.snapshot_rows_inserted,
        "component_rows_inserted": summary.component_rows_inserted,
        "readiness_rows_inserted": summary.readiness_rows_inserted,
        "blockers": list(summary.blockers),
        "distributions": list(summary.distributions),
        "state": summary.state,
        "orders_allowed": summary.orders_allowed,
        "live_trading_authorized": summary.live_trading_authorized,
    }
    return payload


def _write_state(path: str | None, payload: dict) -> None:
    if not path:
        return
    write_json_atomically(path, payload)


def run_once(args: argparse.Namespace) -> dict:
    summary = _capture_with_transient_db_lock_retries(args)
    payload = _summary_dict(summary)
    _write_state(args.state_path, payload)
    return payload


def run_loop(args: argparse.Namespace) -> dict:
    latest: dict = {}
    iteration = 0
    while True:
        iteration += 1
        try:
            latest = run_once(args)
            latest["iteration"] = iteration
            _write_state(args.state_path, latest)
            print(
                "profile_distribution_tick "
                f"iteration={iteration} status={latest.get('status')} "
                f"events={latest.get('event_count')} snapshots={latest.get('snapshot_rows_inserted')} "
                f"components={latest.get('component_rows_inserted')}",
                flush=True,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - loop writes failure state and keeps attempting.
            status = "degraded" if is_transient_database_error(exc) else "failed"
            latest = {
                "iteration": iteration,
                "status": status,
                "error": f"{type(exc).__name__}:{exc}",
                "blockers": ["transient_db_lock_retry_exhausted"] if status == "degraded" else [],
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
            }
            _write_state(args.state_path, latest)
            print(f"profile_distribution_tick iteration={iteration} status={status} error={latest['error']}", flush=True)
        time.sleep(max(1.0, float(args.interval_seconds)))


def _capture_with_transient_db_lock_retries(args: argparse.Namespace):
    last_error: BaseException | None = None
    for attempt in range(4):
        try:
            return capture_top_profile_distributions_once(config=_config(args))
        except Exception as exc:  # noqa: BLE001 - classify before deciding whether to retry.
            if not is_transient_database_error(exc):
                raise
            last_error = exc
            time.sleep(0.35 * (attempt + 1))
    raise last_error or RuntimeError("database lock retry failed")


def main() -> int:
    args = build_parser().parse_args()
    payload = run_loop(args) if args.loop else run_once(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={payload.get('status')}")
        print(f"event_count={payload.get('event_count')}")
        print(f"snapshot_rows_inserted={payload.get('snapshot_rows_inserted')}")
        print(f"component_rows_inserted={payload.get('component_rows_inserted')}")
        print(f"readiness_rows_inserted={payload.get('readiness_rows_inserted')}")
        print(f"blockers={payload.get('blockers')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
