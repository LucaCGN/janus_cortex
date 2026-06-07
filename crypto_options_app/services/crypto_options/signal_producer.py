from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from threading import RLock
from typing import Any, Callable


SIGNAL_PRODUCER_SCHEMA_VERSION = "crypto_options_signal_producer_snapshot_v1"
SERVICE_NAME = "crypto_options_signal_producer"


@dataclass(frozen=True)
class SignalProducerConfig:
    """Read-only profile signal feed settings for the FastAPI service."""

    strategy_id: str = "btc_eth_5m_mid_high"
    symbols: tuple[str, ...] = ("BTC", "ETH")
    profile_refs: tuple[str, ...] = ()
    max_workers: int = 8
    page_limit: int = 200
    max_signal_to_ask_slippage_cents: float = 10.0
    max_spread: float = 0.03
    min_depth_top3_ask_size: float = 5.0
    min_time_remaining_seconds: float = 20.0
    max_time_remaining_seconds: float = 300.0
    include_top_holders: bool = False
    include_scraped_profiles: bool = False
    scrape_profile_limit: int = 100
    use_profile_cache: bool = True
    active_profile_ttl_seconds: int = 3600
    pulse_page_limit: int = 50


@dataclass
class ProfileRuntimeState:
    identity: str
    name: str | None = None
    proxy_wallet: str | None = None
    first_seen_at_utc: str | None = None
    last_seen_at_utc: str | None = None
    observation_count: int = 0
    latest_status: str | None = None
    latest_grade: str | None = None
    previous_grade: str | None = None
    latest_score: float | None = None
    previous_score: float | None = None
    score_delta: float | None = None
    latest_polarity: str | None = None
    latest_activity_utc: str | None = None
    last_trade_seen_at_utc: str | None = None
    last_trade_change_at_utc: str | None = None
    latest_signal_at_utc: str | None = None
    active_signal_count: int = 0
    historical_signal_count: int = 0
    active: bool = False
    active_reason: str | None = None
    active_since_at_utc: str | None = None
    last_pulse_at_utc: str | None = None
    pulse_observation_count: int = 0
    grade_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    attribution: dict[str, Any] = field(default_factory=dict)


