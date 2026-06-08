from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from crypto_options_app.config import CENTRAL_DB_PATH, CENTRAL_POSTGRES_URL
from crypto_options_app.db.postgres_import import PostgresBulkCopyConfig, copy_sqlite_tables_to_postgres


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy selected crypto-options SQLite tables into Postgres.")
    parser.add_argument("--sqlite-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--database-url", default=CENTRAL_POSTGRES_URL)
    parser.add_argument("--tables", nargs="*", default=())
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit-per-table", type=int)
    parser.add_argument("--truncate", action="store_true")
    parser.add_argument("--upsert-update", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bootstrap-schema", action="store_true")
    parser.add_argument("--output-dir", default="crypto_options_app/artifacts/reports")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--status-path", default="crypto_options_app/artifacts/automation/postgres_hot_sync_status.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.loop:
        status_path = Path(args.status_path)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                result = _run_once(args)
                _write_status(
                    status_path,
                    {
                        "status": result.get("status"),
                        "updated_at_utc": datetime.now(UTC).isoformat(),
                        "artifact_path": result.get("artifact_path"),
                        "latest_artifact_path": result.get("latest_artifact_path"),
                        "inserted_rows": result.get("inserted_rows"),
                        "source_rows_seen": result.get("source_rows_seen"),
                        "table_count": result.get("table_count"),
                        "blockers": result.get("blockers", []),
                    },
                )
                if args.json:
                    print(json.dumps(result, sort_keys=True), flush=True)
                else:
                    print(
                        f"{datetime.now(UTC).isoformat()} status={result.get('status')} "
                        f"inserted_rows={result.get('inserted_rows')}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001 - long-running sync reports and retries.
                _write_status(
                    status_path,
                    {
                        "status": "blocked",
                        "updated_at_utc": datetime.now(UTC).isoformat(),
                        "blockers": [f"{type(exc).__name__}:{exc}"],
                    },
                )
                if args.json:
                    print(json.dumps({"status": "blocked", "error": f"{type(exc).__name__}:{exc}"}), flush=True)
                else:
                    print(f"{datetime.now(UTC).isoformat()} status=blocked error={type(exc).__name__}:{exc}", flush=True)
            time.sleep(max(5.0, float(args.interval_seconds)))

    result = _run_once(args)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key in ("status", "table_count", "source_rows_seen", "inserted_rows", "artifact_path"):
            print(f"{key}={result.get(key)}")
    return 0 if result.get("status") in {"ok", "dry_run", "degraded"} else 1


def _run_once(args: argparse.Namespace) -> dict[str, object]:
    result = copy_sqlite_tables_to_postgres(
        PostgresBulkCopyConfig(
            sqlite_path=Path(args.sqlite_path),
            database_url=args.database_url,
            tables=tuple(args.tables),
            batch_size=args.batch_size,
            limit_per_table=args.limit_per_table,
            truncate=args.truncate,
            upsert_update=args.upsert_update,
            dry_run=args.dry_run,
            bootstrap_schema=args.bootstrap_schema,
        )
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"postgres_bulk_copy_{stamp}.json"
    latest_path = output_dir / "postgres_bulk_copy_latest.json"
    text = json.dumps(result, indent=2, sort_keys=True)
    output_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    result["artifact_path"] = str(output_path)
    result["latest_artifact_path"] = str(latest_path)
    return result


def _write_status(path: Path, payload: dict[str, object]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    temp_path = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
