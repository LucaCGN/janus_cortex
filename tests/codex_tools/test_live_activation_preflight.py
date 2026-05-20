from __future__ import annotations

from dataclasses import asdict

from codex_tools.janus import live_activation

PORTFOLIO_ACCOUNT_ID = "56964015-5935-5035-bdab-b056c9277146"


def _live_env() -> dict[str, str]:
    return {
        "JANUS_LIVE_TEST_ENABLED": "true",
        "JANUS_LIVE_ACTIVATION_MODE": "live",
        "JANUS_LIVE_TEST_SESSION_DATE": "2026-05-20",
        "JANUS_LIVE_TEST_EVENT_ID": "event-1",
        "JANUS_LIVE_TEST_ACCOUNT_ID": "account-1",
        "JANUS_LIVE_TEST_EXECUTE": "true",
        "JANUS_LIVE_TEST_LIVE_MONEY": "true",
        "JANUS_LIVE_TEST_ENABLE_LLM_DISPATCH": "true",
        "JANUS_LIVE_TEST_MAX_INTENTS": "2",
        "JANUS_LIVE_TEST_MIN_SIZE": "5",
        "JANUS_LIVE_TEST_MIN_BUY_NOTIONAL_USD": "1",
        "JANUS_PORTFOLIO_MANAGER_ACTIVATION_MODE": "live",
        "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED": "true",
        "JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID": PORTFOLIO_ACCOUNT_ID,
        "JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED": "true",
        "JANUS_PORTFOLIO_MANAGER_REVIEWED_BY": "codex-global-portfolio-agent",
        "JANUS_PORTFOLIO_MANAGER_REASON": "approved live micro-position test",
        "JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR": "true",
        "JANUS_PORTFOLIO_MANAGER_MAX_INITIAL_NOTIONAL_USD": "5",
        "JANUS_PORTFOLIO_MANAGER_TARGET_NOTIONAL_USD": "1",
        "JANUS_PORTFOLIO_MANAGER_DIRECT_TRUTH_MAX_AGE_SECONDS": "30",
    }


def _ready_worker() -> dict[str, object]:
    return {
        "ok": True,
        "status": "running",
        "enabled": True,
        "execute": True,
        "live_money": True,
        "account_id_configured": True,
        "enable_llm_dispatch": True,
        "event_ids": ["event-1"],
    }


def test_live_activation_preflight_blocks_last_game_failure_mode() -> None:
    env = {
        "JANUS_LIVE_TEST_ENABLED": "true",
        "JANUS_LIVE_ACTIVATION_MODE": "live",
        "JANUS_LIVE_TEST_SESSION_DATE": "2026-05-20",
        "JANUS_LIVE_TEST_EVENT_ID": "event-1",
        "JANUS_LIVE_TEST_ACCOUNT_ID": "account-1",
        "JANUS_LIVE_TEST_EXECUTE": "true",
        "JANUS_LIVE_TEST_LIVE_MONEY": "true",
        "JANUS_LIVE_TEST_MAX_INTENTS": "0",
    }

    result = live_activation.build_live_activation_preflight(
        scope="sports-live",
        env=env,
        worker_status={
            "ok": True,
            "status": "stopped",
            "enabled": False,
            "execute": False,
            "live_money": False,
            "account_id_configured": False,
            "event_ids": [],
        },
        janus_status={"ok": True, "status": "ok"},
        require_runtime=True,
    )

    assert result.ready is False
    assert result.status == "blocked"
    assert "sports-live: JANUS_LIVE_TEST_MAX_INTENTS must be > 0" in result.blockers
    assert "sports-live: Enable LLM dispatch or Codex reviewed fallback before live testing" in result.blockers
    assert "sports-live: live strategy worker is not running" in result.blockers
    assert "sports-live.start_live_worker" in result.next_commands
    assert "No order" in result.execution_statement


def test_live_activation_preflight_marks_both_scopes_ready_when_toggles_and_runtime_match() -> None:
    result = live_activation.build_live_activation_preflight(
        scope="both",
        env=_live_env(),
        worker_status=_ready_worker(),
        janus_status={"ok": True, "status": "ok"},
        require_runtime=True,
    )

    assert result.ready is True
    assert result.status == "ready_for_live_activation"
    assert result.blockers == []
    assert result.sports_live is not None
    assert result.sports_live.ready is True
    assert result.portfolio_manager is not None
    assert result.portfolio_manager.ready is True
    assert "--execute --live-money" in result.next_commands["sports-live.start_live_worker"]
    assert "--execution-approved" in result.next_commands["portfolio-manager.live_order"]


def test_portfolio_manager_live_activation_requires_reviewed_runtime_switches() -> None:
    result = live_activation.evaluate_portfolio_manager_activation(
        live_activation.build_portfolio_manager_config(
            {
                "JANUS_PORTFOLIO_MANAGER_ACTIVATION_MODE": "live",
                "JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID": PORTFOLIO_ACCOUNT_ID,
                "JANUS_PORTFOLIO_MANAGER_MAX_INITIAL_NOTIONAL_USD": "5",
                "JANUS_PORTFOLIO_MANAGER_TARGET_NOTIONAL_USD": "1",
                "JANUS_PORTFOLIO_MANAGER_DIRECT_TRUTH_MAX_AGE_SECONDS": "30",
            }
        )
    )

    assert result.ready is False
    assert "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED must be true for live mode" in result.blockers
    assert "JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED must be true for live mode" in result.blockers
    assert "JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR must be true for live mode" in result.blockers


def test_portfolio_manager_live_activation_rejects_wallet_account_id() -> None:
    result = live_activation.evaluate_portfolio_manager_activation(
        live_activation.build_portfolio_manager_config(
            {
                "JANUS_PORTFOLIO_MANAGER_ACTIVATION_MODE": "live",
                "JANUS_PORTFOLIO_MANAGER_ORDER_MANAGEMENT_ENABLED": "true",
                "JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID": "0x7d2F936Af73a54e06E1b78975503Ff1810F94fb4",
                "JANUS_PORTFOLIO_MANAGER_EXECUTION_APPROVED": "true",
                "JANUS_PORTFOLIO_MANAGER_REVIEWED_BY": "codex-global-portfolio-agent",
                "JANUS_PORTFOLIO_MANAGER_REASON": "approved live micro-position test",
                "JANUS_PORTFOLIO_MANAGER_KILL_SWITCH_CLEAR": "true",
                "JANUS_PORTFOLIO_MANAGER_MAX_INITIAL_NOTIONAL_USD": "5",
                "JANUS_PORTFOLIO_MANAGER_TARGET_NOTIONAL_USD": "1",
                "JANUS_PORTFOLIO_MANAGER_DIRECT_TRUTH_MAX_AGE_SECONDS": "30",
            }
        )
    )

    assert result.ready is False
    assert (
        "JANUS_PORTFOLIO_MANAGER_ACCOUNT_ID must be a Janus portfolio account UUID, not a wallet address"
        in result.blockers
    )


def test_live_activation_preflight_payload_is_json_serializable() -> None:
    result = live_activation.build_live_activation_preflight(
        scope="both",
        env=_live_env(),
        worker_status=_ready_worker(),
        janus_status={"ok": True, "status": "ok"},
        require_runtime=True,
    )

    payload = asdict(result)

    assert payload["schema_version"] == live_activation.LIVE_ACTIVATION_PREFLIGHT_SCHEMA_VERSION
    assert payload["sports_live"]["checks"]
