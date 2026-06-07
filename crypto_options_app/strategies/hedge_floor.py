from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


OutcomeSide = Literal["up", "down"]


@dataclass(frozen=True)
class HedgeFloorState:
    realized_cash: float
    up_shares: float
    down_shares: float
    payout_if_up: float
    payout_if_down: float
    guaranteed_floor: float
    protected_floor: float
    surplus_above_floor: float
    weaker_side: OutcomeSide


@dataclass(frozen=True)
class FloorChange:
    side: OutcomeSide
    price: float
    shares: float
    payout_if_up_after: float
    payout_if_down_after: float
    guaranteed_floor_after: float
    weaker_side_improved: bool
    preserves_floor: bool


@dataclass(frozen=True)
class GridViability:
    inversion_intensity: float
    recommended_spacing: float
    rebound_support: float
    liquidity_support: float
    spread_penalty: float
    viability_score: float
    viable: bool


@dataclass(frozen=True)
class PairedSeedEntryProjection:
    up_price: float
    down_price: float
    pair_sum: float
    budget_usd: float
    equal_shares: float
    cost_usd: float
    realized_cash: float
    guaranteed_floor: float
    floor_margin_per_share: float
    floor_margin_ratio: float
    state: HedgeFloorState


def hedge_floor_state(
    *,
    realized_cash: float,
    up_shares: float,
    down_shares: float,
    protected_floor: float | None = None,
) -> HedgeFloorState:
    payout_if_up = float(realized_cash) + float(up_shares)
    payout_if_down = float(realized_cash) + float(down_shares)
    guaranteed_floor = min(payout_if_up, payout_if_down)
    floor = guaranteed_floor if protected_floor is None else float(protected_floor)
    return HedgeFloorState(
        realized_cash=float(realized_cash),
        up_shares=float(up_shares),
        down_shares=float(down_shares),
        payout_if_up=payout_if_up,
        payout_if_down=payout_if_down,
        guaranteed_floor=guaranteed_floor,
        protected_floor=floor,
        surplus_above_floor=max(0.0, guaranteed_floor - floor),
        weaker_side="up" if payout_if_up <= payout_if_down else "down",
    )


def protected_floor_improvement(
    state: HedgeFloorState,
    *,
    side: OutcomeSide,
    price: float,
    shares: float,
) -> FloorChange:
    price = float(price)
    shares = float(shares)
    if side == "up":
        payout_if_up_after = state.payout_if_up + shares * (1.0 - price)
        payout_if_down_after = state.payout_if_down - shares * price
    else:
        payout_if_up_after = state.payout_if_up - shares * price
        payout_if_down_after = state.payout_if_down + shares * (1.0 - price)
    guaranteed_floor_after = min(payout_if_up_after, payout_if_down_after)
    weaker_before = state.weaker_side
    weaker_before_value = state.payout_if_up if weaker_before == "up" else state.payout_if_down
    weaker_after_value = payout_if_up_after if weaker_before == "up" else payout_if_down_after
    return FloorChange(
        side=side,
        price=price,
        shares=shares,
        payout_if_up_after=payout_if_up_after,
        payout_if_down_after=payout_if_down_after,
        guaranteed_floor_after=guaranteed_floor_after,
        weaker_side_improved=weaker_after_value > weaker_before_value + 1e-9,
        preserves_floor=guaranteed_floor_after >= state.protected_floor - 1e-9,
    )


def floor_preserving_order_gate(
    state: HedgeFloorState,
    *,
    side: OutcomeSide,
    price: float,
    shares: float,
) -> tuple[bool, FloorChange]:
    change = protected_floor_improvement(state, side=side, price=price, shares=shares)
    return bool(change.weaker_side_improved or change.preserves_floor), change


def surplus_tail_budget(state: HedgeFloorState, *, reserve_floor: float | None = None) -> float:
    reserve = state.protected_floor if reserve_floor is None else float(reserve_floor)
    return max(0.0, state.guaranteed_floor - reserve)


