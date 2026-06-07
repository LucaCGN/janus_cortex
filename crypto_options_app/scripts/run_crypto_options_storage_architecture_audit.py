from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.reports.storage_architecture import (
    StorageArchitectureAuditOptions,
    build_storage_architecture_audit,
    render_storage_architecture_markdown,
    write_storage_architecture_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit Postgres vs Redis storage architecture pressure.")
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8011/v1/crypto-options-app")
    parser.add_argument("--artifact-root", default=str(DEFAULT_CONFIG.artifact_root))
    parser.add_argument("--endpoint-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--skip-runtime-audit", action="store_true")
    parser.add_argument("--skip-endpoint-timings", action="store_true")
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = StorageArchitectureAuditOptions(
        artifact_root=Path(args.artifact_root),
        backend_base_url=args.backend_base_url,
        include_runtime_audit=not args.skip_runtime_audit,
        include_endpoint_timings=not args.skip_endpoint_timings,
        endpoint_timeout_seconds=args.endpoint_timeout_seconds,
    )
    audit = build_storage_architecture_audit(options)
    if args.write_artifacts:
        audit["artifacts"] = write_storage_architecture_artifacts(
            audit,
            artifact_root=options.artifact_root,
        )
    if args.markdown:
        print(render_storage_architecture_markdown(audit), end="")
    else:
        print(json.dumps(audit, indent=2, sort_keys=True, default=str))
    return 0 if audit.get("status") in {"ok", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
