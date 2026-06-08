from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.reports.repo_cleanup_batches import (
    build_repo_cleanup_batches,
    render_repo_cleanup_batches_markdown,
    write_repo_cleanup_batch_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build reviewed cleanup batches from the latest repo inventory.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_CONFIG.artifact_root))
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_repo_cleanup_batches(artifact_root=Path(args.artifact_root))
    if args.write_artifacts:
        report["artifacts"] = write_repo_cleanup_batch_artifacts(
            report,
            artifact_root=Path(args.artifact_root),
        )
    if args.markdown:
        print(render_repo_cleanup_batches_markdown(report), end="")
    else:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("status") in {"ok", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
