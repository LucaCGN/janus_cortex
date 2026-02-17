from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[3]
_DOTENV_PATH = REPO_ROOT / ".env"
if _DOTENV_PATH.exists():
    load_dotenv(_DOTENV_PATH)


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout: int = 10
    sslmode: str | None = None

    def as_connect_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout,
        }
        if self.sslmode:
            kwargs["sslmode"] = self.sslmode
        return kwargs


def _read_required_env(primary: str, fallback: str | None = None) -> str | None:
    value = os.getenv(primary)
    if value:
        return value
    if fallback:
        fallback_value = os.getenv(fallback)
        if fallback_value:
            return fallback_value
    return None


def get_postgres_settings() -> PostgresSettings:
    host = _read_required_env("JANUS_POSTGRES_HOST", "PGHOST")
    port_raw = _read_required_env("JANUS_POSTGRES_PORT", "PGPORT")
    database = _read_required_env("JANUS_POSTGRES_DB", "PGDATABASE")
    user = _read_required_env("JANUS_POSTGRES_USER", "PGUSER")
    password = _read_required_env("JANUS_POSTGRES_PASSWORD", "PGPASSWORD")

    missing = [
        name
        for name, value in (
            ("JANUS_POSTGRES_HOST", host),
            ("JANUS_POSTGRES_PORT", port_raw),
            ("JANUS_POSTGRES_DB", database),
            ("JANUS_POSTGRES_USER", user),
            ("JANUS_POSTGRES_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"Missing required Postgres environment variables: {missing_text}")

    try:
        port = int(port_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("JANUS_POSTGRES_PORT must be a valid integer") from exc

    connect_timeout_raw = os.getenv("JANUS_POSTGRES_CONNECT_TIMEOUT", "10")
    try:
        connect_timeout = int(connect_timeout_raw)
    except ValueError as exc:
        raise ValueError("JANUS_POSTGRES_CONNECT_TIMEOUT must be a valid integer") from exc

    return PostgresSettings(
        host=host,  # type: ignore[arg-type]
        port=port,
        database=database,  # type: ignore[arg-type]
        user=user,  # type: ignore[arg-type]
        password=password,  # type: ignore[arg-type]
        connect_timeout=connect_timeout,
        sslmode=os.getenv("JANUS_POSTGRES_SSLMODE"),
    )

