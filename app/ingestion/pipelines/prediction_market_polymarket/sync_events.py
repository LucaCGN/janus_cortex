"""Compatibility wrapper for Polymarket event sync pipeline."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.pipelines.daily.polymarket.sync_events import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
