import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_api: calls real external providers and may be flaky",
    )
    config.addinivalue_line(
        "markers",
        "postgres_live: runs against configured Postgres database",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_live_api = os.getenv("JANUS_RUN_LIVE_TESTS") == "1"
    run_live_db = os.getenv("JANUS_RUN_DB_TESTS") == "1"

    skip_live_api = pytest.mark.skip(reason="set JANUS_RUN_LIVE_TESTS=1 to run live API checks")
    skip_live_db = pytest.mark.skip(reason="set JANUS_RUN_DB_TESTS=1 to run Postgres integration checks")
    for item in items:
        if "live_api" in item.keywords and not run_live_api:
            item.add_marker(skip_live_api)
        if "postgres_live" in item.keywords and not run_live_db:
            item.add_marker(skip_live_db)
