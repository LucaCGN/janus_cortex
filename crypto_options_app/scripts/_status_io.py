from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json_atomically(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    temp_path = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(target)