def paired_seed_entry_projection(
    *,
    up_price: float,
    down_price: float,
    budget_usd: float,
    protected_floor: float | None = None,
) -> PairedSeedEntryProjection:
    up_price = max(0.01, min(0.99, float(up_price)))
    down_price = max(0.01, min(0.99, float(down_price)))
    pair_sum = up_price + down_price
    budget = max(0.01, float(budget_usd))
    equal_shares = budget / max(pair_sum, 0.01)
    cost = equal_shares * pair_sum
    state = hedge_floor_state(
        realized_cash=-cost,
        up_shares=equal_shares,
        down_shares=equal_shares,
        protected_floor=protected_floor,
    )
    margin_per_share = 1.0 - pair_sum
    return PairedSeedEntryProjection(
        up_price=up_price,
        down_price=down_price,
        pair_sum=pair_sum,
        budget_usd=budget,
        equal_shares=equal_shares,
        cost_usd=cost,
        realized_cash=-cost,
        guaranteed_floor=state.guaranteed_floor,
        floor_margin_per_share=margin_per_share,
        floor_margin_ratio=state.guaranteed_floor / cost if cost > 0 else 0.0,
        state=state,
    )


def inversion_intensity(path: dict[str, Any]) -> float:
    crossings = max(0.0, _f(path.get("level_crossing_count")))
    flips = max(0.0, _f(path.get("rebound_direction_flip_count")))
    near_center = max(0.0, _f(path.get("near_50c_sample_count")))
    strong_rebounds = max(0.0, _f(path.get("strong_rebound_touch_count")))
    avg_range = max(0.0, _f(path.get("avg_rolling_60s_range")))
    pair_sum_noise = max(0.0, _f(path.get("pair_sum_range")))
    raw = (
        min(1.0, crossings / 8.0) * 0.28
        + min(1.0, flips / 5.0) * 0.24
        + min(1.0, near_center / 8.0) * 0.14
        + min(1.0, strong_rebounds / 5.0) * 0.16
        + min(1.0, avg_range / 0.12) * 0.18
        - min(0.35, pair_sum_noise / 0.35) * 0.10
    )
    return max(0.0, min(1.0, raw))


def grid_spacing_from_inversion_stats(
    *,
    inversion_score: float,
    avg_rolling_60s_range: float,
    spread: float,
) -> float:
    avg_range = max(0.0, float(avg_rolling_60s_range))
    spread = max(0.0, float(spread))
    inversion_score = max(0.0, min(1.0, float(inversion_score)))
    spacing = max(spread * 1.5, avg_range * (0.65 - (0.25 * inversion_score)))
    return round(max(0.01, min(0.12, spacing)), 4)


def rebound_enough_to_cashout(
    path: dict[str, Any],
    *,
    spacing: float,
    spread: float,
    required_multiple: float = 1.6,
) -> bool:
    max_range = max(0.0, _f(path.get("max_rolling_60s_range")))
    avg_range = max(0.0, _f(path.get("avg_rolling_60s_range")))
    strong_rebounds = max(0.0, _f(path.get("strong_rebound_touch_count")))
    target = max(float(spacing), float(spread) * 2.0) * float(required_multiple)
    return max(max_range, avg_range * 1.5) >= target or strong_rebounds >= 2.0


def grid_viability(
    path: dict[str, Any],
    *,
    spread: float,
    liquidity_depth: float,
) -> GridViability:
    inversion_score = inversion_intensity(path)
    avg_range = max(0.0, _f(path.get("avg_rolling_60s_range")))
    spacing = grid_spacing_from_inversion_stats(
        inversion_score=inversion_score,
        avg_rolling_60s_range=avg_range,
        spread=spread,
    )
    rebound_support = 1.0 if rebound_enough_to_cashout(path, spacing=spacing, spread=spread) else 0.0
    liquidity_support = min(1.0, max(0.0, float(liquidity_depth)) / 20.0)
    spread_penalty = min(1.0, max(0.0, float(spread)) / 0.08)
    viability_score = max(
        0.0,
        min(
            1.0,
            inversion_score * 0.45
            + min(1.0, avg_range / max(spacing, 0.01)) * 0.20
            + rebound_support * 0.20
            + liquidity_support * 0.20
            - spread_penalty * 0.25,
        ),
    )
    return GridViability(
        inversion_intensity=inversion_score,
        recommended_spacing=spacing,
        rebound_support=rebound_support,
        liquidity_support=liquidity_support,
        spread_penalty=spread_penalty,
        viability_score=viability_score,
        viable=viability_score >= 0.5,
    )


