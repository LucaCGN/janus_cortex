from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


COMPATIBILITY_WRAPPER_AUDIT_SCHEMA_VERSION = "crypto_options_compatibility_wrapper_audit_v1"

SOURCE_SCAN_ROOTS = (
    "crypto_options_app",
    "tests/crypto_options_app",
    "app",
    "tests/app",
    "codex_tool",
    "tests/codex_tool",
    "codex_tools",
    "tests/codex_tools",
    "tools",
    "tests/tools",
)
EXCLUDED_SCAN_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "artifacts",
    "build",
    "data",
    "dist",
    "global_app_reference",
    "node_modules",
    "tmp",
    "wnba_nba_app_reference",
}
TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_SCAN_FILE_BYTES = 500_000


def build_compatibility_wrapper_audit(
    batches: dict[str, Any] | None = None,
    *,
    repo_root: Path | None = None,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, Any]:
    repo_root = repo_root or Path.cwd()
    batches = batches or _load_latest_batches(artifact_root)
    entries = _batch_4_entries(batches)
    scan_index = _build_scan_index(repo_root)
    wrappers = [
        _audit_wrapper(entry=entry, repo_root=repo_root, scan_index=scan_index)
        for entry in entries
    ]
    family_counts = Counter(wrapper["family"] for wrapper in wrappers)
    decision_counts = Counter(wrapper["recommended_decision"] for wrapper in wrappers)
    active_reference_count = sum(1 for wrapper in wrappers if wrapper["active_reference_count"] > 0)
    documentation_reference_count = sum(1 for wrapper in wrappers if wrapper["documentation_reference_count"] > 0)
    no_reference_count = sum(1 for wrapper in wrappers if wrapper["reference_count"] == 0)
    generated_at = datetime.now(UTC).isoformat()
    gates = {
        "automatic_wrapper_moves_allowed": False,
        "active_import_cutover_required": active_reference_count > 0,
        "frontend_fixed_chat_can_start_after_batch_3": True,
        "signal_strategy_fixed_chat_requires_batch_4_and_github_source_of_truth": True,
        "github_source_of_truth_ready": False,
    }
    return {
        "schema_version": COMPATIBILITY_WRAPPER_AUDIT_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "source_batches_generated_at_utc": batches.get("generated_at_utc"),
        "status": "degraded" if active_reference_count else "review",
        "summary": {
            "wrapper_count": len(wrappers),
            "active_reference_wrapper_count": active_reference_count,
            "documentation_reference_wrapper_count": documentation_reference_count,
            "no_reference_wrapper_count": no_reference_count,
            "family_counts": dict(sorted(family_counts.items())),
            "recommended_decision_counts": dict(sorted(decision_counts.items())),
            "scanned_file_count": len(scan_index),
        },
        "gates": gates,
        "wrappers": wrappers,
        "next_actions": _next_actions(
            active_reference_count=active_reference_count,
            no_reference_count=no_reference_count,
        ),
        "fixed_chat_prompt_paths": {
            "bootstrap": "crypto_options_app/artifacts/team_coordination/fixed_chat_bootstrap.md",
            "frontend": "crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md",
            "signal_strategy": "crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md",
        },
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def render_compatibility_wrapper_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    gates = report.get("gates") or {}
    lines = [
        "# Crypto Options Compatibility Wrapper Audit",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Status: `{report.get('status')}`",
        f"- Manual orders avoided: `{report.get('manual_orders_avoided')}`",
        f"- Live trading authorized: `{report.get('live_trading_authorized')}`",
        "",
        "## Summary",
        f"- Wrapper candidates: `{summary.get('wrapper_count')}`",
        f"- Wrappers referenced by active crypto code/tests: `{summary.get('active_reference_wrapper_count')}`",
        f"- Wrappers referenced only by docs/reference text: `{summary.get('documentation_reference_wrapper_count')}`",
        f"- Wrappers with no detected references: `{summary.get('no_reference_wrapper_count')}`",
        f"- Scanned files: `{summary.get('scanned_file_count')}`",
        "",
        "## Gates",
    ]
    for key, value in gates.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Fixed Chat Prompt Paths"])
    for key, value in (report.get("fixed_chat_prompt_paths") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Families"])
    for family, count in (summary.get("family_counts") or {}).items():
        lines.append(f"- `{family}`: `{count}`")
    lines.extend(["", "## Recommended Decisions"])
    for decision, count in (summary.get("recommended_decision_counts") or {}).items():
        lines.append(f"- `{decision}`: `{count}`")
    lines.extend(["", "## Wrapper Samples"])
    for wrapper in list(report.get("wrappers") or [])[:18]:
        lines.append(
            "- "
            f"`{wrapper.get('path')}` -> `{wrapper.get('recommended_decision')}` "
            f"(active refs: `{wrapper.get('active_reference_count')}`, "
            f"all refs: `{wrapper.get('reference_count')}`)"
        )
        for reference in list(wrapper.get("references") or [])[:3]:
            lines.append(f"  - `{reference.get('path')}`")
    remaining = max(0, len(report.get("wrappers") or []) - 18)
    if remaining:
        lines.append(f"- ... `{remaining}` additional wrappers in JSON artifact")
    lines.extend(["", "## Next Actions"])
    for action in report.get("next_actions") or ["none"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_compatibility_wrapper_audit_artifacts(
    report: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"compatibility_wrapper_audit_{stamp}.json"
    md_path = report_dir / f"compatibility_wrapper_audit_{stamp}.md"
    latest_json = report_dir / "compatibility_wrapper_audit_latest.json"
    latest_md = report_dir / "compatibility_wrapper_audit_latest.md"
    json_text = json.dumps(report, indent=2, sort_keys=True, default=str)
    md_text = render_compatibility_wrapper_audit_markdown(report)
    json_path.write_text(json_text + "\n", encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    latest_json.write_text(json_text + "\n", encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "latest_json_path": str(latest_json),
        "latest_markdown_path": str(latest_md),
    }


def _audit_wrapper(
    *,
    entry: dict[str, Any],
    repo_root: Path,
    scan_index: list[dict[str, str]],
) -> dict[str, Any]:
    path = str(entry.get("path") or "").replace("\\", "/")
    tokens = _search_tokens(path)
    references = _references_for_tokens(path=path, tokens=tokens, scan_index=scan_index)
    active_references = [
        reference
        for reference in references
        if _is_active_code_reference(reference)
    ]
    documentation_references = [
        reference
        for reference in references
        if _is_documentation_reference(reference["path"])
    ]
    family = _wrapper_family(path)
    matching_central_path = _matching_central_path(path, repo_root)
    decision = _recommended_decision(
        family=family,
        active_reference_count=len(active_references),
        reference_count=len(references),
        matching_central_path=matching_central_path,
    )
    return {
        "path": path,
        "family": family,
        "status": entry.get("status", ""),
        "proposed_destination": entry.get("proposed_destination"),
        "matching_central_path": matching_central_path,
        "reference_count": len(references),
        "active_reference_count": len(active_references),
        "documentation_reference_count": len(documentation_references),
        "recommended_decision": decision,
        "references": references[:20],
        "search_tokens": tokens,
    }


def _batch_4_entries(batches: dict[str, Any]) -> list[dict[str, Any]]:
    batch = (batches.get("batches") or {}).get("batch_4_crypto_compatibility_wrapper_cutover") or {}
    return list(batch.get("entries") or [])


def _build_scan_index(repo_root: Path) -> list[dict[str, str]]:
    index: list[dict[str, str]] = []
    for root_name in SOURCE_SCAN_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in _iter_text_files(root):
            rel_path = path.relative_to(repo_root).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
            except OSError:
                continue
            index.append({"path": rel_path, "text": text})
    return index


def _iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in EXCLUDED_SCAN_DIRS and not dirname.startswith(".")
        ]
        for filename in filenames:
            path = Path(current_root) / filename
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > MAX_SCAN_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
    return files


def _search_tokens(path: str) -> list[str]:
    tokens = {path, path.replace("/", "\\")}
    if path.endswith(".py"):
        module = path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        tokens.add(module)
        tokens.add(f"import {module}")
        parts = module.split(".")
        if len(parts) > 1:
            package = ".".join(parts[:-1])
            name = parts[-1]
            tokens.add(f"from {package} import {name}")
            tokens.add(f"from {module} import")
    return sorted(token for token in tokens if token)


def _references_for_tokens(
    *,
    path: str,
    tokens: list[str],
    scan_index: list[dict[str, str]],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for item in scan_index:
        scan_path = item["path"]
        if scan_path == path:
            continue
        text = item["text"]
        matched = [token for token in tokens if token in text]
        if matched:
            references.append({"path": scan_path, "matched_tokens": matched[:5]})
    return references


def _is_active_code_reference(reference: dict[str, str]) -> bool:
    path = reference["path"]
    if not path.endswith(".py"):
        return False
    matched_tokens = reference.get("matched_tokens") or []
    if not any(token.startswith(("from ", "import ")) for token in matched_tokens):
        return False
    if path.startswith("tests/crypto_options_app/"):
        return True
    if not path.startswith("crypto_options_app/"):
        return False
    return not path.startswith(
        (
            "crypto_options_app/artifacts/",
            "crypto_options_app/data/",
            "crypto_options_app/docs/",
        )
    )


def _is_documentation_reference(path: str) -> bool:
    return path.startswith(("crypto_options_app/docs/", "app/docs/")) or path.endswith(".md")


def _wrapper_family(path: str) -> str:
    if path.startswith("app/api/routers/"):
        return "legacy_api_router"
    if path.startswith("app/data/nodes/crypto/"):
        return "legacy_crypto_data_node"
    if path.startswith("app/data/nodes/polymarket/crypto/"):
        return "legacy_polymarket_crypto_data_node"
    if path.startswith("app/data/pipelines/crypto/options/"):
        return "legacy_options_pipeline"
    if path.startswith("app/data/pipelines/crypto/"):
        return "legacy_crypto_pipeline"
    if path.startswith("app/docs/reference/crypto_options"):
        return "legacy_crypto_documentation"
    if path.startswith("app/services/crypto_options/"):
        return "legacy_crypto_service"
    if path.startswith("codex_tool/run_crypto_options_"):
        return "legacy_cli_script_wrapper"
    if path.startswith("codex_tools/polymarket/"):
        return "legacy_polymarket_tool"
    if path.startswith("tests/"):
        return "legacy_crypto_test_wrapper"
    if path.startswith("tools/"):
        return "legacy_tool_wrapper"
    return "crypto_compatibility_wrapper"


def _matching_central_path(path: str, repo_root: Path) -> str | None:
    candidates: list[str] = []
    if path.startswith("codex_tool/"):
        candidates.append(path.replace("codex_tool/", "crypto_options_app/scripts/", 1))
    if path.startswith("app/docs/reference/crypto_options"):
        candidates.append(path.replace("app/docs/reference/crypto_options", "crypto_options_app/docs/reference/crypto_options", 1))
    if path.startswith("tests/app/"):
        candidates.append(path.replace("tests/app/", "tests/crypto_options_app/app/", 1))
    if path.startswith("tests/codex_tool/"):
        candidates.append(path.replace("tests/codex_tool/", "tests/crypto_options_app/codex_tool/", 1))
    for candidate in candidates:
        if (repo_root / candidate).exists():
            return candidate
    return None


def _recommended_decision(
    *,
    family: str,
    active_reference_count: int,
    reference_count: int,
    matching_central_path: str | None,
) -> str:
    if active_reference_count:
        return "keep_temporarily_cut_over_active_callers"
    if matching_central_path and family == "legacy_cli_script_wrapper":
        return "replace_with_crypto_options_app_script_entrypoint"
    if family == "legacy_crypto_test_wrapper":
        return "migrate_or_drop_duplicate_test_after_active_suite_mapping"
    if family == "legacy_crypto_documentation":
        return "migrate_to_crypto_options_docs_or_reference"
    if family in {"legacy_api_router", "legacy_crypto_data_node", "legacy_polymarket_crypto_data_node"}:
        return "hold_for_runtime_import_audit"
    if family in {"legacy_options_pipeline", "legacy_crypto_pipeline", "legacy_crypto_service"}:
        return "hold_for_strategy_signal_import_cutover"
    if family == "legacy_polymarket_tool":
        return "hold_for_runtime_import_audit"
    if reference_count == 0:
        return "migrate_into_compatibility_or_remove_after_tests"
    return "hold_for_import_audit"


def _next_actions(*, active_reference_count: int, no_reference_count: int) -> list[str]:
    actions = [
        "Do not bulk-move compatibility wrappers while active crypto code still imports old app/codex_tool paths.",
        "Cut active callers over to crypto_options_app modules/scripts in small tested groups.",
        "Keep frontend fixed chat eligible after Batch 3; it must use existing prompt and avoid backend promotion/runtime changes.",
        "Hold signal/strategy cleanup fixed chat until Batch 4 import decisions and GitHub issue source-of-truth are ready.",
    ]
    if active_reference_count:
        actions.append(f"Resolve active references for {active_reference_count} wrapper candidates before moving them.")
    if no_reference_count:
        actions.append(f"Review {no_reference_count} no-reference wrappers for compatibility archive or removal after tests.")
    return actions


def _load_latest_batches(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "repo_cleanup_batches_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"repo cleanup batches not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
