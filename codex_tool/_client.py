from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request


DEFAULT_API_ROOT = "http://127.0.0.1:8010"


def base_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--api-root", default=DEFAULT_API_ROOT)
    return parser


def api_json(
    api_root: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    query: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    url = api_root.rstrip("/") + path
    if query:
        url += "?" + parse.urlencode({key: value for key, value in query.items() if value is not None}, doseq=True)
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return {"ok": False, "status_code": exc.code, "url": url, "error": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": repr(exc)}
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw}
    if isinstance(parsed, dict):
        parsed.setdefault("ok", True)
        return parsed
    return {"ok": True, "items": parsed}


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def read_text(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def exit_for_response(response: dict[str, Any]) -> None:
    print_json(response)
    if response.get("ok") is False:
        sys.exit(1)


def cycle_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "session_date": getattr(args, "session_date", None),
        "event_ids": getattr(args, "event_ids", []) or [],
        "run_id": getattr(args, "run_id", None),
        "account_id": getattr(args, "account_id", None),
        "source": getattr(args, "source", "codex"),
        "notes": getattr(args, "notes", None),
        "execute": bool(getattr(args, "execute", False)),
    }


def add_cycle_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--event-id", action="append", dest="event_ids", default=[])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--source", default="codex")
    parser.add_argument("--notes", default=None)
    parser.add_argument("--execute", action="store_true")
    return parser
