from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.sqlite_compaction import (
    SQLiteCompactionConfig,
    compact_sqlite_for_local_services,
    replace_sqlite_with_compacted,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compact the local crypto-options SQLite fallback DB by dropping raw CLOB depth bloat."
    )
    parser.add_argument("--source-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--target-path")
    parser.add_argument("--recent-raw-book-hours", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--keep-order-book-levels", action="store_true")
    parser.add_argument("--keep-raw-json", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--output-dir", default="crypto_options_app/artifacts/reports")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    source_path = Path(args.source_path)
    target_path = Path(args.target_path) if args.target_path else source_path.with_suffix(".compact.sqlite")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = compact_sqlite_for_local_services(
        SQLiteCompactionConfig(
            source_path=source_path,
            target_path=target_path,
            recent_raw_book_hours=args.recent_raw_book_hours,
            batch_size=args.batch_size,
            skip_order_book_levels=not args.keep_order_book_levels,
            sanitize_raw_json=not args.keep_raw_json,
        )
    ).to_dict()
    if args.activate:
        result["activation"] = replace_sqlite_with_compacted(
            source_path=source_path,
            compacted_path=target_path,
        )
    report_path = output_dir / f"sqlite_compaction_{stamp}.json"
    latest_path = output_dir / "sqlite_compaction_latest.json"
    report_text = json.dumps(result, indent=2, sort_keys=True)
    report_path.write_text(report_text, encoding="utf-8")
    latest_path.write_text(report_text, encoding="utf-8")
    result["artifact_path"] = str(report_path)
    result["latest_artifact_path"] = str(latest_path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result.get('status')}")
        print(f"source_bytes={result.get('source_bytes')}")
        print(f"target_bytes={result.get('target_bytes')}")
        print(f"integrity_check={result.get('integrity_check')}")
        print(f"artifact_path={report_path}")
        if args.activate:
            print(f"activation={result.get('activation', {}).get('status')}")
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
