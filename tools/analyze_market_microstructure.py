from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (  # noqa: E402
    PORTFOLIO_SCOPE_ROUTED,
    PORTFOLIO_SCOPE_SINGLE_FAMILY,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.backtests.replay import load_finished_replay_contexts  # noqa: E402
from app.runtime.local_paths import resolve_local_root  # noqa: E402


DEFAULT_LOCAL_ROOT = resolve_local_root()
DEFAULT_SHARED_ROOT = DEFAULT_LOCAL_ROOT / "shared"
DEFAULT_ARCHIVE_ROOT = DEFAULT_LOCAL_ROOT / "archives" / "output" / "nba_analysis"
DEFAULT_TRACKS_ROOT = DEFAULT_LOCAL_ROOT / "tracks" / "live-controller"
DEFAULT_SEASON = "2025-26"
DEFAULT_ANALYSIS_VERSION = "v1_0_1"
DEFAULT_THRESHOLDS = (
    0.005,
    0.01,
    0.015,
    0.02,
    0.03,
    0.04,
    0.05,
    0.075,
    0.10,
    0.125,
    0.15,
    0.175,
    0.19,
    0.20,
    0.225,
    0.25,
    0.30,
    0.35,
)
DEFAULT_LATENCIES_SECONDS = (0.0, 1.0, 3.0, 10.0, 30.0)
DEFAULT_POSITION_FRACTIONS = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.80)
DEFAULT_SAMPLE_SIZES = (10, 50, 100)
DEFAULT_SEEDS = (1107, 2113, 3251, 4421, 5573)
DEFAULT_INITIAL_BANKROLL = 10.0
DEFAULT_MIN_ORDER_DOLLARS = 1.0
DEFAULT_MIN_SHARES = 5.0
DEFAULT_MAX_CONCURRENT_POSITIONS = 2


