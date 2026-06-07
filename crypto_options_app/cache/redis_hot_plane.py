from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from crypto_options_app.config import DEFAULT_CONFIG


@dataclass(frozen=True)
class RedisHotPlaneSettings:
    host: str = "127.0.0.1"
    port: int = 56379
    database: int = 0
    enabled: bool = False

    @classmethod
    def from_url(cls, url: str, *, enabled: bool | None = None) -> "RedisHotPlaneSettings":
        parsed = urlparse(url)
        if parsed.scheme != "redis":
            raise ValueError("Redis URL must use redis://")
        database = int((parsed.path or "/0").lstrip("/") or "0")
        return cls(
            host=parsed.hostname or "127.0.0.1",
            port=int(parsed.port or 6379),
            database=database,
            enabled=DEFAULT_CONFIG.redis_enabled if enabled is None else enabled,
        )


def check_redis_hot_plane(
    settings: RedisHotPlaneSettings | None = None,
    *,
    timeout_seconds: float = 1.0,
) -> dict[str, Any]:
    settings = settings or RedisHotPlaneSettings.from_url(DEFAULT_CONFIG.redis_url)
    if not settings.enabled:
        return {
            "status": "disabled",
            "enabled": False,
            "role": "gated_hot_plane_candidate",
            "host": settings.host,
            "port": settings.port,
            "database": settings.database,
        }
    try:
        with socket.create_connection((settings.host, settings.port), timeout=timeout_seconds) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = sock.recv(16)
    except OSError as exc:
        return {
            "status": "blocked",
            "enabled": True,
            "blockers": [f"redis_unavailable:{type(exc).__name__}:{exc}"],
            "role": "gated_hot_plane_candidate",
            "host": settings.host,
            "port": settings.port,
            "database": settings.database,
        }
    ok = response.startswith(b"+PONG")
    return {
        "status": "ok" if ok else "blocked",
        "enabled": True,
        "blockers": [] if ok else [f"redis_ping_unexpected_response:{response!r}"],
        "role": "gated_hot_plane_candidate",
        "host": settings.host,
        "port": settings.port,
        "database": settings.database,
    }
