from __future__ import annotations

from dataclasses import dataclass

from crypto_options_app.profiles.reconstruction import ProfileEventReconstruction


@dataclass(frozen=True)
class AccountTypeClassification:
    account_type: str
    confidence: float
    reason: str
    source_reconstruction_keys: tuple[str, ...]


def classify_account_type(reconstructions: list[ProfileEventReconstruction]) -> AccountTypeClassification:
    usable = [row for row in reconstructions if row.reconstruction_quality >= 0.5]
    if not usable:
        return AccountTypeClassification("unknown", 0.0, "insufficient_reconstruction_quality", ())

    avg_hedge = _avg(row.hedge_balance for row in usable)
    avg_skew = _avg(row.side_skew for row in usable)
    avg_band = _avg(row.price_band_coverage for row in usable)
    avg_turnover = _avg(row.turnover for row in usable)
    avg_buy_count = _avg(row.buy_count for row in usable)
    sell_events = sum(1 for row in usable if row.sell_count > 0)
    both_side_events = sum(1 for row in usable if row.up.buy_count > 0 and row.down.buy_count > 0)
    directional_events = sum(1 for row in usable if row.side_skew >= 0.75 and row.buy_count > 0)
    source_keys = tuple(row.reconstruction_key for row in usable)

    if sell_events / len(usable) >= 0.5 and avg_turnover >= 0.45:
        return AccountTypeClassification("scalping_trader", min(1.0, avg_turnover), "high_turnover_buy_sell_cycles", source_keys)
    if avg_band >= 0.55 and avg_buy_count >= 5 and both_side_events:
        return AccountTypeClassification("grid_buyer", min(1.0, avg_band), "multi_band_both_side_buying", source_keys)
    if both_side_events / len(usable) >= 0.5 and avg_hedge >= 0.45:
        return AccountTypeClassification("hedger", min(1.0, avg_hedge), "meaningful_both_side_inventory", source_keys)
    if directional_events / len(usable) >= 0.6 and avg_skew >= 0.75:
        return AccountTypeClassification("outcome_predictor", min(1.0, avg_skew), "mostly_one_side_directional_exposure", source_keys)
    return AccountTypeClassification("unknown", 0.35, "mixed_or_conflicting_reconstruction", source_keys)


def _avg(values: object) -> float:
    sequence = list(values)
    if not sequence:
        return 0.0
    return sum(sequence) / len(sequence)
