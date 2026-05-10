from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Ask Janus to reconcile operator/manual order interventions.")
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--action", default="scan")
    parser.add_argument("--external-order-id", action="append", dest="external_order_ids", default=[])
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()
    payload = {
        "account_id": args.account_id,
        "event_id": args.event_id,
        "market_id": args.market_id,
        "action": args.action,
        "external_order_ids": args.external_order_ids,
        "notes": args.notes,
    }
    exit_for_response(api_json(args.api_root, "POST", "/v1/operator/interventions/reconcile", payload))


if __name__ == "__main__":
    main()
