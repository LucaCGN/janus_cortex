from __future__ import annotations

import os
from pathlib import Path


DEFAULT_LOCAL_ROOT_ENV_VAR = "JANUS_LOCAL_ROOT"
LEGACY_WINDOWS_LOCAL_ROOT = Path(r"C:\code-personal\janus-local\janus_cortex")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_local_root() -> Path:
    return repo_root() / "local"


def resolve_local_root() -> Path:
    configured = os.getenv(DEFAULT_LOCAL_ROOT_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return default_local_root()


def resolve_shared_root() -> Path:
    return resolve_local_root() / "shared"


def resolve_live_tracks_root() -> Path:
    return resolve_local_root() / "tracks" / "live-controller"


def resolve_output_root() -> Path:
    return resolve_local_root() / "archives" / "output"


__all__ = [
    "DEFAULT_LOCAL_ROOT_ENV_VAR",
    "LEGACY_WINDOWS_LOCAL_ROOT",
    "default_local_root",
    "repo_root",
    "resolve_live_tracks_root",
    "resolve_local_root",
    "resolve_output_root",
    "resolve_shared_root",
]
