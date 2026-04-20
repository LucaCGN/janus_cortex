from __future__ import annotations

import os
from dataclasses import dataclass


SAFE_DB_TARGETS = {"disposable", "dev_clone"}
UNSAFE_DB_TARGETS = {"shared_live", "live", "production"}
DEFAULT_DB_TARGET = "default"
ALLOW_UNSAFE_DB_TESTS_ENV = "JANUS_ALLOW_UNSAFE_DB_TESTS"

_TARGET_ALIASES = {
    "default": DEFAULT_DB_TARGET,
    "local": "disposable",
    "disposable": "disposable",
    "sandbox": "disposable",
    "dev": "dev_clone",
    "dev_clone": "dev_clone",
    "dev-clone": "dev_clone",
    "clone": "dev_clone",
    "shared_live": "shared_live",
    "shared-live": "shared_live",
    "live": "shared_live",
    "prod": "shared_live",
    "production": "shared_live",
}


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    name: str
    safe_for_db_tests: bool
    description: str


def normalize_database_target(value: str | None) -> str:
    if value is None:
        return DEFAULT_DB_TARGET
    raw = str(value).strip().lower()
    if not raw:
        return DEFAULT_DB_TARGET
    return _TARGET_ALIASES.get(raw, raw.replace("-", "_"))


def get_database_target_name() -> str:
    return normalize_database_target(
        os.getenv("JANUS_DB_TARGET") or os.getenv("JANUS_POSTGRES_TARGET")
    )


def _describe_target(name: str) -> str:
    if name == "disposable":
        return "disposable local Postgres created from migrations only"
    if name == "dev_clone":
        return "non-live clone of shared data used for realistic validation"
    if name in UNSAFE_DB_TARGETS:
        return "shared live or production-like database"
    return "unclassified database target"


def get_database_target() -> DatabaseTarget:
    name = get_database_target_name()
    return DatabaseTarget(
        name=name,
        safe_for_db_tests=name in SAFE_DB_TARGETS or os.getenv(ALLOW_UNSAFE_DB_TESTS_ENV) == "1",
        description=_describe_target(name),
    )


def is_safe_db_test_target(*, target_name: str | None = None) -> bool:
    if os.getenv(ALLOW_UNSAFE_DB_TESTS_ENV) == "1":
        return True
    name = normalize_database_target(target_name) if target_name is not None else get_database_target_name()
    return name in SAFE_DB_TARGETS


def require_safe_db_test_target(action: str, *, target_name: str | None = None) -> str:
    name = normalize_database_target(target_name) if target_name is not None else get_database_target_name()
    if is_safe_db_test_target(target_name=name):
        return name
    raise RuntimeError(
        f"{action} requires JANUS_DB_TARGET=disposable or dev_clone. "
        f"Current target is '{name}'. Set {ALLOW_UNSAFE_DB_TESTS_ENV}=1 only for an explicit override."
    )


def describe_database_target() -> dict[str, object]:
    target = get_database_target()
    return {
        "name": target.name,
        "safe_for_db_tests": target.safe_for_db_tests,
        "description": target.description,
        "override_env": ALLOW_UNSAFE_DB_TESTS_ENV,
    }


__all__ = [
    "ALLOW_UNSAFE_DB_TESTS_ENV",
    "DEFAULT_DB_TARGET",
    "SAFE_DB_TARGETS",
    "DatabaseTarget",
    "describe_database_target",
    "get_database_target",
    "get_database_target_name",
    "is_safe_db_test_target",
    "normalize_database_target",
    "require_safe_db_test_target",
]
