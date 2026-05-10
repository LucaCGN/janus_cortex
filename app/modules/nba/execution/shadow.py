from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.backtests.registry import (
    REPLAY_HF_STRATEGY_GROUP,
    build_strategy_registry,
)


DEFAULT_SHADOW_FAMILIES = (
    "quarter_open_reprice",
    "micro_momentum_continuation",
    "inversion",
    "underdog_range_scalp",
    "favorite_floor_rebound",
    "underdog_liftoff",
    "panic_fade_fast",
    "lead_fragility",
    "halftime_gap_fill",
    "winner_definition",
    "q1_repricing",
    "q4_clutch",
)
LIVE_PROBE_FAMILIES = {"quarter_open_reprice", "micro_momentum_continuation", "underdog_range_scalp"}
SHADOW_SNAPSHOT_JSON_NAME = "shadow_snapshot_latest.json"
SHADOW_SNAPSHOT_CSV_NAME = "shadow_snapshot_latest.csv"
SHADOW_LIVE_COMPARISON_CSV_NAME = "shadow_live_comparison_latest.csv"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _latest_state_row(state_df: pd.DataFrame, *, game_id: str, team_side: str) -> dict[str, Any] | None:
    if state_df.empty:
        return None
    rows = state_df[
        (state_df["game_id"].astype(str) == str(game_id))
        & (state_df["team_side"].astype(str) == str(team_side))
    ]
    if rows.empty:
        return None
    return rows.sort_values("state_index", kind="mergesort").iloc[-1].to_dict()


def _signal_id(subject_name: str, game_id: Any, team_side: Any, entry_state_index: Any) -> str:
    try:
        entry_index = int(float(entry_state_index))
    except (TypeError, ValueError):
        entry_index = 0
    return f"{subject_name}|{str(game_id)}|{str(team_side or '')}|{entry_index}"


def _matchup_label(bundle: dict[str, Any]) -> str:
    game = bundle.get("game") or {}
    away = str(game.get("away_team_slug") or game.get("away_team_name") or "Away")
    home = str(game.get("home_team_slug") or game.get("home_team_name") or "Home")
    return f"{away} at {home}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(path, index=False)


def _promotion_bucket_for_family(family: str) -> str:
    if family in LIVE_PROBE_FAMILIES:
        return "live_probe"
    return "shadow_only"


def _live_action_is_active(card: dict[str, Any] | None) -> bool:
    if not card:
        return False
    action = str(card.get("selected_action") or "").strip().lower()
    state_label = str(card.get("state_label") or "").strip().lower()
    fill_state = str(card.get("fill_state") or "").strip().lower()
    return action in {"buy limit", "hold"} or state_label in {"entry queued", "open position"} or fill_state in {
        "working",
        "filled",
    }


def _comparison_bucket(*, shadow_row: dict[str, Any], controller_card: dict[str, Any] | None) -> str:
    shadow_action = str(shadow_row.get("shadow_action") or "").strip().lower()
    shadow_has_signal = bool(shadow_row.get("signal_id"))
    live_active = _live_action_is_active(controller_card)
    live_family = str((controller_card or {}).get("strategy_family") or "").strip()
    shadow_family = str(shadow_row.get("strategy_family") or "").strip()
    live_side = str((controller_card or {}).get("selected_team_side") or "").strip()
    shadow_side = str(shadow_row.get("team_side") or "").strip()
    side_matches = not live_side or not shadow_side or live_side == shadow_side

    if shadow_action == "would_enter" and live_active and live_family == shadow_family and side_matches:
        return "live_match"
    if shadow_action == "would_enter" and live_active and live_family == shadow_family:
        return "live_different_side"
    if shadow_action == "would_enter" and live_active:
        return "live_different_family"
    if shadow_action == "would_enter":
        return "missed_shadow_signal"
    if live_active and side_matches and not shadow_has_signal:
        return "live_only_action"
    if shadow_has_signal:
        return "blocked_shadow_signal"
    return "both_wait"


