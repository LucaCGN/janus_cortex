import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.databases.safety import ALLOW_UNSAFE_DB_TESTS_ENV, is_safe_db_test_target


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
    safe_live_db_target = is_safe_db_test_target()

    skip_live_api = pytest.mark.skip(reason="set JANUS_RUN_LIVE_TESTS=1 to run live API checks")
    skip_live_db = pytest.mark.skip(reason="set JANUS_RUN_DB_TESTS=1 to run Postgres integration checks")
    skip_unsafe_live_db = pytest.mark.skip(
        reason=(
            "Postgres integration checks require JANUS_DB_TARGET=disposable or dev_clone. "
            f"Set {ALLOW_UNSAFE_DB_TESTS_ENV}=1 only for an explicit override."
        )
    )
    for item in items:
        if "live_api" in item.keywords and not run_live_api:
            item.add_marker(skip_live_api)
        if "postgres_live" in item.keywords:
            if not run_live_db:
                item.add_marker(skip_live_db)
            elif not safe_live_db_target:
                item.add_marker(skip_unsafe_live_db)
