from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from crypto_options_app.strategies.registry import all_strategy_specs


SCRIPT_PATH = Path("crypto_options_app/scripts/run_crypto_options_strategy_validation_probe.py")


def test_strategy_validation_probe_cli_persists_budget_rows_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "probe.sqlite"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db-path",
            str(db_path),
            "--run-id",
            "probe-success",
            "--trusted-cash-balance-usd",
            "150",
            "--scoped-live-execute",
            "--scoped-live-approved",
            "--scoped-live-risk-ack",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    expected_count = len(all_strategy_specs())
    assert payload["verified_candidate"] is True
    assert payload["blocked_result_count"] == 0
    assert payload["live_submission_attempted"] is True
    assert payload["db_counts"]["strategy_validation_runs"] == expected_count
    assert payload["db_counts"]["validation_budget_ledger"] == expected_count
    assert payload["db_counts"]["orders"] == expected_count


def test_strategy_validation_probe_cli_blocks_when_scoped_flags_missing_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "probe-blocked.sqlite"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db-path",
            str(db_path),
            "--run-id",
            "probe-blocked",
            "--trusted-cash-balance-usd",
            "150",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    expected_count = len(all_strategy_specs())
    assert payload["verified_candidate"] is True
    assert payload["blocked_result_count"] == expected_count
    assert payload["live_submission_attempted"] is False
    assert all("env_live_flags_missing" in blockers for blockers in payload["blockers_by_result"])
    assert payload["db_counts"]["strategy_validation_runs"] == 0
    assert payload["db_counts"]["validation_budget_ledger"] == 0
