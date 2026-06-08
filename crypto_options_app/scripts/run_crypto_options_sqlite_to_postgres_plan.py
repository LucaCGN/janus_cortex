from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from crypto_options_app.config import CENTRAL_DB_PATH, CENTRAL_POSTGRES_URL
from crypto_options_app.db.postgres import build_sqlite_to_postgres_plan, write_migration_plan_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a SQLite-to-Postgres migration inventory for crypto-options.")
    parser.add_argument("--sqlite-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--database-url", default=CENTRAL_POSTGRES_URL)
    parser.add_argument("--skip-counts", action="store_true")
    parser.add_argument("--output-dir", default="crypto_options_app/artifacts/reports")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    plan = build_sqlite_to_postgres_plan(
        args.sqlite_path,
        database_url=args.database_url,
        include_counts=not args.skip_counts,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"postgres_migration_plan_{stamp}.json"
    md_path = output_dir / f"postgres_migration_plan_{stamp}.md"
    json_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    write_migration_plan_markdown(plan, md_path)
    latest_json = output_dir / "postgres_migration_plan_latest.json"
    latest_md = output_dir / "postgres_migration_plan_latest.md"
    latest_json.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    write_migration_plan_markdown(plan, latest_md)

    result = {
        "status": "ok",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "latest_json_path": str(latest_json),
        "latest_markdown_path": str(latest_md),
        "table_count": plan["source_sqlite"].get("table_count"),
        "total_counted_rows": plan.get("total_counted_rows"),
        "runtime_cutover_blockers": plan.get("runtime_cutover_blockers", []),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
