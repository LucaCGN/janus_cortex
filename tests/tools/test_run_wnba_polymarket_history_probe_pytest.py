from __future__ import annotations

import json
from pathlib import Path

from tools.run_wnba_polymarket_history_probe import SCHEMA_VERSION, write_probe_artifact


def test_write_probe_artifact_persists_issue_evidence_path_pytest(tmp_path: Path) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "batch_price_history_backtest_complete",
        "price_history_rows": 750,
        "state_panel_rows": 1998,
    }

    artifact_path = write_probe_artifact(payload, artifact_dir=str(tmp_path))

    assert artifact_path.startswith(str(tmp_path))
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    assert artifact["schema_version"] == SCHEMA_VERSION
    assert artifact["artifact_path"] == artifact_path
    assert artifact["price_history_rows"] == 750
