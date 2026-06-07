from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from crypto_options_app.trading.fills import FillState


@dataclass(frozen=True)
class PositionState:
    position_key: str
    strategy_id: str
    event_token_key: str
    shares: float
    cost_basis_usd: float
    status: str
    opened_at_utc: datetime
    source_fill_key: str


def create_position_from_buy_fill(fill: FillState) -> PositionState | None:
    if fill.side.upper() != "BUY":
        return None
    if not fill.reconciled:
        return None
    return PositionState(
        position_key=f"position:{fill.fill_key}",
        strategy_id=fill.strategy_id,
        event_token_key=fill.event_token_key,
        shares=fill.filled_shares,
        cost_basis_usd=round(fill.filled_shares * fill.fill_price, 6),
        status="open",
        opened_at_utc=datetime.now(UTC),
        source_fill_key=fill.fill_key,
    )