class CryptoOptionsSignalProducer:
    """Stateful read-only signal producer for profile discovery, grading, and attribution.

    The producer intentionally has no order-execution capabilities. It wraps the existing
    profile monitor tick, stores profile aging/score drift in memory, and offers a stable
    stream contract for downstream trading services.
    """

    def __init__(
        self,
        *,
        tick_builder: Callable[..., dict[str, Any]] | None = None,
        pulse_builder: Callable[..., dict[str, Any]] | None = None,
        service_name: str = SERVICE_NAME,
    ) -> None:
        self._tick_builder = tick_builder or _default_tick_builder
        self._pulse_builder = pulse_builder or _default_pulse_builder
        self._service_name = service_name
        self._lock = RLock()
        self._sequence = 0
        self._latest_mode = "idle"
        self._latest_monitor: dict[str, Any] | None = None
        self._profile_report_cache: dict[str, Any] | None = None
        self._profiles: dict[str, ProfileRuntimeState] = {}
        self._candidate_support_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._attribution: dict[str, dict[str, Any]] = defaultdict(_empty_attribution)

    def run_tick(
        self,
        config: SignalProducerConfig | None = None,
        *,
        now_utc: datetime | None = None,
        include_full_monitor: bool = False,
        mode: str | None = None,
    ) -> dict[str, Any]:
        config = config or SignalProducerConfig()
        now_utc = now_utc or datetime.now(timezone.utc)
        cache = self._profile_report_cache if config.use_profile_cache else None
        monitor = self._tick_builder(
            strategy_id=config.strategy_id,
            symbols=list(config.symbols),
            profile_refs=list(config.profile_refs),
            now_utc=now_utc,
            max_workers=config.max_workers,
            page_limit=config.page_limit,
            max_signal_to_ask_slippage_cents=config.max_signal_to_ask_slippage_cents,
            max_spread=config.max_spread,
            min_depth_top3_ask_size=config.min_depth_top3_ask_size,
            min_time_remaining_seconds=config.min_time_remaining_seconds,
            max_time_remaining_seconds=config.max_time_remaining_seconds,
            include_top_holders=config.include_top_holders,
            include_scraped_profiles=config.include_scraped_profiles,
            scrape_profile_limit=config.scrape_profile_limit,
            profile_report_cache=cache,
        )
        with self._lock:
            self._sequence += 1
            self._latest_mode = mode or ("cached_profile_refresh" if cache is not None else "full_profile_refresh")
            self._latest_monitor = monitor
            report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else None
            if report is not None:
                self._profile_report_cache = report
            self._update_profiles(monitor, active_profile_ttl_seconds=config.active_profile_ttl_seconds)
            self._index_candidate_support(monitor)
            return self._snapshot_locked(config=config, include_full_monitor=include_full_monitor)

    def run_discovery_tick(
        self,
        config: SignalProducerConfig | None = None,
        *,
        now_utc: datetime | None = None,
        include_full_monitor: bool = False,
    ) -> dict[str, Any]:
        config = config or SignalProducerConfig()
        discovery_config = SignalProducerConfig(
            **{
                **asdict(config),
                "include_top_holders": True,
                "include_scraped_profiles": True,
                "use_profile_cache": False,
            }
        )
        return self.run_tick(
            discovery_config,
            now_utc=now_utc,
            include_full_monitor=include_full_monitor,
            mode="discovery_hourly_full_refresh",
        )

    def run_active_pulse(
        self,
        config: SignalProducerConfig | None = None,
        *,
        now_utc: datetime | None = None,
        include_full_monitor: bool = False,
    ) -> dict[str, Any]:
        config = config or SignalProducerConfig()
        now_utc = now_utc or datetime.now(timezone.utc)
        with self._lock:
            profile_report_cache = dict(self._profile_report_cache or {})
            latest_monitor = dict(self._latest_monitor or {})
            active_profile_identities = self._active_profile_identities_locked(
                now_utc=now_utc,
                active_profile_ttl_seconds=config.active_profile_ttl_seconds,
            )
            active_event_slugs = list(latest_monitor.get("active_event_slugs") or profile_report_cache.get("active_event_slugs") or [])
            active_condition_ids = list(profile_report_cache.get("active_condition_ids") or [])
        if not profile_report_cache:
            with self._lock:
                self._sequence += 1
                self._latest_mode = "active_profile_pulse_blocked_no_profile_cache"
                return self._snapshot_locked(config=config, include_full_monitor=include_full_monitor)
        pulse_report = self._pulse_builder(
            profile_report=profile_report_cache,
            active_profile_identities=active_profile_identities,
            active_event_slugs=active_event_slugs,
            active_condition_ids=active_condition_ids,
            now_utc=now_utc,
            config=config,
        )
        with self._lock:
            merged_report = _merge_profile_report(profile_report_cache, pulse_report)
            monitor = dict(latest_monitor)
            monitor.update(
                {
                    "schema_version": monitor.get("schema_version") or "crypto_options_active_profile_pulse_monitor_v1",
                    "monitor_status": "active_profile_pulse",
                    "generated_at_utc": now_utc.isoformat(),
                    "strategy_id": config.strategy_id,
                    "observed_candidate_count": len(merged_report.get("aggregated_candidates") or []),
                    "eligible_manual_candidate_count": None,
                    "active_event_slugs": active_event_slugs,
                    "profile_signal_report": merged_report,
                    "pulse": {
                        "active_profile_count": len(active_profile_identities),
                        "active_profile_identities": active_profile_identities,
                        "pulse_page_limit": config.pulse_page_limit,
                    },
                }
            )
            self._sequence += 1
            self._latest_mode = "active_profile_pulse"
            self._latest_monitor = monitor
            self._profile_report_cache = merged_report
            self._update_profiles(monitor, active_profile_ttl_seconds=config.active_profile_ttl_seconds)
            self._index_candidate_support(monitor)
            for identity in active_profile_identities:
                if identity in self._profiles:
                    self._profiles[identity].last_pulse_at_utc = now_utc.isoformat()
                    self._profiles[identity].pulse_observation_count += 1
            return self._snapshot_locked(config=config, include_full_monitor=include_full_monitor)

    def latest_snapshot(self, *, include_full_monitor: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(config=None, include_full_monitor=include_full_monitor)

    def profile_states(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._profile_state_payload(state) for state in self._profiles.values()]

    def record_outcome(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Attach downstream trade outcomes back to source profiles.

        The feedback contract accepts explicit supporting_signals/supporting_profiles. When
        omitted, it falls back to the latest candidate support for event_slug + outcome.
        PnL is allocated evenly to avoid double-counting portfolio PnL across profiles.
        """

        event_slug = str(feedback.get("event_slug") or "")
        outcome = str(feedback.get("outcome") or feedback.get("effective_outcome") or "")
        signals = [row for row in feedback.get("supporting_signals") or [] if isinstance(row, dict)]
        if not signals and event_slug and outcome:
            signals = list(self._candidate_support_index.get((event_slug, outcome), []))
        profile_names = [str(item) for item in feedback.get("supporting_profiles") or [] if item]
        identities = self._profile_identities_from_signals(signals)
        if not identities:
            identities = profile_names
        identities = sorted(set(item for item in identities if item))
        pnl_usd = _float(feedback.get("pnl_usd"))
        allocated_pnl = (pnl_usd / len(identities)) if pnl_usd is not None and identities else None
        status = str(feedback.get("status") or "unknown")
        price_bucket = _price_bucket(_float(feedback.get("entry_price") or feedback.get("price")))
        with self._lock:
            self._sequence += 1
            for identity in identities:
                attribution = self._attribution[identity]
                attribution["linked_outcome_count"] += 1
                if allocated_pnl is not None:
                    attribution["allocated_pnl_usd"] = round(float(attribution["allocated_pnl_usd"]) + allocated_pnl, 6)
                    attribution["full_signal_pnl_usd"] = round(float(attribution["full_signal_pnl_usd"]) + float(pnl_usd or 0.0), 6)
                if status in {"settled", "closed", "win", "loss"}:
                    attribution["settled_outcome_count"] += 1
                if pnl_usd is not None and pnl_usd > 0:
                    attribution["win_count"] += 1
                elif pnl_usd is not None and pnl_usd < 0:
                    attribution["loss_count"] += 1
                if price_bucket:
                    attribution["price_buckets"][price_bucket] += 1
                strategy_id = str(feedback.get("strategy_id") or "unknown")
                attribution["strategies"][strategy_id] += 1
                attribution["latest_outcome_at_utc"] = _now_iso()
            return {
                "schema_version": "crypto_options_signal_outcome_feedback_v1",
                "status": "recorded",
                "event_slug": event_slug,
                "outcome": outcome,
                "profile_count": len(identities),
                "profile_identities": identities,
                "allocated_pnl_usd": allocated_pnl,
                "orders_allowed": False,
                "live_trading_authorized": False,
            }

    def reset(self) -> None:
        with self._lock:
            self._sequence = 0
            self._latest_monitor = None
            self._profile_report_cache = None
            self._profiles.clear()
            self._candidate_support_index.clear()
            self._attribution.clear()

    def _update_profiles(self, monitor: dict[str, Any], *, active_profile_ttl_seconds: int) -> None:
        report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
        generated_at = str(monitor.get("generated_at_utc") or report.get("generated_at_utc") or _now_iso())
        generated_dt = _parse_dt(generated_at) or datetime.now(timezone.utc)
        for snapshot in report.get("profiles") or []:
            if not isinstance(snapshot, dict):
                continue
            identity = _profile_identity(snapshot)
            state = self._profiles.get(identity)
            if state is None:
                state = ProfileRuntimeState(identity=identity, first_seen_at_utc=generated_at)
                self._profiles[identity] = state
            state.observation_count += 1
            state.last_seen_at_utc = generated_at
            state.latest_status = _text(snapshot.get("status"))
            profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
            state.name = _text(profile.get("name")) or state.name
            state.proxy_wallet = _text(profile.get("proxy_wallet") or profile.get("proxyWallet")) or state.proxy_wallet
            state.previous_grade = state.latest_grade
            state.previous_score = state.latest_score
            state.latest_grade = _text(snapshot.get("grade"))
            state.latest_score = _float(snapshot.get("score"))
            state.score_delta = (
                round(state.latest_score - state.previous_score, 6)
                if state.latest_score is not None and state.previous_score is not None
                else None
            )
            state.latest_polarity = _text(snapshot.get("polarity"))
            state.grade_reasons = [str(item) for item in snapshot.get("grade_reasons") or []]
            state.metrics = dict(snapshot.get("metrics") or {})
            previous_trade_seen = state.last_trade_seen_at_utc
            metric_activity = _text(state.metrics.get("latest_activity_utc"))
            active_signals = [row for row in snapshot.get("signals") or [] if isinstance(row, dict)]
            historical_signals = [row for row in snapshot.get("historical_signals") or [] if isinstance(row, dict)]
            state.active_signal_count = len(active_signals)
            state.historical_signal_count = len(historical_signals)
            state.latest_signal_at_utc = _latest_signal_time(active_signals) or state.latest_signal_at_utc
            state.latest_activity_utc = _latest_iso_text(metric_activity, state.latest_signal_at_utc)
            state.last_trade_seen_at_utc = state.latest_activity_utc
            if _is_newer_iso(state.last_trade_seen_at_utc, previous_trade_seen):
                state.last_trade_change_at_utc = generated_at
            active, reason = _active_profile_status(
                state=state,
                generated_at=generated_dt,
                active_profile_ttl_seconds=active_profile_ttl_seconds,
            )
            if active and not state.active:
                state.active_since_at_utc = generated_at
            elif not active:
                state.active_since_at_utc = None
            state.active = active
            state.active_reason = reason
            state.attribution = dict(self._attribution.get(identity) or _empty_attribution())

    def _index_candidate_support(self, monitor: dict[str, Any]) -> None:
        report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
        candidates = list(report.get("aggregated_candidates") or [])
        candidates.extend(row for row in monitor.get("observed_candidates") or [] if isinstance(row, dict))
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            key = (str(candidate.get("event_slug") or ""), str(candidate.get("effective_outcome") or candidate.get("outcome") or ""))
            if not key[0] or not key[1]:
                continue
            signals = [row for row in candidate.get("supporting_signals") or [] if isinstance(row, dict)]
            if signals:
                self._candidate_support_index[key] = signals

    def _snapshot_locked(self, *, config: SignalProducerConfig | None, include_full_monitor: bool) -> dict[str, Any]:
        monitor = self._latest_monitor or {}
        report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
        profiles = [self._profile_state_payload(state) for state in self._profiles.values()]
        profiles.sort(key=lambda row: (-(row.get("latest_score") or 0.0), str(row.get("identity") or "")))
        payload = {
            "schema_version": SIGNAL_PRODUCER_SCHEMA_VERSION,
            "service": self._service_name,
            "sequence": self._sequence,
            "status": "ready" if monitor else "idle",
            "producer_mode": self._latest_mode,
            "generated_at_utc": monitor.get("generated_at_utc") or _now_iso(),
            "config": asdict(config) if config else None,
            "monitor": {
                "schema_version": monitor.get("schema_version"),
                "monitor_status": monitor.get("monitor_status"),
                "strategy_id": monitor.get("strategy_id"),
                "target_count": monitor.get("target_count"),
                "observed_candidate_count": monitor.get("observed_candidate_count"),
                "eligible_manual_candidate_count": monitor.get("eligible_manual_candidate_count"),
                "active_event_slugs": monitor.get("active_event_slugs") or [],
            },
            "profile_registry": {
                "known_profile_count": len(self._profiles),
                "active_profile_count": sum(1 for state in self._profiles.values() if state.active),
                "snapshot_profile_count": report.get("profile_snapshot_count"),
                "active_signal_count": report.get("active_signal_count"),
                "candidate_count": report.get("candidate_count"),
                "scored_profile_count": sum(1 for state in self._profiles.values() if state.latest_score is not None),
                "grade_counts": _grade_counts(self._profiles.values()),
            },
            "profile_discovery": {
                "profile_seed_count": report.get("profile_seed_count"),
                "profile_snapshot_source": report.get("profile_snapshot_source") or "fresh_profile_signal_report",
                "profile_snapshot_source_generated_at_utc": report.get("profile_snapshot_source_generated_at_utc"),
                "registry": report.get("profile_registry") or [],
                "top_holder_discovery": report.get("top_holder_discovery") or {},
                "scraped_profile_discovery": report.get("scraped_profile_discovery") or {},
            },
            "profiles": profiles,
            "active_signals": report.get("active_signals") or [],
            "aggregated_candidates": report.get("aggregated_candidates") or [],
            "observed_candidates": monitor.get("observed_candidates") or [],
            "eligible_candidates": monitor.get("eligible_manual_candidates") or [],
            "pulse": monitor.get("pulse") or {},
            "blockers": report.get("blockers") or [],
            "boundary": {
                "orders_allowed": False,
                "live_trading_authorized": False,
                "execution_authority": "none",
                "description": "Read-only signal feed for downstream trading systems.",
            },
        }
        if include_full_monitor:
            payload["raw_monitor"] = monitor
        return _strict_jsonable(payload)

    def _profile_state_payload(self, state: ProfileRuntimeState) -> dict[str, Any]:
        payload = asdict(state)
        payload["age_seconds"] = _seconds_between(state.first_seen_at_utc, state.last_seen_at_utc)
        payload["grade_changed"] = bool(state.previous_grade and state.latest_grade and state.previous_grade != state.latest_grade)
        payload["score_drift_direction"] = _score_drift_direction(state.score_delta)
        payload["attribution"] = dict(self._attribution.get(state.identity) or state.attribution or _empty_attribution())
        return payload

    def _profile_identities_from_signals(self, signals: list[dict[str, Any]]) -> list[str]:
        identities: list[str] = []
        for signal in signals:
            wallet = _text(signal.get("proxy_wallet"))
            name = _text(signal.get("profile_name"))
            if wallet:
                identities.append(f"wallet:{wallet.lower()}")
            elif name:
                identities.append(f"name:{name.lower()}")
        return identities

    def _active_profile_identities_locked(self, *, now_utc: datetime, active_profile_ttl_seconds: int) -> list[str]:
        active: list[str] = []
        for identity, state in self._profiles.items():
            is_active, _ = _active_profile_status(
                state=state,
                generated_at=now_utc,
                active_profile_ttl_seconds=active_profile_ttl_seconds,
            )
            if is_active:
                active.append(identity)
        return sorted(active)


def _default_tick_builder(**kwargs: Any) -> dict[str, Any]:
    from crypto_options_app.pipelines.options.profile_signal_monitor import build_profile_signal_monitor_tick

    return build_profile_signal_monitor_tick(**kwargs)


def _default_pulse_builder(**kwargs: Any) -> dict[str, Any]:
    from crypto_options_app.pipelines.options.profile_signals import refresh_profile_signal_report_from_cache

    profile_report = dict(kwargs["profile_report"])
    active_profile_identities = set(kwargs.get("active_profile_identities") or [])
    profiles = [
        row
        for row in profile_report.get("profiles") or []
        if isinstance(row, dict) and _profile_identity(row) in active_profile_identities
    ]
    pulse_report = dict(profile_report)
    pulse_report["profiles"] = profiles
    pulse_report["profile_snapshot_count"] = len(profiles)
    config = kwargs.get("config") or SignalProducerConfig()
    return refresh_profile_signal_report_from_cache(
        pulse_report,
        active_event_slugs=list(kwargs.get("active_event_slugs") or []),
        active_condition_ids=list(kwargs.get("active_condition_ids") or []),
        now_utc=kwargs.get("now_utc"),
        activity_pages=1,
        page_limit=int(getattr(config, "pulse_page_limit", 50)),
        max_workers=int(getattr(config, "max_workers", 8)),
    )


def _empty_attribution() -> dict[str, Any]:
    return {
        "linked_outcome_count": 0,
        "settled_outcome_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "allocated_pnl_usd": 0.0,
        "full_signal_pnl_usd": 0.0,
        "latest_outcome_at_utc": None,
        "strategies": defaultdict(int),
        "price_buckets": defaultdict(int),
    }


def _profile_identity(snapshot: dict[str, Any]) -> str:
    profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
    wallet = _text(profile.get("proxy_wallet") or profile.get("proxyWallet"))
    if wallet:
        return f"wallet:{wallet.lower()}"
    name = _text(profile.get("name"))
    if name:
        return f"name:{name.lower()}"
    ref = snapshot.get("profile_ref") if isinstance(snapshot.get("profile_ref"), dict) else {}
    normalized = _text(ref.get("normalized_ref") or ref.get("raw_ref"))
    return f"ref:{normalized or json.dumps(ref, sort_keys=True)}"


def _latest_signal_time(signals: list[dict[str, Any]]) -> str | None:
    times = [_text(row.get("signal_at_utc")) for row in signals]
    times = [item for item in times if item]
    return max(times) if times else None


def _merge_profile_report(base_report: dict[str, Any], pulse_report: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_report)
    profiles_by_identity = {
        _profile_identity(row): dict(row)
        for row in base_report.get("profiles") or []
        if isinstance(row, dict)
    }
    for row in pulse_report.get("profiles") or []:
        if isinstance(row, dict):
            profiles_by_identity[_profile_identity(row)] = dict(row)
    profiles = list(profiles_by_identity.values())
    active_signals = [row for row in pulse_report.get("active_signals") or [] if isinstance(row, dict)]
    candidates = [row for row in pulse_report.get("aggregated_candidates") or [] if isinstance(row, dict)]
    merged.update(
        {
            "schema_version": pulse_report.get("schema_version") or base_report.get("schema_version"),
            "generated_at_utc": pulse_report.get("generated_at_utc") or base_report.get("generated_at_utc"),
            "profile_snapshot_source": "active_profile_pulse",
            "profile_snapshot_source_generated_at_utc": base_report.get("generated_at_utc"),
            "profile_snapshot_count": len(profiles),
            "active_signal_count": len(active_signals),
            "candidate_count": len(candidates),
            "profiles": profiles,
            "active_signals": active_signals,
            "aggregated_candidates": candidates,
            "blockers": list(base_report.get("blockers") or []) + list(pulse_report.get("blockers") or []),
        }
    )
    return merged


def _latest_iso_text(*values: str | None) -> str | None:
    parsed = [(value, _parse_dt(value)) for value in values if value]
    parsed = [(value, dt) for value, dt in parsed if dt is not None]
    if not parsed:
        return next((value for value in values if value), None)
    return max(parsed, key=lambda item: item[1])[0]


def _is_newer_iso(candidate: str | None, baseline: str | None) -> bool:
    candidate_dt = _parse_dt(candidate)
    if candidate_dt is None:
        return False
    baseline_dt = _parse_dt(baseline)
    return baseline_dt is None or candidate_dt > baseline_dt


def _active_profile_status(
    *,
    state: ProfileRuntimeState,
    generated_at: datetime,
    active_profile_ttl_seconds: int,
) -> tuple[bool, str | None]:
    if state.active_signal_count > 0:
        return True, "active_recent_signal"
    latest_trade = _parse_dt(state.last_trade_seen_at_utc)
    if latest_trade is not None and 0 <= (generated_at - latest_trade).total_seconds() <= max(active_profile_ttl_seconds, 0):
        return True, "active_recent_trade"
    return False, "inactive_no_recent_trade"


def _grade_counts(states: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state in states:
        grade = state.latest_grade or "U"
        counts[grade] = counts.get(grade, 0) + 1
    return dict(sorted(counts.items()))


def _score_drift_direction(delta: float | None) -> str:
    if delta is None:
        return "unknown"
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def _price_bucket(price: float | None) -> str | None:
    if price is None:
        return None
    buckets = [
        (0.0, 0.10),
        (0.10, 0.25),
        (0.25, 0.40),
        (0.40, 0.55),
        (0.55, 0.70),
        (0.70, 0.90),
        (0.90, 1.0),
    ]
    for low, high in buckets:
        if low <= price < high or (high == 1.0 and price <= high):
            return f"{low:.2f}-{high:.2f}"
    return "out_of_range"


def _seconds_between(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strict_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _strict_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_strict_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


_GLOBAL_SIGNAL_PRODUCER = CryptoOptionsSignalProducer()


def get_crypto_options_signal_producer() -> CryptoOptionsSignalProducer:
    return _GLOBAL_SIGNAL_PRODUCER
