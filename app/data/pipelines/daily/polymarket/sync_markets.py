from __future__ import annotations

import argparse
from typing import Sequence

from app.data.databases.postgres import managed_connection
from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    DEFAULT_EXTRA_EVENT_PROBES,
    EventProbeConfig,
    build_today_nba_event_probes_from_scoreboard,
    run_polymarket_event_seed_pack,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run v0.4.2 polymarket markets/outcomes sync with market-state snapshots."
    )
    parser.add_argument(
        "--probe-set",
        choices=["today_nba", "extras", "combined"],
        default="today_nba",
    )
    parser.add_argument("--max-finished", type=int, default=2)
    parser.add_argument("--max-live", type=int, default=2)
    parser.add_argument("--max-upcoming", type=int, default=2)
    parser.add_argument("--include-upcoming", action="store_true")
    parser.add_argument("--stream-sample-count", type=int, default=3)
    parser.add_argument("--stream-sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--stream-max-outcomes", type=int, default=30)
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--step", action="append", default=[])
    return parser


def _slug_from_probe(probe: EventProbeConfig) -> str:
    return probe.url.rstrip("/").split("/")[-1]


def _filter_missing_only(probes: Sequence[EventProbeConfig]) -> list[EventProbeConfig]:
    if not probes:
        return []
    slugs = [_slug_from_probe(item) for item in probes]
    with managed_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT canonical_slug
                FROM catalog.events
                WHERE canonical_slug = ANY(%s);
                """,
                (slugs,),
            )
            existing = {str(row[0]) for row in cursor.fetchall()}
    return [item for item in probes if _slug_from_probe(item) not in existing]


def _select_probes(args: argparse.Namespace) -> list[EventProbeConfig]:
    selected: list[EventProbeConfig] = []
    if args.probe_set in {"extras", "combined"}:
        selected.extend(DEFAULT_EXTRA_EVENT_PROBES)
    if args.probe_set in {"today_nba", "combined"}:
        today = build_today_nba_event_probes_from_scoreboard(
            max_finished=args.max_finished,
            max_live=args.max_live,
            max_upcoming=args.max_upcoming,
            include_upcoming=args.include_upcoming,
            stream_sample_count=args.stream_sample_count,
            stream_sample_interval_sec=args.stream_sample_interval_sec,
            stream_max_outcomes=args.stream_max_outcomes,
        )
        selected.extend(today.all)

    if args.step:
        wanted = set(args.step)
        selected = [item for item in selected if item.step_code in wanted]

    if args.missing_only:
        selected = _filter_missing_only(selected)
    return selected


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    selected = _select_probes(args)
    if not selected:
        print("No probes selected.")
        return 2

    summary = run_polymarket_event_seed_pack(selected, persist=True)
    print(f"sync_run_id={summary.sync_run_id}")
    print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
    for result in summary.results:
        print(
            " | ".join(
                [
                    f"step={result.step_code}",
                    f"status={result.status}",
                    f"slug={result.slug}",
                    f"markets_seeded={result.markets_seeded}",
                    f"market_state_snapshots={result.market_state_snapshots_inserted}",
                    f"outcomes_seeded={result.outcomes_seeded}",
                    f"history_fetched={result.history_points_fetched}",
                    f"stream_fetched={result.stream_rows_fetched}",
                ]
            )
        )
        if result.error_text:
            print(f"error={result.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
