from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_controller_queue_cli_runs_from_script_path_pytest(tmp_path) -> None:
    env = os.environ.copy()
    env["JANUS_LOCAL_ROOT"] = str(tmp_path / "local")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "controller_queue.py"), "status"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["active_lock_count"] == 0
