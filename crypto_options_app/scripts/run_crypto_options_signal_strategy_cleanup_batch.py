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
from crypto_options_app.strategies.cleanup_batch import (  # noqa: E402
    build_signal_strategy_cleanup_batch,
    write_signal_strategy_cleanup_batch_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only signal/strategy cleanup batch.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--max-signals", type=int, default=24)
    parser.add_argument("--max-strategies", type=int, default=12)
    parser.add_argument("--output-md", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = _build_batch_with_retry(
        Path(args.db_path),
        max_signals=max(0, int(args.max_signals)),
        max_strategies=max(0, int(args.max_strategies)),
    )
    output_md = args.output_md
    if output_md is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_md = str(REPO_ROOT / "crypto_options_app" / "artifacts" / "reports" / f"signal_strategy_cleanup_batch_{stamp}.md")
    report_path = write_signal_strategy_cleanup_batch_report(payload, output_md)
    payload["report_path"] = str(report_path)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"report_path={report_path}")
        print(f"signal_batch={len(payload.get('signals') or [])}")
        print(f"strategy_batch={len(payload.get('strategies') or [])}")
        print(f"policy_contract={payload.get('policy_contract_schema_version')}")
    return 0


def _build_batch_with_retry(
    db_path: Path,
    *,
    max_signals: int,
    max_strategies: int,
    attempts: int = 4,
    delay_seconds: float = 2.0,
) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            with connect(db_path) as conn:
                return build_signal_strategy_cleanup_batch(
                    conn,
                    max_signals=max_signals,
                    max_strategies=max_strategies,
                )
        except Exception as exc:
            if not is_transient_database_error(exc) or attempt == attempts - 1:
                raise
            last_error = exc
            time.sleep(max(0.1, float(delay_seconds)))
    if last_error is not None:
        raise last_error
    with connect(db_path) as conn:
        return build_signal_strategy_cleanup_batch(conn, max_signals=max_signals, max_strategies=max_strategies)


if __name__ == "__main__":
    raise SystemExit(main())
