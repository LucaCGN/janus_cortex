from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.reports.transition_readiness import (
    TransitionReadinessOptions,
    build_transition_readiness_review,
    render_transition_readiness_markdown,
    write_transition_readiness_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review transition readiness for the crypto-options app.")
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8011/v1/crypto-options-app")
    parser.add_argument("--artifact-root", default=str(DEFAULT_CONFIG.artifact_root))
    parser.add_argument("--endpoint-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--skip-git-status", action="store_true")
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    review = build_transition_readiness_review(
        TransitionReadinessOptions(
            artifact_root=Path(args.artifact_root),
            backend_base_url=args.backend_base_url,
            include_git_status=not args.skip_git_status,
            endpoint_timeout_seconds=args.endpoint_timeout_seconds,
        )
    )
    if args.write_artifacts:
        review["artifacts"] = write_transition_readiness_artifacts(
            review,
            artifact_root=Path(args.artifact_root),
        )
    if args.markdown:
        print(render_transition_readiness_markdown(review), end="")
    else:
        print(json.dumps(review, indent=2, sort_keys=True, default=str))
    return 0 if review.get("status") in {"ok", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
