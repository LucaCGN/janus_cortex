from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.databases.postgres import managed_connection
from app.data.databases.safety import describe_database_target
from app.data.pipelines.daily.nba.analysis.consumer_adapters import load_analysis_consumer_snapshot
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    AnalysisConsumerRequest,
    AnalysisUniverseRequest,
)
from app.data.pipelines.daily.nba.analysis.universe import build_analysis_universe_qa_summary, load_analysis_universe


def _count_table_rows(connection: Any, table_name: str, *, season: str, season_phase: str, analysis_version: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT count(*)
            FROM {table_name}
            WHERE season = %s AND season_phase = %s AND analysis_version = %s;
            """,
            (season, season_phase, analysis_version),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def collect_snapshot(*, season: str, season_phase: str, analysis_version: str, output_root: str | None = None) -> dict[str, object]:
    with managed_connection() as connection:
        universe_bundle = load_analysis_universe(
            connection,
            AnalysisUniverseRequest(
                season=season,
                season_phase=season_phase,
                coverage_filter="all",
                analysis_version=analysis_version,
            ),
        )
        universe_summary = build_analysis_universe_qa_summary(universe_bundle.full_universe)
        mart_counts = {
            "nba_analysis_game_team_profiles": _count_table_rows(
                connection,
                "nba.nba_analysis_game_team_profiles",
                season=season,
                season_phase=season_phase,
                analysis_version=analysis_version,
            ),
            "nba_analysis_state_panel": _count_table_rows(
                connection,
                "nba.nba_analysis_state_panel",
                season=season,
                season_phase=season_phase,
                analysis_version=analysis_version,
            ),
            "nba_analysis_team_season_profiles": _count_table_rows(
                connection,
                "nba.nba_analysis_team_season_profiles",
                season=season,
                season_phase=season_phase,
                analysis_version=analysis_version,
            ),
            "nba_analysis_opening_band_profiles": _count_table_rows(
                connection,
                "nba.nba_analysis_opening_band_profiles",
                season=season,
                season_phase=season_phase,
                analysis_version=analysis_version,
            ),
            "nba_analysis_winner_definition_profiles": _count_table_rows(
                connection,
                "nba.nba_analysis_winner_definition_profiles",
                season=season,
                season_phase=season_phase,
                analysis_version=analysis_version,
            ),
        }

    consumer_snapshot = None
    if output_root:
        consumer_payload = load_analysis_consumer_snapshot(
            AnalysisConsumerRequest(
                season=season,
                season_phase=season_phase,
                analysis_version=analysis_version,
                output_root=output_root,
            )
        )
        consumer_snapshot = {
            "output_dir": consumer_payload.get("output_dir"),
            "benchmark_contract_version": ((consumer_payload.get("benchmark") or {}).get("contract_version")),
            "benchmark_experiment_id": (((consumer_payload.get("benchmark") or {}).get("experiment") or {}).get("experiment_id")),
            "report_section_count": len(((consumer_payload.get("report") or {}).get("sections") or [])),
            "model_track_count": len(((consumer_payload.get("models") or {}).get("tracks") or [])),
        }

    return {
        "database_target": describe_database_target(),
        "season": season,
        "season_phase": season_phase,
        "analysis_version": analysis_version,
        "universe": universe_summary,
        "mart_counts": mart_counts,
        "consumer_snapshot": consumer_snapshot,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect a non-live validation snapshot for the NBA analysis module.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--season-phase", default=DEFAULT_SEASON_PHASE)
    parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    parser.add_argument("--output-root", default=None)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    payload = collect_snapshot(
        season=args.season,
        season_phase=args.season_phase,
        analysis_version=args.analysis_version,
        output_root=args.output_root,
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
