from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PortfolioGroup = Literal[
    "janus-controlled",
    "codex-assisted",
    "operator-manual",
    "watch-only",
    "future-domain-candidate",
    "unknown",
]
SourceActor = Literal["janus", "codex", "operator", "external", "unknown"]
TargetState = Literal["target_present", "target_stale", "target_missing", "target_unknown"]
RiskBucket = Literal["janus-sports", "global-portfolio", "future-domain", "operator-manual", "unknown"]
TimeHorizon = Literal["intraday", "short", "medium", "long", "unknown"]
ResolutionRisk = Literal["low", "medium", "high", "unknown"]
PortfolioSide = Literal["yes", "no", "long", "short", "unknown"]


NO_EXECUTION_STATEMENT = "No execution is authorized by this artifact."


class GlobalPortfolioWatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watch_id: str = Field(min_length=1)
    market_title: str = Field(min_length=1)
    market_slug: str | None = None
    outcome: str | None = None
    side: PortfolioSide = "unknown"
    group: PortfolioGroup = "unknown"
    source_actor: SourceActor = "unknown"
    thesis: str | None = None
    entry_basis: str | None = None
    current_target: dict[str, Any] | None = None
    target_state: TargetState = "target_unknown"
    rebuy_ladder: list[dict[str, Any]] = Field(default_factory=list)
    risk_bucket: RiskBucket = "global-portfolio"
    time_horizon: TimeHorizon = "unknown"
    event_resolution_risk: ResolutionRisk = "unknown"
    source_evidence: list[str] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    concentration_tags: list[str] = Field(default_factory=list)
    policy_flags: list[str] = Field(default_factory=list)
    operator_review_questions: list[str] = Field(default_factory=list)
    recommended_followups: list[str] = Field(default_factory=list)
    execution_authorized: bool = False
    order_preparation_authorized: bool = False
    live_order_impact: Literal["none", "read-only"] = "none"

    @model_validator(mode="after")
    def _enforce_read_only_watchlist(self) -> "GlobalPortfolioWatchlistEntry":
        if self.execution_authorized:
            raise ValueError("global portfolio watchlist entries cannot authorize execution")
        if self.order_preparation_authorized:
            raise ValueError("global portfolio watchlist entries cannot authorize order preparation")
        if not self.source_evidence:
            self.source_caveats.append("source_evidence_missing")
        if self.target_state in {"target_missing", "target_stale"} and not self.operator_review_questions:
            self.operator_review_questions.append("target requires operator review before any action")
        if self.target_state == "target_present":
            _append_unique(self.policy_flags, "target_present")
            if self.current_target is None:
                _append_unique(self.policy_flags, "target_present_metadata_missing")
                _append_unique(
                    self.operator_review_questions,
                    "Target is marked present but current_target metadata is missing.",
                )
        if self.target_state == "target_missing":
            _append_unique(self.policy_flags, "target_missing")
        if self.target_state == "target_stale":
            _append_unique(self.policy_flags, "target_stale")
        if self.rebuy_ladder:
            _append_unique(self.policy_flags, "rebuy_ladder_present")
            _append_unique(
                self.operator_review_questions,
                "Rebuy ladder requires operator review before any action.",
            )
        if self.group == "future-domain-candidate" and self.risk_bucket == "global-portfolio":
            self.risk_bucket = "future-domain"
        if self.group == "operator-manual" and self.source_actor == "unknown":
            self.source_actor = "operator"
        if self.group == "future-domain-candidate":
            _append_unique(self.policy_flags, "future_domain_watch_only")
        if self.group == "operator-manual":
            _append_unique(self.policy_flags, "operator_manual_review")
        if self.event_resolution_risk == "high":
            _append_unique(self.policy_flags, "high_resolution_risk")
        return self


class GlobalPortfolioWatchlistArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "global_portfolio_watchlist_v1"
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#45"
    entries: list[GlobalPortfolioWatchlistEntry] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    no_execution_statement: str = NO_EXECUTION_STATEMENT
    execution_authorized: bool = False
    order_preparation_authorized: bool = False
    live_order_impact: Literal["none"] = "none"
    summary: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _summarize_and_enforce_read_only(self) -> "GlobalPortfolioWatchlistArtifact":
        if self.execution_authorized:
            raise ValueError("global portfolio artifacts cannot authorize execution")
        if self.order_preparation_authorized:
            raise ValueError("global portfolio artifacts cannot authorize order preparation")
        if self.no_execution_statement != NO_EXECUTION_STATEMENT:
            raise ValueError("no_execution_statement must preserve the standard non-action wording")
        apply_watchlist_policy_flags(self.entries)
        self.summary = build_watchlist_summary(self.entries, source_caveats=self.source_caveats)
        return self


def build_watchlist_artifact(
    entries: list[dict[str, Any] | GlobalPortfolioWatchlistEntry],
    *,
    source_caveats: list[str] | None = None,
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolioWatchlistArtifact:
    generated_at = _parse_datetime(generated_at_utc)
    normalized_entries = [_entry_from_raw(entry, index) for index, entry in enumerate(entries, start=1)]
    return GlobalPortfolioWatchlistArtifact(
        generated_at_utc=generated_at,
        entries=normalized_entries,
        source_caveats=list(source_caveats or []),
    )


def build_watchlist_summary(
    entries: list[GlobalPortfolioWatchlistEntry],
    *,
    source_caveats: list[str] | None = None,
) -> dict[str, Any]:
    groups = Counter(entry.group for entry in entries)
    target_states = Counter(entry.target_state for entry in entries)
    risk_buckets = Counter(entry.risk_bucket for entry in entries)
    policy_flags = Counter(flag for entry in entries for flag in entry.policy_flags)
    needs_operator_review = sum(
        1 for entry in entries if entry.target_state in {"target_missing", "target_stale"} or entry.operator_review_questions
    )
    target_uncovered_or_stale = sum(1 for entry in entries if entry.target_state in {"target_missing", "target_stale"})
    rebuy_ladder_rows = sum(1 for entry in entries if entry.rebuy_ladder)
    paired_exposure_rows = sum(1 for entry in entries if "paired_yes_no_exposure" in entry.policy_flags)
    return {
        "entry_count": len(entries),
        "groups": dict(sorted(groups.items())),
        "target_states": dict(sorted(target_states.items())),
        "risk_buckets": dict(sorted(risk_buckets.items())),
        "policy_flags": dict(sorted(policy_flags.items())),
        "target_policy": {
            "target_present_rows": target_states.get("target_present", 0),
            "target_uncovered_or_stale_rows": target_uncovered_or_stale,
            "rebuy_ladder_rows": rebuy_ladder_rows,
            "paired_exposure_rows": paired_exposure_rows,
            "policy_authority": "review_only_no_execution",
        },
        "needs_operator_review_count": needs_operator_review,
        "source_caveat_count": len(source_caveats or []),
        "execution_authorized": False,
        "order_preparation_authorized": False,
    }


def apply_watchlist_policy_flags(entries: list[GlobalPortfolioWatchlistEntry]) -> None:
    side_by_market: dict[str, set[str]] = {}
    for entry in entries:
        if entry.side not in {"yes", "no"}:
            continue
        market_key = entry.market_slug or entry.market_title
        side_by_market.setdefault(market_key, set()).add(entry.side)

    paired_markets = {market_key for market_key, sides in side_by_market.items() if {"yes", "no"}.issubset(sides)}
    if not paired_markets:
        return

    for entry in entries:
        market_key = entry.market_slug or entry.market_title
        if market_key not in paired_markets:
            continue
        _append_unique(entry.policy_flags, "paired_yes_no_exposure")
        _append_unique(
            entry.operator_review_questions,
            "Resolve paired Yes/No exposure before interpreting directional thesis.",
        )


def load_watchlist_source(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload], []
    if isinstance(payload, dict):
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("watchlist source 'entries' must be a list")
        caveats = payload.get("source_caveats", [])
        if not isinstance(caveats, list):
            raise ValueError("watchlist source 'source_caveats' must be a list")
        return [dict(item) for item in entries], [str(item) for item in caveats]
    raise ValueError("watchlist source must be a JSON object or list")


def render_watchlist_report(artifact: GlobalPortfolioWatchlistArtifact, *, artifact_path: str | None = None) -> str:
    generated_at = artifact.generated_at_utc.isoformat().replace("+00:00", "Z")
    lines = [
        "# Global Portfolio Watchlist Schema - 2026-05-18",
        "",
        f"- timestamp_utc: `{generated_at}`",
        "- automation: `janus-master-controller`",
        "- GitHub issue: `#45`",
        "- persona: `development-agent`",
        "- live-order impact: none. No orders were placed, cancelled, replaced, submitted, prepared, or authorized.",
        f"- non-action statement: `{artifact.no_execution_statement}`",
    ]
    if artifact_path is not None:
        lines.append(f"- artifact: `{artifact_path}`")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- entries: `{artifact.summary['entry_count']}`",
            f"- groups: `{artifact.summary['groups']}`",
            f"- target states: `{artifact.summary['target_states']}`",
            f"- operator-review rows: `{artifact.summary['needs_operator_review_count']}`",
            f"- target policy: `{artifact.summary['target_policy']}`",
            f"- policy flags: `{artifact.summary['policy_flags']}`",
            "",
            "## Watchlist Rows",
            "",
            "| Watch id | Group | Market | Outcome | Target state | Risk bucket |",
            "|---|---|---|---|---|---|",
        ]
    )
    if artifact.entries:
        for entry in artifact.entries:
            lines.append(
                f"| {entry.watch_id} | {entry.group} | {entry.market_title} | {entry.outcome or ''} | "
                f"{entry.target_state} | {entry.risk_bucket} |"
            )
    else:
        lines.append("| none | watch-only | No source rows supplied |  | target_unknown | global-portfolio |")
    lines.extend(
        [
            "",
            "## Schema Decision",
            "",
            "- This is a read-only artifact format for the global portfolio explorer and future target/rebuy ledger.",
            "- It separates source actor, position group, target status, rebuy ladder, risk bucket, horizon, source evidence, caveats, and operator-review questions.",
            "- It rejects execution and order-preparation authority in both row and artifact validation.",
            "- Direct CLOB/account truth remains required before any portfolio-state claim.",
            f"- This report was rendered from `{artifact.summary['entry_count']}` supplied watchlist source rows; source evidence and caveats remain row-local.",
            "",
            "## Target Policy Review",
            "",
            "- Policy flags are review-only signals for stale targets, uncovered targets, rebuy ladders, paired exposure, and future-domain/watch-only routing.",
            f"- Target policy summary: `{artifact.summary['target_policy']}`",
            "- No policy flag authorizes execution, order preparation, risk-budget promotion, or market-order use.",
            "",
            "## Next Safe Action",
            "",
            "Keep `#45` open for repeated read-only explorer runs, stale-target/rebuy policy hardening, and durable tooling gaps. No execution, order preparation, or risk-budget promotion is authorized by this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def _entry_from_raw(entry: dict[str, Any] | GlobalPortfolioWatchlistEntry, index: int) -> GlobalPortfolioWatchlistEntry:
    if isinstance(entry, GlobalPortfolioWatchlistEntry):
        return entry
    raw = dict(entry)
    raw.setdefault("watch_id", _stable_watch_id(raw, index))
    return GlobalPortfolioWatchlistEntry.model_validate(raw)


def _stable_watch_id(raw: dict[str, Any], index: int) -> str:
    parts = [
        str(raw.get("group") or "watch"),
        str(raw.get("market_slug") or raw.get("market_title") or f"row-{index}"),
        str(raw.get("outcome") or raw.get("side") or "unknown"),
    ]
    slug = "-".join(_slugify(part) for part in parts if part)
    return slug or f"watch-row-{index}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:120] or "unknown"


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _parse_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "GlobalPortfolioWatchlistArtifact",
    "GlobalPortfolioWatchlistEntry",
    "NO_EXECUTION_STATEMENT",
    "apply_watchlist_policy_flags",
    "build_watchlist_artifact",
    "build_watchlist_summary",
    "load_watchlist_source",
    "render_watchlist_report",
]
