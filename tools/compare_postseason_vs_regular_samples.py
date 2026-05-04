from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.nba.analysis.benchmark_integration import resolve_default_shared_root
from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, DEFAULT_OUTPUT_ROOT, DEFAULT_SEASON


DEFAULT_SAMPLE_COUNT = 1000
DEFAULT_RANDOM_SEED = 20260429
ARTIFACT_NAME = "regular_sample_vs_postseason"


@dataclass(slots=True)
class SubjectPair:
    subject_id: str
    regular_trades_path: Path
    postseason_standard_path: Path
    postseason_replay_path: Path | None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _subject_from_standard_path(path: Path) -> str:
    name = path.stem
    if name.startswith("standard_"):
        return name[len("standard_") :]
    return name


def _normalize_game_id_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.strip()


def _portfolio_like_trade_metrics(frame: pd.DataFrame, *, games_considered: int) -> dict[str, Any]:
    if frame.empty:
        return {
            "games_considered": int(games_considered),
            "trade_count": 0,
            "traded_game_count": 0,
            "trades_per_game": 0.0,
            "win_rate": None,
            "avg_return": None,
            "median_return": None,
            "sum_return_unit_stake": 0.0,
            "ending_bankroll_unit_stake": 10.0,
        }
    returns = pd.to_numeric(frame["gross_return_with_slippage"], errors="coerce").dropna()
    traded_game_count = int(frame["game_id"].nunique()) if "game_id" in frame.columns else 0
    return {
        "games_considered": int(games_considered),
        "trade_count": int(len(returns)),
        "traded_game_count": traded_game_count,
        "trades_per_game": float(len(returns) / games_considered) if games_considered else None,
        "win_rate": float((returns > 0).mean()) if len(returns) else None,
        "avg_return": float(returns.mean()) if len(returns) else None,
        "median_return": float(returns.median()) if len(returns) else None,
        "sum_return_unit_stake": float(returns.sum()) if len(returns) else 0.0,
        "ending_bankroll_unit_stake": float(10.0 + returns.sum()) if len(returns) else 10.0,
    }


def _metric_percentile(sample_values: pd.Series, observed: float | None) -> float | None:
    resolved = _safe_float(observed)
    clean = pd.to_numeric(sample_values, errors="coerce").dropna()
    if resolved is None or clean.empty:
        return None
    return float((clean <= resolved).mean())


def _distribution_row(
    *,
    subject_id: str,
    metric: str,
    sample_values: pd.Series,
    postseason_value: float | None,
) -> dict[str, Any]:
    clean = pd.to_numeric(sample_values, errors="coerce").dropna()
    if clean.empty:
        return {
            "subject_id": subject_id,
            "metric": metric,
            "regular_sample_mean": None,
            "regular_sample_median": None,
            "regular_sample_p05": None,
            "regular_sample_p25": None,
            "regular_sample_p75": None,
            "regular_sample_p95": None,
            "postseason_value": postseason_value,
            "postseason_percentile": None,
            "postseason_outside_5_95_flag": None,
        }
    p05 = float(clean.quantile(0.05))
    p95 = float(clean.quantile(0.95))
    percentile = _metric_percentile(clean, postseason_value)
    resolved = _safe_float(postseason_value)
    return {
        "subject_id": subject_id,
        "metric": metric,
        "regular_sample_mean": float(clean.mean()),
        "regular_sample_median": float(clean.median()),
        "regular_sample_p05": p05,
        "regular_sample_p25": float(clean.quantile(0.25)),
        "regular_sample_p75": float(clean.quantile(0.75)),
        "regular_sample_p95": p95,
        "postseason_value": resolved,
        "postseason_percentile": percentile,
        "postseason_outside_5_95_flag": bool(resolved is not None and (resolved < p05 or resolved > p95)),
    }


def _discover_subject_pairs(*, regular_backtests_root: Path, replay_root: Path) -> tuple[list[SubjectPair], list[dict[str, Any]]]:
    pairs: list[SubjectPair] = []
    skipped: list[dict[str, Any]] = []
    for postseason_path in sorted(replay_root.glob("standard_*.csv")):
        subject_id = _subject_from_standard_path(postseason_path)
        if subject_id.startswith("controller_"):
            skipped.append(
                {
                    "subject_id": subject_id,
                    "reason": "no_exact_regular_controller_artifact",
                    "postseason_standard_path": str(postseason_path),
                }
            )
            continue
        regular_path = regular_backtests_root / f"{subject_id}_trades.csv"
        if not regular_path.exists():
            skipped.append(
                {
                    "subject_id": subject_id,
                    "reason": "no_exact_regular_family_trade_file",
                    "postseason_standard_path": str(postseason_path),
                    "expected_regular_path": str(regular_path),
                }
            )
            continue
        replay_path = replay_root / f"replay_{subject_id}.csv"
        pairs.append(
            SubjectPair(
                subject_id=subject_id,
                regular_trades_path=regular_path,
                postseason_standard_path=postseason_path,
                postseason_replay_path=replay_path if replay_path.exists() else None,
            )
        )
    return pairs, skipped


