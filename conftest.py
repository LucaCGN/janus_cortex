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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("JANUS_RUN_LIVE_TESTS") == "1":
        return

    skip_live = pytest.mark.skip(reason="set JANUS_RUN_LIVE_TESTS=1 to run live API checks")
    for item in items:
        if "live_api" in item.keywords:
            item.add_marker(skip_live)
