from __future__ import annotations

from pathlib import Path

from crypto_options_app.config import CryptoOptionsAppConfig
from crypto_options_app.reports.live_dashboard import DEFAULT_ARTIFACT_ROOT as DASHBOARD_ARTIFACT_ROOT
from crypto_options_app.reports.system_integrity import DEFAULT_ARTIFACT_ROOT as HEALTH_ARTIFACT_ROOT
from crypto_options_app.workers.core_flow_live_runner import DEFAULT_ARTIFACT_ROOT as CORE_FLOW_ARTIFACT_ROOT
from crypto_options_app.workers.core_flow_live_runner import DEFAULT_DB_PATH as CORE_FLOW_DB_PATH
from crypto_options_app.workers.signal_live_runner import DEFAULT_ACTIVE_PROFILE_POOL, DEFAULT_ARTIFACT_ROOT, DEFAULT_DB_PATH


def test_crypto_options_defaults_are_centralized() -> None:
    config = CryptoOptionsAppConfig()
    assert config.db_path == Path("crypto_options_app/data/crypto_options_data.sqlite")
    assert config.artifact_root == Path("crypto_options_app/artifacts")
    assert config.active_profile_pool_path == Path("crypto_options_app/data/profile-pool/active_crypto_profile_pool.txt")
    assert config.docs_root == Path("crypto_options_app/docs/reference/crypto_options")
    assert DEFAULT_DB_PATH == config.db_path
    assert CORE_FLOW_DB_PATH == config.db_path
    assert DEFAULT_ARTIFACT_ROOT == config.artifact_root
    assert CORE_FLOW_ARTIFACT_ROOT == config.artifact_root
    assert DASHBOARD_ARTIFACT_ROOT == config.artifact_root
    assert HEALTH_ARTIFACT_ROOT == config.artifact_root
    assert DEFAULT_ACTIVE_PROFILE_POOL == config.active_profile_pool_path


def test_legacy_doc_folder_is_pointer_only() -> None:
    pointer = Path("app/docs/reference/crypto_options/README.md")
    assert pointer.exists()
    text = pointer.read_text(encoding="utf-8")
    assert "crypto_options_app/docs/reference/crypto_options" in text
    canonical = Path("crypto_options_app/docs/reference/crypto_options/technical_specs")
    assert canonical.exists()


def test_codex_tool_crypto_scripts_are_wrappers() -> None:
    wrapper_paths = sorted(Path("codex_tool").glob("run_crypto_options*.py"))
    assert wrapper_paths
    for wrapper in wrapper_paths:
        text = wrapper.read_text(encoding="utf-8")
        assert "crypto_options_app.scripts" in text


def test_runtime_code_does_not_default_to_old_crypto_artifact_roots() -> None:
    runtime_files = [
        Path("crypto_options_app/config.py"),
        Path("crypto_options_app/reports/live_dashboard.py"),
        Path("crypto_options_app/reports/system_integrity.py"),
        Path("crypto_options_app/workers/core_flow_live_runner.py"),
        Path("crypto_options_app/workers/signal_live_runner.py"),
        Path("crypto_options_app/scripts/run_crypto_options_app_core_flow_live.py"),
        Path("crypto_options_app/scripts/run_crypto_options_app_signal_live.py"),
        Path("crypto_options_app/scripts/run_crypto_options_app_settlement_report.py"),
    ]
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        assert "local/shared/artifacts/crypto-options-app" not in text
        assert "local/shared/artifacts/crypto-options-research/crypto_options_data.sqlite" not in text
