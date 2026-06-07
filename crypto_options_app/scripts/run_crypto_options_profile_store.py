from __future__ import annotations

"""Initialize and inspect the crypto options profile SQLite store."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.pipelines.options.profile_store import (  # noqa: E402
    ingest_profile_signal_artifact,
    initialize_profile_store,
    profile_store_summary,
)
from crypto_options_app.pipelines.options.profile_fetch_service import (  # noqa: E402
    ProfileFetchServiceConfig,
    run_active_pool_profile_fetch_once,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the separate crypto options profile SQLite store.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the profile store schema.")
    init.set_defaults(func=_cmd_init)

    ingest = sub.add_parser("ingest-artifact", help="Ingest a profile signal monitor/report JSON artifact.")
    ingest.add_argument("--artifact", required=True)
    ingest.add_argument(
        "--profile-pool",
        default=None,
        help="Optional active profile pool text file. All valid refs are added to profile_refs, even if not fetched in the artifact.",
    )
    ingest.set_defaults(func=_cmd_ingest)

    summary = sub.add_parser("summary", help="Print profile store counts and top pools.")
    summary.set_defaults(func=_cmd_summary)

    fetch = sub.add_parser("fetch-active-pool", help="Fetch active profile pool data and persist raw/profile rows.")
    fetch.add_argument("--profile-pool", required=True)
    fetch.add_argument("--active-profile-pool-limit", type=int, default=120)
    fetch.add_argument("--max-concurrency", type=int, default=8)
    fetch.add_argument("--page-limit", type=int, default=200)
    fetch.add_argument("--activity-pages", type=int, default=1)
    fetch.add_argument("--positions-pages", type=int, default=1)
    fetch.add_argument("--trades-pages", type=int, default=1)
    fetch.add_argument("--closed-pages", type=int, default=1)
    fetch.set_defaults(func=_cmd_fetch_active_pool)
    return parser


def _cmd_init(args: argparse.Namespace) -> dict[str, object]:
    path = initialize_profile_store(args.db_path)
    return {"status": "initialized", "db_path": str(path)}


def _cmd_ingest(args: argparse.Namespace) -> dict[str, object]:
    return ingest_profile_signal_artifact(
        args.artifact,
        db_path=args.db_path,
        profile_pool_path=args.profile_pool,
    )


def _cmd_summary(args: argparse.Namespace) -> dict[str, object]:
    return profile_store_summary(args.db_path)


def _cmd_fetch_active_pool(args: argparse.Namespace) -> dict[str, object]:
    config = ProfileFetchServiceConfig(
        activity_pages=args.activity_pages,
        positions_pages=args.positions_pages,
        trades_pages=args.trades_pages,
        closed_pages=args.closed_pages,
        page_limit=args.page_limit,
        max_concurrency=args.max_concurrency,
        active_profile_pool_limit=args.active_profile_pool_limit,
    )
    return asyncio.run(
        run_active_pool_profile_fetch_once(
            active_profile_pool_path=args.profile_pool,
            db_path=args.db_path,
            source_label=f"active_pool_fetch:{Path(args.profile_pool).name}",
            config=config,
        )
    )


def main() -> int:
    args = build_parser().parse_args()
    payload = args.func(args)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
