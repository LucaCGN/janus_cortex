from __future__ import annotations

import argparse

from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    DEFAULT_EXTRA_EVENT_PROBES,
    EventProbeConfig,
    build_today_nba_event_probes_from_scoreboard,
    run_polymarket_event_seed_pack,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run v0.4.* Polymarket event ingestion probes against Postgres."
    )
    parser.add_argument(
        "--probe-set",
        choices=["today_nba", "extras", "combined"],
        default="today_nba",
        help="Probe set selection.",
    )
    parser.add_argument("--max-finished", type=int, default=2)
    parser.add_argument("--max-live", type=int, default=2)
    parser.add_argument("--max-upcoming", type=int, default=1)
    parser.add_argument("--include-upcoming", action="store_true")
    parser.add_argument("--stream-sample-count", type=int, default=3)
    parser.add_argument("--stream-sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--stream-max-outcomes", type=int, default=30)
    parser.add_argument(
        "--step",
        action="append",
        default=[],
        help="Optional step_code filter (can be repeated).",
    )
    return parser


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
    return selected


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    selected = _select_probes(args)
    if not selected:
        print("No probes selected from the requested probe-set.")
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
                    f"market_state_snapshots={result.market_state_snapshots_inserted}",
                    f"history_fetched={result.history_points_fetched}",
                    f"stream_fetched={result.stream_rows_fetched}",
                    f"stream_inserted={result.stream_rows_inserted}",
                ]
            )
        )
        if result.error_text:
            print(f"error={result.error_text}")
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
