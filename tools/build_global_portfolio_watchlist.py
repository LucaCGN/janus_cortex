from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.modules.agentic.global_portfolio import (
    build_watchlist_artifact,
    load_watchlist_source,
    render_watchlist_report,
)


DEFAULT_OUTPUT_DIR = REPO_ROOT / "local" / "shared" / "artifacts" / "global-portfolio-watchlist" / "2026-05-18"
DEFAULT_REPORT_PATH = (
    REPO_ROOT / "local" / "shared" / "reports" / "daily-live-validation" / "global_portfolio_watchlist_schema_2026-05-18.md"
)


def build_from_source(
    *,
    source_json: Path | None = None,
    generated_at_utc: str | None = None,
) -> Any:
    source_caveats: list[str] = []
    entries: list[dict[str, Any]] = []
    if source_json is None:
        source_caveats.append("no_source_json_provided_schema_only")
    else:
        payload = json.loads(source_json.read_text(encoding="utf-8"))
        entries, source_caveats = load_watchlist_source(payload)
    return build_watchlist_artifact(
        entries,
        source_caveats=source_caveats,
        generated_at_utc=generated_at_utc,
    )


def write_outputs(
    artifact: Any,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = artifact.generated_at_utc.strftime("%Y%m%dT%H%M%SZ")
    artifact_path = output_dir / f"issue45_global_portfolio_watchlist_{stamp}.json"
    artifact_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        render_watchlist_report(artifact, artifact_path=_repo_relative(artifact_path)),
        encoding="utf-8",
    )
    return {"artifact_path": str(artifact_path), "report_path": str(report_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json", type=Path, default=None)
    parser.add_argument("--generated-at-utc", default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    artifact = build_from_source(source_json=args.source_json, generated_at_utc=args.generated_at_utc)
    if args.json_only:
        print(json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    paths = write_outputs(artifact, output_dir=args.output_dir, report_path=args.report_path)
    print(json.dumps(paths, indent=2, sort_keys=True))
    return 0


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