def tail_reversal_probability(
    *,
    touch_price_cents: int,
    time_remaining_seconds: float,
    inversion_score: float,
    volatility_score: float,
    spread: float | None = None,
    liquidity_depth: float | None = None,
    depth_pressure_score: float | None = None,
    crypto_context_bucket: str | None = None,
    crypto_distance_bucket: str | None = None,
    profile_context_bucket: str | None = None,
    profile_price_context_bucket: str | None = None,
    tables: dict[str, Any] | None = None,
) -> float:
    bucket = _time_bucket(time_remaining_seconds)
    volatility_bucket = _volatility_bucket(volatility_score)
    execution_context_bucket = None
    if spread is not None and liquidity_depth is not None:
        execution_context_bucket = (
            f"{_spread_bucket(float(spread))}|"
            f"{_liquidity_bucket(float(liquidity_depth))}|"
            f"{_slippage_bucket(max(0.0, float(spread)) / max(float(liquidity_depth), 1.0))}"
        )
    depth_pressure_bucket = None if depth_pressure_score is None else _depth_pressure_bucket(depth_pressure_score)
    if tables:
        touched = tables.get(f"touched_{touch_price_cents}c") or {}
        if not touched and isinstance(tables.get("bucket_probabilities"), dict):
            touched = tables["bucket_probabilities"].get(f"touched_{touch_price_cents}c") or {}
        if (
            profile_price_context_bucket
            and crypto_distance_bucket
            and isinstance(tables.get("profile_price_crypto_context_probabilities"), dict)
        ):
            profile_price_crypto_touched = tables["profile_price_crypto_context_probabilities"].get(
                f"touched_{touch_price_cents}c"
            ) or {}
            profile_price_crypto_bucket = (
                profile_price_crypto_touched.get(bucket) if isinstance(profile_price_crypto_touched, dict) else None
            )
            if isinstance(profile_price_crypto_bucket, dict):
                profile_price_crypto_volatility = profile_price_crypto_bucket.get(volatility_bucket)
                if isinstance(profile_price_crypto_volatility, dict):
                    profile_price_crypto_values = profile_price_crypto_volatility.get(profile_price_context_bucket)
                    if isinstance(profile_price_crypto_values, dict):
                        profile_price_crypto_value = profile_price_crypto_values.get(crypto_distance_bucket)
                        if profile_price_crypto_value is not None:
                            return max(0.0, min(1.0, float(profile_price_crypto_value)))
        if (
            profile_price_context_bucket
            and execution_context_bucket
            and isinstance(tables.get("profile_price_execution_context_probabilities"), dict)
        ):
            profile_price_execution_touched = tables["profile_price_execution_context_probabilities"].get(
                f"touched_{touch_price_cents}c"
            ) or {}
            profile_price_execution_bucket = (
                profile_price_execution_touched.get(bucket)
                if isinstance(profile_price_execution_touched, dict)
                else None
            )
            if isinstance(profile_price_execution_bucket, dict):
                profile_price_execution_volatility = profile_price_execution_bucket.get(volatility_bucket)
                if isinstance(profile_price_execution_volatility, dict):
                    profile_price_execution_values = profile_price_execution_volatility.get(profile_price_context_bucket)
                    if isinstance(profile_price_execution_values, dict):
                        profile_price_execution_value = profile_price_execution_values.get(execution_context_bucket)
                        if profile_price_execution_value is not None:
                            return max(0.0, min(1.0, float(profile_price_execution_value)))
        if (
            crypto_distance_bucket
            and execution_context_bucket
            and isinstance(tables.get("crypto_distance_execution_context_probabilities"), dict)
        ):
            crypto_distance_execution_touched = tables["crypto_distance_execution_context_probabilities"].get(
                f"touched_{touch_price_cents}c"
            ) or {}
            crypto_distance_execution_bucket = (
                crypto_distance_execution_touched.get(bucket)
                if isinstance(crypto_distance_execution_touched, dict)
                else None
            )
            if isinstance(crypto_distance_execution_bucket, dict):
                crypto_distance_execution_volatility = crypto_distance_execution_bucket.get(volatility_bucket)
                if isinstance(crypto_distance_execution_volatility, dict):
                    crypto_distance_execution_values = crypto_distance_execution_volatility.get(crypto_distance_bucket)
                    if isinstance(crypto_distance_execution_values, dict):
                        crypto_distance_execution_value = crypto_distance_execution_values.get(execution_context_bucket)
                        if crypto_distance_execution_value is not None:
                            return max(0.0, min(1.0, float(crypto_distance_execution_value)))
        if profile_price_context_bucket and isinstance(tables.get("profile_price_context_probabilities"), dict):
            profile_price_touched = tables["profile_price_context_probabilities"].get(
                f"touched_{touch_price_cents}c"
            ) or {}
            profile_price_bucket = profile_price_touched.get(bucket) if isinstance(profile_price_touched, dict) else None
            if isinstance(profile_price_bucket, dict):
                profile_price_volatility = profile_price_bucket.get(volatility_bucket)
                if isinstance(profile_price_volatility, dict):
                    profile_price_value = profile_price_volatility.get(profile_price_context_bucket)
                    if profile_price_value is not None:
                        return max(0.0, min(1.0, float(profile_price_value)))
        if profile_context_bucket and isinstance(tables.get("profile_context_probabilities"), dict):
            profile_touched = tables["profile_context_probabilities"].get(f"touched_{touch_price_cents}c") or {}
            profile_bucket = profile_touched.get(bucket) if isinstance(profile_touched, dict) else None
            if isinstance(profile_bucket, dict):
                profile_volatility = profile_bucket.get(volatility_bucket)
                if isinstance(profile_volatility, dict):
                    profile_value = profile_volatility.get(profile_context_bucket)
                    if profile_value is not None:
                        return max(0.0, min(1.0, float(profile_value)))
        if crypto_context_bucket and isinstance(tables.get("crypto_context_probabilities"), dict):
            crypto_touched = tables["crypto_context_probabilities"].get(f"touched_{touch_price_cents}c") or {}
            crypto_bucket = crypto_touched.get(bucket) if isinstance(crypto_touched, dict) else None
            if isinstance(crypto_bucket, dict):
                crypto_volatility = crypto_bucket.get(volatility_bucket)
                if isinstance(crypto_volatility, dict):
                    crypto_value = crypto_volatility.get(crypto_context_bucket)
                    if crypto_value is not None:
                        return max(0.0, min(1.0, float(crypto_value)))
        if crypto_distance_bucket and isinstance(tables.get("crypto_distance_context_probabilities"), dict):
            crypto_distance_touched = (
                tables["crypto_distance_context_probabilities"].get(f"touched_{touch_price_cents}c") or {}
            )
            crypto_distance_time_bucket = (
                crypto_distance_touched.get(bucket) if isinstance(crypto_distance_touched, dict) else None
            )
            if isinstance(crypto_distance_time_bucket, dict):
                crypto_distance_volatility = crypto_distance_time_bucket.get(volatility_bucket)
                if isinstance(crypto_distance_volatility, dict):
                    crypto_distance_value = crypto_distance_volatility.get(crypto_distance_bucket)
                    if crypto_distance_value is not None:
                        return max(0.0, min(1.0, float(crypto_distance_value)))
        if execution_context_bucket and isinstance(tables.get("execution_context_probabilities"), dict):
            execution_touched = tables["execution_context_probabilities"].get(f"touched_{touch_price_cents}c") or {}
            execution_bucket = execution_touched.get(bucket) if isinstance(execution_touched, dict) else None
            if isinstance(execution_bucket, dict):
                execution_volatility = execution_bucket.get(volatility_bucket)
                if isinstance(execution_volatility, dict):
                    execution_value = execution_volatility.get(execution_context_bucket)
                    if execution_value is not None:
                        return max(0.0, min(1.0, float(execution_value)))
        if depth_pressure_bucket and isinstance(tables.get("microstructure_context_probabilities"), dict):
            microstructure_touched = tables["microstructure_context_probabilities"].get(f"touched_{touch_price_cents}c") or {}
            microstructure_bucket = microstructure_touched.get(bucket) if isinstance(microstructure_touched, dict) else None
            if isinstance(microstructure_bucket, dict):
                microstructure_volatility = microstructure_bucket.get(volatility_bucket)
                if isinstance(microstructure_volatility, dict):
                    context_value = microstructure_volatility.get(depth_pressure_bucket)
                    if context_value is not None:
                        return max(0.0, min(1.0, float(context_value)))
        if isinstance(tables.get("context_probabilities"), dict):
            context_touched = tables["context_probabilities"].get(f"touched_{touch_price_cents}c") or {}
            context_bucket = context_touched.get(bucket) if isinstance(context_touched, dict) else None
            if isinstance(context_bucket, dict):
                context_value = context_bucket.get(volatility_bucket)
                if context_value is not None:
                    return max(0.0, min(1.0, float(context_value)))
        bucket_value = touched.get(bucket)
        if bucket_value is not None:
            return max(0.0, min(1.0, float(bucket_value)))
    base = {1: 0.34, 5: 0.27, 10: 0.21}.get(int(touch_price_cents), 0.16)
    time_bonus = {"lt60": 0.03, "60_180": 0.00, "gt180": -0.04}[bucket]
    score = base + time_bonus + (float(inversion_score) * 0.18) + (float(volatility_score) * 0.12)
    return max(0.0, min(1.0, score))