def _load_game_universe(regular_output_root: Path) -> list[str]:
    game_profiles = _read_csv(regular_output_root / "nba_analysis_game_team_profiles.csv")
    if game_profiles.empty or "game_id" not in game_profiles.columns:
        return []
    return sorted(_normalize_game_id_series(game_profiles["game_id"]).dropna().unique().tolist())


def _build_random_sample_metrics(
    *,
    subject_pairs: list[SubjectPair],
    regular_game_ids: list[str],
    sample_size: int,
    sample_count: int,
    random_seed: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(random_seed)
    if len(regular_game_ids) < sample_size:
        raise ValueError(f"Need at least {sample_size} regular games; found {len(regular_game_ids)}.")
    regular_frames: dict[str, pd.DataFrame] = {}
    for pair in subject_pairs:
        frame = _read_csv(pair.regular_trades_path)
        if not frame.empty:
            frame = frame.copy()
            frame["game_id"] = _normalize_game_id_series(frame["game_id"])
        regular_frames[pair.subject_id] = frame
    rows: list[dict[str, Any]] = []
    game_array = np.asarray(regular_game_ids, dtype=object)
    for sample_index in range(sample_count):
        sampled_games = set(rng.choice(game_array, size=sample_size, replace=False).tolist())
        for pair in subject_pairs:
            frame = regular_frames[pair.subject_id]
            sample_frame = frame[frame["game_id"].isin(sampled_games)].copy() if not frame.empty else frame
            rows.append(
                {
                    "sample_index": sample_index,
                    "subject_id": pair.subject_id,
                    **_portfolio_like_trade_metrics(sample_frame, games_considered=sample_size),
                }
            )
    return pd.DataFrame(rows)


def _build_postseason_metrics(*, subject_pairs: list[SubjectPair], sample_size: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for pair in subject_pairs:
        standard_frame = _read_csv(pair.postseason_standard_path)
        if not standard_frame.empty:
            standard_frame = standard_frame.copy()
            standard_frame["game_id"] = _normalize_game_id_series(standard_frame["game_id"])
        row = {
            "subject_id": pair.subject_id,
            "mode": "postseason_standard",
            **_portfolio_like_trade_metrics(standard_frame, games_considered=sample_size),
        }
        replay_frame = _read_csv(pair.postseason_replay_path) if pair.postseason_replay_path else pd.DataFrame()
        replay_metrics = _portfolio_like_trade_metrics(replay_frame, games_considered=sample_size)
        row.update(
            {
                "replay_trade_count": replay_metrics["trade_count"],
                "replay_trades_per_game": replay_metrics["trades_per_game"],
                "replay_avg_return": replay_metrics["avg_return"],
                "replay_ending_bankroll_unit_stake": replay_metrics["ending_bankroll_unit_stake"],
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _build_distribution_summary(
    *,
    random_metrics_df: pd.DataFrame,
    postseason_metrics_df: pd.DataFrame,
) -> pd.DataFrame:
    metrics = [
        "trade_count",
        "traded_game_count",
        "trades_per_game",
        "win_rate",
        "avg_return",
        "median_return",
        "sum_return_unit_stake",
        "ending_bankroll_unit_stake",
    ]
    rows: list[dict[str, Any]] = []
    postseason_lookup = postseason_metrics_df.set_index("subject_id") if not postseason_metrics_df.empty else pd.DataFrame()
    for subject_id, group in random_metrics_df.groupby("subject_id", sort=True):
        for metric in metrics:
            postseason_value = None
            if not postseason_lookup.empty and subject_id in postseason_lookup.index:
                postseason_value = _safe_float(postseason_lookup.at[subject_id, metric])
            rows.append(
                _distribution_row(
                    subject_id=str(subject_id),
                    metric=metric,
                    sample_values=group[metric],
                    postseason_value=postseason_value,
                )
            )
    return pd.DataFrame(rows)


def _render_report(payload: dict[str, Any], summary_df: pd.DataFrame, skipped_df: pd.DataFrame) -> str:
    lines = [
        "# Regular Sample Vs Postseason Comparison",
        "",
        f"- published_at: `{payload['published_at']}`",
        f"- sample_size_games: `{payload['sample_size_games']}`",
        f"- random_sample_count: `{payload['random_sample_count']}`",
        f"- regular_game_count: `{payload['regular_game_count']}`",
        f"- exact_subject_count: `{payload['exact_subject_count']}`",
        "",
        "## Read",
        "",
        (
            "- This compares exact deterministic family lanes where both regular-season and postseason standard trade "
            "artifacts exist. It does not compare the current `controller_vnext_*` subjects because exact regular-season "
            "controller artifacts are not present in the archive."
        ),
        "- The result answers whether the postseason standard sample looks unusual versus random same-size regular-season samples.",
        "- It does not prove replay executability for the regular season; that still requires running replay over regular-season candidates.",
        "",
        "## Postseason Extremeness",
        "",
    ]
    if summary_df.empty:
        lines.append("- No exact subject pairs were available.")
    else:
        pivot = summary_df[summary_df["metric"].isin(["avg_return", "trade_count", "trades_per_game"])].copy()
        for row in pivot.to_dict(orient="records"):
            flag = "outside_5_95" if bool(row.get("postseason_outside_5_95_flag")) else "inside_5_95"
            lines.append(
                f"- `{row.get('subject_id')}` metric `{row.get('metric')}`: postseason `{row.get('postseason_value')}` "
                f"vs regular p05/p95 `{row.get('regular_sample_p05')}`/`{row.get('regular_sample_p95')}` "
                f"percentile `{row.get('postseason_percentile')}` status `{flag}`"
            )
    if not skipped_df.empty:
        lines.extend(["", "## Skipped Subjects", ""])
        for row in skipped_df.to_dict(orient="records"):
            lines.append(f"- `{row.get('subject_id')}`: `{row.get('reason')}`")
    lines.extend(
        [
            "",
            "## Action Plan",
            "",
            "- Use this same-size sampling result as the standard-label variance check.",
            "- If postseason metrics land inside the regular random-sample band, treat season-wide training as statistically reasonable for standard labels.",
            "- Next, run replay over regular-season candidates so the all-season training labels match the current replay baseline.",
            "- After regular-season replay exists, rerun ML with `season_phases = regular_season, play_in, playoffs` and evaluate postseason holdout separately.",
            "",
            "## Artifacts",
            "",
            f"- random samples CSV: `{payload['artifacts']['random_sample_metrics_csv']}`",
            f"- postseason metrics CSV: `{payload['artifacts']['postseason_metrics_csv']}`",
            f"- distribution summary CSV: `{payload['artifacts']['distribution_summary_csv']}`",
            f"- skipped subjects CSV: `{payload['artifacts']['skipped_subjects_csv']}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare postseason deterministic family results against random same-size regular-season samples."
    )
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    parser.add_argument("--shared-root", default=None)
    parser.add_argument("--analysis-output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--random-seed", type=int, default=DEFAULT_RANDOM_SEED)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    shared_root = Path(args.shared_root) if args.shared_root else resolve_default_shared_root()
    analysis_output_root = Path(args.analysis_output_root)
    replay_root = shared_root / "artifacts" / "replay-engine-hf" / args.season / "postseason_execution_replay"
    regular_output_root = analysis_output_root / args.season / "regular_season" / args.analysis_version
    regular_backtests_root = regular_output_root / "backtests"
    replay_run = json.loads((replay_root / "replay_run.json").read_text(encoding="utf-8"))
    sample_size = int(args.sample_size or replay_run.get("finished_game_count") or 32)
    artifact_root = shared_root / "artifacts" / "ml-trading-lane" / args.season / ARTIFACT_NAME
    report_root = shared_root / "reports" / "ml-trading-lane"
    artifact_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)

    regular_game_ids = _load_game_universe(regular_output_root)
    subject_pairs, skipped_rows = _discover_subject_pairs(
        regular_backtests_root=regular_backtests_root,
        replay_root=replay_root,
    )
    random_metrics_df = _build_random_sample_metrics(
        subject_pairs=subject_pairs,
        regular_game_ids=regular_game_ids,
        sample_size=sample_size,
        sample_count=int(args.sample_count),
        random_seed=int(args.random_seed),
    )
    postseason_metrics_df = _build_postseason_metrics(subject_pairs=subject_pairs, sample_size=sample_size)
    distribution_summary_df = _build_distribution_summary(
        random_metrics_df=random_metrics_df,
        postseason_metrics_df=postseason_metrics_df,
    )
    skipped_df = pd.DataFrame(skipped_rows)

    random_path = artifact_root / "random_sample_metrics.csv"
    postseason_path = artifact_root / "postseason_metrics.csv"
    summary_path = artifact_root / "distribution_summary.csv"
    skipped_path = artifact_root / "skipped_subjects.csv"
    payload_path = artifact_root / "run_payload.json"
    report_path = report_root / "regular_sample_vs_postseason_report.md"

    random_metrics_df.to_csv(random_path, index=False)
    postseason_metrics_df.to_csv(postseason_path, index=False)
    distribution_summary_df.to_csv(summary_path, index=False)
    skipped_df.to_csv(skipped_path, index=False)

    payload = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "season": args.season,
        "analysis_version": args.analysis_version,
        "sample_size_games": sample_size,
        "random_sample_count": int(args.sample_count),
        "random_seed": int(args.random_seed),
        "regular_game_count": len(regular_game_ids),
        "exact_subject_count": len(subject_pairs),
        "skipped_subject_count": len(skipped_rows),
        "artifacts": {
            "random_sample_metrics_csv": str(random_path),
            "postseason_metrics_csv": str(postseason_path),
            "distribution_summary_csv": str(summary_path),
            "skipped_subjects_csv": str(skipped_path),
            "report_markdown": str(report_path),
        },
    }
    payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    report_path.write_text(_render_report(payload, distribution_summary_df, skipped_df), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
