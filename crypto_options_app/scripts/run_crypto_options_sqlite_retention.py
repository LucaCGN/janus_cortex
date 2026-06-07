from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.sqlite_retention import SQLiteRetentionConfig, trim_sqlite_raw_option_storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trim raw high-volume Polymarket option capture storage in the local SQLite DB."
    )
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--recent-raw-book-hours", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=25000)
    parser.add_argument("--keep-order-book-levels", action="store_true")
    parser.add_argument("--keep-raw-json", action="store_true")
    parser.add_argument("--no-checkpoint-each-batch", action="store_true")
    parser.add_argument("--output-dir", default="crypto_options_app/artifacts/reports")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = trim_sqlite_raw_option_storage(
        SQLiteRetentionConfig(
            db_path=Path(args.db_path),
            recent_raw_book_hours=args.recent_raw_book_hours,
            batch_size=args.batch_size,
            delete_order_book_levels=not args.keep_order_book_levels,
            sanitize_raw_json=not args.keep_raw_json,
            checkpoint_each_batch=not args.no_checkpoint_each_batch,
        )
    ).to_dict()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"sqlite_retention_{stamp}.json"
    latest_path = output_dir / "sqlite_retention_latest.json"
    text = json.dumps(result, indent=2, sort_keys=True)
    output_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    result["artifact_path"] = str(output_path)
    result["latest_artifact_path"] = str(latest_path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result.get('status')}")
        print(f"before_free_pages_bytes={result.get('before', {}).get('free_pages_bytes')}")
        print(f"after_free_pages_bytes={result.get('after', {}).get('free_pages_bytes')}")
        print(f"before_wal_bytes={result.get('before', {}).get('wal_bytes')}")
        print(f"after_wal_bytes={result.get('after', {}).get('wal_bytes')}")
        print(f"artifact_path={output_path}")
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