def profile_tail_context_bucket(profile_context: dict[str, Any] | None) -> str | None:
    if not isinstance(profile_context, dict):
        return None
    breakdown = profile_context.get("component_breakdown") if isinstance(profile_context.get("component_breakdown"), dict) else {}
    groups = breakdown.get("by_grade_style") if isinstance(breakdown.get("by_grade_style"), dict) else {}
    label = "aggregate"
    best_payload: dict[str, Any] = {}
    best_weight = -1.0
    for raw_label, payload in groups.items():
        if not isinstance(payload, dict):
            continue
        up_ratio = _f(payload.get("up_pressure_ratio"))
        down_ratio = _f(payload.get("down_pressure_ratio"))
        weight = max(up_ratio, down_ratio)
        if weight > best_weight:
            best_weight = weight
            label = "_".join(
                part.strip().lower().replace("+", "plus").replace(" ", "_")
                for part in str(raw_label).split("/")
                if part.strip()
            ) or "aggregate"
            best_payload = payload
    tilt_side = _pressure_side(best_payload.get("pressure_delta"))
    price_side = _price_side(
        profile_context.get("up_reconstructed_profile_price"),
        profile_context.get("down_reconstructed_profile_price"),
    )
    return f"{label}|{tilt_side}|{price_side}"


