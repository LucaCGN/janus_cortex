from __future__ import annotations

"""Generate the issue #47 crypto-options research artifact.

This CLI is read-only. It writes research artifacts only and never places, cancels,
signs, broadcasts, redeems, or routes orders.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable  # noqa: E402
from app.data.nodes.crypto.candles import fetch_binance_candles_for_events, normalize_candle_records  # noqa: E402
from app.data.nodes.crypto.reference import normalize_reference_price_reports  # noqa: E402
from app.data.nodes.crypto.reference import ChainlinkDataStreamsRestProvider, CryptoReferenceSourceUnavailable  # noqa: E402
from app.data.nodes.crypto.reference import fetch_chainlink_reference_reports_for_events  # noqa: E402
from app.data.nodes.crypto.reference import reference_reports_from_candles  # noqa: E402
from crypto_options_app.data_nodes.polymarket_crypto.history import (  # noqa: E402
    download_pmxt_orderbooks_for_events,
    fetch_clob_prices_history_for_event_windows,
    fetch_clob_prices_history_for_outcomes,
    fetch_current_order_books_for_outcomes,
    normalize_clob_observations,
    read_pmxt_orderbook_parquet,
)
from crypto_options_app.data_nodes.polymarket_crypto.accounts import build_polymarket_account_research_report  # noqa: E402
from crypto_options_app.data_nodes.polymarket_crypto.markets import (  # noqa: E402
    fetch_gamma_event_by_slug,
    fetch_recurring_updown_events_by_slug_range,
    normalize_polymarket_crypto_events,
)
from app.data.pipelines.crypto.options.reporting import strict_jsonable, write_research_artifacts  # noqa: E402
from app.services.crypto_options.service import build_crypto_options_service_report  # noqa: E402


def run(args: argparse.Namespace) -> dict[str, Any]:
    ingestion_attempts: dict[str, Any] = {}
    events_df = _load_events(
        getattr(args, "events_json", None),
        gamma_event_slug=getattr(args, "gamma_event_slug", None),
        slug_range_start=getattr(args, "event_range_start", None),
        slug_range_end=getattr(args, "event_range_end", None),
        slug_range_symbol=getattr(args, "event_range_symbol", "BTC"),
        slug_range_cadence_minutes=getattr(args, "event_range_cadence_minutes", 5),
        slug_range_limit=getattr(args, "event_range_limit", None),
        ingestion_attempts=ingestion_attempts,
    )
    clob_df = _load_clob(args.clob_json, args.clob_csv)
    if getattr(args, "fetch_clob_window_history", False) and clob_df.empty and not events_df.empty:
        clob_df = fetch_clob_prices_history_for_event_windows(
            events_df,
            pre_window_seconds=getattr(args, "clob_window_pre_seconds", 300),
            post_window_seconds=getattr(args, "clob_window_post_seconds", 300),
            fidelity=getattr(args, "clob_fidelity", None) or 1,
        )
        ingestion_attempts["polymarket_clob_window_history"] = {"status": "complete", "rows": int(len(clob_df))}
    elif getattr(args, "fetch_clob_price_history", False) and clob_df.empty and not events_df.empty:
        clob_df = fetch_clob_prices_history_for_outcomes(
            events_df,
            interval=getattr(args, "clob_interval", None),
            fidelity=getattr(args, "clob_fidelity", None),
        )
    if getattr(args, "fetch_current_books", False) and not events_df.empty:
        clob_df = _concat_frames([clob_df, fetch_current_order_books_for_outcomes(events_df)])
    if getattr(args, "pmxt_parquet", None):
        token_ids = set(events_df["token_id"].dropna().astype(str)) if "token_id" in events_df.columns else None
        clob_df = _concat_frames([clob_df, read_pmxt_orderbook_parquet(args.pmxt_parquet, token_ids=token_ids)])
    if getattr(args, "download_pmxt_for_event_hours", False) and not events_df.empty:
        downloaded_clob, pmxt_attempts = download_pmxt_orderbooks_for_events(
            events_df,
            cache_dir=getattr(args, "pmxt_cache_dir", None) or str(REPO_ROOT / "local" / "shared" / "artifacts" / "crypto-options-research" / "pmxt-cache"),
            max_download_bytes=_mb_to_bytes(getattr(args, "pmxt_max_download_mb", None)),
            pre_window_seconds=getattr(args, "pmxt_window_pre_seconds", 900),
            post_window_seconds=getattr(args, "pmxt_window_post_seconds", 0),
        )
        clob_df = _concat_frames([clob_df, downloaded_clob])
        ingestion_attempts["pmxt_event_hour_downloads"] = pmxt_attempts
    candles_df = _load_candles(args.candles_csv)
    if getattr(args, "fetch_binance_candles", False) and candles_df.empty and not events_df.empty:
        try:
            candles_df = fetch_binance_candles_for_events(
                events_df,
                interval=getattr(args, "binance_interval", "1m"),
                lookback_minutes=getattr(args, "binance_lookback_minutes", 60),
                lookahead_minutes=getattr(args, "binance_lookahead_minutes", 5),
            )
            ingestion_attempts["binance_candles_fetch"] = {"status": "complete", "rows": int(len(candles_df))}
        except Exception as exc:  # noqa: BLE001 - provider failure should not kill artifact generation.
            ingestion_attempts["binance_candles_fetch"] = {"status": "blocked", "blocker": str(exc)}
    reference_df = _load_reference(getattr(args, "reference_json", None), getattr(args, "reference_csv", None))
    if getattr(args, "fetch_chainlink_reference", False) and reference_df.empty and not events_df.empty:
        try:
            reference_df = fetch_chainlink_reference_reports_for_events(events_df, ChainlinkDataStreamsRestProvider.from_env())
            ingestion_attempts["chainlink_reference_fetch"] = {"status": "complete", "rows": int(len(reference_df))}
        except CryptoReferenceSourceUnavailable as exc:
            ingestion_attempts["chainlink_reference_fetch"] = {"status": "blocked", "blocker": str(exc)}
    if getattr(args, "use_exchange_proxy_labels", False) and reference_df.empty and not candles_df.empty:
        reference_df = reference_reports_from_candles(candles_df, source="exchange_proxy_binance")
        ingestion_attempts["exchange_proxy_reference"] = {
            "status": "complete",
            "rows": int(len(reference_df)),
            "authority": "exchange_proxy",
            "canonical_settlement": False,
        }
    payload = build_crypto_options_service_report(events_df=events_df, clob_df=clob_df, candles_df=candles_df, reference_df=reference_df)
    if ingestion_attempts:
        payload["ingestion_attempts"] = ingestion_attempts
    if getattr(args, "account_handle", None) or getattr(args, "account_address", None):
        try:
            payload["account_reverse_engineering"] = build_polymarket_account_research_report(
                handle=getattr(args, "account_handle", None),
                address=getattr(args, "account_address", None),
                activity_pages=getattr(args, "account_activity_pages", 1),
                positions_pages=getattr(args, "account_positions_pages", 1),
                page_limit=getattr(args, "account_page_limit", 500),
            )
        except Exception as exc:  # noqa: BLE001 - account analysis should not block core research artifact generation.
            payload["account_reverse_engineering"] = {
                "schema_version": "polymarket_crypto_account_research_v1",
                "status": "blocked",
                "blockers": [f"{type(exc).__name__}:{exc}"],
                "live_trading_authorized": False,
            }
    artifacts = write_research_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return to_jsonable(strict_jsonable(payload))


def _load_events(
    path: str | None,
    *,
    gamma_event_slug: str | None = None,
    slug_range_start: str | None = None,
    slug_range_end: str | None = None,
    slug_range_symbol: str = "BTC",
    slug_range_cadence_minutes: int = 5,
    slug_range_limit: int | None = None,
    ingestion_attempts: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if gamma_event_slug:
        return normalize_polymarket_crypto_events([fetch_gamma_event_by_slug(gamma_event_slug)])
    if slug_range_start and slug_range_end:
        events, attempts = fetch_recurring_updown_events_by_slug_range(
            symbol=slug_range_symbol,
            cadence_minutes=slug_range_cadence_minutes,
            start=slug_range_start,
            end=slug_range_end,
            limit=slug_range_limit,
        )
        if ingestion_attempts is not None:
            ingestion_attempts["polymarket_gamma_slug_range"] = {
                "status": "complete" if events else "blocked",
                "attempts": attempts,
                "event_count": len(events),
            }
        return normalize_polymarket_crypto_events(events)
    if not path:
        return normalize_polymarket_crypto_events([])
    data = _read_json(path)
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        data = data["events"]
    if isinstance(data, list) and (not data or "markets" in data[0]):
        return normalize_polymarket_crypto_events(data)
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame()


def _load_clob(json_path: str | None, csv_path: str | None) -> pd.DataFrame:
    if json_path:
        data = _read_json(json_path)
        if isinstance(data, dict) and isinstance(data.get("observations"), list):
            data = data["observations"]
        return normalize_clob_observations(data if isinstance(data, list) else [])
    if csv_path:
        frame = pd.read_csv(csv_path)
        return normalize_clob_observations(frame.to_dict(orient="records"))
    return normalize_clob_observations([])


def _load_candles(path: str | None) -> pd.DataFrame:
    if not path:
        return normalize_candle_records([])
    frame = pd.read_csv(path)
    return normalize_candle_records(frame.to_dict(orient="records"))


def _load_reference(json_path: str | None, csv_path: str | None) -> pd.DataFrame:
    if json_path:
        data = _read_json(json_path)
        if isinstance(data, dict) and isinstance(data.get("reports"), list):
            data = data["reports"]
        return normalize_reference_price_reports(data if isinstance(data, list) else [])
    if csv_path:
        frame = pd.read_csv(csv_path)
        return normalize_reference_price_reports(frame.to_dict(orient="records"))
    return normalize_reference_price_reports([])


def _concat_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return normalize_clob_observations([])
    return pd.concat(non_empty, ignore_index=True)


def _mb_to_bytes(value: int | None) -> int | None:
    if value is None or int(value) <= 0:
        return None
    return int(value) * 1024 * 1024


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the read-only crypto-options research audit/backtest artifact generator.")
    parser.add_argument("--events-json", default=None, help="Optional Gamma-like events JSON or normalized outcome rows.")
    parser.add_argument("--gamma-event-slug", default=None, help="Optional public Gamma event slug to fetch read-only.")
    parser.add_argument("--event-range-start", default=None, help="UTC start for generated recurring up/down slugs.")
    parser.add_argument("--event-range-end", default=None, help="UTC end for generated recurring up/down slugs.")
    parser.add_argument("--event-range-symbol", default="BTC", help="Symbol for generated recurring up/down slugs.")
    parser.add_argument("--event-range-cadence-minutes", type=int, default=5, help="Cadence minutes for generated up/down slugs.")
    parser.add_argument("--event-range-limit", type=int, default=None, help="Maximum generated slugs to fetch.")
    parser.add_argument("--clob-json", default=None, help="Optional normalized CLOB observation JSON.")
    parser.add_argument("--clob-csv", default=None, help="Optional normalized CLOB observation CSV.")
    parser.add_argument("--fetch-clob-price-history", action="store_true", help="Fetch public read-only Polymarket odds history for loaded event tokens.")
    parser.add_argument("--fetch-clob-window-history", action="store_true", help="Fetch public Polymarket odds history around each event window with explicit start/end timestamps.")
    parser.add_argument("--clob-window-pre-seconds", type=int, default=300, help="Seconds before event open to include in windowed odds history.")
    parser.add_argument("--clob-window-post-seconds", type=int, default=300, help="Seconds after event close to include in windowed odds history.")
    parser.add_argument("--clob-interval", default=None, help="Optional Polymarket price-history interval such as 1h, 1d, all, or max.")
    parser.add_argument("--clob-fidelity", type=int, default=None, help="Optional price-history fidelity in minutes.")
    parser.add_argument("--fetch-current-books", action="store_true", help="Fetch current public Polymarket order books for loaded event tokens.")
    parser.add_argument("--pmxt-parquet", action="append", default=None, help="Optional PMXT v2 orderbook Parquet file. Repeatable.")
    parser.add_argument("--download-pmxt-for-event-hours", action="store_true", help="Download PMXT v2 hourly Parquet files for loaded event windows.")
    parser.add_argument("--pmxt-cache-dir", default=None, help="Local cache directory for downloaded PMXT v2 Parquet files.")
    parser.add_argument("--pmxt-max-download-mb", type=int, default=750, help="Maximum PMXT file size to download per hour. Use 0 for no limit.")
    parser.add_argument("--pmxt-window-pre-seconds", type=int, default=900, help="Seconds before each event open to retain from PMXT history.")
    parser.add_argument("--pmxt-window-post-seconds", type=int, default=0, help="Seconds after each event close to retain from PMXT history.")
    parser.add_argument("--candles-csv", default=None, help="Optional exchange-backed underlying candle CSV.")
    parser.add_argument("--fetch-binance-candles", action="store_true", help="Fetch public Binance candles for loaded event windows.")
    parser.add_argument("--binance-interval", default="1m", help="Binance kline interval for public candle fetch.")
    parser.add_argument("--binance-lookback-minutes", type=int, default=60, help="Minutes before the event window to fetch for indicators.")
    parser.add_argument("--binance-lookahead-minutes", type=int, default=5, help="Minutes after the event window to fetch for boundary labels.")
    parser.add_argument("--reference-json", default=None, help="Optional decoded Chainlink/reference reports JSON.")
    parser.add_argument("--reference-csv", default=None, help="Optional decoded Chainlink/reference reports CSV.")
    parser.add_argument("--fetch-chainlink-reference", action="store_true", help="Fetch authenticated Chainlink Data Streams reports from environment credentials.")
    parser.add_argument("--use-exchange-proxy-labels", action="store_true", help="Use fetched exchange candles as explicitly non-canonical proxy labels if no reference reports exist.")
    parser.add_argument("--account-handle", default=None, help="Optional public Polymarket @handle for account reverse-engineering research.")
    parser.add_argument("--account-address", default=None, help="Optional public Polymarket proxy wallet address for account research.")
    parser.add_argument("--account-activity-pages", type=int, default=1, help="Number of public Data API activity pages to fetch for account research.")
    parser.add_argument("--account-positions-pages", type=int, default=1, help="Number of public Data API position pages to fetch for account research.")
    parser.add_argument("--account-page-limit", type=int, default=500, help="Public Data API page limit for account research, capped by provider at 500.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    if "--json" in sys.argv:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        artifacts = payload.get("artifacts") or {}
        audit = payload.get("data_audit") or {}
        comparison = payload.get("strategy_comparison") or {}
        print(f"status={audit.get('status')}")
        print(f"strategy_status={comparison.get('status')}")
        print(f"polymarket_only_btc_reconstruction={(audit.get('polymarket_only_underlying_price_reconstruction') or {}).get('answer')}")
        print(f"live_trading_authorized={payload.get('live_trading_authorized')}")
        print(f"artifact_json={artifacts.get('json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
