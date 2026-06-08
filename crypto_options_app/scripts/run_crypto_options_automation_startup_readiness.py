from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.reports.automation_startup_readiness import (
    AutomationStartupReadinessOptions,
    build_automation_startup_readiness,
    render_automation_startup_readiness_markdown,
    write_automation_startup_readiness_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review limited automation startup readiness.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_CONFIG.artifact_root))
    parser.add_argument(
        "--team-coordination-root",
        default="crypto_options_app/artifacts/team_coordination",
    )
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    review = build_automation_startup_readiness(
        AutomationStartupReadinessOptions(
            artifact_root=Path(args.artifact_root),
            team_coordination_root=Path(args.team_coordination_root),
        )
    )
    if args.write_artifacts:
        review["artifacts"] = write_automation_startup_readiness_artifacts(
            review,
            artifact_root=Path(args.artifact_root),
        )
    if args.markdown:
        print(render_automation_startup_readiness_markdown(review), end="")
    else:
        print(json.dumps(review, indent=2, sort_keys=True, default=str))
    return 0 if review.get("status") in {"ready_to_schedule", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
