from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.reports.runtime_audit import (
    RuntimeAuditOptions,
    build_runtime_audit,
    render_runtime_audit_markdown,
    write_runtime_audit_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the crypto-options runtime platform.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8011/v1/crypto-options-app/health")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:8012/")
    parser.add_argument("--artifact-root", default=str(DEFAULT_CONFIG.artifact_root))
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-endpoints", action="store_true")
    parser.add_argument("--skip-processes", action="store_true")
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = RuntimeAuditOptions(
        backend_url=args.backend_url,
        frontend_url=args.frontend_url,
        artifact_root=Path(args.artifact_root),
        check_docker=not args.skip_docker,
        check_endpoints=not args.skip_endpoints,
        include_processes=not args.skip_processes,
    )
    audit = build_runtime_audit(options)
    if args.write_artifacts:
        audit["artifacts"] = write_runtime_audit_artifacts(audit, artifact_root=options.artifact_root)
    if args.markdown:
        print(render_runtime_audit_markdown(audit), end="")
    else:
        print(json.dumps(audit, indent=2, sort_keys=True, default=str))
    return 0 if audit.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
