from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime.controller_queue import (
    append_pass_ledger,
    check_claim,
    claim_lock,
    queue_status,
    release_lock,
)


def _csv_or_list(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for item in str(value).split(","):
            text = item.strip()
            if text:
                items.append(text)
    return items


def _json_output(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _add_resource_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", action="append", dest="files", default=[], help="File lock. Repeatable or comma-separated.")
    parser.add_argument("--module", action="append", dest="modules", default=[], help="Module lock. Repeatable or comma-separated.")
    parser.add_argument("--event", action="append", dest="events", default=[], help="Event lock. Repeatable or comma-separated.")
    parser.add_argument("--service", action="append", dest="services", default=[], help="Service lock. Repeatable or comma-separated.")
    parser.add_argument("--domain", action="append", dest="domains", default=[], help="Domain lock. Repeatable or comma-separated.")
    parser.add_argument("--market", action="append", dest="markets", default=[], help="Market lock. Repeatable or comma-separated.")
    parser.add_argument("--runtime", action="append", dest="runtimes", default=[], help="Runtime lock. Repeatable or comma-separated.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Janus controller active locks and pass ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Print active lock status.")
    status.set_defaults(func=_status)

    check = subparsers.add_parser("check", help="Check whether a claim would be accepted without writing a lock.")
    check.add_argument("--issue", required=False)
    _add_resource_args(check)
    check.add_argument("--require-clean-worktree", action="store_true")
    check.add_argument("--repo-root", type=Path)
    check.set_defaults(func=_check)

    claim = subparsers.add_parser("claim", help="Claim an issue/resource scope before editing.")
    claim.add_argument("--issue", required=False)
    claim.add_argument("--persona", required=True)
    claim.add_argument("--owner", required=True)
    claim.add_argument("--branch")
    claim.add_argument("--worktree")
    claim.add_argument("--lock-id")
    claim.add_argument("--stale-after-minutes", type=int, default=120)
    claim.add_argument("--require-clean-worktree", action="store_true")
    claim.add_argument("--repo-root", type=Path)
    _add_resource_args(claim)
    claim.set_defaults(func=_claim)

    release = subparsers.add_parser("release", help="Release an active lock and move it out of active locks.")
    release.add_argument("--lock-id", required=True)
    release.add_argument("--outcome", required=True)
    release.add_argument("--material-output", action="append", default=[])
    release.add_argument("--evidence", action="append", default=[])
    release.set_defaults(func=_release)

    ledger = subparsers.add_parser("ledger", help="Append a controller pass ledger entry without claiming a lock.")
    ledger.add_argument("--outcome", required=True)
    ledger.add_argument("--classification")
    ledger.add_argument("--persona")
    ledger.add_argument("--issue")
    ledger.add_argument("--owner")
    ledger.add_argument("--no-op-reason")
    ledger.add_argument("--material-output", action="append", default=[])
    ledger.add_argument("--evidence", action="append", default=[])
    ledger.set_defaults(func=_ledger)
    return parser


def _resource_kwargs(args: argparse.Namespace) -> dict[str, list[str]]:
    return {
        "files": _csv_or_list(args.files),
        "modules": _csv_or_list(args.modules),
        "events": _csv_or_list(args.events),
        "services": _csv_or_list(args.services),
        "domains": _csv_or_list(args.domains),
        "markets": _csv_or_list(args.markets),
        "runtimes": _csv_or_list(args.runtimes),
    }


def _status(args: argparse.Namespace) -> int:
    _json_output(queue_status())
    return 0


def _check(args: argparse.Namespace) -> int:
    payload = check_claim(
        issue=args.issue,
        **_resource_kwargs(args),
        require_clean_worktree=args.require_clean_worktree,
        repo_root=args.repo_root,
    )
    _json_output(payload)
    return 0 if payload.get("ok") else 2


def _claim(args: argparse.Namespace) -> int:
    payload = claim_lock(
        issue=args.issue,
        persona=args.persona,
        owner=args.owner,
        branch=args.branch,
        worktree=args.worktree,
        lock_id=args.lock_id,
        stale_after_minutes=args.stale_after_minutes,
        require_clean_worktree=args.require_clean_worktree,
        repo_root=args.repo_root,
        **_resource_kwargs(args),
    )
    _json_output(payload)
    return 0 if payload.get("ok") else 2


def _release(args: argparse.Namespace) -> int:
    payload = release_lock(
        args.lock_id,
        outcome=args.outcome,
        material_outputs=_csv_or_list(args.material_output),
        evidence_links=_csv_or_list(args.evidence),
    )
    _json_output(payload)
    return 0 if payload.get("ok") else 2


def _ledger(args: argparse.Namespace) -> int:
    payload = append_pass_ledger(
        {
            "outcome": args.outcome,
            "classification": args.classification,
            "selected_persona": args.persona,
            "issue": args.issue,
            "owner": args.owner,
            "no_op_reason": args.no_op_reason,
            "material_outputs": _csv_or_list(args.material_output),
            "evidence_links": _csv_or_list(args.evidence),
        }
    )
    _json_output({"status": "recorded", "ok": True, "entry": payload})
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
