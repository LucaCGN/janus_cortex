from __future__ import annotations

import json

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
    parser.add_argument("--external-trade-id", action="append", dest="external_trade_ids", default=[])
    parser.add_argument("--strategy-family", default=None)
    parser.add_argument("--manual-reason", default=None)
    parser.add_argument("--target-status", default=None)
    parser.add_argument("--stop-status", default=None)
    parser.add_argument("--hedge-status", default=None)
    parser.add_argument("--protective-order-status", default=None)
    parser.add_argument("--expected-close-path", default=None)
    parser.add_argument("--final-pnl-usd", type=float, default=None)
    parser.add_argument("--metadata-json", default=None)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()
    metadata = json.loads(args.metadata_json) if args.metadata_json else {}
    payload = {
        "account_id": args.account_id,
        "event_id": args.event_id,
        "market_id": args.market_id,
        "action": args.action,
        "external_order_ids": args.external_order_ids,
        "external_trade_ids": args.external_trade_ids,
        "strategy_family": args.strategy_family,
        "manual_reason": args.manual_reason,
        "target_status": args.target_status,
        "stop_status": args.stop_status,
        "hedge_status": args.hedge_status,
        "protective_order_status": args.protective_order_status,
        "expected_close_path": args.expected_close_path,
        "final_pnl_usd": args.final_pnl_usd,
        "metadata": metadata,
        "notes": args.notes,
    }
    exit_for_response(api_json(args.api_root, "POST", "/v1/operator/interventions/reconcile", payload))


if __name__ == "__main__":
    main()
