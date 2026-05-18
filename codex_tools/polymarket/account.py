"""Read-only direct Polymarket account snapshot helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT

ACCOUNT_SNAPSHOT_SCHEMA_VERSION = "polymarket_account_read_snapshot_v1"


@dataclass(frozen=True)
class PolymarketAccountReadSnapshot:
    schema_version: str
    status: str
    account_id: str
    wallet_address: str
    read_at_utc: str
    open_orders: list[dict[str, Any]]
    open_positions: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    open_order_count: int
    open_position_count: int
    trade_count: int
    section_status: dict[str, str]
    errors: list[dict[str, str]]
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


Reader = Callable[..., list[Any]]


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _jsonable_item(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        dumped = legacy_dict()
        if isinstance(dumped, dict):
            return dumped
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return dict(attrs)
    return {"value": value}


def _jsonable_rows(values: list[Any]) -> list[dict[str, Any]]:
    return [_jsonable_item(value) for value in values]


def _load_credentials_from_env() -> Any:
    from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials

    return PolymarketCredentials.from_env()


def _default_position_reader() -> Reader:
    from app.data.nodes.polymarket.blockchain.manage_portfolio import view_open_positions

    return view_open_positions


def _default_order_reader() -> Reader:
    from app.data.nodes.polymarket.blockchain.manage_portfolio import view_orders

    return view_orders


def _default_trade_reader() -> Reader:
    from app.data.nodes.polymarket.blockchain.manage_portfolio import view_trades

    return view_trades


def _section_error(section: str, exc: Exception) -> dict[str, str]:
    return {"section": section, "error": str(exc)}


def _overall_status(section_status: dict[str, str], errors: list[dict[str, str]]) -> str:
    if errors:
        return "read_only_snapshot_error"
    ok_count = sum(1 for status in section_status.values() if status == "ok")
    blocked_count = sum(1 for status in section_status.values() if status.startswith("blocked_"))
    if ok_count and blocked_count:
        return "read_only_snapshot_partial"
    if blocked_count and not ok_count:
        return "blocked_missing_account_credentials"
    return "read_only_snapshot"


def read_account_snapshot(
    *,
    creds: Any | None = None,
    account_id: str | None = None,
    event_slug: str | None = None,
    min_position_size: float = 0.0,
    include_trades: bool = True,
    position_reader: Reader | None = None,
    order_reader: Reader | None = None,
    trade_reader: Reader | None = None,
    read_at_utc: datetime | None = None,
) -> PolymarketAccountReadSnapshot:
    """Read direct Polymarket account state without preparing or submitting orders."""

    credentials = creds if creds is not None else _load_credentials_from_env()
    wallet_address = str(getattr(credentials, "wallet_address", "") or "")
    private_key = str(getattr(credentials, "private_key", "") or "")
    resolved_account_id = str(account_id or wallet_address or getattr(credentials, "funder_address", "") or "")
    timestamp = _coerce_utc(read_at_utc).isoformat().replace("+00:00", "Z")

    section_status: dict[str, str] = {}
    errors: list[dict[str, str]] = []
    open_orders: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    if wallet_address:
        try:
            reader = position_reader or _default_position_reader()
            open_positions = _jsonable_rows(
                reader(credentials, event_slug=event_slug, min_size=min_position_size)
            )
            section_status["open_positions"] = "ok"
        except Exception as exc:  # noqa: BLE001
            section_status["open_positions"] = "error"
            errors.append(_section_error("open_positions", exc))
    else:
        section_status["open_positions"] = "blocked_missing_wallet_address"

    if private_key:
        try:
            reader = order_reader or _default_order_reader()
            open_orders = _jsonable_rows(reader(credentials, open_only=True))
            section_status["open_orders"] = "ok"
        except Exception as exc:  # noqa: BLE001
            section_status["open_orders"] = "error"
            errors.append(_section_error("open_orders", exc))
    else:
        section_status["open_orders"] = "blocked_missing_clob_credentials"

    if not include_trades:
        section_status["trades"] = "skipped"
    elif private_key:
        try:
            reader = trade_reader or _default_trade_reader()
            trades = _jsonable_rows(reader(credentials))
            section_status["trades"] = "ok"
        except Exception as exc:  # noqa: BLE001
            section_status["trades"] = "error"
            errors.append(_section_error("trades", exc))
    else:
        section_status["trades"] = "blocked_missing_clob_credentials"

    return PolymarketAccountReadSnapshot(
        schema_version=ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
        status=_overall_status(section_status, errors),
        account_id=resolved_account_id,
        wallet_address=wallet_address,
        read_at_utc=timestamp,
        open_orders=open_orders,
        open_positions=open_positions,
        trades=trades,
        open_order_count=len(open_orders),
        open_position_count=len(open_positions),
        trade_count=len(trades),
        section_status=section_status,
        errors=errors,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


__all__ = [
    "ACCOUNT_SNAPSHOT_SCHEMA_VERSION",
    "PolymarketAccountReadSnapshot",
    "read_account_snapshot",
]
