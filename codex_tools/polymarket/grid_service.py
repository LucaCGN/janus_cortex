"""Preview-only 1c grid service planning for global Polymarket positions.

The planner is intentionally inert. It turns a direct account snapshot into a
reviewable service-spawn plan, but it does not prepare, sign, submit, cancel,
or replace orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT

GRID_SERVICE_SCHEMA_VERSION = "polymarket_grid_service_preview_v1"

_COVERED_BASKETBALL_MARKERS = (" nba", "nba-", "wnba", "women's national basketball", "spurs vs.", "thunder")
_OTHER_BASKETBALL_MARKERS = (
    "basketball",
    "euroleague",
    "fiba",
    "liga acb",
    "nbl",
    "kbl",
    "cba",
    "bbl",
    "ncaa",
)
_CATEGORY_MARKERS = {
    "science_aliens": ("alien", "ufo", "uap", "extraterrestrial"),
    "geopolitics": ("geopolitic", "iran", "israel", "russia", "ukraine", "china", "taiwan", "hormuz", "war"),
    "elections": ("election", "runoff", "president", "senate", "mayor", "minister"),
    "ai_models": ("openai", "anthropic", "google ai", "gpt", "claude", "gemini", "ai model"),
    "economics": ("fed", "rate", "inflation", "cpi", "gdp", "recession", "oil", "tariff"),
}


@dataclass(frozen=True)
class PolymarketGridCandidate:
    status: str
    category: str
    market_slug: str
    title: str
    token_id: str
    side: str
    size: str
    average_price: str | None
    current_price: str | None
    pnl_percent: str | None
    absolute_move_percent: str | None
    existing_open_order_count: int
    existing_open_order_prices: list[str]
    grid_step_cents: int
    proposed_next_leg: dict[str, Any]
    required_before_service_spawn: list[str]
    rationale: str


@dataclass(frozen=True)
class PolymarketGridServicePreview:
    schema_version: str
    status: str
    generated_at_utc: str
    source_snapshot_status: str | None
    min_abs_pnl_percent: str
    grid_step_cents: int
    candidate_count: int
    candidates: list[dict[str, Any]]
    skipped_count: int
    skipped: list[dict[str, Any]]
    service_spawn_authorized: bool
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _coerce_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_decimal(item: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = _decimal(item.get(key))
        if value is not None:
            return value
    return None


def _token_id(item: dict[str, Any]) -> str:
    return _first_text(item, ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId"))


def _market_slug(item: dict[str, Any]) -> str:
    return _first_text(item, ("market_slug", "event_slug", "slug", "market"))


def _title(item: dict[str, Any]) -> str:
    return _first_text(item, ("title", "market_title", "event_title", "question", "name"))


def _classify_position(position: dict[str, Any]) -> tuple[str, bool]:
    haystack = " ".join(
        value.lower()
        for value in (
            _title(position),
            _market_slug(position),
            str(position.get("category") or ""),
            str(position.get("description") or ""),
        )
        if value
    )
    if any(marker in haystack for marker in _COVERED_BASKETBALL_MARKERS):
        return "covered_basketball", True
    if any(marker in haystack for marker in _OTHER_BASKETBALL_MARKERS):
        return "other_basketball", False
    for category, markers in _CATEGORY_MARKERS.items():
        if any(marker in haystack for marker in markers):
            return category, False
    return "uncategorized", False


def _pnl_percent(position: dict[str, Any]) -> Decimal | None:
    explicit = _first_decimal(
        position,
        (
            "pnl_percent",
            "percent_pnl",
            "percentPnl",
            "totalPercentChange",
            "unrealized_pnl_percent",
            "percent_change",
        ),
    )
    if explicit is not None:
        return explicit
    average_price = _first_decimal(position, ("average_price", "avg_price", "avgPrice", "entry_price"))
    current_price = _first_decimal(position, ("current_price", "cur_price", "curPrice", "market_price", "price"))
    if average_price is None or current_price is None or average_price == 0:
        return None
    return ((current_price - average_price) / average_price) * Decimal("100")


def _open_orders_for_token(open_orders: list[dict[str, Any]], token_id: str) -> list[dict[str, Any]]:
    if not token_id:
        return []
    return [order for order in open_orders if _token_id(order) == token_id]


def _order_price(order: dict[str, Any]) -> str:
    price = _first_decimal(order, ("price", "limit_price", "limitPrice"))
    return str(price) if price is not None else ""


def _proposed_leg(
    *,
    position: dict[str, Any],
    current_price: Decimal | None,
    grid_step_cents: int,
) -> dict[str, Any]:
    step = Decimal(grid_step_cents) / Decimal("100")
    size = _first_decimal(position, ("size", "quantity", "shares", "balance")) or Decimal("0")
    if current_price is None:
        return {
            "action": "review_only",
            "reason": "current_price_missing",
            "order_preparation_allowed": False,
        }
    sell_price = min(current_price + step, Decimal("0.99"))
    rebuy_price = max(current_price - step, Decimal("0.01"))
    return {
        "action": "grid_sell_then_rebuy_preview",
        "sell_limit_price": str(sell_price.quantize(Decimal("0.01"))),
        "rebuy_limit_price": str(rebuy_price.quantize(Decimal("0.01"))),
        "size": str(size),
        "order_preparation_allowed": False,
    }


def build_grid_service_preview(
    direct_truth_snapshot: dict[str, Any],
    *,
    now_utc: datetime | str | None = None,
    min_abs_pnl_percent: Decimal | float | str = Decimal("5"),
    grid_step_cents: int = 1,
    include_other_basketball: bool = True,
    include_covered_basketball: bool = False,
) -> PolymarketGridServicePreview:
    """Build an inert high-frequency grid service plan from direct account truth."""

    generated_at = _coerce_utc(now_utc).isoformat().replace("+00:00", "Z")
    threshold = _decimal(min_abs_pnl_percent) or Decimal("5")
    open_positions = list(direct_truth_snapshot.get("open_positions") or [])
    open_orders = list(direct_truth_snapshot.get("open_orders") or [])
    candidates: list[PolymarketGridCandidate] = []
    skipped: list[dict[str, Any]] = []
    required = [
        "fresh direct CLOB/account truth for the target token",
        "named global-portfolio grid risk budget",
        "kill-switch clear proof",
        "minimum-order proof for every generated leg",
        "target/stop/rebuy policy for the specific market",
        "durable ledger idempotency key before every leg",
        "approved service-spawn path with rate limits and reconciliation",
    ]

    for position in open_positions:
        if not isinstance(position, dict):
            skipped.append({"reason": "position_not_object", "position": str(position)})
            continue
        category, covered = _classify_position(position)
        if covered and not include_covered_basketball:
            skipped.append({"reason": "covered_basketball_managed_by_janus", "title": _title(position), "category": category})
            continue
        if category == "other_basketball" and not include_other_basketball:
            skipped.append({"reason": "other_basketball_scan_disabled", "title": _title(position), "category": category})
            continue

        token_id = _token_id(position)
        pnl_percent = _pnl_percent(position)
        if pnl_percent is None:
            skipped.append({"reason": "pnl_percent_unavailable", "title": _title(position), "token_id": token_id})
            continue
        absolute_move = abs(pnl_percent)
        if absolute_move < threshold:
            skipped.append(
                {
                    "reason": "move_below_threshold",
                    "title": _title(position),
                    "token_id": token_id,
                    "pnl_percent": str(pnl_percent),
                    "threshold": str(threshold),
                }
            )
            continue

        matching_orders = _open_orders_for_token(open_orders, token_id)
        average_price = _first_decimal(position, ("average_price", "avg_price", "avgPrice", "entry_price"))
        current_price = _first_decimal(position, ("current_price", "cur_price", "curPrice", "market_price", "price"))
        candidates.append(
            PolymarketGridCandidate(
                status="review_required",
                category=category,
                market_slug=_market_slug(position),
                title=_title(position),
                token_id=token_id,
                side=_first_text(position, ("side", "outcome", "outcome_name")) or "position",
                size=str(_first_decimal(position, ("size", "quantity", "shares", "balance")) or ""),
                average_price=str(average_price) if average_price is not None else None,
                current_price=str(current_price) if current_price is not None else None,
                pnl_percent=str(pnl_percent),
                absolute_move_percent=str(absolute_move),
                existing_open_order_count=len(matching_orders),
                existing_open_order_prices=[price for price in (_order_price(order) for order in matching_orders) if price],
                grid_step_cents=grid_step_cents,
                proposed_next_leg=_proposed_leg(
                    position=position,
                    current_price=current_price,
                    grid_step_cents=grid_step_cents,
                ),
                required_before_service_spawn=required,
                rationale=(
                    "Existing position has a move large enough to review for 1c sell/rebuy grid harvesting; "
                    "service execution remains disabled until all gates are proven."
                ),
            )
        )

    status = "candidate_review_required" if candidates else "no_grid_candidates"
    return PolymarketGridServicePreview(
        schema_version=GRID_SERVICE_SCHEMA_VERSION,
        status=status,
        generated_at_utc=generated_at,
        source_snapshot_status=str(direct_truth_snapshot.get("status") or direct_truth_snapshot.get("schema_version") or ""),
        min_abs_pnl_percent=str(threshold),
        grid_step_cents=grid_step_cents,
        candidate_count=len(candidates),
        candidates=[asdict(candidate) for candidate in candidates],
        skipped_count=len(skipped),
        skipped=skipped,
        service_spawn_authorized=False,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


__all__ = [
    "GRID_SERVICE_SCHEMA_VERSION",
    "PolymarketGridCandidate",
    "PolymarketGridServicePreview",
    "build_grid_service_preview",
]