def _build_live_shadow_comparison(
    *,
    run_id: str,
    family_rows: list[dict[str, Any]],
    controller_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    card_by_game = {str(card.get("game_id") or ""): card for card in controller_cards}
    rows: list[dict[str, Any]] = []
    for shadow_row in family_rows:
        game_id = str(shadow_row.get("game_id") or "")
        card = card_by_game.get(game_id)
        bucket = _comparison_bucket(shadow_row=shadow_row, controller_card=card)
        rows.append(
            {
                "run_id": run_id,
                "game_id": game_id,
                "matchup": shadow_row.get("matchup") or (card or {}).get("matchup"),
                "team_side": shadow_row.get("team_side"),
                "strategy_family": shadow_row.get("strategy_family"),
                "promotion_bucket": shadow_row.get("promotion_bucket"),
                "comparison_bucket": bucket,
                "shadow_action": shadow_row.get("shadow_action"),
                "shadow_reason": shadow_row.get("shadow_reason"),
                "shadow_signal_id": shadow_row.get("signal_id"),
                "shadow_entry_price": shadow_row.get("signal_entry_price"),
                "shadow_signal_strength": shadow_row.get("signal_strength"),
                "shadow_opening_band": shadow_row.get("opening_band"),
                "shadow_period_label": shadow_row.get("period_label"),
                "shadow_context_bucket": shadow_row.get("context_bucket"),
                "shadow_signal_age_seconds": shadow_row.get("first_attempt_signal_age_seconds"),
                "shadow_quote_age_seconds": shadow_row.get("first_attempt_quote_age_seconds"),
                "shadow_spread_cents": shadow_row.get("spread_cents"),
                "coverage_status": shadow_row.get("coverage_status"),
                "live_controller_name": (card or {}).get("controller_name"),
                "live_strategy_family": (card or {}).get("strategy_family"),
                "live_selected_team_side": (card or {}).get("selected_team_side"),
                "live_selected_action": (card or {}).get("selected_action"),
                "live_state_label": (card or {}).get("state_label"),
                "live_note": (card or {}).get("note"),
                "live_fill_state": (card or {}).get("fill_state"),
                "live_open_order_count": (card or {}).get("open_order_count"),
                "live_open_position_count": (card or {}).get("open_position_count"),
                "live_best_bid": (card or {}).get("best_bid"),
                "live_best_ask": (card or {}).get("best_ask"),
                "live_stop_price": (card or {}).get("stop_price"),
                "live_realized_pnl": (card or {}).get("realized_pnl"),
            }
        )

    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket = str(row.get("comparison_bucket") or "unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    missed_bands: list[dict[str, Any]] = []
    missed_rows = [row for row in rows if row.get("comparison_bucket") == "missed_shadow_signal"]
    if missed_rows:
        missed_frame = pd.DataFrame(missed_rows)
        group_cols = ["strategy_family", "shadow_opening_band"]
        for keys, group in missed_frame.groupby(group_cols, dropna=False, sort=True):
            family, opening_band = keys if isinstance(keys, tuple) else (keys, None)
            entry_prices = pd.to_numeric(group["shadow_entry_price"], errors="coerce")
            missed_bands.append(
                {
                    "strategy_family": None if pd.isna(family) else str(family),
                    "opening_band": None if pd.isna(opening_band) else str(opening_band),
                    "missed_signal_count": int(len(group)),
                    "avg_shadow_entry_price": (
                        round(float(entry_prices.dropna().mean()), 6) if not entry_prices.dropna().empty else None
                    ),
                    "games": sorted({str(value) for value in group["game_id"].dropna().tolist()}),
                }
            )

    summary = {
        "row_count": len(rows),
        "bucket_counts": bucket_counts,
        "missed_shadow_signal_count": bucket_counts.get("missed_shadow_signal", 0),
        "live_match_count": bucket_counts.get("live_match", 0),
        "live_only_action_count": bucket_counts.get("live_only_action", 0),
        "blocked_shadow_signal_count": bucket_counts.get("blocked_shadow_signal", 0),
    }
    return {
        "schema_version": "live_shadow_comparison_v1",
        "summary": summary,
        "missed_opportunity_bands": missed_bands,
        "rows": to_jsonable(rows),
    }


def _build_family_shadow_rows(
    *,
    state_df: pd.DataFrame,
    bundles: dict[str, dict[str, Any]],
    diagnostics_by_game: dict[str, dict[str, Any]],
    game_ids: list[str],
    families: list[str],
    snapshot_at: datetime,
) -> list[dict[str, Any]]:
    registry = build_strategy_registry(strategy_group=REPLAY_HF_STRATEGY_GROUP)
    rows: list[dict[str, Any]] = []
    if state_df.empty:
        return rows

    selected_game_ids = [str(game_id) for game_id in game_ids]
    for family in families:
        definition = registry.get(family)
        if definition is None:
            continue
        trade_df = pd.DataFrame(definition.simulator(state_df, slippage_cents=0))
        if not trade_df.empty:
            trade_df["game_id"] = trade_df["game_id"].astype(str)
            trade_df["team_side"] = trade_df["team_side"].astype(str)
            trade_df = trade_df[trade_df["game_id"].isin(selected_game_ids)].copy()
        grouped_keys = {(str(row.get("game_id") or ""), str(row.get("team_side") or "")) for row in trade_df.to_dict(orient="records")}
        for record in trade_df.to_dict(orient="records"):
            game_id = str(record.get("game_id") or "")
            team_side = str(record.get("team_side") or "")
            bundle = bundles.get(game_id) or {}
            latest_state = _latest_state_row(state_df, game_id=game_id, team_side=team_side)
            latest_state_index = int(latest_state.get("state_index") or -1) if latest_state else None
            entry_state_index = int(record.get("entry_state_index") or -1)
            signal_entry_at = _safe_datetime(record.get("entry_at"))
            latest_event_at = _safe_datetime((latest_state or {}).get("event_at"))
            signal_age_seconds = None
            if signal_entry_at is not None and latest_event_at is not None:
                signal_age_seconds = max(0.0, (latest_event_at - signal_entry_at).total_seconds())
            state_lag = None if latest_state_index is None else max(0, latest_state_index - entry_state_index)
            orderbook = ((bundle.get("live_orderbooks") or {}).get(team_side) or {})
            quote_time = _safe_datetime(orderbook.get("timestamp"))
            quote_age_seconds = None
            if quote_time is not None:
                quote_age_seconds = max(0.0, (snapshot_at - quote_time).total_seconds())
            spread_cents = _safe_float(orderbook.get("spread_cents"))
            best_bid = _safe_float(orderbook.get("best_bid"))
            best_ask = _safe_float(orderbook.get("best_ask"))

            shadow_action = "would_enter"
            shadow_reason = "eligible"
            if latest_state is None:
                shadow_action = "wait"
                shadow_reason = "latest_state_unavailable"
            elif state_lag is not None and state_lag > 0 and signal_age_seconds is not None and signal_age_seconds > 60.0:
                shadow_action = "wait"
                shadow_reason = "stale_signal"
            elif best_bid is None or best_ask is None:
                shadow_action = "wait"
                shadow_reason = "orderbook_unavailable"
            elif spread_cents is not None and spread_cents > 2.0:
                shadow_action = "wait"
                shadow_reason = "spread_too_wide"

            rows.append(
                {
                    "candidate_id": family,
                    "subject_name": family,
                    "subject_type": "family",
                    "promotion_bucket": _promotion_bucket_for_family(family),
                    "matchup": _matchup_label(bundle),
                    "game_id": game_id,
                    "team_side": team_side,
                    "strategy_family": family,
                    "signal_id": _signal_id(family, game_id, team_side, record.get("entry_state_index")),
                    "entry_state_index": entry_state_index,
                    "exit_state_index": int(record.get("exit_state_index") or -1),
                    "signal_entry_at": signal_entry_at.isoformat() if signal_entry_at else None,
                    "signal_exit_at": str(record.get("exit_at") or ""),
                    "signal_entry_price": _safe_float(record.get("entry_price")),
                    "signal_exit_price": _safe_float(record.get("exit_price")),
                    "signal_strength": _safe_float(record.get("signal_strength")),
                    "entry_rule": definition.entry_rule,
                    "exit_rule": definition.exit_rule,
                    "opening_band": record.get("opening_band"),
                    "period_label": record.get("period_label"),
                    "context_bucket": record.get("context_bucket"),
                    "latest_state_index": latest_state_index,
                    "latest_event_at": latest_event_at.isoformat() if latest_event_at else None,
                    "first_attempt_signal_age_seconds": signal_age_seconds,
                    "first_attempt_quote_age_seconds": quote_age_seconds,
                    "first_attempt_state_lag": state_lag,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread_cents": spread_cents,
                    "shadow_action": shadow_action,
                    "shadow_reason": shadow_reason,
                    "coverage_status": diagnostics_by_game.get(game_id, {}).get("coverage_status"),
                }
            )

        for game_id in selected_game_ids:
            for team_side in ("away", "home"):
                if (str(game_id), team_side) in grouped_keys:
                    continue
                bundle = bundles.get(str(game_id)) or {}
                latest_state = _latest_state_row(state_df, game_id=str(game_id), team_side=team_side)
                rows.append(
                    {
                        "candidate_id": family,
                        "subject_name": family,
                        "subject_type": "family",
                        "promotion_bucket": _promotion_bucket_for_family(family),
                        "matchup": _matchup_label(bundle),
                        "game_id": str(game_id),
                        "team_side": team_side,
                        "strategy_family": family,
                        "signal_id": None,
                        "entry_state_index": None,
                        "exit_state_index": None,
                        "signal_entry_at": None,
                        "signal_exit_at": None,
                        "signal_entry_price": None,
                        "signal_exit_price": None,
                        "signal_strength": None,
                        "entry_rule": definition.entry_rule,
                        "exit_rule": definition.exit_rule,
                        "opening_band": None,
                        "period_label": None,
                        "context_bucket": None,
                        "latest_state_index": int(latest_state.get("state_index") or -1) if latest_state else None,
                        "latest_event_at": str((latest_state or {}).get("event_at") or ""),
                        "first_attempt_signal_age_seconds": None,
                        "first_attempt_quote_age_seconds": None,
                        "first_attempt_state_lag": None,
                        "best_bid": None,
                        "best_ask": None,
                        "spread_cents": None,
                        "shadow_action": "wait",
                        "shadow_reason": "no_strategy_signal",
                        "coverage_status": diagnostics_by_game.get(str(game_id), {}).get("coverage_status"),
                    }
                )
    return rows


def build_live_shadow_snapshot(
    *,
    run_id: str,
    run_root: Path,
    snapshot: dict[str, Any],
    controller_cards: list[dict[str, Any]] | None = None,
    game_ids: list[str] | None = None,
    families: list[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    snapshot_at = _now_utc()
    selected_game_ids = [str(game_id) for game_id in (game_ids or list(snapshot.get("bundles", {}).keys()))]
    selected_families = [str(family) for family in (families or DEFAULT_SHADOW_FAMILIES)]
    state_df = snapshot.get("state_df")
    if not isinstance(state_df, pd.DataFrame):
        state_df = pd.DataFrame()
    family_rows = _build_family_shadow_rows(
        state_df=state_df,
        bundles=snapshot.get("bundles") or {},
        diagnostics_by_game=snapshot.get("diagnostics_by_game") or {},
        game_ids=selected_game_ids,
        families=selected_families,
        snapshot_at=snapshot_at,
    )
    active_rows = [row for row in family_rows if row.get("shadow_action") == "would_enter"]
    blocked_rows = [row for row in family_rows if row.get("signal_id") and row.get("shadow_action") != "would_enter"]
    live_comparison = _build_live_shadow_comparison(
        run_id=run_id,
        family_rows=family_rows,
        controller_cards=controller_cards or [],
    )
    summary_rows: list[dict[str, Any]] = []
    for family in selected_families:
        family_slice = [row for row in family_rows if row.get("strategy_family") == family]
        summary_rows.append(
            {
                "subject_name": family,
                "promotion_bucket": _promotion_bucket_for_family(family),
                "tracked_games": len({str(row.get("game_id") or "") for row in family_slice}),
                "active_signal_count": len([row for row in family_slice if row.get("shadow_action") == "would_enter"]),
                "blocked_signal_count": len([row for row in family_slice if row.get("signal_id") and row.get("shadow_action") != "would_enter"]),
                "stale_signal_count": len([row for row in family_slice if row.get("shadow_reason") == "stale_signal"]),
                "no_signal_count": len([row for row in family_slice if row.get("shadow_reason") == "no_strategy_signal"]),
            }
        )

    payload = {
        "snapshot_at_utc": snapshot_at.isoformat(),
        "run_id": run_id,
        "run_root": str(run_root),
        "game_ids": selected_game_ids,
        "families": selected_families,
        "controller_cards": to_jsonable(controller_cards or []),
        "family_shadow": to_jsonable(family_rows),
        "live_shadow_comparison": live_comparison,
        "summary": summary_rows,
        "active_signals": to_jsonable(active_rows),
        "blocked_signals": to_jsonable(blocked_rows),
        "artifacts": {
            "shadow_snapshot_json": str(run_root / SHADOW_SNAPSHOT_JSON_NAME),
            "shadow_snapshot_csv": str(run_root / SHADOW_SNAPSHOT_CSV_NAME),
            "shadow_live_comparison_csv": str(run_root / SHADOW_LIVE_COMPARISON_CSV_NAME),
        },
    }
    if persist:
        _write_json(run_root / SHADOW_SNAPSHOT_JSON_NAME, payload)
        _write_csv(run_root / SHADOW_SNAPSHOT_CSV_NAME, family_rows)
        _write_csv(run_root / SHADOW_LIVE_COMPARISON_CSV_NAME, live_comparison["rows"])
    return payload


__all__ = [
    "DEFAULT_SHADOW_FAMILIES",
    "SHADOW_LIVE_COMPARISON_CSV_NAME",
    "SHADOW_SNAPSHOT_CSV_NAME",
    "SHADOW_SNAPSHOT_JSON_NAME",
    "build_live_shadow_snapshot",
]
