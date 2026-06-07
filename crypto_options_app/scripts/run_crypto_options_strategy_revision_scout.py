from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.db.connection import connect  # noqa: E402
from crypto_options_app.db.errors import is_transient_database_error  # noqa: E402
from crypto_options_app.strategies.revision_scout import build_strategy_revision_scout, write_strategy_revision_scout_report  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only strategy revision scout report.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--output-md", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = _build_scout_with_retry(Path(args.db_path))
    output_md = args.output_md
    if output_md is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_md = str(REPO_ROOT / "crypto_options_app" / "artifacts" / "reports" / f"strategy_revision_scout_{stamp}.md")
    report_path = write_strategy_revision_scout_report(payload, output_md)
    payload["report_path"] = str(report_path)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"report_path={report_path}")
        print(f"promotion_ready={payload['signal_gate']['promotion_ready_signal_count']}")
        print(f"revision_needed={payload['signal_gate']['revision_signal_count']}")
        print(f"recommended_lanes={len(payload['recommended_next_lanes'])}")
    return 0


def _build_scout_with_retry(db_path: Path, *, attempts: int = 4, delay_seconds: float = 2.0) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            with connect(db_path) as conn:
                return build_strategy_revision_scout(conn)
        except Exception as exc:
            if not is_transient_database_error(exc) or attempt == attempts - 1:
                raise
            last_error = exc
            time.sleep(max(0.1, float(delay_seconds)))
    if last_error is not None:
        raise last_error
    with connect(db_path) as conn:
        return build_strategy_revision_scout(conn)


if __name__ == "__main__":
    raise SystemExit(main())
