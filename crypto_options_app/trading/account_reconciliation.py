from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable


EventOutcomeResolver = Any


@dataclass(frozen=True)
class AccountPositionSettlement:
    strategy_id: str
    event_token_key: str
    outcome: str
    shares: float
    cost_basis_usd: float
    realized_pnl_usd: float
    settled_at_utc: str
    source: str = "account_authoritative_position"
    validation_run_id: str | None = None
    account_position_id: str | None = None
    event_slug: str | None = None
    resolved_outcome: str | None = None
    raw: dict[str, Any] | None = None


def apply_account_position_settlements(
    conn: Any,
    settlements: list[AccountPositionSettlement],
    *,
    default_max_loss_usd: float = 10.0,
) -> dict[str, Any]:
    """Write account-authoritative settlement PnL into the live validation ledger.

    Polymarket auto-redeems winners but leaves losing $0 positions visible until
    redemption. The app stop gates need one normalized signal for both cases:
    realized account PnL, zero remaining open cost, and an optional hard stop.
    """

    updated_ledgers = 0
    closed_positions = 0
    missing_ledgers: list[str] = []
    for settlement in settlements:
        ledger_key = _ledger_key_for_settlement(conn, settlement)
        if ledger_key is None:
            missing_ledgers.append(_settlement_reference(settlement))
            continue
        existing = conn.execute(
            "SELECT ledger_json, budget_cap_usd, stop_reason FROM validation_budget_ledger WHERE validation_budget_ledger_key=?",
            (ledger_key,),
        ).fetchone()
        if existing is None:
            missing_ledgers.append(_settlement_reference(settlement))
            continue
        ledger_json = _json_load(existing["ledger_json"], {})
        ledger_json["account_authoritative_settlement"] = asdict(settlement)
        max_loss_usd = _strategy_loss_limit_usd(ledger_json, fallback=default_max_loss_usd)
        realized_pnl = round(float(settlement.realized_pnl_usd), 8)
        hard_stop = int(realized_pnl <= -abs(max_loss_usd))
        stop_reason = existing["stop_reason"]
        if hard_stop:
            stop_reason = _append_reason(stop_reason, "account_authoritative_loss_stop")
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            UPDATE validation_budget_ledger
               SET completed_at_utc=?,
                   realized_pnl_usd=?,
                   open_cost_usd=0,
                   remaining_validation_budget_usd=CASE
                       WHEN budget_cap_usd - notional_filled_usd + ? > 0
                       THEN budget_cap_usd - notional_filled_usd + ?
                       ELSE 0
                   END,
                   hard_stop_triggered=CASE WHEN ? = 1 THEN 1 ELSE hard_stop_triggered END,
                   stop_reason=?,
                   lifecycle_audit_status=COALESCE(lifecycle_audit_status, 'passed'),
                   reconciliation_status='reconciled',
                   ledger_json=?,
                   updated_at_utc=?
             WHERE validation_budget_ledger_key=?
            """,
            (
                settlement.settled_at_utc,
                realized_pnl,
                realized_pnl,
                realized_pnl,
                hard_stop,
                stop_reason,
                json.dumps(ledger_json, sort_keys=True, default=str),
                now,
                ledger_key,
            ),
        )
        updated_ledgers += 1
        closed_positions += _close_matching_positions(conn, settlement, now=now)
    return {
        "schema_version": "crypto_options_account_position_settlement_writeback_v1",
        "settlement_count": len(settlements),
        "updated_ledger_count": updated_ledgers,
        "closed_position_count": closed_positions,
        "missing_ledger_count": len(missing_ledgers),
        "missing_ledgers": missing_ledgers,
    }


def reconcile_pending_exchange_orders(
    conn: Any,
    *,
    open_orders: list[Any],
    trades: list[Any],
    now_dt: datetime | None = None,
    stale_after_seconds: float = 300.0,
    default_max_loss_usd: float = 10.0,
) -> dict[str, Any]:
    """Reconcile submitted CLOB orders against open-order and trade history.

    The live runner can submit a GTC paired exit that fills after the initial
    strategy pass has returned. This sweep keeps the local order, fill,
    position, and validation ledger aligned with the exchange without requiring
    a manual repair step.
    """

    now_dt = now_dt or datetime.now(UTC)
    now = now_dt.isoformat()
    open_order_ids = {
        str(_field(order, "id", "order_id", "orderID") or "").strip().lower()
        for order in open_orders
        if str(_field(order, "id", "order_id", "orderID") or "").strip()
    }
    trade_matches = _trades_by_order_id(trades)
    rows = conn.execute(
        """
        SELECT o.order_key, o.intent_key, o.exchange_order_id, o.status,
               o.order_json, o.submitted_at_utc, ei.strategy_id,
               ei.event_token_key, ei.side, ei.intent_type
          FROM orders o
          LEFT JOIN execution_intents ei ON ei.intent_key=o.intent_key
         WHERE o.exchange_order_id LIKE '0x%'
           AND (
                lower(o.status) IN ('submitted', 'partially_filled', 'open', 'live')
                OR (
                    lower(o.status)='expired'
                    AND EXISTS (
                        SELECT 1
                          FROM fills f
                          JOIN positions p ON p.position_key=('position:' || f.fill_key)
                         WHERE f.order_key=o.order_key
                           AND p.status='open'
                    )
                )
           )
         ORDER BY o.updated_at_utc ASC
         LIMIT 100
        """
    ).fetchall()
    filled_orders = 0
    expired_orders = 0
    still_open_orders = 0
    ledger_updates = 0
    closed_positions = 0
    partial_positions = 0
    checked_order_ids: list[str] = []
    for row in rows:
        order_id = str(row["exchange_order_id"] or "").strip()
        if not order_id:
            continue
        checked_order_ids.append(order_id)
        normalized_order_id = order_id.lower()
        if normalized_order_id in open_order_ids:
            still_open_orders += 1
            continue
        matched_trades = trade_matches.get(normalized_order_id, [])
        if matched_trades:
            result = _apply_pending_order_trade_match(
                conn,
                dict(row),
                matched_trades,
                now=now,
                default_max_loss_usd=float(default_max_loss_usd),
            )
            filled_orders += 1
            ledger_updates += int(result.get("ledger_updates", 0))
            closed_positions += int(result.get("closed_positions", 0))
            partial_positions += int(result.get("partial_positions", 0))
            continue
        submitted_at = _parse_datetime(row["submitted_at_utc"])
        if submitted_at is not None and submitted_at <= now_dt - timedelta(seconds=float(stale_after_seconds)):
            result = _expire_pending_order(conn, dict(row), now=now, default_max_loss_usd=float(default_max_loss_usd))
            expired_orders += 1
            ledger_updates += int(result.get("ledger_updates", 0))
            closed_positions += int(result.get("closed_positions", 0))
    return {
        "schema_version": "crypto_options_pending_exchange_order_reconciliation_v1",
        "checked_order_count": len(checked_order_ids),
        "still_open_order_count": still_open_orders,
        "filled_order_count": filled_orders,
        "expired_order_count": expired_orders,
        "updated_ledger_count": ledger_updates,
        "closed_position_count": closed_positions,
        "partial_position_count": partial_positions,
        "checked_order_ids": checked_order_ids[:20],
    }


def cancel_underpriced_paired_exit_orders(
    conn: Any,
    *,
    open_orders: list[Any],
    cancel_order_fn: Callable[[str], Any] | None = None,
    now_dt: datetime | None = None,
    min_profit_cents: float = 1.0,
) -> dict[str, Any]:
    """Cancel app-created paired exits that cannot clear their entry basis.

    The live runner submits a sell after a buy fills. That sell must be priced
    above the true effective entry basis, including submitted/JIT cost evidence,
    not just the raw fill price. If an older runtime created an underpriced
    paired exit, the app should stop that order automatically instead of leaving
    a loss-making exit in the book.
    """

    now_dt = now_dt or datetime.now(UTC)
    now = now_dt.isoformat()
    open_order_ids = {
        str(_field(order, "id", "order_id", "orderID") or "").strip().lower()
        for order in open_orders
        if str(_field(order, "id", "order_id", "orderID") or "").strip()
    }
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT o.order_key, o.intent_key, o.exchange_order_id, o.status,
                   o.order_json, o.submitted_at_utc, o.updated_at_utc,
                   ei.candidate_key, ei.strategy_id, ei.event_token_key,
                   ei.intent_type, ei.order_type, ei.side, ei.intent_json
              FROM orders o
              JOIN execution_intents ei ON ei.intent_key=o.intent_key
             WHERE lower(ei.intent_type)='paired_exit'
               AND upper(ei.side)='SELL'
               AND lower(o.status) IN ('submitted', 'partially_filled', 'open', 'live')
             ORDER BY o.updated_at_utc ASC
             LIMIT 100
            """
        ).fetchall()
    ]
    checked = 0
    unsafe = 0
    cancelled = 0
    cancel_failed = 0
    not_exchange_open = 0
    unresolved = 0
    details: list[dict[str, Any]] = []
    for row in rows:
        checked += 1
        order_id = str(row.get("exchange_order_id") or "").strip()
        if order_id.lower() not in open_order_ids:
            not_exchange_open += 1
            details.append(
                {
                    "order_key": row.get("order_key"),
                    "exchange_order_id": order_id,
                    "status": "not_exchange_open",
                }
            )
            continue
        check = _paired_exit_safety_check(conn, row, min_profit_cents=float(min_profit_cents))
        details.append(check)
        if check.get("status") == "unresolved":
            unresolved += 1
            continue
        if not check.get("unsafe"):
            continue
        unsafe += 1
        if cancel_order_fn is None:
            continue
        cancel_result = cancel_order_fn(order_id)
        success = _cancel_result_success(cancel_result)
        _record_paired_exit_safety_result(
            conn,
            row,
            check=check,
            cancel_result=cancel_result,
            cancelled=success,
            now=now,
        )
        if success:
            cancelled += 1
        else:
            cancel_failed += 1
    return {
        "schema_version": "crypto_options_underpriced_paired_exit_safety_v1",
        "checked_order_count": checked,
        "unsafe_order_count": unsafe,
        "cancelled_order_count": cancelled,
        "cancel_failed_count": cancel_failed,
        "not_exchange_open_count": not_exchange_open,
        "unresolved_order_count": unresolved,
        "details": details[:20],
    }