def profile_tail_price_context_bucket(profile_context: dict[str, Any] | None) -> str | None:
    if not isinstance(profile_context, dict):
        return None
    breakdown = profile_context.get("component_breakdown") if isinstance(profile_context.get("component_breakdown"), dict) else {}
    groups = breakdown.get("by_grade_style") if isinstance(breakdown.get("by_grade_style"), dict) else {}
    label = "aggregate"
    best_payload: dict[str, Any] = {}
    best_weight = -1.0
    for raw_label, payload in groups.items():
        if not isinstance(payload, dict):
            continue
        up_ratio = _f(payload.get("up_pressure_ratio"))
        down_ratio = _f(payload.get("down_pressure_ratio"))
        weight = max(up_ratio, down_ratio)
        if weight > best_weight:
            best_weight = weight
            label = "_".join(
                part.strip().lower().replace("+", "plus").replace(" ", "_")
                for part in str(raw_label).split("/")
                if part.strip()
            ) or "aggregate"
            best_payload = payload
    tilt_side = _pressure_side(best_payload.get("pressure_delta"))
    price_delta_bucket = _profile_price_delta_bucket(
        _price_delta(
            profile_context.get("up_reconstructed_profile_price"),
            profile_context.get("down_reconstructed_profile_price"),
        )
    )
    return f"{label}|{tilt_side}|{price_delta_bucket}"


