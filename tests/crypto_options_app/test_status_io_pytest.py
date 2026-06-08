from __future__ import annotations

import json

from crypto_options_app.scripts._status_io import write_json_atomically


def test_write_json_atomically_replaces_existing_file(tmp_path) -> None:
    path = tmp_path / "status.json"
    path.write_text("", encoding="utf-8")

    write_json_atomically(path, {"status": "healthy", "iteration": 3})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"iteration": 3, "status": "healthy"}
    assert not list(tmp_path.glob("*.tmp"))