def account_position_settlements_from_portfolio(
    conn: Any,
    *,
    open_positions: list[Any],
    closed_positions: list[Any],
    now_dt: datetime | None = None,
) -> list[AccountPositionSettlement]:
    """Map Polymarket Data API portfolio rows to local live validation positions."""

    now_dt = now_dt or datetime.now(UTC)
    settlements: list[AccountPositionSettlement] = []
    portfolio_rows: list[tuple[str, Any]] = [("closed_position", row) for row in closed_positions]
    portfolio_rows.extend(("open_position", row) for row in open_positions)
    for source, portfolio_position in portfolio_rows:
        event_slug = _field(portfolio_position, "slug", "market_slug", "marketSlug", "event_slug", "eventSlug")
        outcome = _field(portfolio_position, "outcome")
        if not event_slug or not outcome:
            continue
        local_rows = _matching_local_positions(conn, event_slug=event_slug, outcome=outcome)
        if not local_rows:
            continue
        local_total_cost = sum(float(row.get("cost_basis_usd") or 0.0) for row in local_rows)
        settled_at = _field(portfolio_position, "end_date", "endDate") or now_dt.isoformat()
        for local in local_rows:
            cost_basis = float(local.get("cost_basis_usd") or 0.0)
            if cost_basis <= 0:
                continue
            pnl = _portfolio_realized_pnl_for_local_position(
                portfolio_position,
                source=source,
                local_cost_basis_usd=cost_basis,
                local_total_cost_basis_usd=local_total_cost,
                now_dt=now_dt,
            )
            if pnl is None:
                continue
            settlements.append(
                AccountPositionSettlement(
                    validation_run_id=local.get("validation_run_id"),
                    strategy_id=str(local["strategy_id"]),
                    event_token_key=str(local["event_token_key"]),
                    event_slug=str(event_slug),
                    outcome=str(outcome),
                    shares=float(local.get("shares") or 0.0),
                    cost_basis_usd=cost_basis,
                    realized_pnl_usd=round(float(pnl), 8),
                    settled_at_utc=str(settled_at),
                    source=f"polymarket_data_api_{source}",
                    account_position_id=_field(portfolio_position, "asset", "condition_id", "conditionId"),
                    raw=_object_dict(portfolio_position),
                )
            )
    return settlements


