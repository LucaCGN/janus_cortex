from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_options_app.config import CENTRAL_POSTGRES_URL
from crypto_options_app.db.postgres import (
    CryptoOptionsPostgresSettings,
    check_postgres_connection,
    initialize_postgres_schema,
    postgres_driver_status,
)
from crypto_options_app.db.postgres_shadow import (
    DEFAULT_SHADOW_PARITY_TABLES,
    PostgresShadowParityConfig,
    build_postgres_shadow_parity_report,
    write_postgres_shadow_parity_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or bootstrap the crypto-options Postgres target.")
    parser.add_argument("--database-url", default=CENTRAL_POSTGRES_URL)
    parser.add_argument("--check-connection", action="store_true")
    parser.add_argument("--bootstrap-schema", action="store_true")
    parser.add_argument("--shadow-parity", action="store_true")
    parser.add_argument("--sqlite-path", default="crypto_options_app/data/crypto_options_data.sqlite")
    parser.add_argument("--tables", nargs="*", default=None)
    parser.add_argument("--write-latest", action="store_true")
    parser.add_argument("--artifact-root", default="crypto_options_app/artifacts")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = CryptoOptionsPostgresSettings.from_url(args.database_url)
    payload: dict[str, object] = {
        "compose_file": str(Path("crypto_options_app/docker-compose.postgres.yml")),
        "database_url": args.database_url,
        "driver": postgres_driver_status(),
    }
    if args.check_connection:
        payload["connection"] = check_postgres_connection(settings)
    if args.bootstrap_schema:
        payload["bootstrap"] = initialize_postgres_schema(settings)
    if args.shadow_parity:
        tables = tuple(args.tables) if args.tables else DEFAULT_SHADOW_PARITY_TABLES
        shadow_parity = build_postgres_shadow_parity_report(
            PostgresShadowParityConfig(
                sqlite_path=Path(args.sqlite_path),
                database_url=args.database_url,
                artifact_root=Path(args.artifact_root),
                tables=tables,
            )
        )
        if args.write_latest:
            shadow_parity["artifact_path"] = str(
                write_postgres_shadow_parity_report(
                    shadow_parity,
                    artifact_root=Path(args.artifact_root),
                )
            )
        payload["shadow_parity"] = shadow_parity

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
