from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.replay_config_review import build_replay_config_review, write_replay_config_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate one read-only Janus postgame replay/config review.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--report-limit", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    review = build_replay_config_review(day=args.session_date, report_limit=args.report_limit)
    result = write_replay_config_review(review)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["markdown_path"])


if __name__ == "__main__":
    main()