def crypto_tail_context_bucket(crypto_context: dict[str, Any] | None) -> str | None:
    if not isinstance(crypto_context, dict):
        return None
    summaries = crypto_context.get("summaries") if isinstance(crypto_context.get("summaries"), list) else []
    scores = [_f(summary.get("summary_score")) for summary in summaries if isinstance(summary, dict)]
    if not scores:
        return None
    score = sum(scores) / len(scores)
    if score <= -0.45:
        distance_bucket = "far_down"
    elif score <= -0.15:
        distance_bucket = "moderate_down"
    elif score < 0.15:
        distance_bucket = "neutral"
    elif score < 0.45:
        distance_bucket = "moderate_up"
    else:
        distance_bucket = "far_up"
    symbol = str(crypto_context.get("symbol") or "unknown").strip().lower() or "unknown"
    return f"{symbol}|{distance_bucket}"


def crypto_tail_distance_bucket(crypto_context: dict[str, Any] | None) -> str | None:
    bucket = crypto_tail_context_bucket(crypto_context)
    if not bucket or "|" not in bucket:
        return None
    return bucket.split("|", 1)[1]


def _time_bucket(time_remaining_seconds: float) -> str:
    seconds = float(time_remaining_seconds)
    if seconds < 60.0:
        return "lt60"
    if seconds <= 180.0:
        return "60_180"
    return "gt180"


def _volatility_bucket(volatility_score: float) -> str:
    score = max(0.0, min(1.0, float(volatility_score)))
    if score < 0.34:
        return "calm"
    if score < 0.67:
        return "active"
    return "violent"


def _depth_pressure_bucket(depth_pressure_score: float) -> str:
    score = float(depth_pressure_score)
    if score <= -0.2:
        return "down_supportive"
    if score >= 0.2:
        return "up_supportive"
    return "balanced"


def _spread_bucket(avg_spread: float) -> str:
    spread = max(0.0, float(avg_spread))
    if spread < 0.025:
        return "tight"
    if spread < 0.05:
        return "normal"
    return "wide"


def _liquidity_bucket(avg_depth: float) -> str:
    depth = max(0.0, float(avg_depth))
    if depth < 20.0:
        return "shallow"
    if depth < 40.0:
        return "medium"
    return "deep"


def _slippage_bucket(slippage_proxy: float) -> str:
    score = max(0.0, float(slippage_proxy))
    if score < 0.001:
        return "low"
    if score < 0.003:
        return "moderate"
    return "elevated"


def _pressure_side(value: Any) -> str:
    score = _f(value)
    if score >= 0.1:
        return "up"
    if score <= -0.1:
        return "down"
    return "balanced"


def _price_side(up_price: Any, down_price: Any) -> str:
    up = _f(up_price)
    down = _f(down_price)
    if up <= 0.0 and down <= 0.0:
        return "balanced"
    if up - down >= 0.05:
        return "up"
    if down - up >= 0.05:
        return "down"
    return "balanced"


def _price_delta(up_price: Any, down_price: Any) -> float | None:
    up = _f(up_price)
    down = _f(down_price)
    if up <= 0.0 and down <= 0.0:
        return None
    return up - down


def _profile_price_delta_bucket(price_delta: float | None) -> str:
    if price_delta is None:
        return "balanced_narrow"
    delta = float(price_delta)
    abs_delta = abs(delta)
    side = "up" if delta >= 0.0 else "down"
    if abs_delta < 0.05:
        return "balanced_narrow"
    if abs_delta < 0.20:
        return f"{side}_lean"
    if abs_delta < 0.40:
        return f"{side}_strong"
    return f"{side}_extreme"


def _f(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
