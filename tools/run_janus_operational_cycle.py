from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime.local_paths import resolve_local_root, resolve_shared_root


DEFAULT_API_ROOT = "http://127.0.0.1:8010"
DEFAULT_LOCAL_ROOT = resolve_local_root()
DEFAULT_SHARED_ROOT = resolve_shared_root()
DEFAULT_SEASON = "2025-26"
DEFAULT_ACCOUNT_ID = "56964015-5935-5035-bdab-b056c9277146"
DEFAULT_ENTRY_TARGET_NOTIONAL_USD = 1.0
DEFAULT_MAX_ENTRY_ORDERS_PER_GAME = 10
DEFAULT_MAX_ENTRY_NOTIONAL_PER_GAME_USD = 10.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one operational Janus cycle: data refresh, live-test orchestration, and shadow/latency monitoring."
    )
    parser.add_argument("--api-root", default=DEFAULT_API_ROOT)
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--local-root", default=str(DEFAULT_LOCAL_ROOT))
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--session-date", default=date.today().isoformat())
    parser.add_argument(
        "--stage",
        action="append",
        default=[],
        choices=("data-refresh", "live-test", "shadow-test", "postgame"),
        help="Stage to run. Repeatable. Defaults to data-refresh and shadow-test.",
    )
    parser.add_argument("--game-id", action="append", dest="game_ids", default=[])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID)
    parser.add_argument("--start-live-run", action="store_true")
    parser.add_argument("--live-money", action="store_true", help="Set dry_run=false when starting the live run.")
    parser.add_argument("--entries-enabled", action="store_true", help="Enable entries when starting a live run.")
    parser.add_argument("--entry-target-notional-usd", type=float, default=DEFAULT_ENTRY_TARGET_NOTIONAL_USD)
    parser.add_argument("--max-entry-orders-per-game", type=int, default=DEFAULT_MAX_ENTRY_ORDERS_PER_GAME)
    parser.add_argument("--max-entry-notional-per-game-usd", type=float, default=DEFAULT_MAX_ENTRY_NOTIONAL_PER_GAME_USD)
    parser.add_argument("--poll-live-sec", type=float, default=2.0)
    parser.add_argument("--poll-idle-sec", type=float, default=10.0)
    parser.add_argument("--notes", default=None)
    return parser.parse_args()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _api_json(
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


def _discover_games(api_root: str, session_date: str, explicit_game_ids: list[str]) -> list[dict[str, Any]]:
    if explicit_game_ids:
        games = []
        for game_id in explicit_game_ids:
            games.append({"game_id": str(game_id)})
        return games
    payload = _api_json(
        api_root,
        "GET",
        "/v1/nba/games",
        query={"game_date": session_date, "limit": 50},
        timeout=60,
    )
    items = payload.get("items") if payload.get("ok", True) else []
    return [item for item in items or [] if str(item.get("game_id") or "").strip()]


def _stage_dirs(shared_root: Path, session_date: str) -> tuple[Path, Path]:
    artifact_dir = shared_root / "artifacts" / "daily-live-validation" / session_date / "operational_cycle"
    report_dir = shared_root / "reports" / "daily-live-validation"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir, report_dir


def _run_data_refresh(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    api_root = str(args.api_root)
    calls: list[dict[str, Any]] = []

    def post(path: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
        result = _api_json(api_root, "POST", path, payload, timeout=timeout)
        calls.append({"method": "POST", "path": path, "payload": payload, "result": result})
        return result

    post(
        "/v1/sync/nba/schedule",
        {
            "season": args.season,
            "anchor_date": args.session_date,
            "schedule_window_days": 3,
            "include_live_snapshots": True,
            "include_play_by_play": True,
        },
    )
    games = _discover_games(api_root, args.session_date, list(args.game_ids or []))
    post(
        "/v1/sync/polymarket/events",
        {
            "probe_set": "today_nba",
            "session_date": args.session_date,
            "max_finished": 6,
            "max_live": 6,
            "max_upcoming": 6,
            "include_upcoming": True,
            "stream_sample_count": 2,
            "stream_sample_interval_sec": 0.5,
            "stream_max_outcomes": 30,
            "missing_only": False,
            "steps": [],
        },
        timeout=240,
    )
    post(
        "/v1/sync/polymarket/markets",
        {
            "probe_set": "today_nba",
            "session_date": args.session_date,
            "max_finished": 6,
            "max_live": 6,
            "max_upcoming": 6,
            "include_upcoming": True,
            "stream_sample_count": 2,
            "stream_sample_interval_sec": 0.5,
            "stream_max_outcomes": 30,
            "missing_only": False,
            "steps": [],
        },
        timeout=240,
    )
    post("/v1/sync/nba/mappings", {"lookback_days": 2, "lookahead_days": 2})
    for game in games:
        game_id = str(game.get("game_id") or "")
        if not game_id:
            continue
        post(f"/v1/sync/nba/live/{game_id}", {"include_live_snapshots": True, "include_play_by_play": True})
    for scope in ("positions", "orders", "trades"):
        post(f"/v1/sync/polymarket/{scope}", {"wallet_address": None, "limit": 500})
    summary = {
        "stage": "data-refresh",
        "generated_at_utc": _now_utc().isoformat(),
        "session_date": args.session_date,
        "game_ids": [str(game.get("game_id")) for game in games],
        "call_count": len(calls),
        "failed_calls": [call for call in calls if not bool(call.get("result", {}).get("ok", True))],
        "calls": calls,
    }
    _write_json(artifact_dir / "data_refresh_summary.json", summary)
    return summary


def _start_or_read_live_run(args: argparse.Namespace, artifact_dir: Path, games: list[dict[str, Any]]) -> dict[str, Any]:
    run_id = args.run_id or f"live-{args.session_date}-operational-cycle"
    game_ids = [str(game.get("game_id") or "") for game in games if str(game.get("game_id") or "").strip()]
    if args.start_live_run:
        payload = {
            "run_id": run_id,
            "execution_profile_version": "v1",
            "controller_name": "controller_vnext_unified_v1 :: balanced",
            "fallback_controller_name": "controller_vnext_deterministic_v1 :: tight",
            "game_ids": game_ids,
            "dry_run": not bool(args.live_money),
            "entries_enabled": bool(args.entries_enabled),
            "entry_target_notional_usd": float(args.entry_target_notional_usd),
            "max_entry_orders_per_game": int(args.max_entry_orders_per_game),
            "max_entry_notional_per_game_usd": float(args.max_entry_notional_per_game_usd),
            "poll_interval_live_sec": float(args.poll_live_sec),
            "poll_interval_idle_sec": float(args.poll_idle_sec),
            "stop_loss_mode": "market_on_local_trigger",
            "account_id": args.account_id,
            "notes": args.notes
            or "operational-cycle bounded live test: minimum share sizing, max 10 entries per game, order-manager supervised",
        }
        result = _api_json(str(args.api_root), "POST", "/v1/nba/live/runs", payload, timeout=120)
    else:
        result = _api_json(str(args.api_root), "GET", f"/v1/nba/live/runs/{run_id}", timeout=60)
    summary = {
        "stage": "live-test",
        "generated_at_utc": _now_utc().isoformat(),
        "run_id": run_id,
        "requested_game_ids": game_ids,
        "live_money": bool(args.live_money),
        "entries_enabled": bool(args.entries_enabled),
        "started": bool(args.start_live_run),
        "result": result,
        "risk_contract": {
            "entry_target_notional_usd": float(args.entry_target_notional_usd),
            "minimum_shares": 5,
            "max_entry_orders_per_game": int(args.max_entry_orders_per_game),
            "max_entry_notional_per_game_usd": float(args.max_entry_notional_per_game_usd),
            "live_money_requires_explicit_flag": True,
        },
    }
    _write_json(artifact_dir / "live_test_summary.json", summary)
    return summary


def _latest_run_root(local_root: Path, run_id: str) -> Path | None:
    tracks_root = local_root / "tracks" / "live-controller"
    if not tracks_root.exists():
        return None
    candidates = [path for path in tracks_root.glob(f"*/{run_id}") if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _summarize_tick_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        tick = payload.get("tick") or {}
        orderbook = payload.get("orderbook") or {}
        rows.append(
            {
                "game_id": payload.get("game_id"),
                "outcome_id": payload.get("outcome_id"),
                "tick_at": tick.get("ts"),
                "price": tick.get("price"),
                "best_bid": orderbook.get("best_bid"),
                "best_ask": orderbook.get("best_ask"),
                "spread_cents": orderbook.get("spread_cents"),
            }
        )
    if not rows:
        return {"exists": True, "tick_count": 0}
    timestamps = [
        datetime.fromisoformat(str(row["tick_at"]).replace("Z", "+00:00"))
        for row in rows
        if row.get("tick_at")
    ]
    return {
        "exists": True,
        "tick_count": len(rows),
        "game_count": len({str(row.get("game_id")) for row in rows if row.get("game_id")}),
        "outcome_count": len({str(row.get("outcome_id")) for row in rows if row.get("outcome_id")}),
        "first_tick_at": min(timestamps).isoformat() if timestamps else None,
        "last_tick_at": max(timestamps).isoformat() if timestamps else None,
        "median_spread_cents": _median([row.get("spread_cents") for row in rows]),
    }


def _median(values: list[Any]) -> float | None:
    numeric = sorted(float(value) for value in values if value is not None)
    if not numeric:
        return None
    middle = len(numeric) // 2
    if len(numeric) % 2:
        return numeric[middle]
    return (numeric[middle - 1] + numeric[middle]) / 2.0


def _summary_needs_attention(summary: dict[str, Any]) -> bool:
    if summary.get("failed_calls"):
        return True
    for call in summary.get("calls") or []:
        result = call.get("result") if isinstance(call, dict) else None
        if isinstance(result, dict) and _api_result_needs_attention(result):
            return True
    result = summary.get("result") or summary.get("shadow_result") or {}
    return isinstance(result, dict) and _api_result_needs_attention(result)


def _api_result_needs_attention(result: dict[str, Any]) -> bool:
    if not bool(result.get("ok", True)):
        return True
    status = str(result.get("status") or "").lower()
    return status in {"blocked", "error", "failed", "needs_attention"}


def _summaries_need_attention(summaries: list[dict[str, Any]]) -> bool:
    return any(_summary_needs_attention(summary) for summary in summaries)


def _run_shadow_test(args: argparse.Namespace, artifact_dir: Path) -> dict[str, Any]:
    run_id = args.run_id or f"live-{args.session_date}-operational-cycle"
    shadow = _api_json(str(args.api_root), "GET", f"/v1/nba/live/runs/{run_id}/shadow", query={"persist": "true"}, timeout=180)
    run_root = _latest_run_root(Path(args.local_root), run_id)
    tick_summary = _summarize_tick_file(run_root / "live_orderbook_ticks.jsonl") if run_root else {"exists": False}
    summary = {
        "stage": "shadow-test",
        "generated_at_utc": _now_utc().isoformat(),
        "run_id": run_id,
        "run_root": str(run_root) if run_root else None,
        "shadow_result": shadow,
        "live_orderbook_tick_summary": tick_summary,
        "shadow_contract": {
            "purpose": "evaluate strategies that are not yet feasible in full historical CLOB replay",
            "required_daily_outputs": [
                "signals_seen",
                "signals_blocked",
                "orderbook_latency",
                "feed_stalls",
                "hypothetical_fill_quality",
                "candidate_for_backbone_inclusion",
            ],
        },
    }
    _write_json(artifact_dir / "shadow_test_summary.json", summary)
    return summary


def _render_status(session_date: str, summaries: list[dict[str, Any]], report_path: Path) -> None:
    lines = [
        "# Operational Cycle Status",
        "",
        f"- Updated: `{_now_utc().isoformat()}`",
        f"- Session date: `{session_date}`",
        "",
        "## Stages",
        "",
    ]
    for summary in summaries:
        ok = not _summary_needs_attention(summary)
        lines.append(f"- `{summary.get('stage')}`: `{'ok' if ok else 'needs_attention'}`")
    lines.extend(
        [
            "",
            "## Current Live-Test Contract",
            "",
            "- Minimum live order: exchange minimum, currently represented as `5` shares with `$1` target notional.",
            "- Maximum live test entries per game: `10`.",
            "- Real-money runs require explicit `--live-money --entries-enabled --start-live-run` flags.",
            "- Strategies that cannot be historically replayed must still run in shadow and publish daily fill/latency evidence before promotion.",
        ]
    )
    _write_text(report_path, "\n".join(lines) + "\n")


def main() -> int:
    args = _parse_args()
    stages = tuple(args.stage or ("data-refresh", "shadow-test"))
    shared_root = Path(args.shared_root).expanduser().resolve()
    artifact_dir, report_dir = _stage_dirs(shared_root, args.session_date)
    summaries: list[dict[str, Any]] = []

    games = _discover_games(str(args.api_root), args.session_date, list(args.game_ids or []))
    if "data-refresh" in stages:
        summaries.append(_run_data_refresh(args, artifact_dir))
        games = _discover_games(str(args.api_root), args.session_date, list(args.game_ids or []))
    if "live-test" in stages:
        summaries.append(_start_or_read_live_run(args, artifact_dir, games))
    if "shadow-test" in stages:
        summaries.append(_run_shadow_test(args, artifact_dir))
    if "postgame" in stages:
        summaries.append(
            {
                "stage": "postgame",
                "generated_at_utc": _now_utc().isoformat(),
                "status": "manual_replay_required",
                "next_command": "python tools\\analyze_market_microstructure.py --season 2025-26 --output-name market_microstructure_backbone_v1",
            }
        )

    _write_json(
        artifact_dir / "operational_cycle_summary.json",
        {
            "generated_at_utc": _now_utc().isoformat(),
            "session_date": args.session_date,
            "stages": list(stages),
            "summaries": summaries,
        },
    )
    _render_status(
        args.session_date,
        summaries,
        report_dir / f"operational_cycle_status_{args.session_date}.md",
    )
    needs_attention = _summaries_need_attention(summaries)
    print(
        json.dumps(
            {
                "artifact_dir": str(artifact_dir),
                "stage_count": len(summaries),
                "status": "needs_attention" if needs_attention else "success",
            },
            indent=2,
        )
    )
    return 1 if needs_attention else 0


if __name__ == "__main__":
    raise SystemExit(main())
