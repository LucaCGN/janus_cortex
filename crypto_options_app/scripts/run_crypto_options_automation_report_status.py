from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.reports.automation_report_status import (
    AutomationReportStatusOptions,
    build_automation_report_status,
    render_automation_report_status_markdown,
    write_automation_report_status_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review limited automation report freshness.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_CONFIG.artifact_root))
    parser.add_argument(
        "--automation-status-root",
        default="crypto_options_app/artifacts/team_coordination/automation_status",
    )
    parser.add_argument("--write-artifacts", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    review = build_automation_report_status(
        AutomationReportStatusOptions(
            artifact_root=Path(args.artifact_root),
            automation_status_root=Path(args.automation_status_root),
        )
    )
    if args.write_artifacts:
        review["artifacts"] = write_automation_report_status_artifacts(
            review,
            artifact_root=Path(args.artifact_root),
        )
    if args.markdown:
        print(render_automation_report_status_markdown(review), end="")
    else:
        print(json.dumps(review, indent=2, sort_keys=True, default=str))
    return 0 if review.get("status") in {"fresh", "degraded", "pending_first_reports"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