def account_position_settlements_from_local_event_resolution(
    conn: Any,
    *,
    event_outcome_resolver: EventOutcomeResolver,
    now_dt: datetime | None = None,
    settle_after_seconds: float = 60.0,
    max_positions: int = 250,
) -> list[AccountPositionSettlement]:
    """Settle stale local live positions from event resolution when account API omits them.

    Polymarket's Data API can drop very short 5-minute markets from both
    `/positions` and `/closed-positions` after the market closes. For live stop
    gates, the durable local fill plus resolved market outcome is enough to
    close the ledger conservatively.
    """

    now_dt = now_dt or datetime.now(UTC)
    rows = conn.execute(
        """
        SELECT p.position_key, p.strategy_id, p.event_token_key, p.shares,
               p.cost_basis_usd, p.opened_at_utc, p.updated_at_utc,
               et.event_slug AS token_event_slug, et.outcome AS token_outcome,
               o.order_json
          FROM positions p
          LEFT JOIN event_tokens et ON et.event_token_key=p.event_token_key
          LEFT JOIN fills f ON p.position_key=('position:' || f.fill_key)
          LEFT JOIN orders o ON o.order_key=f.order_key
         WHERE p.status='open'
           AND p.event_token_key LIKE '%-updown-5m-%:%'
         ORDER BY p.updated_at_utc DESC
         LIMIT ?
        """,
        (int(max_positions),),
    ).fetchall()
    settlements: list[AccountPositionSettlement] = []
    resolved_by_slug: dict[str, str | None] = {}
    for row in rows:
        item = dict(row)
        event_slug, outcome = _slug_outcome_from_local_position(item)
        if not event_slug or not outcome:
            continue
        event_row = _event_by_slug(conn, event_slug)
        end_dt = _parse_datetime((event_row or {}).get("event_end_time_utc"))
        if end_dt is None or end_dt > now_dt - timedelta(seconds=float(settle_after_seconds)):
            continue
        if event_slug not in resolved_by_slug:
            try:
                resolved_by_slug[event_slug] = event_outcome_resolver(event_slug)
            except Exception:  # noqa: BLE001 - unresolved provider rows stay open for the next sweep.
                resolved_by_slug[event_slug] = None
        resolved = _normalize_outcome(resolved_by_slug.get(event_slug))
        if not resolved:
            continue
        normalized_outcome = _normalize_outcome(outcome)
        shares = float(item.get("shares") or 0.0)
        cost_basis = float(item.get("cost_basis_usd") or 0.0)
        realized = round((shares - cost_basis) if normalized_outcome == resolved else -abs(cost_basis), 8)
        settlements.append(
            AccountPositionSettlement(
                validation_run_id=_validation_run_id_from_order_json(item.get("order_json"), item.get("strategy_id")),
                strategy_id=str(item["strategy_id"]),
                event_token_key=str(item["event_token_key"]),
                event_slug=event_slug,
                outcome=str(outcome).title(),
                resolved_outcome=resolved.title(),
                shares=shares,
                cost_basis_usd=cost_basis,
                realized_pnl_usd=realized,
                settled_at_utc=now_dt.isoformat(),
                source="local_event_resolution_fallback",
                account_position_id=str(item.get("position_key") or ""),
                raw={
                    "event_end_time_utc": (event_row or {}).get("event_end_time_utc"),
                    "position_key": item.get("position_key"),
                    "resolver": "event_outcome_resolver",
                },
            )
        )
    return settlements


