from __future__ import annotations

import pytest

from app.data.databases.safety import (
    ALLOW_UNSAFE_DB_TESTS_ENV,
    describe_database_target,
    get_database_target_name,
    is_safe_db_test_target,
    normalize_database_target,
    require_safe_db_test_target,
)


def test_normalize_database_target_aliases_pytest() -> None:
    assert normalize_database_target(None) == "default"
    assert normalize_database_target("") == "default"
    assert normalize_database_target("local") == "disposable"
    assert normalize_database_target("dev-clone") == "dev_clone"
    assert normalize_database_target("production") == "shared_live"


def test_safe_db_target_detection_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JANUS_DB_TARGET", "disposable")
    monkeypatch.delenv(ALLOW_UNSAFE_DB_TESTS_ENV, raising=False)
    assert get_database_target_name() == "disposable"
    assert is_safe_db_test_target() is True

    monkeypatch.setenv("JANUS_DB_TARGET", "shared_live")
    assert is_safe_db_test_target() is False


def test_require_safe_db_target_can_be_overridden_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JANUS_DB_TARGET", "shared_live")
    monkeypatch.delenv(ALLOW_UNSAFE_DB_TESTS_ENV, raising=False)
    with pytest.raises(RuntimeError, match="JANUS_DB_TARGET=disposable or dev_clone"):
        require_safe_db_test_target("db integration tests")

    monkeypatch.setenv(ALLOW_UNSAFE_DB_TESTS_ENV, "1")
    assert require_safe_db_test_target("db integration tests") == "shared_live"


def test_describe_database_target_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JANUS_DB_TARGET", "dev_clone")
    payload = describe_database_target()
    assert payload["name"] == "dev_clone"
    assert payload["safe_for_db_tests"] is True
    assert "non-live clone" in str(payload["description"])
