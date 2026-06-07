from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crypto_options_app.profiles.classifier import AccountTypeClassification
from crypto_options_app.profiles.grading import GradeResult, UsageEligibility
from crypto_options_app.profiles.reconstruction import ProfileEventReconstruction


@dataclass(frozen=True)
class GeneratorOutput:
    generator_id: str
    profile_key: str
    account_type: str
    confidence: float
    source_reconstruction_keys: tuple[str, ...]
    payload: dict[str, Any] = field(default_factory=dict)
    usage_eligible: bool = False
    can_emit_live: bool = False


def build_generator_outputs(
    *,
    profile_key: str,
    classification: AccountTypeClassification,
    reconstructions: list[ProfileEventReconstruction],
    grade: GradeResult,
    usage: UsageEligibility,
    buying_ahead_rows: list[dict[str, Any]] | None = None,
) -> list[GeneratorOutput]:
    source_keys = classification.source_reconstruction_keys
    confidence = _execution_confidence(classification, reconstructions, grade, usage)
    can_emit = usage.usage_eligible and confidence >= 0.6
    outputs: list[GeneratorOutput] = []

    if classification.account_type in {"outcome_predictor", "hedger"}:
        outputs.append(
            GeneratorOutput(
                generator_id="outcome_expectation",
                profile_key=profile_key,
                account_type=classification.account_type,
                confidence=confidence,
                source_reconstruction_keys=source_keys,
                payload=_outcome_payload(reconstructions),
                usage_eligible=usage.usage_eligible,
                can_emit_live=can_emit,
            )
        )
    if classification.account_type in {"hedger", "grid_buyer"}:
        outputs.append(
            GeneratorOutput(
                generator_id="hedge_proportion",
                profile_key=profile_key,
                account_type=classification.account_type,
                confidence=confidence,
                source_reconstruction_keys=source_keys,
                payload=_hedge_payload(reconstructions),
                usage_eligible=usage.usage_eligible,
                can_emit_live=can_emit,
            )
        )
    if classification.account_type == "grid_buyer":
        outputs.append(
            GeneratorOutput(
                generator_id="band_rebound",
                profile_key=profile_key,
                account_type=classification.account_type,
                confidence=confidence,
                source_reconstruction_keys=source_keys,
                payload=_band_payload(reconstructions),
                usage_eligible=usage.usage_eligible,
                can_emit_live=can_emit,
            )
        )
    if classification.account_type == "scalping_trader":
        outputs.append(
            GeneratorOutput(
                generator_id="volatility_liquidity",
                profile_key=profile_key,
                account_type=classification.account_type,
                confidence=confidence,
                source_reconstruction_keys=source_keys,
                payload=_volatility_liquidity_payload(reconstructions),
                usage_eligible=usage.usage_eligible,
                can_emit_live=can_emit,
            )
        )
    if buying_ahead_rows:
        outputs.append(
            GeneratorOutput(
                generator_id="buying_ahead",
                profile_key=profile_key,
                account_type=classification.account_type,
                confidence=confidence,
                source_reconstruction_keys=source_keys,
                payload={"rows": buying_ahead_rows},
                usage_eligible=usage.usage_eligible,
                can_emit_live=can_emit,
            )
        )
    return outputs


def _execution_confidence(
    classification: AccountTypeClassification,
    reconstructions: list[ProfileEventReconstruction],
    grade: GradeResult,
    usage: UsageEligibility,
) -> float:
    if not reconstructions:
        return 0.0
    avg_quality = sum(row.reconstruction_quality for row in reconstructions) / len(reconstructions)
    grade_quality = grade.score / 100.0
    usage_penalty = 1.0 if usage.usage_eligible else 0.5
    return round(min(1.0, classification.confidence * 0.45 + avg_quality * 0.35 + grade_quality * 0.20) * usage_penalty, 6)


def _outcome_payload(reconstructions: list[ProfileEventReconstruction]) -> dict[str, Any]:
    up_shares = sum(row.up.open_shares for row in reconstructions)
    down_shares = sum(row.down.open_shares for row in reconstructions)
    expected_side = "Up" if up_shares >= down_shares else "Down"
    total = up_shares + down_shares
    return {
        "expected_side": expected_side,
        "up_open_shares": up_shares,
        "down_open_shares": down_shares,
        "side_weight": 0.0 if total == 0 else max(up_shares, down_shares) / total,
    }


def _hedge_payload(reconstructions: list[ProfileEventReconstruction]) -> dict[str, Any]:
    up_shares = sum(row.up.open_shares for row in reconstructions)
    down_shares = sum(row.down.open_shares for row in reconstructions)
    total = up_shares + down_shares
    return {
        "target_up_ratio": 0.0 if total == 0 else up_shares / total,
        "target_down_ratio": 0.0 if total == 0 else down_shares / total,
        "avg_hedge_balance": _avg(row.hedge_balance for row in reconstructions),
    }


def _band_payload(reconstructions: list[ProfileEventReconstruction]) -> dict[str, Any]:
    return {
        "avg_price_band_coverage": _avg(row.price_band_coverage for row in reconstructions),
        "avg_buy_count": _avg(row.buy_count for row in reconstructions),
    }


def _volatility_liquidity_payload(reconstructions: list[ProfileEventReconstruction]) -> dict[str, Any]:
    return {
        "avg_turnover": _avg(row.turnover for row in reconstructions),
        "avg_sell_count": _avg(row.sell_count for row in reconstructions),
    }


def _avg(values: object) -> float:
    sequence = list(values)
    if not sequence:
        return 0.0
    return round(sum(sequence) / len(sequence), 6)