def _apply_pending_order_trade_match(
    conn: Any,
    row: dict[str, Any],
    trades: list[Any],
    *,
    now: str,
    default_max_loss_usd: float,
) -> dict[str, int]:
    size, price, filled_at = _aggregate_trade_fill(trades, fallback_time=now)
    if size <= 0 or price <= 0:
        return {"ledger_updates": 0, "closed_positions": 0, "partial_positions": 0}
    source = {
        "source": "clob_trade_history_pending_order_reconciliation",
        "exchange_order_id": row.get("exchange_order_id"),
        "filled_shares": size,
        "filled_price": price,
        "filled_at_utc": filled_at,
        "trades": [_object_dict(trade) for trade in trades],
    }
    order_payload = _json_load(row.get("order_json"), {})
    order_payload["clob_pending_order_reconciliation"] = source
    conn.execute(
        """
        UPDATE orders
           SET status='filled',
               order_json=?,
               updated_at_utc=?
         WHERE order_key=?
        """,
        (json.dumps(order_payload, sort_keys=True, default=str), now, row["order_key"]),
    )
    fill_key = f"fill:{row['order_key']}:{size}:{price}"
    conn.execute(
        """
        INSERT INTO fills(fill_key, order_key, event_token_key, filled_size, filled_price, filled_at_utc, source_json, inserted_at_utc)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fill_key) DO UPDATE SET
            filled_size=excluded.filled_size,
            filled_price=excluded.filled_price,
            source_json=excluded.source_json
        """,
        (
            fill_key,
            row["order_key"],
            row.get("event_token_key"),
            size,
            price,
            filled_at,
            json.dumps(source, sort_keys=True, default=str),
            now,
        ),
    )
    if str(row.get("side") or "").upper() != "SELL":
        return {"ledger_updates": 0, "closed_positions": 0, "partial_positions": 0}
    exit_rows = conn.execute(
        """
        SELECT eo.exit_order_key, eo.exit_plan_key, ep.position_key,
               p.strategy_id, p.event_token_key, p.shares, p.cost_basis_usd
          FROM exit_orders eo
          LEFT JOIN exit_plans ep ON ep.exit_plan_key=eo.exit_plan_key
          LEFT JOIN positions p ON p.position_key=ep.position_key
         WHERE eo.order_key=?
         LIMIT 1
        """,
        (row["order_key"],),
    ).fetchall()
    if not exit_rows:
        return {"ledger_updates": 0, "closed_positions": 0, "partial_positions": 0}
    exit_row = dict(exit_rows[0])
    shares_before = float(exit_row.get("shares") or 0.0)
    cost_before = float(exit_row.get("cost_basis_usd") or 0.0)
    closed_fraction = 1.0 if shares_before <= 0 else min(1.0, float(size) / shares_before)
    allocated_cost = round(cost_before * closed_fraction, 8)
    realized_pnl = round((float(size) * float(price)) - allocated_cost, 8)
    status = "filled" if closed_fraction >= 0.999 else "partially_filled"
    conn.execute(
        """
        UPDATE exit_orders
           SET status=?,
               source_json=?
         WHERE exit_order_key=?
        """,
        (status, json.dumps({**source, "realized_pnl_usd": realized_pnl}, sort_keys=True, default=str), exit_row["exit_order_key"]),
    )
    conn.execute(
        """
        UPDATE exit_plans
           SET status=?,
               updated_at_utc=?
         WHERE exit_plan_key=?
        """,
        ("covered" if status == "filled" else "partial", now, exit_row["exit_plan_key"]),
    )
    closed_positions = 0
    partial_positions = 0
    if status == "filled":
        conn.execute(
            """
            UPDATE positions
               SET status='settled',
                   updated_at_utc=?
             WHERE position_key=?
            """,
            (now, exit_row["position_key"]),
        )
        closed_positions = 1
    else:
        remaining_shares = max(0.0, shares_before - float(size))
        remaining_cost = max(0.0, cost_before - allocated_cost)
        conn.execute(
            """
            UPDATE positions
               SET shares=?,
                   cost_basis_usd=?,
                   updated_at_utc=?
             WHERE position_key=?
            """,
            (remaining_shares, remaining_cost, now, exit_row["position_key"]),
        )
        partial_positions = 1
    ledger_updates = _apply_pending_order_ledger_update(
        conn,
        row=row,
        strategy_id=str(exit_row.get("strategy_id") or row.get("strategy_id") or ""),
        realized_pnl=realized_pnl,
        allocated_cost=allocated_cost,
        source=source,
        now=now,
        default_max_loss_usd=default_max_loss_usd,
    )
    return {
        "ledger_updates": ledger_updates,
        "closed_positions": closed_positions,
        "partial_positions": partial_positions,
    }


