from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.wnba.analysis.development_loop import evaluate_wnba_standard_development_loop
from tools.run_wnba_analysis_bundle import build_wnba_analysis_bundle
from tools.run_wnba_polymarket_history_probe import run_probe


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate whether WNBA can enter the standard JANUS development loop.")
    parser.add_argument("--season", default="2026")
    parser.add_argument("--sample-game-id", default=None)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--with-price-history-probe", action="store_true")
    parser.add_argument("--price-event-limit", type=int, default=10)
    parser.add_argument("--price-fidelity", type=int, default=1)
    parser.add_argument("--migrations-applied", action="store_true")
    parser.add_argument("--migrations-not-applied", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _migration_flag(args: argparse.Namespace) -> bool | None:
    if args.migrations_applied and args.migrations_not_applied:
        raise ValueError("Use only one of --migrations-applied or --migrations-not-applied.")
    if args.migrations_applied:
        return True
    if args.migrations_not_applied:
        return False
    return None


def main() -> int:
    args = _build_parser().parse_args()
    migrations_applied = _migration_flag(args)
    analysis_payload = build_wnba_analysis_bundle(
        season=args.season,
        sample_game_id=args.sample_game_id,
        use_fixture=args.fixture,
    )
    price_payload = None
    if args.with_price_history_probe:
        try:
            price_payload = run_probe(
                season=args.season,
                event_limit=args.price_event_limit,
                game_id=args.sample_game_id,
                fidelity=args.price_fidelity,
            )
        except Exception as exc:  # noqa: BLE001 - provider health is reported as a blocker.
            price_payload = {
                "status": "blocked",
                "blockers": ["wnba_price_history_probe_error"],
                "error_text": repr(exc),
            }
    payload = evaluate_wnba_standard_development_loop(
        analysis_payload=analysis_payload,
        price_history_payload=price_payload,
        migrations_applied=migrations_applied,
    )
    result = {
        "development_loop": payload,
        "analysis": {
            "sample_game_id": analysis_payload.get("sample_game_id"),
            "source_mode": analysis_payload.get("source_mode"),
            "data_status": (analysis_payload.get("data_audit") or {}).get("status"),
            "integration_status": (analysis_payload.get("integration_readiness") or {}).get("status"),
            "ml_status": (analysis_payload.get("ml_training") or {}).get("status"),
            "historical_backfill_status": (analysis_payload.get("historical_backfill") or {}).get("status"),
        },
        "price_history_probe": price_payload,
    }

    if args.json:
        print(json.dumps(result, default=_jsonable, indent=2, sort_keys=True))
    else:
        loop = result["development_loop"]
        print(f"status={loop['status']}")
        print(f"standard_loop_allowed={loop['standard_loop_allowed']}")
        print(f"routing_priority={loop['routing_priority']}")
        print(f"orders_allowed={loop['orders_allowed']}")
        print(f"passive_shadow_ready={loop['passive_shadow_ready']}")
        print(f"price_history_probe_ready={loop['price_history_probe_ready']}")
        print(f"minimum_before_standard_loop={','.join(loop['minimum_before_standard_loop'])}")
        print(f"calibrated_or_live_blockers={','.join(loop['calibrated_or_live_blockers'])}")
        print(f"verdict={loop['verdict']}")
        print("next_tasks=" + ",".join(task["id"] + ":" + task["status"] for task in loop["next_tasks"]))
    return 0 if result["development_loop"]["status"] != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
