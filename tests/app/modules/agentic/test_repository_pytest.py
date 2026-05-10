from __future__ import annotations

import uuid

from app.modules.agentic import repository


def test_readable_watch_session_keys_map_to_stable_uuid_pytest() -> None:
    key = "watch-nba-nyk-phi-2026-05-10"

    first = repository._coerce_uuid(key, namespace="watch-session")  # noqa: SLF001
    second = repository._coerce_uuid(key, namespace="watch-session")  # noqa: SLF001

    assert first == second
    assert str(uuid.UUID(first)) == first


def test_uuid_watch_session_ids_are_preserved_pytest() -> None:
    value = str(uuid.uuid4())

    assert repository._coerce_uuid(value, namespace="watch-session") == value  # noqa: SLF001
