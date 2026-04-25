from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.contracts import WINDOWS_LOCAL_ROOT


LIVE_PRIMARY_CONTROLLER = "controller_vnext_unified_v1 :: balanced"
LIVE_FALLBACK_CONTROLLER = "controller_vnext_deterministic_v1 :: tight"
LIVE_EXECUTION_PROFILE_VERSION = "v1"
LIVE_RUN_ROOT_SUFFIX = Path("tracks") / "live-controller"
LIVE_DEFAULT_RUN_ID = "live-2026-04-23-v1"
LIVE_DEFAULT_POLL_MS = 5000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_live_tracks_root() -> Path:
    configured_root = os.getenv("JANUS_LOCAL_ROOT")
    if configured_root:
        return Path(configured_root) / LIVE_RUN_ROOT_SUFFIX
    if WINDOWS_LOCAL_ROOT.exists():
        return WINDOWS_LOCAL_ROOT / LIVE_RUN_ROOT_SUFFIX
    return Path("output") / "live-controller"


class LiveRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default=LIVE_DEFAULT_RUN_ID)
    execution_profile_version: str = Field(default=LIVE_EXECUTION_PROFILE_VERSION)
    controller_name: str = Field(default=LIVE_PRIMARY_CONTROLLER)
    fallback_controller_name: str = Field(default=LIVE_FALLBACK_CONTROLLER)
    game_ids: list[str] = Field(default_factory=list)
    dry_run: bool = Field(default=True)
    entries_enabled: bool = Field(default=True)
    poll_interval_live_sec: float = Field(default=5.0, ge=1.0, le=60.0)
    poll_interval_idle_sec: float = Field(default=15.0, ge=1.0, le=120.0)
    stop_loss_mode: str = Field(default="market_on_local_trigger")
    account_id: str | None = Field(default=None)
    notes: str | None = Field(default=None)

    def run_root(self) -> Path:
        return resolve_live_tracks_root() / utc_now().date().isoformat() / self.run_id


class LiveRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default=LIVE_DEFAULT_RUN_ID)
    execution_profile_version: str = Field(default=LIVE_EXECUTION_PROFILE_VERSION)
    controller_name: str = Field(default=LIVE_PRIMARY_CONTROLLER)
    fallback_controller_name: str = Field(default=LIVE_FALLBACK_CONTROLLER)
    game_ids: list[str] = Field(default_factory=list)
    dry_run: bool = Field(default=True)
    entries_enabled: bool = Field(default=True)
    poll_interval_live_sec: float = Field(default=5.0, ge=1.0, le=60.0)
    poll_interval_idle_sec: float = Field(default=15.0, ge=1.0, le=120.0)
    stop_loss_mode: str = Field(default="market_on_local_trigger")
    account_id: str | None = Field(default=None)
    notes: str | None = Field(default=None)

    def to_config(self) -> LiveRunConfig:
        return LiveRunConfig(**self.model_dump())


def build_live_order_metadata(
    *,
    config: LiveRunConfig,
    controller_name: str,
    controller_source: str,
    game_id: str,
    market_id: str,
    outcome_id: str,
    strategy_family: str,
    signal_id: str,
    signal_price: float | None,
    signal_timestamp: Any,
    entry_reason: str,
    stop_price: float | None,
    order_policy: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": config.run_id,
        "execution_profile_version": config.execution_profile_version,
        "controller_name": controller_name,
        "controller_source": controller_source,
        "game_id": game_id,
        "market_id": market_id,
        "outcome_id": outcome_id,
        "strategy_family": strategy_family,
        "signal_id": signal_id,
        "signal_price": signal_price,
        "signal_timestamp": signal_timestamp,
        "entry_reason": entry_reason,
        "stop_price": stop_price,
        "order_policy": order_policy,
    }
    if extra:
        payload.update(extra)
    return to_jsonable(payload)


__all__ = [
    "LIVE_DEFAULT_POLL_MS",
    "LIVE_DEFAULT_RUN_ID",
    "LIVE_EXECUTION_PROFILE_VERSION",
    "LIVE_FALLBACK_CONTROLLER",
    "LIVE_PRIMARY_CONTROLLER",
    "LiveRunConfig",
    "LiveRunCreateRequest",
    "build_live_order_metadata",
    "resolve_live_tracks_root",
    "utc_now",
]