@dataclass(frozen=True, slots=True)
class TradeSource:
    phase: str
    artifact_root: Path
    mode: str
    source_quality: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze price resistance bands, CLOB proxy impact, full-season/postseason PnL, and sizing sweeps."
    )
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument("--tracks-root", default=str(DEFAULT_TRACKS_ROOT))
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--analysis-version", default=DEFAULT_ANALYSIS_VERSION)
    parser.add_argument("--output-name", default="market_microstructure_backbone_v1")
    parser.add_argument("--regular-artifact", default="full_regular_execution_replay_v1")
    parser.add_argument("--postseason-artifact", default="postseason_execution_replay")
    parser.add_argument("--regular-supplement-artifact", action="append", default=["grid_regular_standard_backtest_v1"])
    parser.add_argument("--postseason-supplement-artifact", action="append", default=["grid_postseason_standard_backtest_v1"])
    parser.add_argument("--latency-seconds", action="append", type=float, default=[])
    parser.add_argument("--position-fraction", action="append", type=float, default=[])
    parser.add_argument("--sample-size", action="append", type=int, default=[])
    parser.add_argument("--seed", action="append", type=int, default=[])
    parser.add_argument("--initial-bankroll", type=float, default=DEFAULT_INITIAL_BANKROLL)
    parser.add_argument("--min-order-dollars", type=float, default=DEFAULT_MIN_ORDER_DOLLARS)
    parser.add_argument("--min-shares", type=float, default=DEFAULT_MIN_SHARES)
    parser.add_argument("--max-concurrent-positions", type=int, default=DEFAULT_MAX_CONCURRENT_POSITIONS)
    return parser.parse_args()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_timestamp(value: Any) -> pd.Timestamp | None:
    resolved = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(resolved):
        return None
    return resolved


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(to_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def _subject_stem(subject_name: str) -> str:
    return str(subject_name).replace(" ", "_").replace("::", "__").replace("/", "_")


def _price_band(price: float | None, *, width: float = 0.05) -> str:
    if price is None or pd.isna(price):
        return "unknown"
    bounded = max(0.0, min(1.0, float(price)))
    low = math.floor(bounded / width) * width
    high = min(1.0, low + width)
    return f"{low:.2f}-{high:.2f}"


def _phase_archive_state_path(archive_root: Path, season: str, phase: str, analysis_version: str) -> Path:
    return archive_root / season / phase / analysis_version / "nba_analysis_state_panel.parquet"


def _load_state_panel(
    *,
    archive_root: Path,
    season: str,
    phase: str,
    analysis_version: str,
) -> pd.DataFrame:
    archive_path = _phase_archive_state_path(archive_root, season, phase, analysis_version)
    frame = _read_parquet(archive_path)
    if frame.empty and phase == "regular_season":
        _, frame, _ = load_finished_replay_contexts(
            season=season,
            analysis_version=analysis_version,
            season_phase=phase,
            season_phases=(phase,),
        )
    if frame.empty:
        return frame
    work = frame.copy()
    work["game_id"] = work["game_id"].astype(str).str.zfill(10)
    if "event_at" in work.columns:
        work["event_at"] = pd.to_datetime(work["event_at"], errors="coerce", utc=True)
    for column in (
        "team_price",
        "opening_price",
        "score_diff",
        "seconds_to_game_end",
        "clock_elapsed_seconds",
        "state_index",
        "gap_before_seconds",
        "gap_after_seconds",
    ):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    return work.sort_values(["game_id", "team_side", "event_at", "state_index"], kind="mergesort").reset_index(drop=True)


def _load_all_state_panels(*, archive_root: Path, season: str, analysis_version: str) -> dict[str, pd.DataFrame]:
    panels = {
        "regular_season": _load_state_panel(
            archive_root=archive_root,
            season=season,
            phase="regular_season",
            analysis_version=analysis_version,
        ),
        "play_in": _load_state_panel(
            archive_root=archive_root,
            season=season,
            phase="play_in",
            analysis_version=analysis_version,
        ),
        "playoffs": _load_state_panel(
            archive_root=archive_root,
            season=season,
            phase="playoffs",
            analysis_version=analysis_version,
        ),
    }
    postseason_frames = [frame for key, frame in panels.items() if key in {"play_in", "playoffs"} and not frame.empty]
    panels["postseason"] = pd.concat(postseason_frames, ignore_index=True, sort=False) if postseason_frames else pd.DataFrame()
    return panels


def _iter_live_orderbook_paths(tracks_root: Path) -> Iterable[Path]:
    if not tracks_root.exists():
        return []
    return tracks_root.rglob("live_orderbook_ticks.jsonl")


def _load_live_tick_profile(tracks_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for path in _iter_live_orderbook_paths(tracks_root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            tick = payload.get("tick") or {}
            orderbook = payload.get("orderbook") or {}
            price = _safe_float(tick.get("price"))
            bid = _safe_float(orderbook.get("best_bid") if orderbook else tick.get("bid"))
            ask = _safe_float(orderbook.get("best_ask") if orderbook else tick.get("ask"))
            spread = _safe_float(orderbook.get("spread_cents"))
            if spread is None and bid is not None and ask is not None:
                spread = max(0.0, (ask - bid) * 100.0)
            rows.append(
                {
                    "source_path": str(path),
                    "observed_at": payload.get("observed_at"),
                    "tick_at": tick.get("ts") or (orderbook.get("timestamp") if orderbook else None),
                    "game_id": str(payload.get("game_id") or "").zfill(10),
                    "market_id": payload.get("market_id"),
                    "side": payload.get("side"),
                    "outcome_id": payload.get("outcome_id"),
                    "price": price,
                    "bid": bid,
                    "ask": ask,
                    "spread_cents": spread,
                    "bid_size": _safe_float(orderbook.get("bid_size")),
                    "ask_size": _safe_float(orderbook.get("ask_size")),
                    "tick_size": _safe_float(orderbook.get("tick_size")),
                    "price_band": _price_band(price),
                }
            )
    ticks = pd.DataFrame(rows)
    if ticks.empty:
        return ticks, pd.DataFrame()
    ticks["tick_at"] = pd.to_datetime(ticks["tick_at"], errors="coerce", utc=True)
    ticks = ticks.sort_values(["game_id", "outcome_id", "tick_at"], kind="mergesort").reset_index(drop=True)
    ticks["cadence_seconds"] = ticks.groupby(["game_id", "outcome_id"])["tick_at"].diff().dt.total_seconds()
    summary = (
        ticks.groupby("price_band", dropna=False)
        .agg(
            tick_count=("price", "size"),
            game_count=("game_id", "nunique"),
            median_price=("price", "median"),
            median_spread_cents=("spread_cents", "median"),
            p90_spread_cents=("spread_cents", lambda values: values.dropna().quantile(0.90) if not values.dropna().empty else None),
            median_tick_size=("tick_size", "median"),
            median_bid_size=("bid_size", "median"),
            median_ask_size=("ask_size", "median"),
            median_cadence_seconds=("cadence_seconds", "median"),
            p90_cadence_seconds=("cadence_seconds", lambda values: values.dropna().quantile(0.90) if not values.dropna().empty else None),
        )
        .reset_index()
        .sort_values("price_band", kind="mergesort")
    )
    return ticks, summary


def _spread_lookup_from_profile(tick_summary: pd.DataFrame) -> dict[str, float]:
    lookup: dict[str, float] = {}
    if tick_summary.empty:
        return lookup
    for row in tick_summary.to_dict(orient="records"):
        value = _safe_float(row.get("median_spread_cents"))
        if value is not None and value > 0:
            lookup[str(row.get("price_band"))] = value
    return lookup


def _resolve_spread_cents(price: float | None, spread_lookup: dict[str, float], global_spread: float) -> float:
    if price is None or pd.isna(price):
        return global_spread
    return max(0.1, float(spread_lookup.get(_price_band(float(price)), global_spread)))


def _resolve_price_after_latency(group: pd.DataFrame, timestamp: pd.Timestamp | None, latency_seconds: float, fallback_price: float | None) -> float | None:
    if timestamp is None or group.empty or "event_at" not in group.columns:
        return fallback_price
    event_at = group["event_at"]
    target = timestamp + pd.Timedelta(seconds=float(latency_seconds))
    index = event_at.searchsorted(target, side="left")
    if index >= len(group):
        index = len(group) - 1
    price = _safe_float(group.iloc[int(index)].get("team_price"))
    return price if price is not None else fallback_price


def _build_state_groups(state_df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    if state_df.empty:
        return {}
    return {
        (str(game_id).zfill(10), str(team_side)): group.sort_values(["event_at", "state_index"], kind="mergesort").reset_index(drop=True)
        for (game_id, team_side), group in state_df.groupby(["game_id", "team_side"], dropna=False)
    }


def _apply_clob_proxy_to_trades(
    frame: pd.DataFrame,
    *,
    state_df: pd.DataFrame,
    state_groups: dict[tuple[str, str], pd.DataFrame] | None = None,
    latency_seconds: float,
    spread_lookup: dict[str, float],
    global_spread_cents: float,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    work["game_id"] = work["game_id"].astype(str).str.zfill(10)
    for column in ("entry_at", "exit_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column], errors="coerce", utc=True)
    grouped_states = state_groups if state_groups is not None else _build_state_groups(state_df)
    adjusted_rows: list[dict[str, Any]] = []
    for row in work.to_dict(orient="records"):
        key = (str(row.get("game_id")).zfill(10), str(row.get("team_side")))
        group = grouped_states.get(key, pd.DataFrame())
        entry_mid = _resolve_price_after_latency(
            group,
            _safe_timestamp(row.get("entry_at")),
            latency_seconds,
            _safe_float(row.get("entry_price")),
        )
        exit_mid = _resolve_price_after_latency(
            group,
            _safe_timestamp(row.get("exit_at")),
            latency_seconds,
            _safe_float(row.get("exit_price")),
        )
        entry_spread_cents = _resolve_spread_cents(entry_mid, spread_lookup, global_spread_cents)
        exit_spread_cents = _resolve_spread_cents(exit_mid, spread_lookup, global_spread_cents)
        entry_exec = min(0.999, (entry_mid or 0.0) + (entry_spread_cents / 200.0))
        exit_exec = max(0.0, (exit_mid or 0.0) - (exit_spread_cents / 200.0))
        clob_return = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
        row.update(
            {
                "clob_latency_seconds": float(latency_seconds),
                "clob_entry_mid_price": entry_mid,
                "clob_exit_mid_price": exit_mid,
                "clob_entry_spread_cents": entry_spread_cents,
                "clob_exit_spread_cents": exit_spread_cents,
                "entry_exec_price": entry_exec,
                "exit_exec_price": exit_exec,
                "gross_return_with_slippage": clob_return,
                "clob_proxy_return": clob_return,
            }
        )
        adjusted_rows.append(row)
    return pd.DataFrame(adjusted_rows)


def _load_subject_names(artifact_roots: list[Path]) -> list[str]:
    names: set[str] = set()
    ignored = {
        "attempt_trace",
        "blocker_summary",
        "candidate_lifecycle",
        "candidate_ranking",
        "divergence_summary",
        "game_gap",
        "game_manifest",
        "live_summary",
        "live_slate_expectation",
        "portfolio_summary",
        "promotion_table",
        "quarter_summary",
        "run",
        "signal_summary",
        "slate_expectation",
        "subject_summary",
        "window_summary",
        "subject_summary",
    }
    for root in artifact_roots:
        found_summary = False
        for summary_name in ("replay_subject_summary.csv", "standard_subject_summary.csv"):
            summary = _read_csv(root / summary_name)
            if not summary.empty and "subject_name" in summary.columns:
                names.update(str(value) for value in summary["subject_name"].dropna().tolist())
                found_summary = True
        if found_summary:
            continue
        for path in list(root.glob("standard_*.csv")) + list(root.glob("replay_*.csv")):
            name = path.stem.removeprefix("standard_").removeprefix("replay_")
            if name in ignored:
                continue
            names.add(name)
    return sorted(names)


def _load_trade_frame(artifact_roots: list[Path], subject_name: str, mode: str) -> pd.DataFrame:
    stem = _subject_stem(subject_name)
    frame = pd.DataFrame()
    for root in artifact_roots:
        candidate = _read_csv(root / f"{mode}_{stem}.csv")
        if not candidate.empty:
            frame = candidate
    if frame.empty:
        return frame
    required_columns = {"game_id", "team_side", "entry_at", "exit_at", "entry_price", "exit_price", "gross_return_with_slippage"}
    if not required_columns.issubset(frame.columns):
        return pd.DataFrame()
    work = frame.copy()
    work["game_id"] = work["game_id"].astype(str).str.zfill(10)
    for column in ("entry_at", "exit_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column], errors="coerce", utc=True)
    return work


def _artifact_roots(shared_root: Path, season: str, artifact_names: list[str]) -> list[Path]:
    roots: list[Path] = []
    for name in artifact_names:
        path = shared_root / "artifacts" / "replay-engine-hf" / season / str(name)
        if path.exists():
            roots.append(path)
    return roots


def _portfolio_scope(subject_name: str) -> str:
    return PORTFOLIO_SCOPE_ROUTED if "::" in subject_name else PORTFOLIO_SCOPE_SINGLE_FAMILY


def _simulate_portfolio(
    frame: pd.DataFrame,
    *,
    subject_name: str,
    sample_name: str,
    initial_bankroll: float,
    position_fraction: float,
    min_order_dollars: float,
    min_shares: float,
    max_concurrent_positions: int,
) -> dict[str, Any]:
    summary, _ = simulate_trade_portfolio(
        frame,
        sample_name=sample_name,
        strategy_family=subject_name,
        portfolio_scope=_portfolio_scope(subject_name),
        strategy_family_members=(subject_name,),
        initial_bankroll=initial_bankroll,
        position_size_fraction=position_fraction,
        game_limit=None,
        min_order_dollars=min_order_dollars,
        min_shares=min_shares,
        max_concurrent_positions=max_concurrent_positions,
        concurrency_mode="shared_cash_equal_split",
        sizing_mode="static",
        target_exposure_fraction=0.80,
        random_slippage_max_cents=0,
        random_slippage_seed=1107,
    )
    return summary


def _run_full_phase_simulations(
    *,
    shared_root: Path,
    season: str,
    state_panels: dict[str, pd.DataFrame],
    spread_lookup: dict[str, float],
    global_spread_cents: float,
    regular_artifact: str,
    postseason_artifact: str,
    regular_supplements: list[str],
    postseason_supplements: list[str],
    latencies_seconds: tuple[float, ...],
    position_fractions: tuple[float, ...],
    initial_bankroll: float,
    min_order_dollars: float,
    min_shares: float,
    max_concurrent_positions: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    phase_roots = {
        "regular_season": _artifact_roots(shared_root, season, [regular_artifact, *regular_supplements]),
        "postseason": _artifact_roots(shared_root, season, [postseason_artifact, *postseason_supplements]),
    }
    rows: list[dict[str, Any]] = []
    sizing_rows: list[dict[str, Any]] = []
    for phase, roots in phase_roots.items():
        if not roots:
            continue
        subject_names = _load_subject_names(roots)
        state_df = state_panels.get(phase, pd.DataFrame())
        state_groups = _build_state_groups(state_df)
        for subject_name in subject_names:
            for mode in ("standard", "replay"):
                frame = _load_trade_frame(roots, subject_name, mode)
                if frame.empty:
                    continue
                source_quality = "replay_poll" if mode == "replay" else "standard_state"
                for fraction in position_fractions:
                    summary = _simulate_portfolio(
                        frame,
                        subject_name=subject_name,
                        sample_name=f"{phase}_{mode}_{fraction}",
                        initial_bankroll=initial_bankroll,
                        position_fraction=fraction,
                        min_order_dollars=min_order_dollars,
                        min_shares=min_shares,
                        max_concurrent_positions=max_concurrent_positions,
                    )
                    sizing_rows.append(
                        {
                            "phase": phase,
                            "subject_name": subject_name,
                            "subject_type": "controller" if "::" in subject_name else "family",
                            "execution_model": source_quality,
                            "position_fraction": fraction,
                            "latency_seconds": None,
                            "ending_bankroll": summary.get("ending_bankroll"),
                            "total_pnl_amount": summary.get("total_pnl_amount"),
                            "compounded_return": summary.get("compounded_return"),
                            "max_drawdown_pct": summary.get("max_drawdown_pct"),
                            "executed_trade_count": summary.get("executed_trade_count"),
                            "skipped_min_order_count": summary.get("skipped_min_order_count"),
                        }
                    )
                base_fraction = 0.10
                summary = _simulate_portfolio(
                    frame,
                    subject_name=subject_name,
                    sample_name=f"{phase}_{mode}_base",
                    initial_bankroll=initial_bankroll,
                    position_fraction=base_fraction,
                    min_order_dollars=min_order_dollars,
                    min_shares=min_shares,
                    max_concurrent_positions=max_concurrent_positions,
                )
                rows.append(
                    {
                        "phase": phase,
                        "subject_name": subject_name,
                        "subject_type": "controller" if "::" in subject_name else "family",
                        "execution_model": source_quality,
                        "position_fraction": base_fraction,
                        "latency_seconds": None,
                        "signal_trade_count": int(len(frame)),
                        "ending_bankroll": summary.get("ending_bankroll"),
                        "total_pnl_amount": summary.get("total_pnl_amount"),
                        "compounded_return": summary.get("compounded_return"),
                        "max_drawdown_pct": summary.get("max_drawdown_pct"),
                        "executed_trade_count": summary.get("executed_trade_count"),
                    }
                )
                if mode != "standard" or state_df.empty:
                    continue
                for latency in latencies_seconds:
                    adjusted = _apply_clob_proxy_to_trades(
                        frame,
                        state_df=state_df,
                        state_groups=state_groups,
                        latency_seconds=latency,
                        spread_lookup=spread_lookup,
                        global_spread_cents=global_spread_cents,
                    )
                    for fraction in position_fractions:
                        adjusted_summary = _simulate_portfolio(
                            adjusted,
                            subject_name=subject_name,
                            sample_name=f"{phase}_clob_{latency}_{fraction}",
                            initial_bankroll=initial_bankroll,
                            position_fraction=fraction,
                            min_order_dollars=min_order_dollars,
                            min_shares=min_shares,
                            max_concurrent_positions=max_concurrent_positions,
                        )
                        sizing_rows.append(
                            {
                                "phase": phase,
                                "subject_name": subject_name,
                                "subject_type": "controller" if "::" in subject_name else "family",
                                "execution_model": "clob_proxy",
                                "position_fraction": fraction,
                                "latency_seconds": latency,
                                "ending_bankroll": adjusted_summary.get("ending_bankroll"),
                                "total_pnl_amount": adjusted_summary.get("total_pnl_amount"),
                                "compounded_return": adjusted_summary.get("compounded_return"),
                                "max_drawdown_pct": adjusted_summary.get("max_drawdown_pct"),
                                "executed_trade_count": adjusted_summary.get("executed_trade_count"),
                                "skipped_min_order_count": adjusted_summary.get("skipped_min_order_count"),
                            }
                        )
                    if latency in {0.0, 3.0, 10.0}:
                        adjusted_summary = _simulate_portfolio(
                            adjusted,
                            subject_name=subject_name,
                            sample_name=f"{phase}_clob_{latency}_base",
                            initial_bankroll=initial_bankroll,
                            position_fraction=base_fraction,
                            min_order_dollars=min_order_dollars,
                            min_shares=min_shares,
                            max_concurrent_positions=max_concurrent_positions,
                        )
                        rows.append(
                            {
                                "phase": phase,
                                "subject_name": subject_name,
                                "subject_type": "controller" if "::" in subject_name else "family",
                                "execution_model": "clob_proxy",
                                "position_fraction": base_fraction,
                                "latency_seconds": latency,
                                "signal_trade_count": int(len(adjusted)),
                                "ending_bankroll": adjusted_summary.get("ending_bankroll"),
                                "total_pnl_amount": adjusted_summary.get("total_pnl_amount"),
                                "compounded_return": adjusted_summary.get("compounded_return"),
                                "max_drawdown_pct": adjusted_summary.get("max_drawdown_pct"),
                                "executed_trade_count": adjusted_summary.get("executed_trade_count"),
                            }
                        )
    full_df = pd.DataFrame(rows)
    sizing_df = pd.DataFrame(sizing_rows)
    return full_df, sizing_df


def _run_random_sample_simulations(
    *,
    shared_root: Path,
    season: str,
    state_panels: dict[str, pd.DataFrame],
    spread_lookup: dict[str, float],
    global_spread_cents: float,
    regular_artifact: str,
    postseason_artifact: str,
    regular_supplements: list[str],
    postseason_supplements: list[str],
    sample_sizes: tuple[int, ...],
    seeds: tuple[int, ...],
    initial_bankroll: float,
    position_fraction: float,
    min_order_dollars: float,
    min_shares: float,
    max_concurrent_positions: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    phase_roots = {
        "regular_season": _artifact_roots(shared_root, season, [regular_artifact, *regular_supplements]),
        "postseason": _artifact_roots(shared_root, season, [postseason_artifact, *postseason_supplements]),
    }
    rows: list[dict[str, Any]] = []
    for phase, roots in phase_roots.items():
        state_df = state_panels.get(phase, pd.DataFrame())
        if not roots or state_df.empty:
            continue
        all_game_ids = sorted(state_df["game_id"].astype(str).str.zfill(10).dropna().unique().tolist())
        state_groups = _build_state_groups(state_df)
        subject_names = _load_subject_names(roots)
        for subject_name in subject_names:
            model_frames: list[tuple[str, float | None, pd.DataFrame]] = []
            for mode in ("standard", "replay"):
                frame = _load_trade_frame(roots, subject_name, mode)
                if frame.empty:
                    continue
                model_frames.append(("replay_poll" if mode == "replay" else "standard_state", None, frame))
                if mode == "standard":
                    adjusted = _apply_clob_proxy_to_trades(
                        frame,
                        state_df=state_df,
                        state_groups=state_groups,
                        latency_seconds=3.0,
                        spread_lookup=spread_lookup,
                        global_spread_cents=global_spread_cents,
                    )
                    model_frames.append(("clob_proxy", 3.0, adjusted))
            for execution_model, latency_seconds, frame in model_frames:
                for sample_size in sample_sizes:
                    resolved_size = min(int(sample_size), len(all_game_ids))
                    if resolved_size <= 0:
                        continue
                    for seed in seeds:
                        sample_games = set(pd.Series(all_game_ids).sample(n=resolved_size, random_state=int(seed)).tolist())
                        sample = frame[frame["game_id"].astype(str).str.zfill(10).isin(sample_games)].copy()
                        summary = _simulate_portfolio(
                            sample,
                            subject_name=subject_name,
                            sample_name=f"{phase}_{execution_model}_{resolved_size}_{seed}",
                            initial_bankroll=initial_bankroll,
                            position_fraction=position_fraction,
                            min_order_dollars=min_order_dollars,
                            min_shares=min_shares,
                            max_concurrent_positions=max_concurrent_positions,
                        )
                        rows.append(
                            {
                                "phase": phase,
                                "subject_name": subject_name,
                                "subject_type": "controller" if "::" in subject_name else "family",
                                "execution_model": execution_model,
                                "latency_seconds": latency_seconds,
                                "sample_size": resolved_size,
                                "seed": int(seed),
                                "signal_trade_count": int(len(sample)),
                                "ending_bankroll": summary.get("ending_bankroll"),
                                "total_pnl_amount": summary.get("total_pnl_amount"),
                                "compounded_return": summary.get("compounded_return"),
                                "max_drawdown_pct": summary.get("max_drawdown_pct"),
                                "executed_trade_count": summary.get("executed_trade_count"),
                                "skipped_min_order_count": summary.get("skipped_min_order_count"),
                            }
                        )
    sample_df = pd.DataFrame(rows)
    if sample_df.empty:
        return sample_df, pd.DataFrame()
    work = sample_df.copy()
    work["ending_bankroll"] = pd.to_numeric(work["ending_bankroll"], errors="coerce")
    work["max_drawdown_pct"] = pd.to_numeric(work["max_drawdown_pct"], errors="coerce")
    work["executed_trade_count"] = pd.to_numeric(work["executed_trade_count"], errors="coerce").fillna(0.0)
    aggregate = (
        work.groupby(["phase", "subject_name", "subject_type", "execution_model", "latency_seconds", "sample_size"], dropna=False)
        .agg(
            sample_count=("seed", "count"),
            mean_ending_bankroll=("ending_bankroll", "mean"),
            median_ending_bankroll=("ending_bankroll", "median"),
            min_ending_bankroll=("ending_bankroll", "min"),
            p10_ending_bankroll=("ending_bankroll", lambda values: values.dropna().quantile(0.10) if not values.dropna().empty else None),
            p90_ending_bankroll=("ending_bankroll", lambda values: values.dropna().quantile(0.90) if not values.dropna().empty else None),
            positive_sample_rate=("ending_bankroll", lambda values: float((values.dropna() > initial_bankroll).mean()) if not values.dropna().empty else None),
            mean_max_drawdown_pct=("max_drawdown_pct", "mean"),
            mean_executed_trade_count=("executed_trade_count", "mean"),
        )
        .reset_index()
        .sort_values(
            ["phase", "execution_model", "sample_size", "median_ending_bankroll", "mean_ending_bankroll"],
            ascending=[True, True, True, False, False],
            kind="mergesort",
        )
    )
    return sample_df, aggregate


def _optimal_sizing(sizing_df: pd.DataFrame, *, initial_bankroll: float) -> pd.DataFrame:
    if sizing_df.empty:
        return pd.DataFrame()
    work = sizing_df.copy()
    work["ending_bankroll"] = pd.to_numeric(work["ending_bankroll"], errors="coerce")
    work["max_drawdown_pct"] = pd.to_numeric(work["max_drawdown_pct"], errors="coerce").fillna(0.0)
    work["executed_trade_count"] = pd.to_numeric(work["executed_trade_count"], errors="coerce").fillna(0.0)
    work["risk_adjusted_score"] = (
        (work["ending_bankroll"] - initial_bankroll)
        - (initial_bankroll * work["max_drawdown_pct"] * 0.75)
        + (work["executed_trade_count"].clip(upper=20) * 0.01)
    )
    idx = work.groupby(["phase", "subject_name", "execution_model", "latency_seconds"], dropna=False)["risk_adjusted_score"].idxmax()
    best = work.loc[idx].sort_values(["phase", "execution_model", "risk_adjusted_score"], ascending=[True, True, False], kind="mergesort")
    return best.reset_index(drop=True)


def _score_gap_bucket(value: Any) -> str:
    resolved = _safe_float(value)
    if resolved is None:
        return "unknown"
    gap = abs(resolved)
    if gap <= 3:
        return "gap_0_3"
    if gap <= 6:
        return "gap_4_6"
    if gap <= 10:
        return "gap_7_10"
    if gap <= 15:
        return "gap_11_15"
    return "gap_16_plus"


def _band_rows_for_group(group: pd.DataFrame, *, phase: str, threshold: float) -> list[dict[str, Any]]:
    prices = pd.to_numeric(group["team_price"], errors="coerce")
    opening = _safe_float(group.iloc[0].get("opening_price"))
    if opening is not None and opening > 0.45 and prices.min(skipna=True) > 0.35:
        return []
    touch_positions = prices[prices <= threshold].index
    if len(touch_positions) == 0:
        return []
    first_label = touch_positions[0]
    first_pos = int(group.index.get_loc(first_label))
    first_row = group.iloc[first_pos]
    future = group.iloc[first_pos:]
    future_prices = pd.to_numeric(future["team_price"], errors="coerce")
    first_price = _safe_float(first_row.get("team_price")) or threshold
    future_max = _safe_float(future_prices.max()) or first_price
    future_min = _safe_float(future_prices.min()) or first_price
    rows: list[dict[str, Any]] = []
    for gain in (0.005, 0.01, 0.02, 0.05, 0.10):
        target = min(0.99, first_price + gain)
        hits = future[future_prices >= target]
        seconds_to_hit = None
        if not hits.empty:
            first_hit = hits.iloc[0]
            first_time = _safe_timestamp(first_row.get("event_at"))
            hit_time = _safe_timestamp(first_hit.get("event_at"))
            if first_time is not None and hit_time is not None:
                seconds_to_hit = max(0.0, (hit_time - first_time).total_seconds())
        rows.append(
            {
                "phase": phase,
                "game_id": str(first_row.get("game_id")).zfill(10),
                "team_side": first_row.get("team_side"),
                "team_slug": first_row.get("team_slug"),
                "threshold_price": threshold,
                "first_touch_price": first_price,
                "first_touch_period": first_row.get("period_label"),
                "first_touch_score_diff": _safe_float(first_row.get("score_diff")),
                "first_touch_score_gap_bucket": _score_gap_bucket(first_row.get("score_diff")),
                "future_max_price": future_max,
                "future_min_price": future_min,
                "max_rebound": future_max - first_price,
                "max_drawdown_after_touch": first_price - future_min,
                "rebound_gain": gain,
                "rebound_target_price": target,
                "rebounded_flag": bool(future_max >= target),
                "seconds_to_rebound": seconds_to_hit,
                "reached_20c_after_touch_flag": bool(future_max >= 0.20),
                "final_winner_flag": bool(first_row.get("final_winner_flag")) if "final_winner_flag" in first_row else None,
            }
        )
    return rows


def _analyze_resistance_bands(state_panels: dict[str, pd.DataFrame], thresholds: tuple[float, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for phase in ("regular_season", "play_in", "playoffs", "postseason"):
        frame = state_panels.get(phase, pd.DataFrame())
        if frame.empty:
            continue
        required = {"game_id", "team_side", "team_price", "opening_price"}
        if not required.issubset(frame.columns):
            continue
        work = frame.dropna(subset=["team_price"]).copy()
        for (_, _), group in work.groupby(["game_id", "team_side"], dropna=False):
            group = group.sort_values(["event_at", "state_index"], kind="mergesort").reset_index(drop=True)
            for threshold in thresholds:
                rows.extend(_band_rows_for_group(group, phase=phase, threshold=threshold))
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, pd.DataFrame()
    summary = (
        detail.groupby(["phase", "threshold_price", "rebound_gain", "first_touch_score_gap_bucket"], dropna=False)
        .agg(
            touch_count=("game_id", "nunique"),
            rebound_rate=("rebounded_flag", "mean"),
            reached_20c_rate=("reached_20c_after_touch_flag", "mean"),
            median_max_rebound=("max_rebound", "median"),
            p75_max_rebound=("max_rebound", lambda values: values.dropna().quantile(0.75) if not values.dropna().empty else None),
            median_seconds_to_rebound=("seconds_to_rebound", "median"),
            winner_rate=("final_winner_flag", "mean"),
        )
        .reset_index()
        .sort_values(["phase", "threshold_price", "rebound_gain", "first_touch_score_gap_bucket"], kind="mergesort")
    )
    return detail, summary


def _rank_resistance_band_expected_value(band_detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if band_detail.empty:
        return pd.DataFrame(), pd.DataFrame()
    work = band_detail.copy()
    for column in (
        "threshold_price",
        "first_touch_price",
        "rebound_gain",
        "max_rebound",
        "max_drawdown_after_touch",
    ):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work["rebounded_flag"] = work["rebounded_flag"].astype(bool)
    work["safe_entry_price"] = work["first_touch_price"].clip(lower=0.005)
    work["target_return_pct"] = work["rebound_gain"] / work["safe_entry_price"]
    # This is a target/stop proxy, not a chronological fill simulator. It ranks price
    # bands for research before the tick-level order replay exists.
    work["stop_loss_abs"] = work["rebound_gain"].clip(upper=0.04)
    work["stop_loss_pct"] = work["stop_loss_abs"] / work["safe_entry_price"]
    work["target_stop_ev_pct"] = (
        work["rebounded_flag"].astype(float) * work["target_return_pct"]
        - (1.0 - work["rebounded_flag"].astype(float)) * work["stop_loss_pct"]
    )
    work["probability_weighted_upside_pct"] = work["rebounded_flag"].astype(float) * work["target_return_pct"]
    group_columns = ["phase", "threshold_price", "rebound_gain", "first_touch_score_gap_bucket"]
    ranked = (
        work.groupby(group_columns, dropna=False)
        .agg(
            event_count=("game_id", "count"),
            game_count=("game_id", "nunique"),
            avg_entry_price=("first_touch_price", "mean"),
            rebound_rate=("rebounded_flag", "mean"),
            avg_target_return_pct=("target_return_pct", "mean"),
            probability_weighted_upside_pct=("probability_weighted_upside_pct", "mean"),
            target_stop_ev_pct=("target_stop_ev_pct", "mean"),
            median_max_rebound=("max_rebound", "median"),
            median_drawdown_after_touch=("max_drawdown_after_touch", "median"),
            median_seconds_to_rebound=("seconds_to_rebound", "median"),
            winner_rate=("final_winner_flag", "mean"),
        )
        .reset_index()
    )
    ranked = ranked[ranked["event_count"] >= 10].copy()
    ranked = ranked.sort_values(
        ["phase", "target_stop_ev_pct", "probability_weighted_upside_pct", "event_count"],
        ascending=[True, False, False, False],
        kind="mergesort",
    )

    explain_rows: list[dict[str, Any]] = []
    for (phase, threshold, gain), group in work.groupby(["phase", "threshold_price", "rebound_gain"], dropna=False):
        if len(group) < 10:
            continue
        baseline = float(group["target_stop_ev_pct"].mean())
        for feature in ("first_touch_score_gap_bucket", "first_touch_period"):
            if feature not in group.columns:
                continue
            for value, feature_group in group.groupby(feature, dropna=False):
                if len(feature_group) < 5:
                    continue
                explain_rows.append(
                    {
                        "phase": phase,
                        "threshold_price": threshold,
                        "rebound_gain": gain,
                        "feature": feature,
                        "feature_value": value,
                        "event_count": int(len(feature_group)),
                        "baseline_ev_pct": baseline,
                        "feature_ev_pct": float(feature_group["target_stop_ev_pct"].mean()),
                        "ev_lift_pct": float(feature_group["target_stop_ev_pct"].mean() - baseline),
                        "feature_rebound_rate": float(feature_group["rebounded_flag"].mean()),
                        "baseline_rebound_rate": float(group["rebounded_flag"].mean()),
                    }
                )
    explain = pd.DataFrame(explain_rows)
    if not explain.empty:
        explain = explain.sort_values(
            ["phase", "ev_lift_pct", "event_count"],
            ascending=[True, False, False],
            kind="mergesort",
        )
    return ranked.reset_index(drop=True), explain.reset_index(drop=True)


def _render_report(
    *,
    output_dir: Path,
    tick_summary: pd.DataFrame,
    full_df: pd.DataFrame,
    optimal_df: pd.DataFrame,
    sample_aggregate_df: pd.DataFrame,
    band_summary: pd.DataFrame,
    band_ev_df: pd.DataFrame,
    band_explain_df: pd.DataFrame,
) -> str:
    lines = [
        "# Market Microstructure And Backbone Backtest",
        "",
        f"- Generated at `{datetime.now(timezone.utc).isoformat()}`.",
        f"- Output root: `{output_dir}`.",
        "- CLOB proxy uses observed live orderbook spread/cadence by price band where available, then applies buy-at-ask/sell-at-bid plus latency on historical state-panel prices.",
        "- This is closer to real CLOB mechanics than state-panel replay, but still cannot reconstruct historical hidden queue position or unobserved orderbook depth for old games.",
        "",
        "## Live Tick Profile",
        "",
        "| Price Band | Ticks | Games | Median Spread Cents | P90 Spread | Median Cadence Seconds |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in tick_summary.head(30).to_dict(orient="records") if not tick_summary.empty else []:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.get('price_band')}`",
                    str(int(row.get("tick_count") or 0)),
                    str(int(row.get("game_count") or 0)),
                    _fmt(row.get("median_spread_cents")),
                    _fmt(row.get("p90_spread_cents")),
                    _fmt(row.get("median_cadence_seconds")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Full Simulation Leaders",
            "",
            "| Phase | Model | Subject | Latency | End Bankroll | Drawdown | Trades |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    if not full_df.empty:
        leaders = full_df.copy()
        leaders["ending_bankroll"] = pd.to_numeric(leaders["ending_bankroll"], errors="coerce")
        leaders = leaders.sort_values(["phase", "execution_model", "ending_bankroll"], ascending=[True, True, False], kind="mergesort")
        for row in leaders.groupby(["phase", "execution_model"], dropna=False).head(8).to_dict(orient="records"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.get('phase')}`",
                        f"`{row.get('execution_model')}`",
                        f"`{row.get('subject_name')}`",
                        _fmt(row.get("latency_seconds"), 1),
                        _fmt(row.get("ending_bankroll")),
                        _fmt(row.get("max_drawdown_pct")),
                        str(int(row.get("executed_trade_count") or 0)),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Best Sizing By Risk-Adjusted Score",
            "",
            "| Phase | Model | Subject | Latency | Fraction | End Bankroll | Drawdown | Trades |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    if not optimal_df.empty:
        for row in optimal_df.groupby(["phase", "execution_model"], dropna=False).head(10).to_dict(orient="records"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.get('phase')}`",
                        f"`{row.get('execution_model')}`",
                        f"`{row.get('subject_name')}`",
                        _fmt(row.get("latency_seconds"), 1),
                        _fmt(row.get("position_fraction")),
                        _fmt(row.get("ending_bankroll")),
                        _fmt(row.get("max_drawdown_pct")),
                        str(int(row.get("executed_trade_count") or 0)),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Price-Level EV Leaders",
            "",
            "EV uses a target/stop proxy: if the target rebound is reached, return is `gain / entry_price`; otherwise loss is `min(gain, 4c) / entry_price`. It ranks candidate resistance bands before true tick-level order replay exists.",
            "",
            "| Phase | Threshold | Gain | Score Gap | Events | Rebound Rate | Target Return | EV | Median Seconds |",
            "|---|---:|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    if not band_ev_df.empty:
        leaders = band_ev_df.copy()
        leaders["target_stop_ev_pct"] = pd.to_numeric(leaders["target_stop_ev_pct"], errors="coerce")
        leaders = leaders.sort_values(["phase", "target_stop_ev_pct"], ascending=[True, False], kind="mergesort")
        for row in leaders.groupby("phase", dropna=False).head(12).to_dict(orient="records"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.get('phase')}`",
                        _fmt(row.get("threshold_price"), 3),
                        _fmt(row.get("rebound_gain"), 3),
                        f"`{row.get('first_touch_score_gap_bucket')}`",
                        str(int(row.get("event_count") or 0)),
                        _fmt(row.get("rebound_rate"), 3),
                        _fmt(row.get("avg_target_return_pct"), 3),
                        _fmt(row.get("target_stop_ev_pct"), 3),
                        _fmt(row.get("median_seconds_to_rebound"), 1),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Statistical Explainers For Band Edge",
            "",
            "| Phase | Threshold | Gain | Feature | Value | Events | EV Lift | Rebound Rate |",
            "|---|---:|---:|---|---|---:|---:|---:|",
        ]
    )
    if not band_explain_df.empty:
        for row in band_explain_df.groupby("phase", dropna=False).head(12).to_dict(orient="records"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.get('phase')}`",
                        _fmt(row.get("threshold_price"), 3),
                        _fmt(row.get("rebound_gain"), 3),
                        f"`{row.get('feature')}`",
                        f"`{row.get('feature_value')}`",
                        str(int(row.get("event_count") or 0)),
                        _fmt(row.get("ev_lift_pct"), 3),
                        _fmt(row.get("feature_rebound_rate"), 3),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Random 10/50/100 Game Sample Leaders",
            "",
            "| Phase | Model | Sample | Subject | Median | P10 | Positive Rate | Mean Trades |",
            "|---|---|---:|---|---:|---:|---:|---:|",
        ]
    )
    if not sample_aggregate_df.empty:
        leaders = sample_aggregate_df.copy()
        leaders["median_ending_bankroll"] = pd.to_numeric(leaders["median_ending_bankroll"], errors="coerce")
        leaders = leaders.sort_values(
            ["phase", "execution_model", "sample_size", "median_ending_bankroll"],
            ascending=[True, True, True, False],
            kind="mergesort",
        )
        for row in leaders.groupby(["phase", "execution_model", "sample_size"], dropna=False).head(5).to_dict(orient="records"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.get('phase')}`",
                        f"`{row.get('execution_model')}`",
                        str(int(row.get("sample_size") or 0)),
                        f"`{row.get('subject_name')}`",
                        _fmt(row.get("median_ending_bankroll")),
                        _fmt(row.get("p10_ending_bankroll")),
                        _fmt(row.get("positive_sample_rate"), 2),
                        _fmt(row.get("mean_executed_trade_count"), 2),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Resistance Band Highlights",
            "",
            "| Phase | Threshold | Gain | Score Gap | Touches | Rebound Rate | Reached 20c | Median Rebound | Median Seconds |",
            "|---|---:|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    if not band_summary.empty:
        focus = band_summary[
            (band_summary["threshold_price"].isin([0.005, 0.01, 0.02, 0.05, 0.10, 0.15, 0.19, 0.20]))
            & (band_summary["rebound_gain"].isin([0.005, 0.01, 0.02, 0.05]))
        ].copy()
        focus = focus.sort_values(["phase", "threshold_price", "rebound_gain", "touch_count"], ascending=[True, True, True, False], kind="mergesort")
        for row in focus.head(80).to_dict(orient="records"):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.get('phase')}`",
                        _fmt(row.get("threshold_price"), 3),
                        _fmt(row.get("rebound_gain"), 3),
                        f"`{row.get('first_touch_score_gap_bucket')}`",
                        str(int(row.get("touch_count") or 0)),
                        _fmt(row.get("rebound_rate"), 3),
                        _fmt(row.get("reached_20c_rate"), 3),
                        _fmt(row.get("median_max_rebound"), 3),
                        _fmt(row.get("median_seconds_to_rebound"), 1),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- Full simulation: `{output_dir / 'full_phase_strategy_simulation.csv'}`",
            f"- Sizing sweep: `{output_dir / 'position_sizing_sweep.csv'}`",
            f"- Optimal sizing: `{output_dir / 'optimal_position_sizing.csv'}`",
            f"- Random sample aggregate: `{output_dir / 'random_sample_aggregate.csv'}`",
            f"- Resistance band EV ranking: `{output_dir / 'resistance_band_ev_ranking.csv'}`",
            f"- Resistance band explainers: `{output_dir / 'resistance_band_explainers.csv'}`",
            f"- Band summary: `{output_dir / 'resistance_band_summary.csv'}`",
            f"- Live tick profile: `{output_dir / 'live_tick_profile_by_price_band.csv'}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt(value: Any, digits: int = 3) -> str:
    resolved = _safe_float(value)
    if resolved is None or math.isnan(resolved):
        return ""
    return f"{resolved:.{digits}f}"


def main() -> None:
    args = _parse_args()
    shared_root = Path(args.shared_root).expanduser().resolve()
    archive_root = Path(args.archive_root).expanduser().resolve()
    tracks_root = Path(args.tracks_root).expanduser().resolve()
    output_dir = shared_root / "artifacts" / "replay-engine-hf" / args.season / args.output_name
    report_dir = shared_root / "reports" / "replay-engine-hf"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    latencies = tuple(args.latency_seconds or DEFAULT_LATENCIES_SECONDS)
    fractions = tuple(args.position_fraction or DEFAULT_POSITION_FRACTIONS)
    sample_sizes = tuple(args.sample_size or DEFAULT_SAMPLE_SIZES)
    seeds = tuple(args.seed or DEFAULT_SEEDS)
    state_panels = _load_all_state_panels(
        archive_root=archive_root,
        season=args.season,
        analysis_version=args.analysis_version,
    )
    live_ticks, tick_summary = _load_live_tick_profile(tracks_root)
    spread_values = pd.to_numeric(live_ticks.get("spread_cents"), errors="coerce").dropna() if not live_ticks.empty else pd.Series(dtype=float)
    global_spread_cents = float(spread_values.median()) if not spread_values.empty else 1.0
    spread_lookup = _spread_lookup_from_profile(tick_summary)
    full_df, sizing_df = _run_full_phase_simulations(
        shared_root=shared_root,
        season=args.season,
        state_panels=state_panels,
        spread_lookup=spread_lookup,
        global_spread_cents=global_spread_cents,
        regular_artifact=args.regular_artifact,
        postseason_artifact=args.postseason_artifact,
        regular_supplements=list(args.regular_supplement_artifact or []),
        postseason_supplements=list(args.postseason_supplement_artifact or []),
        latencies_seconds=latencies,
        position_fractions=fractions,
        initial_bankroll=float(args.initial_bankroll),
        min_order_dollars=float(args.min_order_dollars),
        min_shares=float(args.min_shares),
        max_concurrent_positions=int(args.max_concurrent_positions),
    )
    optimal_df = _optimal_sizing(sizing_df, initial_bankroll=float(args.initial_bankroll))
    sample_df, sample_aggregate_df = _run_random_sample_simulations(
        shared_root=shared_root,
        season=args.season,
        state_panels=state_panels,
        spread_lookup=spread_lookup,
        global_spread_cents=global_spread_cents,
        regular_artifact=args.regular_artifact,
        postseason_artifact=args.postseason_artifact,
        regular_supplements=list(args.regular_supplement_artifact or []),
        postseason_supplements=list(args.postseason_supplement_artifact or []),
        sample_sizes=sample_sizes,
        seeds=seeds,
        initial_bankroll=float(args.initial_bankroll),
        position_fraction=0.10,
        min_order_dollars=float(args.min_order_dollars),
        min_shares=float(args.min_shares),
        max_concurrent_positions=int(args.max_concurrent_positions),
    )
    band_detail, band_summary = _analyze_resistance_bands(state_panels, DEFAULT_THRESHOLDS)
    band_ev_df, band_explain_df = _rank_resistance_band_expected_value(band_detail)

    live_ticks.to_csv(output_dir / "live_orderbook_tick_sample.csv", index=False)
    tick_summary.to_csv(output_dir / "live_tick_profile_by_price_band.csv", index=False)
    full_df.to_csv(output_dir / "full_phase_strategy_simulation.csv", index=False)
    sizing_df.to_csv(output_dir / "position_sizing_sweep.csv", index=False)
    optimal_df.to_csv(output_dir / "optimal_position_sizing.csv", index=False)
    sample_df.to_csv(output_dir / "random_sample_results.csv", index=False)
    sample_aggregate_df.to_csv(output_dir / "random_sample_aggregate.csv", index=False)
    band_detail.to_csv(output_dir / "resistance_band_detail.csv", index=False)
    band_summary.to_csv(output_dir / "resistance_band_summary.csv", index=False)
    band_ev_df.to_csv(output_dir / "resistance_band_ev_ranking.csv", index=False)
    band_explain_df.to_csv(output_dir / "resistance_band_explainers.csv", index=False)
    _write_json(
        output_dir / "run_summary.json",
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "season": args.season,
            "analysis_version": args.analysis_version,
            "output_dir": str(output_dir),
            "state_panel_rows": {phase: int(len(frame)) for phase, frame in state_panels.items()},
            "state_panel_games": {phase: int(frame["game_id"].nunique()) if not frame.empty else 0 for phase, frame in state_panels.items()},
            "live_tick_count": int(len(live_ticks)),
            "live_tick_game_count": int(live_ticks["game_id"].nunique()) if not live_ticks.empty else 0,
            "global_spread_cents": global_spread_cents,
            "latencies_seconds": list(latencies),
            "position_fractions": list(fractions),
            "sample_sizes": list(sample_sizes),
            "seeds": list(seeds),
            "portfolio": {
                "initial_bankroll": float(args.initial_bankroll),
                "min_order_dollars": float(args.min_order_dollars),
                "min_shares": float(args.min_shares),
                "max_concurrent_positions": int(args.max_concurrent_positions),
            },
        },
    )
    report = _render_report(
        output_dir=output_dir,
        tick_summary=tick_summary,
        full_df=full_df,
        optimal_df=optimal_df,
        sample_aggregate_df=sample_aggregate_df,
        band_summary=band_summary,
        band_ev_df=band_ev_df,
        band_explain_df=band_explain_df,
    )
    (report_dir / "market_microstructure_backbone.md").write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "report": str(report_dir / "market_microstructure_backbone.md"),
                "live_tick_count": int(len(live_ticks)),
                "full_rows": int(len(full_df)),
                "sizing_rows": int(len(sizing_df)),
                "sample_rows": int(len(sample_df)),
                "band_rows": int(len(band_summary)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
