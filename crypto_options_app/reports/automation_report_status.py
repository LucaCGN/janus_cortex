from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


AUTOMATION_REPORT_STATUS_SCHEMA_VERSION = "crypto_options_automation_report_status_v1"
APP_ROOT = Path("crypto_options_app")
TEAM_COORDINATION_ROOT = APP_ROOT / "artifacts" / "team_coordination"
AUTOMATION_STATUS_ROOT = TEAM_COORDINATION_ROOT / "automation_status"


@dataclass(frozen=True)
class AutomationReportStatusOptions:
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    automation_status_root: Path = AUTOMATION_STATUS_ROOT
    now_utc: datetime | None = None


AUTOMATION_REPORT_CONTRACTS: dict[str, dict[str, Any]] = {
    "crypto-options-db-data-observability": {
        "latest_markdown": "db_data_observability_latest.md",
        "cadence_minutes": 15,
        "max_age_minutes": 45,
        "required_sections": ["DB backend", "A/B/C/D freshness", "Manual orders avoided"],
    },
    "crypto-options-signal-strategy-queue-worker": {
        "latest_markdown": "signal_strategy_queue_worker_latest.md",
        "cadence_minutes": 15,
        "max_age_minutes": 45,
        "required_sections": ["Rows considered", "Decision", "Manual orders avoided"],
    },
    "crypto-options-frontend-status-reporter": {
        "latest_markdown": "frontend_status_reporter_latest.md",
        "cadence_minutes": 60,
        "max_age_minutes": 180,
        "required_sections": ["Endpoints checked", "Frontend blockers", "Manual orders avoided"],
    },
}


def build_automation_report_status(
    options: AutomationReportStatusOptions | None = None,
) -> dict[str, Any]:
    options = options or AutomationReportStatusOptions()
    status_root = Path(options.automation_status_root)
    now = options.now_utc or datetime.now(UTC)
    rows = {
        automation_id: _automation_report_row(
            automation_id=automation_id,
            contract=contract,
            status_root=status_root,
            now=now,
        )
        for automation_id, contract in AUTOMATION_REPORT_CONTRACTS.items()
    }
    counts: dict[str, int] = {}
    for row in rows.values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    status = "fresh" if counts.get("fresh") == len(rows) else "degraded"
    if counts.get("pending_first_report") == len(rows):
        status = "pending_first_reports"
    return {
        "schema_version": AUTOMATION_REPORT_STATUS_SCHEMA_VERSION,
        "generated_at_utc": now.isoformat(),
        "status": status,
        "status_counts": counts,
        "status_root": str(status_root),
        "automations": rows,
        "safety": {
            "manual_orders_avoided": True,
            "live_trading_authorized": False,
            "orders_allowed": False,
            "note": "Report freshness does not authorize live trading or manual orders.",
        },
    }


def render_automation_report_status_markdown(review: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Automation Report Status",
        "",
        f"- Generated: `{review.get('generated_at_utc')}`",
        f"- Status: `{review.get('status')}`",
        f"- Status root: `{review.get('status_root')}`",
        "- Live authority: `none`",
        "",
        "## Automations",
    ]
    for automation_id, row in (review.get("automations") or {}).items():
        lines.extend(
            [
                "",
                f"### `{automation_id}`",
                "",
                f"- Status: `{row.get('status')}`",
                f"- Latest markdown: `{row.get('latest_markdown_path')}`",
                f"- Age minutes: `{row.get('age_minutes')}`",
                f"- Max age minutes: `{row.get('max_age_minutes')}`",
                "- Missing sections: "
                + (
                    "`none`"
                    if not row.get("missing_sections")
                    else ", ".join(f"`{item}`" for item in row.get("missing_sections") or [])
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- No automation report can authorize live trading.",
            "- No automation report can authorize manual orders.",
            "- Missing or stale reports should block adding more standing automations.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_automation_report_status_artifacts(
    review: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"automation_report_status_{stamp}.json"
    md_path = report_dir / f"automation_report_status_{stamp}.md"
    latest_json = report_dir / "automation_report_status_latest.json"
    latest_md = report_dir / "automation_report_status_latest.md"
    json_text = json.dumps(review, indent=2, sort_keys=True, default=str)
    md_text = render_automation_report_status_markdown(review)
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


def _automation_report_row(
    *,
    automation_id: str,
    contract: dict[str, Any],
    status_root: Path,
    now: datetime,
) -> dict[str, Any]:
    latest_path = status_root / str(contract["latest_markdown"])
    max_age_minutes = int(contract["max_age_minutes"])
    if not latest_path.exists():
        return {
            "status": "pending_first_report",
            "latest_markdown_path": str(latest_path),
            "exists": False,
            "age_minutes": None,
            "max_age_minutes": max_age_minutes,
            "missing_sections": list(contract.get("required_sections") or []),
        }
    stat = latest_path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    age_minutes = round((now - modified).total_seconds() / 60.0, 2)
    text = latest_path.read_text(encoding="utf-8", errors="replace")
    missing_sections = [section for section in contract.get("required_sections") or [] if section not in text]
    if missing_sections:
        status = "malformed"
    elif age_minutes > max_age_minutes:
        status = "stale"
    else:
        status = "fresh"
    return {
        "status": status,
        "latest_markdown_path": str(latest_path),
        "exists": True,
        "modified_at_utc": modified.isoformat(),
        "age_minutes": age_minutes,
        "max_age_minutes": max_age_minutes,
        "missing_sections": missing_sections,
    }
