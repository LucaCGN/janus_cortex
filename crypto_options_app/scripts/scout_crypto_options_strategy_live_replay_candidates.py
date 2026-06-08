from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT, CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.workers.strategy_live_replay import (  # noqa: E402
    DEFAULT_STRATEGY_IDS,
    StrategyLiveReplayConfig,
    scout_strategy_live_replay_candidates,
)

SCENARIO_SELECTOR_CHOICES = (
    "latest",
    "profile_preferred",
    "profile_opposed",
    "profile_group",
    "profile_group_quality",
    "high_inversion",
    "high_inversion_disjoint",
    "recent_high_inversion_disjoint",
    "hedge_grid_ready",
    "hedge_grid_closed_cycle_ready",
    "tail_touch",
    "tail_touch_forward_edge_clean",
    "low_range_no_edge",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scout read-only strategy live-replay candidate windows without running strategy validation."
    )
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--output-dir", default=str(CENTRAL_ARTIFACT_ROOT / "reports"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--strategy-ids", nargs="+", default=list(DEFAULT_STRATEGY_IDS))
    parser.add_argument("--forward-mark-horizon-seconds", type=float, default=60.0)
    parser.add_argument("--max-scenarios", type=int, default=12)
    parser.add_argument(
        "--scenario-selector",
        choices=SCENARIO_SELECTOR_CHOICES,
        nargs="+",
        default=["tail_touch_forward_edge_clean"],
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selectors = tuple(str(selector) for selector in args.scenario_selector)
    payloads = [
        scout_strategy_live_replay_candidates(
            StrategyLiveReplayConfig(
                db_path=Path(args.db_path),
                run_id=args.run_id if len(selectors) == 1 else None,
                strategy_ids=tuple(str(strategy_id) for strategy_id in args.strategy_ids),
                forward_mark_horizon_seconds=float(args.forward_mark_horizon_seconds),
                max_scenarios=int(args.max_scenarios),
                scenario_selector=selector,
            )
        )
        for selector in selectors
    ]
    if not args.no_write_report:
        for payload in payloads:
            _write_report(payload, output_dir=Path(args.output_dir))
        if len(payloads) > 1:
            _write_batch_report(payloads, output_dir=Path(args.output_dir))
    payload = payloads[0] if len(payloads) == 1 else _batch_payload(payloads)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in payloads:
            print(f"run_id={item['run_id']}")
            print(f"selector={item['scenario_selector']}")
            print(f"scenario_count={item['scenario_count']}")
            print(f"distinct_event_count={item['distinct_event_count']}")
            print(f"distinct_event_token_count={item['distinct_event_token_count']}")
            print(f"sources={','.join(item['scenario_sources'])}")
            print(f"blockers={','.join(item['blockers'])}")
    return 0


def _write_report(payload: dict, *, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(payload.get("run_id") or "strategy-live-replay-scout")
    selector = _safe_selector_name(str(payload.get("scenario_selector") or "unknown"))
    text = json.dumps(payload, indent=2, sort_keys=True)
    timestamped = output_dir / f"{run_id}.json"
    latest = output_dir / "strategy_replay_candidate_scout_latest.json"
    selector_latest = output_dir / f"strategy_replay_candidate_scout_{selector}_latest.json"
    timestamped.write_text(text, encoding="utf-8")
    for path in (latest, selector_latest):
        temp_latest = path.with_name(f"{path.name}.tmp")
        temp_latest.write_text(text, encoding="utf-8")
        temp_latest.replace(path)


def _write_batch_report(payloads: list[dict], *, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _batch_payload(payloads)
    text = json.dumps(payload, indent=2, sort_keys=True)
    latest = output_dir / "strategy_replay_candidate_scout_batch_latest.json"
    temp_latest = latest.with_name(f"{latest.name}.tmp")
    temp_latest.write_text(text, encoding="utf-8")
    temp_latest.replace(latest)


def _batch_payload(payloads: list[dict]) -> dict:
    event_keys = sorted(
        {
            str(key)
            for payload in payloads
            for key in payload.get("distinct_event_keys", [])
            if str(key).strip()
        }
    )
    token_keys = sorted(
        {
            str(key)
            for payload in payloads
            for key in payload.get("distinct_event_token_keys", [])
            if str(key).strip()
        }
    )
    return {
        "schema_version": "crypto_options_strategy_live_replay_candidate_scout_batch_v1",
        "mode": "live_replay_candidate_scout_batch",
        "selector_count": len(payloads),
        "scenario_count": sum(int(payload.get("scenario_count") or 0) for payload in payloads),
        "distinct_event_count": len(event_keys),
        "distinct_event_token_count": len(token_keys),
        "distinct_event_keys": event_keys[:50],
        "distinct_event_token_keys": token_keys[:50],
        "scouts": payloads,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": all(bool(payload.get("manual_orders_avoided", True)) for payload in payloads),
    }


def _safe_selector_name(selector: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in selector)
    return safe.strip("_") or "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
