from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.pipelines.canonical.fixture_loader import (  # noqa: E402
    load_dataframe_fixture,
    load_default_mapping_fixtures,
)
from app.data.pipelines.canonical.mapping_service import build_canonical_mapping_result  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run canonical mapping against saved fixtures.")
    parser.add_argument("--events-fixture", default="gamma_nba_events_fixture")
    parser.add_argument("--moneyline-fixture", default="gamma_nba_moneyline_fixture")
    parser.add_argument("--schedule-fixture", default="nba_schedule_fixture")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    if (
        args.events_fixture == "gamma_nba_events_fixture"
        and args.moneyline_fixture == "gamma_nba_moneyline_fixture"
        and args.schedule_fixture == "nba_schedule_fixture"
    ):
        events_df, moneyline_df, schedule_df = load_default_mapping_fixtures()
    else:
        events_df = load_dataframe_fixture(args.events_fixture)
        moneyline_df = load_dataframe_fixture(args.moneyline_fixture)
        schedule_df = load_dataframe_fixture(args.schedule_fixture)

    result = build_canonical_mapping_result(
        events_df=events_df,
        moneyline_df=moneyline_df,
        schedule_df=schedule_df,
    )

    print(f"events={result.stats_json.get('event_count', 0)}")
    print(f"markets={result.stats_json.get('market_count', 0)}")
    print(f"outcomes={result.stats_json.get('outcome_count', 0)}")
    print(f"quality_errors={result.quality_report.error_count}")
    print(f"quality_warnings={result.quality_report.warning_count}")

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"output_json={output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
