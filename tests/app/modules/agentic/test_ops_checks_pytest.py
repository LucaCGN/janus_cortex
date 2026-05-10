from __future__ import annotations

from app.modules.agentic import ops_checks


def test_classify_portfolio_mirror_authority_quarantines_cash_and_position_mismatch_pytest() -> None:
    authority = ops_checks.classify_portfolio_mirror_authority(
        collateral={"ready": True, "balance_usd": 103.81},
        direct_positions={"ok": True, "positions": []},
        mirror_summary={"cash_usd": 0.0, "equity_usd": 0.0},
        mirror_positions=[{"outcome_id": "old-resolved"}],
    )

    assert authority["status"] == "quarantined"
    assert authority["authoritative_for_live"] is False
    assert [reason["reason"] for reason in authority["quarantine_reasons"]] == [
        "portfolio_mirror_cash_mismatch",
        "portfolio_mirror_equity_mismatch",
        "portfolio_mirror_position_mismatch",
    ]


def test_build_integrity_snapshot_keeps_direct_clob_ready_while_mirror_quarantined_pytest(monkeypatch) -> None:
    fake_account = {
        "account_id": "account-123",
        "provider_code": "polymarket",
        "wallet_address": "0xwallet",
        "proxy_wallet_address": "0xproxy",
        "chain_id": 137,
        "is_active": True,
    }

    monkeypatch.setattr(ops_checks, "get_agentic_database_status", lambda: {"ok": True})
    monkeypatch.setattr(ops_checks, "resolve_trading_account", lambda connection, *, account_id=None: fake_account)
    monkeypatch.setattr(ops_checks, "build_live_creds", lambda account: {"creds": True})
    monkeypatch.setattr(
        ops_checks,
        "fetch_clob_collateral_status",
        lambda creds, *, required_notional_usd: {"ready": True, "balance_usd": 103.81},
    )
    monkeypatch.setattr(ops_checks, "_safe_direct_orders", lambda creds: {"ok": True, "orders": []})
    monkeypatch.setattr(ops_checks, "_safe_direct_positions", lambda creds: {"ok": True, "positions": []})
    monkeypatch.setattr(
        ops_checks,
        "fetch_account_summary",
        lambda connection, *, account_id: {"cash_usd": 0.0, "equity_usd": 0.0},
    )
    monkeypatch.setattr(
        ops_checks,
        "list_latest_positions",
        lambda connection, *, account_id: [{"outcome_id": "old-resolved"}],
    )

    snapshot = ops_checks.build_integrity_snapshot(object(), account_id="account-123")

    assert snapshot["ready_for_live_minimum_orders"] is True
    assert snapshot["portfolio_mirror"]["ok"] is False
    assert snapshot["portfolio_mirror"]["status"] == "quarantined"
    assert snapshot["portfolio_mirror"]["authoritative_for_live"] is False
    blocker_reasons = [blocker["reason"] for blocker in snapshot["blockers"]]
    assert blocker_reasons == [
        "portfolio_mirror_cash_mismatch",
        "portfolio_mirror_equity_mismatch",
        "portfolio_mirror_position_mismatch",
    ]
    assert {blocker["severity"] for blocker in snapshot["blockers"]} == {"non_blocking_for_live_if_direct_clob_ready"}


def test_build_integrity_snapshot_marks_healthy_mirror_authoritative_pytest(monkeypatch) -> None:
    fake_account = {
        "account_id": "account-123",
        "provider_code": "polymarket",
        "wallet_address": "0xwallet",
        "proxy_wallet_address": "0xproxy",
        "chain_id": 137,
        "is_active": True,
    }

    monkeypatch.setattr(ops_checks, "get_agentic_database_status", lambda: {"ok": True})
    monkeypatch.setattr(ops_checks, "resolve_trading_account", lambda connection, *, account_id=None: fake_account)
    monkeypatch.setattr(ops_checks, "build_live_creds", lambda account: {"creds": True})
    monkeypatch.setattr(
        ops_checks,
        "fetch_clob_collateral_status",
        lambda creds, *, required_notional_usd: {"ready": True, "balance_usd": 103.81},
    )
    monkeypatch.setattr(ops_checks, "_safe_direct_orders", lambda creds: {"ok": True, "orders": []})
    monkeypatch.setattr(ops_checks, "_safe_direct_positions", lambda creds: {"ok": True, "positions": []})
    monkeypatch.setattr(
        ops_checks,
        "fetch_account_summary",
        lambda connection, *, account_id: {"cash_usd": 103.81, "equity_usd": 103.81},
    )
    monkeypatch.setattr(ops_checks, "list_latest_positions", lambda connection, *, account_id: [])

    snapshot = ops_checks.build_integrity_snapshot(object(), account_id="account-123")

    assert snapshot["ready_for_live_minimum_orders"] is True
    assert snapshot["portfolio_mirror"]["ok"] is True
    assert snapshot["portfolio_mirror"]["status"] == "authoritative"
    assert snapshot["portfolio_mirror"]["authoritative_for_live"] is True
    assert snapshot["blockers"] == []
