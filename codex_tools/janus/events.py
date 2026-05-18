"""Janus event/review helpers for the target Codex tools namespace."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from typing import Any

from codex_tools.janus.client import api_json, base_parser, exit_for_response

EVENT_AGENT_CONTEXT_PATH_TEMPLATE = "/v1/events/{event_id}/agent-context"
EVENT_REVIEW_BUNDLE_PATH_TEMPLATE = "/v1/events/{event_id}/review-bundle"
REPLAY_FROM_WATCH_SESSION_PATH = "/v1/replay/from-watch-session"
WATCHLIST_EVENTS_PATH = "/v1/watchlists/events"
WATCHLIST_CATEGORIES = ("nba", "crypto_options", "geopolitics", "other")


def get_event_context(
    api_root: str,
    event_id: str,
    *,
    session_date: str | None = None,
) -> dict[str, Any]:
    """Return Janus event context for Codex or Janus LLM review."""
    return api_json(
        api_root,
        "GET",
        EVENT_AGENT_CONTEXT_PATH_TEMPLATE.format(event_id=event_id),
        query={"session_date": session_date},
    )


def get_event_review_bundle(
    api_root: str,
    event_id: str,
    *,
    session_date: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Return one Janus event review bundle."""
    return api_json(
        api_root,
        "GET",
        EVENT_REVIEW_BUNDLE_PATH_TEMPLATE.format(event_id=event_id),
        query={"session_date": session_date, "account_id": account_id},
    )


def build_replay_from_watch_session_payload(args: Namespace) -> dict[str, Any]:
    """Build the replay request payload used by the legacy replay CLI."""
    return {
        "watch_session_id": args.watch_session_id,
        "event_key": args.event_key,
        "output_name": args.output_name,
        "notes": args.notes,
    }


def create_replay_from_watch_session(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Request replay construction from a captured market watch session."""
    return api_json(api_root, "POST", REPLAY_FROM_WATCH_SESSION_PATH, payload)


def build_watchlist_event(args: Namespace) -> dict[str, Any]:
    """Build one watchlist event payload from CLI args."""
    return {
        "event_key": args.event_key,
        "category": args.category,
        "title": args.title,
        "source_urls": args.source_urls,
        "market_id": args.market_id,
        "notes": args.notes,
        "passive_only": not bool(args.active),
    }


def build_watchlist_payload(args: Namespace) -> dict[str, Any]:
    """Build the Janus watchlist payload used by the legacy watch CLI."""
    return {"source": args.source, "events": [build_watchlist_event(args)]}


def add_watchlist_events(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Add market events to the Janus passive/live watchlist."""
    return api_json(api_root, "POST", WATCHLIST_EVENTS_PATH, payload)


def build_event_context_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--session-date", default=None)
    return parser


def build_event_review_bundle_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--account-id", default=None)
    return parser


def build_replay_from_watch_session_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--watch-session-id", required=True)
    parser.add_argument("--event-key", default=None)
    parser.add_argument("--output-name", default=None)
    parser.add_argument("--notes", default=None)
    return parser


def build_watch_market_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--category", default="other", choices=WATCHLIST_CATEGORIES)
    parser.add_argument("--source-url", action="append", dest="source_urls", default=[])
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--active", action="store_true", help="Set passive_only=false.")
    parser.add_argument("--source", default="codex")
    return parser


def main_for_event_context(description: str) -> None:
    args = build_event_context_parser(description).parse_args()
    exit_for_response(get_event_context(args.api_root, args.event_id, session_date=args.session_date))


def main_for_event_review_bundle(description: str) -> None:
    args = build_event_review_bundle_parser(description).parse_args()
    exit_for_response(
        get_event_review_bundle(
            args.api_root,
            args.event_id,
            session_date=args.session_date,
            account_id=args.account_id,
        )
    )


def main_for_replay_from_watch_session(description: str) -> None:
    args = build_replay_from_watch_session_parser(description).parse_args()
    exit_for_response(create_replay_from_watch_session(args.api_root, build_replay_from_watch_session_payload(args)))


def main_for_watch_market(description: str) -> None:
    args = build_watch_market_parser(description).parse_args()
    exit_for_response(add_watchlist_events(args.api_root, build_watchlist_payload(args)))
