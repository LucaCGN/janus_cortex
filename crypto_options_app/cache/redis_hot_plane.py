from __future__ import annotations

import socket
import json
from dataclasses import dataclass
from typing import Any, Callable
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


RedisCommandExecutor = Callable[[tuple[str, ...]], Any]


class RedisHotPlaneClient:
    """Small Redis hot-plane adapter for TTL cache and queue ownership only."""

    def __init__(
        self,
        settings: RedisHotPlaneSettings | None = None,
        *,
        command_executor: RedisCommandExecutor | None = None,
        timeout_seconds: float = 1.0,
        namespace: str = "crypto_options",
    ) -> None:
        self.settings = settings or RedisHotPlaneSettings.from_url(DEFAULT_CONFIG.redis_url)
        self._command_executor = command_executor
        self.timeout_seconds = timeout_seconds
        self.namespace = namespace.strip(":") or "crypto_options"

    def set_json_cache(self, key: str, payload: dict[str, Any], *, ttl_seconds: int) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        response = self._execute("SET", self._key("cache", key), encoded, "EX", str(int(ttl_seconds)))
        if _is_disabled_response(response):
            return response
        return {"status": "ok" if response == "OK" else "blocked", "response": response}

    def get_json_cache(self, key: str) -> dict[str, Any]:
        response = self._execute("GET", self._key("cache", key))
        if _is_disabled_response(response):
            return response
        if response is None:
            return {"status": "miss", "payload": None}
        if not isinstance(response, str):
            return {"status": "blocked", "payload": None, "blockers": [f"unexpected_cache_response:{response!r}"]}
        return {"status": "ok", "payload": json.loads(response)}

    def acquire_ttl_lock(self, key: str, owner: str, *, ttl_seconds: int) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        response = self._execute("SET", self._key("lock", key), owner, "NX", "EX", str(int(ttl_seconds)))
        if _is_disabled_response(response):
            return response
        return {"status": "ok" if response == "OK" else "held", "acquired": response == "OK", "owner": owner}

    def release_ttl_lock(self, key: str, owner: str) -> dict[str, Any]:
        script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] "
            "then return redis.call('DEL', KEYS[1]) else return 0 end"
        )
        response = self._execute("EVAL", script, "1", self._key("lock", key), owner)
        if _is_disabled_response(response):
            return response
        released = response == 1
        return {"status": "ok" if released else "not_owner", "released": released, "owner": owner}

    def _key(self, kind: str, key: str) -> str:
        clean_key = str(key).strip(":")
        if not clean_key:
            raise ValueError("Redis hot-plane key must not be empty")
        return f"{self.namespace}:{kind}:{clean_key}"

    def _execute(self, *parts: str) -> Any:
        if not self.settings.enabled:
            return {
                "status": "disabled",
                "enabled": False,
                "role": "gated_hot_plane_candidate",
            }
        if self._command_executor is not None:
            return self._command_executor(tuple(str(part) for part in parts))
        return _execute_redis_command(self.settings, tuple(str(part) for part in parts), timeout_seconds=self.timeout_seconds)


def _is_disabled_response(response: Any) -> bool:
    return isinstance(response, dict) and response.get("status") == "disabled"


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
        response = _execute_redis_command(settings, ("PING",), timeout_seconds=timeout_seconds)
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
    ok = response == "PONG"
    return {
        "status": "ok" if ok else "blocked",
        "enabled": True,
        "blockers": [] if ok else [f"redis_ping_unexpected_response:{response!r}"],
        "role": "gated_hot_plane_candidate",
        "host": settings.host,
        "port": settings.port,
        "database": settings.database,
    }


def _execute_redis_command(
    settings: RedisHotPlaneSettings,
    parts: tuple[str, ...],
    *,
    timeout_seconds: float = 1.0,
) -> Any:
    with socket.create_connection((settings.host, settings.port), timeout=timeout_seconds) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendall(_encode_resp_command(parts))
        return _read_resp_response(sock)


def _encode_resp_command(parts: tuple[str, ...]) -> bytes:
    output = [f"*{len(parts)}\r\n".encode("utf-8")]
    for part in parts:
        raw = str(part).encode("utf-8")
        output.append(f"${len(raw)}\r\n".encode("utf-8"))
        output.append(raw)
        output.append(b"\r\n")
    return b"".join(output)


def _read_resp_response(sock: socket.socket) -> Any:
    prefix = sock.recv(1)
    if prefix == b"+":
        return _read_resp_line(sock).decode("utf-8", errors="replace")
    if prefix == b":":
        return int(_read_resp_line(sock).decode("ascii"))
    if prefix == b"$":
        length = int(_read_resp_line(sock).decode("ascii"))
        if length < 0:
            return None
        data = _recv_exact(sock, length)
        _recv_exact(sock, 2)
        return data.decode("utf-8", errors="replace")
    if prefix == b"-":
        raise OSError(_read_resp_line(sock).decode("utf-8", errors="replace"))
    raise OSError(f"unsupported_redis_response_prefix:{prefix!r}")


def _read_resp_line(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise OSError("redis_response_closed")
        chunks.append(chunk)
        if len(chunks) >= 2 and chunks[-2:] == [b"\r", b"\n"]:
            return b"".join(chunks[:-2])


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("redis_response_closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
