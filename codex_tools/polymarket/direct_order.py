"""Janus-mediated direct order call for the global portfolio manager.

This module gives the Codex portfolio manager a concrete one-shot order
surface. It intentionally routes through Janus'
``/v1/portfolio/manager/order-management`` endpoint instead of raw CLOB calls,
so the existing runtime flag, kill switch, ledger, idempotency, risk, and
reconciliation gates remain authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codex_tools.janus import client as janus_client
from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT

PORTFOLIO_MANAGER_DIRECT_ORDER_SCHEMA_VERSION = "polymarket_portfolio_manager_direct_order_call_v1"
PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENDPOINT = "/v1/portfolio/manager/order-management"


@dataclass(frozen=True)
class PortfolioManagerDirectOrderCall:
    schema_version: str
    api_root: str
    endpoint: str
    dry_run: bool
    execution_approved: bool
    reviewer_metadata_present: bool
    order_management_call_attempted: bool
    order_preparation_attempted: bool
    order_submission_attempted: bool
    live_order_impact: str
    payload: dict[str, Any]
    response: dict[str, Any]
    execution_statement: str


def build_portfolio_manager_order_management_payload(
    *,
    action_plan: dict[str, Any],
    account_id: str | None,
    requested_order: dict[str, Any],
    execute: bool = False,
    execution_approved: bool = False,
    reviewed_by: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the request payload for the approved Janus order-management path."""

    return {
        "account_id": account_id,
        "action_plan": dict(action_plan),
        "requested_order": dict(requested_order),
        "dry_run": not execute,
        "execution_approved": bool(execution_approved),
        "reviewed_by": reviewed_by,
        "reason": reason,
    }


def call_portfolio_manager_order_management(
    *,
    action_plan: dict[str, Any],
    account_id: str | None,
    requested_order: dict[str, Any],
    api_root: str = janus_client.DEFAULT_API_ROOT,
    execute: bool = False,
    execution_approved: bool = False,
    reviewed_by: str | None = None,
    reason: str | None = None,
    timeout: int = 120,
) -> PortfolioManagerDirectOrderCall:
    """Call the Janus portfolio manager order path.

    ``execute=False`` is a dry-run preview. ``execute=True`` can place an order
    only if Janus accepts the request and its server-side gates pass.
    """

    reviewer_metadata_present = bool(str(reviewed_by or "").strip() and str(reason or "").strip())
    if execute and (not execution_approved or not reviewer_metadata_present):
        raise ValueError("execute=true requires execution_approved=true plus reviewed_by and reason")

    payload = build_portfolio_manager_order_management_payload(
        action_plan=action_plan,
        account_id=account_id,
        requested_order=requested_order,
        execute=execute,
        execution_approved=execution_approved,
        reviewed_by=reviewed_by,
        reason=reason,
    )
    response = janus_client.api_json(
        api_root,
        "POST",
        PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENDPOINT,
        payload,
        timeout=timeout,
    )
    execution_mode = bool(execute)
    return PortfolioManagerDirectOrderCall(
        schema_version=PORTFOLIO_MANAGER_DIRECT_ORDER_SCHEMA_VERSION,
        api_root=api_root,
        endpoint=PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENDPOINT,
        dry_run=not execution_mode,
        execution_approved=bool(execution_approved),
        reviewer_metadata_present=reviewer_metadata_present,
        order_management_call_attempted=True,
        order_preparation_attempted=execution_mode,
        order_submission_attempted=execution_mode,
        live_order_impact="order-path" if execution_mode else "read-only",
        payload=payload,
        response=response,
        execution_statement=(
            "Janus portfolio-manager order-management call was sent; inspect response side effects and direct-CLOB reconciliation."
            if execution_mode
            else NO_EXECUTION_STATEMENT
        ),
    )


__all__ = [
    "PORTFOLIO_MANAGER_DIRECT_ORDER_SCHEMA_VERSION",
    "PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENDPOINT",
    "PortfolioManagerDirectOrderCall",
    "build_portfolio_manager_order_management_payload",
    "call_portfolio_manager_order_management",
]
