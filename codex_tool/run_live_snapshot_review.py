from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.live_snapshot import build_normalized_live_snapshot_review, write_normalized_live_snapshot_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a read-only normalized NBA/WNBA live snapshot review.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    review = build_normalized_live_snapshot_review(day=args.session_date)
    result = write_normalized_live_snapshot_review(review)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["markdown_path"])


if __name__ == "__main__":
    main()
