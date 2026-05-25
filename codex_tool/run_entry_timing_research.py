from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.entry_timing_research import build_entry_timing_matrix, write_entry_timing_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a read-only Janus entry-timing research matrix.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--fixture-backtest-path", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    matrix = build_entry_timing_matrix(
        day=args.session_date,
        fixture_backtest_path=Path(args.fixture_backtest_path) if args.fixture_backtest_path else None,
    )
    result = write_entry_timing_matrix(matrix)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["markdown_path"])


if __name__ == "__main__":
    main()
