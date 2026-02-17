from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.data.pipelines.canonical.models import CanonicalBundle


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str
    canonical_event_id: Optional[str] = None
    canonical_market_id: Optional[str] = None
    canonical_outcome_id: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, value: str) -> str:
        token = value.strip().lower()
        if token not in {"error", "warning"}:
            raise ValueError("severity must be error or warning")
        return token


class QualityGateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    passed: bool
    error_count: int
    warning_count: int
    issues: list[QualityIssue] = Field(default_factory=list)
    metrics_json: dict[str, int | float | str] = Field(default_factory=dict)


def _issue_sort_key(issue: QualityIssue) -> tuple[str, str, str, str, str]:
    return (
        issue.severity,
        issue.code,
        issue.canonical_event_id or "",
        issue.canonical_market_id or "",
        issue.canonical_outcome_id or "",
    )


def run_quality_gates(
    bundle: CanonicalBundle,
    now: Optional[datetime] = None,
) -> QualityGateReport:
    now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    issues: list[QualityIssue] = []

    for event in bundle.events:
        if not event.source_refs:
            issues.append(
                QualityIssue(
                    severity="error",
                    code="event_missing_sources",
                    message="event has no source refs",
                    canonical_event_id=event.canonical_event_id,
                )
            )
        if not event.markets:
            issues.append(
                QualityIssue(
                    severity="warning",
                    code="event_without_markets",
                    message="event has no mapped markets yet",
                    canonical_event_id=event.canonical_event_id,
                )
            )
        if event.start_time and event.status == "open" and event.start_time < (now - timedelta(hours=12)):
            issues.append(
                QualityIssue(
                    severity="warning",
                    code="event_open_after_start",
                    message="event start_time is in the past but status is open",
                    canonical_event_id=event.canonical_event_id,
                )
            )
        if event.start_time and event.status == "closed" and event.start_time > now:
            issues.append(
                QualityIssue(
                    severity="warning",
                    code="event_closed_before_start",
                    message="event is marked closed before start_time",
                    canonical_event_id=event.canonical_event_id,
                )
            )

        for market in event.markets:
            if not market.source_refs:
                issues.append(
                    QualityIssue(
                        severity="warning",
                        code="market_missing_sources",
                        message="market has no source refs",
                        canonical_event_id=event.canonical_event_id,
                        canonical_market_id=market.canonical_market_id,
                    )
                )
            if len(market.outcomes) < 2:
                issues.append(
                    QualityIssue(
                        severity="error",
                        code="market_outcome_cardinality",
                        message="market has fewer than two outcomes",
                        canonical_event_id=event.canonical_event_id,
                        canonical_market_id=market.canonical_market_id,
                    )
                )

            probs = [o.implied_prob for o in market.outcomes if o.implied_prob is not None]
            if len(probs) >= 2:
                total = sum(probs)
                if total < 0.85 or total > 1.15:
                    issues.append(
                        QualityIssue(
                            severity="warning",
                            code="market_probability_imbalance",
                            message=f"sum(implied_prob)={total:.4f} outside [0.85, 1.15]",
                            canonical_event_id=event.canonical_event_id,
                            canonical_market_id=market.canonical_market_id,
                        )
                    )

            token_ids: set[str] = set()
            for outcome in market.outcomes:
                if not outcome.source_refs:
                    issues.append(
                        QualityIssue(
                            severity="warning",
                            code="outcome_missing_sources",
                            message="outcome has no source refs",
                            canonical_event_id=event.canonical_event_id,
                            canonical_market_id=market.canonical_market_id,
                            canonical_outcome_id=outcome.canonical_outcome_id,
                        )
                    )
                if outcome.last_price is None and outcome.implied_prob is None:
                    issues.append(
                        QualityIssue(
                            severity="warning",
                            code="outcome_missing_price",
                            message="outcome has neither last_price nor implied_prob",
                            canonical_event_id=event.canonical_event_id,
                            canonical_market_id=market.canonical_market_id,
                            canonical_outcome_id=outcome.canonical_outcome_id,
                        )
                    )
                if outcome.token_id:
                    if outcome.token_id in token_ids:
                        issues.append(
                            QualityIssue(
                                severity="warning",
                                code="market_duplicate_token_id",
                                message="duplicate token_id detected in market",
                                canonical_event_id=event.canonical_event_id,
                                canonical_market_id=market.canonical_market_id,
                                canonical_outcome_id=outcome.canonical_outcome_id,
                            )
                        )
                    token_ids.add(outcome.token_id)

    ordered = sorted(issues, key=_issue_sort_key)
    error_count = len([i for i in ordered if i.severity == "error"])
    warning_count = len([i for i in ordered if i.severity == "warning"])
    return QualityGateReport(
        passed=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
        issues=ordered,
        metrics_json={
            "events_checked": len(bundle.events),
            "markets_checked": sum(len(e.markets) for e in bundle.events),
            "outcomes_checked": sum(len(m.outcomes) for e in bundle.events for m in e.markets),
        },
    )
