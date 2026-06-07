from __future__ import annotations

"""Import legacy crypto-options profile and market shards into the centralized DB."""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.db.imports import import_legacy_shards, run_import_parity_checks  # noqa: E402


DEFAULT_PROFILE_SHARD = Path("local/shared/artifacts/crypto-options-research/profile-store/crypto_options_profiles.sqlite")
DEFAULT_MARKET_SHARD = Path("local/shared/artifacts/crypto-options-research/market-data/crypto_market_data.sqlite")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import legacy crypto-options shards into the centralized app DB.")
    parser.add_argument("--target-db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--profile-db-path", default=str(DEFAULT_PROFILE_SHARD))
    parser.add_argument("--market-db-path", default=str(DEFAULT_MARKET_SHARD))
    parser.add_argument("--skip-profile", action="store_true")
    parser.add_argument("--skip-market", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    profile_path = None if args.skip_profile else Path(args.profile_db_path)
    market_path = None if args.skip_market else Path(args.market_db_path)
    missing = [
        str(path)
        for path in (profile_path, market_path)
        if path is not None and not path.exists()
    ]
    if missing:
        payload = {
            "status": "blocked",
            "blockers": ["legacy_shard_missing"],
            "missing_paths": missing,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    result = import_legacy_shards(
        target_db_path=Path(args.target_db_path),
        profile_db_path=profile_path,
        market_db_path=market_path,
    )
    parity = run_import_parity_checks(
        target_db_path=Path(args.target_db_path),
        profile_db_path=profile_path,
        market_db_path=market_path,
    )
    payload = {
        "status": "ok" if parity["passed"] else "degraded",
        "import": result,
        "parity": parity,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={payload['status']}")
        print(f"target_db_path={args.target_db_path}")
        print(f"profile={result.get('profile')}")
        print(f"market={result.get('market')}")
        print(f"parity_passed={parity['passed']}")
    return 0 if parity["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
