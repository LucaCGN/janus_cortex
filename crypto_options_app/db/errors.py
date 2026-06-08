from __future__ import annotations


def is_transient_database_error(exc: BaseException) -> bool:
    """Return true for retryable SQLite/Postgres lock and serialization errors."""

    text = f"{type(exc).__name__}:{exc}".lower()
    return (
        ("operationalerror" in text and ("locked" in text or "busy" in text))
        or "database is locked" in text
        or "database table is locked" in text
        or "database is busy" in text
        or "deadlockdetected" in text
        or "deadlock detected" in text
        or "locknotavailable" in text
        or "could not obtain lock" in text
        or "canceling statement due to statement timeout" in text
        or "lock timeout" in text
        or "serializationfailure" in text
    )


def is_database_full_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}:{exc}".lower()
    return "database or disk is full" in text
