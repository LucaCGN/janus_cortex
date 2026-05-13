from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Start the service-owned Janus live strategy worker.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--event-id", action="append", dest="event_ids", default=[])
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--source", default="janus-live-strategy-worker")
    parser.add_argument("--interval-seconds", type=float, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--live-money", action="store_true")
    parser.add_argument("--enable-llm-dispatch", action="store_true")
    parser.add_argument("--submit-candidate-strategy-plan", action="store_true")
    parser.add_argument("--max-intents", type=int, default=None)
    parser.add_argument("--orderbook-sample-count", type=int, default=None)
    parser.add_argument("--orderbook-sample-interval-sec", type=float, default=None)
    parser.add_argument("--min-size", type=float, default=None)
    parser.add_argument("--min-buy-notional-usd", type=float, default=None)
    parser.add_argument("--share-precision", type=int, default=None)
    parser.add_argument("--manual-target-delta-cents", type=float, default=None)
    parser.add_argument("--no-auto-protect-manual-positions", action="store_true")
    args = parser.parse_args()
    payload = {
        "session_date": args.session_date,
        "event_ids": args.event_ids,
        "account_id": args.account_id,
        "source": args.source,
        "interval_seconds": args.interval_seconds,
        "timeout_seconds": args.timeout_seconds,
        "execute": args.execute,
        "live_money": args.live_money,
        "enable_llm_dispatch": args.enable_llm_dispatch,
        "submit_candidate_strategy_plan": args.submit_candidate_strategy_plan,
        "max_intents": args.max_intents,
        "orderbook_sample_count": args.orderbook_sample_count,
        "orderbook_sample_interval_sec": args.orderbook_sample_interval_sec,
        "min_size": args.min_size,
        "min_buy_notional_usd": args.min_buy_notional_usd,
        "share_precision": args.share_precision,
        "manual_target_delta_cents": args.manual_target_delta_cents,
    }
    if args.no_auto_protect_manual_positions:
        payload["auto_protect_manual_positions"] = False
    exit_for_response(
        api_json(
            args.api_root,
            "POST",
            "/v1/ops/live-strategy-worker/start",
            {key: value for key, value in payload.items() if value is not None},
        )
    )


if __name__ == "__main__":
    main()
