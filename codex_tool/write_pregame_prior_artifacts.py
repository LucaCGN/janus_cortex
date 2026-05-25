from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.modules.agentic.pregame_priors import write_pregame_prior_artifacts_from_research_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Adopt NBA/WNBA pregame research bundle JSON as per-event optional prior artifacts."
    )
    parser.add_argument("--bundle-path", required=True, help="Path to a pregame research JSON bundle.")
    parser.add_argument("--source", default=None, help="Override the source recorded on each prior.")
    parser.add_argument("--root", default=None, help="Optional artifact root for tests or alternate local roots.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = write_pregame_prior_artifacts_from_research_bundle(
        args.bundle_path,
        source=args.source,
        root=Path(args.root) if args.root else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
