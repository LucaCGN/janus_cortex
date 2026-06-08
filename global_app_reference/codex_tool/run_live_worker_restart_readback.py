from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.store import artifacts_root, session_date
from codex_tools.janus.client import base_parser, exit_for_response
from codex_tools.janus.worker import (
    build_live_strategy_worker_restart_readback,
    get_live_strategy_worker_status,
    write_live_strategy_worker_restart_readback_artifact,
)


def main() -> None:
    parser = base_parser("Read-only Janus live worker restart/fail-closed status probe.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--event-id", action="append", dest="event_ids", default=[])
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--write-artifact", action="store_true")
    args = parser.parse_args()

    status = get_live_strategy_worker_status(args.api_root)
    payload = build_live_strategy_worker_restart_readback(
        status,
        api_root=args.api_root,
        session_date=args.session_date,
        expected_event_ids=args.event_ids,
    )
    if args.write_artifact:
        root = (
            Path(args.artifact_root)
            if args.artifact_root
            else artifacts_root() / "live-worker-restart-readback" / session_date(args.session_date)
        )
        payload["artifact"] = write_live_strategy_worker_restart_readback_artifact(payload, root=root)
    exit_for_response(payload)


if __name__ == "__main__":
    main()