def _apply_pending_order_ledger_update(
    conn: Any,
    *,
    row: dict[str, Any],
    strategy_id: str,
    realized_pnl: float,
    allocated_cost: float,
    source: dict[str, Any],
    now: str,
    default_max_loss_usd: float,
) -> int:
    validation_run_id = _validation_run_id_from_order_json(row.get("order_json"), strategy_id)
    ledger_key = None
    if validation_run_id:
        ledger = conn.execute(
            """
            SELECT validation_budget_ledger_key, ledger_json, realized_pnl_usd,
                   open_cost_usd, budget_cap_usd, notional_filled_usd, stop_reason
              FROM validation_budget_ledger
             WHERE validation_run_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (validation_run_id,),
        ).fetchone()
    else:
        ledger = None
    if ledger is None:
        ledger = conn.execute(
            """
            SELECT validation_budget_ledger_key, ledger_json, realized_pnl_usd,
                   open_cost_usd, budget_cap_usd, notional_filled_usd, stop_reason
              FROM validation_budget_ledger
             WHERE strategy_or_component_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
    if ledger is None:
        return 0
    ledger_key = str(ledger["validation_budget_ledger_key"])
    ledger_json = _json_load(ledger["ledger_json"], {})
    ledger_json.setdefault("pending_order_reconciliations", []).append({**source, "realized_pnl_usd": realized_pnl})
    next_realized = round(float(ledger["realized_pnl_usd"] or 0.0) + float(realized_pnl), 8)
    next_open_cost = max(0.0, round(float(ledger["open_cost_usd"] or 0.0) - float(allocated_cost), 8))
    max_loss_usd = _strategy_loss_limit_usd(ledger_json, fallback=default_max_loss_usd)
    hard_stop = int(next_realized <= -abs(max_loss_usd))
    stop_reason = ledger["stop_reason"]
    if hard_stop:
        stop_reason = _append_reason(stop_reason, "pending_order_reconciliation_loss_stop")
    conn.execute(
        """
        UPDATE validation_budget_ledger
           SET completed_at_utc=CASE WHEN ? <= 0 THEN ? ELSE completed_at_utc END,
               realized_pnl_usd=?,
               open_cost_usd=?,
               remaining_validation_budget_usd=CASE
                   WHEN budget_cap_usd - notional_filled_usd + ? > 0
                   THEN budget_cap_usd - notional_filled_usd + ?
                   ELSE 0
               END,
               hard_stop_triggered=CASE WHEN ? = 1 THEN 1 ELSE hard_stop_triggered END,
               stop_reason=?,
               lifecycle_audit_status=COALESCE(lifecycle_audit_status, 'passed'),
               reconciliation_status='reconciled',
               ledger_json=?,
               updated_at_utc=?
         WHERE validation_budget_ledger_key=?
        """,
        (
            next_open_cost,
            now,
            next_realized,
            next_open_cost,
            next_realized,
            next_realized,
            hard_stop,
            stop_reason,
            json.dumps(ledger_json, sort_keys=True, default=str),
            now,
            ledger_key,
        ),
    )
    return 1


def _expire_pending_order(
    conn: Any,
    row: dict[str, Any],
    *,
    now: str,
    default_max_loss_usd: float,
) -> dict[str, int]:
    payload = _json_load(row.get("order_json"), {})
    payload["clob_pending_order_reconciliation"] = {
        "source": "clob_open_orders_and_trade_history_absent",
        "exchange_order_id": row.get("exchange_order_id"),
        "reconciled_at_utc": now,
        "result": "expired_or_cancelled_without_fill",
    }
    conn.execute(
        """
        UPDATE orders
           SET status='expired',
               order_json=?,
               updated_at_utc=?
         WHERE order_key=?
        """,
        (json.dumps(payload, sort_keys=True, default=str), now, row["order_key"]),
    )
    conn.execute(
        """
        UPDATE exit_orders
           SET status='expired',
               source_json=?
         WHERE order_key=?
           AND lower(status) IN ('submitted', 'open', 'live')
        """,
        (json.dumps(payload, sort_keys=True, default=str), row["order_key"]),
    )
    if str(row.get("side") or "").upper() == "SELL":
        return {"ledger_updates": 0, "closed_positions": 0}
    optimistic_rows = [
        dict(item)
        for item in conn.execute(
            """
            SELECT f.fill_key, f.filled_size, f.filled_price,
                   p.position_key, p.strategy_id, p.cost_basis_usd, p.status
              FROM fills f
              LEFT JOIN positions p ON p.position_key=('position:' || f.fill_key)
             WHERE f.order_key=?
               AND p.status='open'
            """,
            (row["order_key"],),
        ).fetchall()
    ]
    if not optimistic_rows:
        return {"ledger_updates": 0, "closed_positions": 0}
    removed_cost = round(sum(float(item.get("cost_basis_usd") or 0.0) for item in optimistic_rows), 8)
    cursor = conn.execute(
        """
        UPDATE positions
           SET status='expired_unfilled',
               updated_at_utc=?
         WHERE position_key IN ({})
           AND status='open'
        """.format(",".join("?" for _ in optimistic_rows)),
        (now, *(item["position_key"] for item in optimistic_rows)),
    )
    closed_positions = int(getattr(cursor, "rowcount", 0) or 0)
    ledger_updates = _reverse_expired_entry_ledger(
        conn,
        row=row,
        strategy_id=str(optimistic_rows[0].get("strategy_id") or row.get("strategy_id") or ""),
        removed_cost=removed_cost,
        source=payload["clob_pending_order_reconciliation"],
        now=now,
        default_max_loss_usd=default_max_loss_usd,
    )
    return {"ledger_updates": ledger_updates, "closed_positions": closed_positions}


def _reverse_expired_entry_ledger(
    conn: Any,
    *,
    row: dict[str, Any],
    strategy_id: str,
    removed_cost: float,
    source: dict[str, Any],
    now: str,
    default_max_loss_usd: float,
) -> int:
    validation_run_id = _validation_run_id_from_order_json(row.get("order_json"), strategy_id)
    ledger = None
    if validation_run_id:
        ledger = conn.execute(
            """
            SELECT validation_budget_ledger_key, ledger_json, realized_pnl_usd,
                   open_cost_usd, budget_cap_usd, notional_filled_usd, stop_reason
              FROM validation_budget_ledger
             WHERE validation_run_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (validation_run_id,),
        ).fetchone()
    if ledger is None:
        ledger = conn.execute(
            """
            SELECT validation_budget_ledger_key, ledger_json, realized_pnl_usd,
                   open_cost_usd, budget_cap_usd, notional_filled_usd, stop_reason
              FROM validation_budget_ledger
             WHERE strategy_or_component_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
    if ledger is None:
        return 0
    ledger_json = _json_load(ledger["ledger_json"], {})
    ledger_json.setdefault("pending_order_reconciliations", []).append(
        {**source, "local_optimistic_entry_reversed_usd": round(float(removed_cost), 8)}
    )
    realized = float(ledger["realized_pnl_usd"] or 0.0)
    next_open_cost = max(0.0, round(float(ledger["open_cost_usd"] or 0.0) - float(removed_cost), 8))
    next_filled = max(0.0, round(float(ledger["notional_filled_usd"] or 0.0) - float(removed_cost), 8))
    max_loss_usd = _strategy_loss_limit_usd(ledger_json, fallback=default_max_loss_usd)
    hard_stop = int(realized <= -abs(max_loss_usd))
    stop_reason = _append_reason(ledger["stop_reason"], "entry_order_expired_without_exchange_fill")
    conn.execute(
        """
        UPDATE validation_budget_ledger
           SET completed_at_utc=CASE WHEN ? <= 0 THEN ? ELSE completed_at_utc END,
               notional_filled_usd=?,
               open_cost_usd=?,
               remaining_validation_budget_usd=CASE
                   WHEN budget_cap_usd - ? + ? > 0
                   THEN budget_cap_usd - ? + ?
                   ELSE 0
               END,
               hard_stop_triggered=CASE WHEN ? = 1 THEN 1 ELSE hard_stop_triggered END,
               stop_reason=?,
               reconciliation_status='reconciled',
               ledger_json=?,
               updated_at_utc=?
         WHERE validation_budget_ledger_key=?
        """,
        (
            next_open_cost,
            now,
            next_filled,
            next_open_cost,
            next_filled,
            realized,
            next_filled,
            realized,
            hard_stop,
            stop_reason,
            json.dumps(ledger_json, sort_keys=True, default=str),
            now,
            ledger["validation_budget_ledger_key"],
        ),
    )
    return 1


def _paired_exit_safety_check(
    conn: Any,
    row: dict[str, Any],
    *,
    min_profit_cents: float,
) -> dict[str, Any]:
    exit_payload = _json_load(row.get("order_json"), {})
    intent_payload = _json_load(row.get("intent_json"), {})
    exit_price = _first_float(
        intent_payload.get("limit_price"),
        exit_payload.get("limit_price"),
        _nested(exit_payload, "order_source_payload", "live_executor_response", "legacy_submission", "order_request", "price"),
        _nested(exit_payload, "order_source_payload", "live_executor_response", "legacy_submission", "execution_quality", "submitted_limit_price"),
    )
    entry_rows = [
        dict(item)
        for item in conn.execute(
            """
            SELECT i.intent_key, i.intent_json, o.order_key, o.order_json,
                   f.filled_size, f.filled_price,
                   p.position_key, p.shares, p.cost_basis_usd
              FROM execution_intents i
              LEFT JOIN orders o ON o.intent_key=i.intent_key
              LEFT JOIN fills f ON f.order_key=o.order_key
              LEFT JOIN positions p ON p.position_key=('position:' || f.fill_key)
             WHERE i.candidate_key=?
               AND lower(i.intent_type)='entry'
               AND upper(i.side)='BUY'
             ORDER BY i.created_at_utc ASC
             LIMIT 5
            """,
            (row.get("candidate_key"),),
        ).fetchall()
    ]
    if exit_price is None or not entry_rows:
        return {
            "status": "unresolved",
            "order_key": row.get("order_key"),
            "exchange_order_id": row.get("exchange_order_id"),
            "exit_price": exit_price,
            "reason": "missing_exit_price_or_entry",
        }
    entry_basis = max(_entry_basis_candidates(entry) for entry in entry_rows)
    required_exit_price = min(0.99, round(entry_basis + (float(min_profit_cents) / 100.0), 4))
    return {
        "status": "checked",
        "order_key": row.get("order_key"),
        "exchange_order_id": row.get("exchange_order_id"),
        "strategy_id": row.get("strategy_id"),
        "event_token_key": row.get("event_token_key"),
        "exit_price": round(float(exit_price), 6),
        "entry_basis_price": round(float(entry_basis), 6),
        "required_exit_price": round(float(required_exit_price), 6),
        "unsafe": float(exit_price) + 1e-9 < float(required_exit_price),
    }


def _entry_basis_candidates(entry: dict[str, Any]) -> float:
    intent_payload = _json_load(entry.get("intent_json"), {})
    order_payload = _json_load(entry.get("order_json"), {})
    source = _nested(order_payload, "attribution", "order_source_payload", "live_executor_response") or _nested(
        intent_payload, "attribution", "order_source_payload", "live_executor_response"
    )
    if not isinstance(source, dict):
        source = {}
    legacy = source.get("legacy_submission") if isinstance(source.get("legacy_submission"), dict) else {}
    order_request = legacy.get("order_request") if isinstance(legacy.get("order_request"), dict) else {}
    execution_quality = legacy.get("execution_quality") if isinstance(legacy.get("execution_quality"), dict) else {}
    jit_quote = legacy.get("jit_quote") if isinstance(legacy.get("jit_quote"), dict) else {}
    shares = _first_float(entry.get("shares"), entry.get("filled_size"), order_request.get("size"), execution_quality.get("filled_shares"))
    values = [
        _optional_float(entry.get("filled_price")),
        _price_from_cost_and_shares(entry.get("cost_basis_usd"), shares),
        _optional_float(order_request.get("price")),
        _optional_float(order_request.get("submitted_limit_price")),
        _optional_float(order_request.get("pre_jit_price")),
        _optional_float(execution_quality.get("submitted_limit_price")),
        _optional_float(execution_quality.get("realized_price")),
        _optional_float(execution_quality.get("pre_jit_limit_price")),
        _optional_float(jit_quote.get("submitted_limit_price")),
        _price_from_cost_and_shares(order_request.get("estimated_total_cost_usd"), shares),
        _price_from_cost_and_shares(jit_quote.get("estimated_total_cost_usd"), shares),
    ]
    concrete = [float(value) for value in values if value is not None and 0 < float(value) < 1]
    return max(concrete) if concrete else 1.0


def _record_paired_exit_safety_result(
    conn: Any,
    row: dict[str, Any],
    *,
    check: dict[str, Any],
    cancel_result: Any,
    cancelled: bool,
    now: str,
) -> None:
    payload = _json_load(row.get("order_json"), {})
    payload["paired_exit_safety_guard"] = {
        **check,
        "cancelled": bool(cancelled),
        "cancel_result": _object_dict(cancel_result) or str(cancel_result),
        "checked_at_utc": now,
    }
    status = "cancelled" if cancelled else "unsafe_cancel_failed"
    conn.execute(
        """
        UPDATE orders
           SET status=?,
               order_json=?,
               updated_at_utc=?
         WHERE order_key=?
        """,
        (status, json.dumps(payload, sort_keys=True, default=str), now, row["order_key"]),
    )
    conn.execute(
        """
        UPDATE exit_orders
           SET status=?,
               source_json=?
         WHERE order_key=?
           AND lower(status) IN ('submitted', 'open', 'live')
        """,
        (status, json.dumps(payload, sort_keys=True, default=str), row["order_key"]),
    )
    _record_paired_exit_safety_ledger(
        conn,
        row,
        payload=payload,
        now=now,
        cancelled=cancelled,
    )


def _record_paired_exit_safety_ledger(
    conn: Any,
    row: dict[str, Any],
    *,
    payload: dict[str, Any],
    now: str,
    cancelled: bool,
) -> None:
    strategy_id = str(row.get("strategy_id") or "")
    validation_run_id = _validation_run_id_from_order_json(row.get("order_json"), strategy_id)
    ledger = None
    if validation_run_id:
        ledger = conn.execute(
            """
            SELECT validation_budget_ledger_key, ledger_json, stop_reason
              FROM validation_budget_ledger
             WHERE validation_run_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (validation_run_id,),
        ).fetchone()
    if ledger is None and strategy_id:
        ledger = conn.execute(
            """
            SELECT validation_budget_ledger_key, ledger_json, stop_reason
              FROM validation_budget_ledger
             WHERE strategy_or_component_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
    if ledger is None:
        return
    ledger_json = _json_load(ledger["ledger_json"], {})
    ledger_json.setdefault("paired_exit_safety_guard", []).append(payload["paired_exit_safety_guard"])
    reason = "underpriced_paired_exit_cancelled" if cancelled else "underpriced_paired_exit_cancel_failed"
    conn.execute(
        """
        UPDATE validation_budget_ledger
           SET stop_reason=?,
               reconciliation_status='pending_safe_exit_replacement',
               ledger_json=?,
               updated_at_utc=?
         WHERE validation_budget_ledger_key=?
        """,
        (
            _append_reason(ledger["stop_reason"], reason),
            json.dumps(ledger_json, sort_keys=True, default=str),
            now,
            ledger["validation_budget_ledger_key"],
        ),
    )


def _cancel_result_success(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return bool(value.get("success"))
    if hasattr(value, "success"):
        return bool(getattr(value, "success"))
    return False


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _price_from_cost_and_shares(cost: Any, shares: Any) -> float | None:
    cost_value = _optional_float(cost)
    shares_value = _optional_float(shares)
    if cost_value is None or shares_value is None or shares_value <= 0:
        return None
    return cost_value / shares_value


def _nested(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _trades_by_order_id(trades: list[Any]) -> dict[str, list[Any]]:
    output: dict[str, list[Any]] = {}
    for trade in trades:
        for key in (
            _field(trade, "taker_order_id", "takerOrderId"),
            _field(trade, "maker_order_id", "makerOrderId"),
            _field(trade, "order_id", "orderID"),
        ):
            order_id = str(key or "").strip().lower()
            if order_id:
                output.setdefault(order_id, []).append(trade)
    return output


def _aggregate_trade_fill(trades: list[Any], *, fallback_time: str) -> tuple[float, float, str]:
    total_size = 0.0
    total_notional = 0.0
    latest_ts: int | None = None
    for trade in trades:
        size = _optional_float(_field(trade, "size")) or 0.0
        price = _optional_float(_field(trade, "price")) or 0.0
        total_size += size
        total_notional += size * price
        timestamp = _optional_int(_field(trade, "timestamp"))
        if timestamp is not None:
            latest_ts = timestamp if latest_ts is None else max(latest_ts, timestamp)
    if total_size <= 0:
        return 0.0, 0.0, fallback_time
    filled_at = (
        datetime.fromtimestamp(latest_ts, tz=UTC).isoformat()
        if latest_ts is not None and latest_ts > 0
        else fallback_time
    )
    return round(total_size, 8), round(total_notional / total_size, 8), filled_at



def _ledger_key_for_settlement(conn: Any, settlement: AccountPositionSettlement) -> str | None:
    if settlement.validation_run_id:
        row = conn.execute(
            """
            SELECT validation_budget_ledger_key
              FROM validation_budget_ledger
             WHERE validation_run_id=?
             ORDER BY updated_at_utc DESC
             LIMIT 1
            """,
            (settlement.validation_run_id,),
        ).fetchone()
        if row is not None:
            return str(row["validation_budget_ledger_key"])
    row = conn.execute(
        """
        SELECT validation_budget_ledger_key
          FROM validation_budget_ledger
         WHERE strategy_or_component_id=?
           AND (open_cost_usd > 0 OR notional_filled_usd > 0)
         ORDER BY COALESCE(completed_at_utc, updated_at_utc, inserted_at_utc) DESC
         LIMIT 1
        """,
        (settlement.strategy_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["validation_budget_ledger_key"])


def _matching_local_positions(conn: Any, *, event_slug: str, outcome: str) -> list[dict[str, Any]]:
    normalized_outcome = str(outcome or "").strip().lower()
    slug_side_key = f"{event_slug}:{normalized_outcome}"
    rows = conn.execute(
        """
        SELECT p.position_key, p.strategy_id, p.event_token_key, p.shares,
               p.cost_basis_usd, et.event_slug, et.outcome, o.order_json
          FROM positions p
          LEFT JOIN event_tokens et ON et.event_token_key=p.event_token_key
          LEFT JOIN fills f ON p.position_key=('position:' || f.fill_key)
          LEFT JOIN orders o ON o.order_key=f.order_key
        WHERE p.status='open'
           AND (et.event_slug=? OR p.event_token_key LIKE ?)
           AND (
                lower(et.outcome)=lower(?)
                OR lower(p.event_token_key)=lower(?)
                OR lower(p.event_token_key) LIKE ?
           )
         ORDER BY p.updated_at_utc DESC
         LIMIT 20
        """,
        (event_slug, f"{event_slug}:%", outcome, slug_side_key, f"{slug_side_key}:%"),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["validation_run_id"] = _validation_run_id_from_order_json(item.get("order_json"), item.get("strategy_id"))
        output.append(item)
    return output


def _close_matching_positions(conn: Any, settlement: AccountPositionSettlement, *, now: str) -> int:
    cursor = conn.execute(
        """
        UPDATE positions
           SET status='settled',
               updated_at_utc=?
         WHERE strategy_id=?
           AND event_token_key=?
           AND status='open'
        """,
        (now, settlement.strategy_id, settlement.event_token_key),
    )
    return int(getattr(cursor, "rowcount", 0) or 0)


def _portfolio_realized_pnl_for_local_position(
    portfolio_position: Any,
    *,
    source: str,
    local_cost_basis_usd: float,
    local_total_cost_basis_usd: float,
    now_dt: datetime,
) -> float | None:
    if source == "closed_position":
        realized = _optional_float(_field(portfolio_position, "realized_pnl", "realizedPnl"))
        if realized is not None:
            return _allocate_aggregate_pnl(
                realized,
                local_cost_basis_usd=local_cost_basis_usd,
                local_total_cost_basis_usd=local_total_cost_basis_usd,
            )
    current_value = _optional_float(_field(portfolio_position, "current_value", "currentValue"))
    cash_pnl = _optional_float(_field(portfolio_position, "cash_pnl", "cashPnl"))
    end_date = _parse_datetime(_field(portfolio_position, "end_date", "endDate"))
    if current_value == 0.0 and end_date is not None and end_date <= now_dt:
        return -abs(local_cost_basis_usd)
    if cash_pnl is not None and end_date is not None and end_date <= now_dt:
        return _allocate_aggregate_pnl(
            cash_pnl,
            local_cost_basis_usd=local_cost_basis_usd,
            local_total_cost_basis_usd=local_total_cost_basis_usd,
        )
    return None


def _allocate_aggregate_pnl(
    aggregate_pnl: float,
    *,
    local_cost_basis_usd: float,
    local_total_cost_basis_usd: float,
) -> float:
    if local_total_cost_basis_usd <= 0:
        return float(aggregate_pnl)
    share = max(0.0, float(local_cost_basis_usd)) / float(local_total_cost_basis_usd)
    return float(aggregate_pnl) * share


def _validation_run_id_from_order_json(order_json: Any, strategy_id: Any) -> str | None:
    payload = _json_load(order_json, {})
    app_run_id = _app_run_id_from_order_payload(payload)
    if app_run_id:
        return f"validation-run:{app_run_id}:{strategy_id}"
    candidate_key = str(payload.get("candidate_key") or "")
    prefix = "candidate:"
    strategy_part = str(strategy_id or "")
    if candidate_key.startswith(prefix) and strategy_part:
        body = candidate_key[len(prefix) :]
        marker = f":{strategy_part}:"
        if marker in body:
            run_id = body.rsplit(marker, 1)[0]
            if run_id:
                return f"validation-run:{run_id}:{strategy_id}"
    return None


def _app_run_id_from_order_payload(payload: dict[str, Any]) -> str | None:
    attribution = payload.get("attribution")
    if not isinstance(attribution, dict):
        return None
    source_payload = attribution.get("order_source_payload")
    if not isinstance(source_payload, dict):
        return None
    response = source_payload.get("live_executor_response")
    if not isinstance(response, dict):
        return None
    legacy = response.get("legacy_submission")
    if not isinstance(legacy, dict):
        return None
    request = legacy.get("order_request")
    if not isinstance(request, dict):
        return None
    app_run_id = str(request.get("app_run_id") or "").strip()
    return app_run_id or None


def _slug_outcome_from_local_position(row: dict[str, Any]) -> tuple[str | None, str | None]:
    token_slug = str(row.get("token_event_slug") or "").strip()
    token_outcome = str(row.get("token_outcome") or "").strip()
    if token_slug and token_outcome:
        return token_slug, token_outcome
    event_token_key = str(row.get("event_token_key") or "").strip()
    if ":" not in event_token_key:
        return None, token_outcome or None
    slug, raw_outcome, *_ = event_token_key.split(":")
    return slug.strip() or None, raw_outcome.strip() or token_outcome or None


def _event_by_slug(conn: Any, event_slug: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT event_key, event_slug, event_end_time_utc
          FROM events
         WHERE event_slug=?
         ORDER BY updated_at_utc DESC
         LIMIT 1
        """,
        (event_slug,),
    ).fetchone()
    return None if row is None else dict(row)


def _normalize_outcome(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in {"up", "yes"}:
        return "up"
    if raw in {"down", "no"}:
        return "down"
    return raw or None


def _strategy_loss_limit_usd(ledger_json: dict[str, Any], *, fallback: float) -> float:
    for source in (
        ledger_json.get("promotion_policy"),
        ledger_json.get("strategy_policy"),
        ledger_json.get("risk_gates"),
    ):
        if not isinstance(source, dict):
            continue
        for key in ("max_supervised_live_loss_usd", "max_live_loss_usd", "hard_stop_loss_usd"):
            try:
                return abs(float(source[key]))
            except (KeyError, TypeError, ValueError):
                continue
    return abs(float(fallback))


def _append_reason(existing: Any, reason: str) -> str:
    values = [item for item in str(existing or "").split(",") if item]
    if reason not in values:
        values.append(reason)
    return ",".join(values)


def _settlement_reference(settlement: AccountPositionSettlement) -> str:
    if settlement.validation_run_id:
        return f"{settlement.strategy_id}:{settlement.validation_run_id}"
    return f"{settlement.strategy_id}:{settlement.event_token_key}"


def _json_load(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _field(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value.get(name)
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _object_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        return asdict(value)
    except TypeError:
        return {}


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
