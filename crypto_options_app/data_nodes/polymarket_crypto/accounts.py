from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import pandas as pd


POLYMARKET_WEB_BASE_URL = "https://polymarket.com"
POLYMARKET_DATA_API_BASE_URL = "https://data-api.polymarket.com"
POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_USER_PNL_API_BASE_URL = "https://user-pnl-api.polymarket.com/user-pnl"
PUBLIC_HEADERS = {
    "User-Agent": "Janus crypto-options research (read-only)",
    "Accept": "application/json,text/html,*/*",
}


def build_polymarket_account_research_report(
    *,
    handle: str | None = None,
    address: str | None = None,
    activity_pages: int = 1,
    positions_pages: int = 1,
    page_limit: int = 500,
) -> dict[str, Any]:
    """Build a public, read-only account behavior report.

    This fetches public profile/activity/position data only. It does not use
    authenticated account endpoints and does not infer private identity.
    """

    resolved = resolve_polymarket_profile(handle=handle, address=address)
    proxy_wallet = resolved.get("proxyWallet") or address
    if not proxy_wallet:
        return {
            "schema_version": "polymarket_crypto_account_research_v1",
            "status": "blocked",
            "blockers": ["missing_resolved_proxy_wallet"],
            "live_trading_authorized": False,
        }
    activity = fetch_user_activity(str(proxy_wallet), pages=activity_pages, limit=page_limit)
    positions = fetch_user_positions(str(proxy_wallet), pages=positions_pages, limit=page_limit)
    activity_df = normalize_user_activity(activity)
    positions_df = normalize_user_positions(positions)
    return analyze_polymarket_account(
        profile=resolved,
        activity_df=activity_df,
        positions_df=positions_df,
        activity_pages=activity_pages,
        positions_pages=positions_pages,
    )


def resolve_polymarket_profile(*, handle: str | None = None, address: str | None = None) -> dict[str, Any]:
    if address:
        try:
            profile = fetch_public_profile(address)
        except Exception:  # noqa: BLE001 - some active wallet profiles are not Gamma public-profile rows.
            profile = {}
        if profile:
            return profile
        return {"proxyWallet": address}
    if not handle:
        return {}
    seed = extract_profile_seed_from_html(fetch_profile_page(handle), handle=handle)
    proxy_wallet = seed.get("proxyWallet")
    if proxy_wallet:
        try:
            profile = fetch_public_profile(str(proxy_wallet))
        except Exception:  # noqa: BLE001 - keep profile-page seed if public-profile enrichment fails.
            profile = {}
        return {**seed, **profile} if profile else seed
    return seed


def fetch_profile_page(handle: str) -> str:
    normalized = str(handle).strip()
    if normalized.startswith("@"):
        path = normalized
    else:
        path = f"@{normalized}"
    request = Request(f"{POLYMARKET_WEB_BASE_URL}/{path}", headers=PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        return response.read().decode("utf-8", errors="replace")


def fetch_market_page(event_slug_or_url: str) -> str:
    """Fetch a public Polymarket market page for read-only profile discovery."""

    request = Request(_market_page_url(event_slug_or_url), headers=PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        return response.read().decode("utf-8", errors="replace")


def extract_profile_seed_from_html(document: str, *, handle: str | None = None) -> dict[str, Any]:
    """Extract a public proxy wallet from the Polymarket profile page payload."""

    match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(?P<payload>.*?)</script>', document, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(html.unescape(match.group("payload")))
    except json.JSONDecodeError:
        return {}
    candidates: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            proxy = value.get("proxyWallet")
            if proxy:
                candidates.append(
                    {
                        "proxyWallet": proxy,
                        "name": value.get("name"),
                        "pseudonym": value.get("pseudonym"),
                        "source": "polymarket_profile_page_next_data",
                    }
                )
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    if not candidates:
        return {}
    target = str(handle or "").lstrip("@").lower()
    for candidate in candidates:
        if target and str(candidate.get("name") or "").lower() == target:
            return candidate
    wallets = [str(candidate["proxyWallet"]) for candidate in candidates if candidate.get("proxyWallet")]
    if wallets:
        wallet = pd.Series(wallets).mode().iloc[0]
        for candidate in candidates:
            if str(candidate.get("proxyWallet")) == str(wallet):
                return candidate
    return candidates[0]


def fetch_public_profile(address: str) -> dict[str, Any]:
    query = urlencode({"address": str(address)})
    request = Request(f"{POLYMARKET_GAMMA_BASE_URL}/public-profile?{query}", headers=PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_user_activity(address: str, *, pages: int = 1, limit: int = 500) -> list[dict[str, Any]]:
    return _fetch_data_api_pages("activity", address, pages=pages, limit=limit)


def fetch_user_positions(address: str, *, pages: int = 1, limit: int = 500) -> list[dict[str, Any]]:
    return _fetch_data_api_pages("positions", address, pages=pages, limit=limit)


def fetch_user_trades(address: str, *, pages: int = 1, limit: int = 500) -> list[dict[str, Any]]:
    return _fetch_data_api_pages("trades", address, pages=pages, limit=limit)


def fetch_user_closed_positions(address: str, *, pages: int = 1, limit: int = 500) -> list[dict[str, Any]]:
    return _fetch_data_api_pages("closed-positions", address, pages=pages, limit=limit)


def fetch_user_pnl_history(address: str, *, interval: str = "1m", fidelity: str = "1d") -> list[dict[str, Any]]:
    query = urlencode({"user_address": str(address), "interval": str(interval), "fidelity": str(fidelity)})
    request = Request(f"{POLYMARKET_USER_PNL_API_BASE_URL}?{query}", headers=PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def fetch_top_holders_for_markets(
    condition_ids: list[str],
    *,
    limit: int = 20,
    min_balance: int = 1,
) -> list[dict[str, Any]]:
    """Fetch public top holders for one or more condition IDs.

    The Data API expects the `market` query parameter to be a comma-separated
    list of 0x-prefixed condition IDs, not event slugs or token IDs.
    """

    cleaned = [str(item).strip() for item in condition_ids if _is_condition_id(str(item).strip())]
    if not cleaned:
        return []
    safe_limit = min(max(int(limit), 0), 20)
    safe_min_balance = min(max(int(min_balance), 0), 999999)
    query = urlencode({"market": ",".join(cleaned), "limit": safe_limit, "minBalance": safe_min_balance})
    request = Request(f"{POLYMARKET_DATA_API_BASE_URL}/holders?{query}", headers=PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def scrape_market_profile_refs(
    event_slugs_or_urls: list[str],
    *,
    limit: int = 100,
    page_fetcher: Any | None = None,
) -> list[dict[str, Any]]:
    """Scrape public market pages for profile references and visible top-holder rows.

    This is a fallback for cases where the Data API is sparse or delayed. It is
    deliberately read-only and returns seedable profile refs plus optional side/share
    context when the rendered or copied page text contains the Top Holders table.
    """

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    safe_limit = max(0, int(limit))
    for target in event_slugs_or_urls:
        url = _market_page_url(target)
        try:
            document = page_fetcher(url) if page_fetcher else fetch_market_page(url)
        except Exception as exc:  # noqa: BLE001 - one market scrape should not block profile discovery.
            rows.append(
                {
                    "source": "market_page_scrape",
                    "source_url": url,
                    "event_slug": _event_slug_from_market_url(url),
                    "status": "blocked",
                    "blocker": f"{type(exc).__name__}:{exc}",
                }
            )
            continue
        parsed_rows = extract_top_holder_rows_from_market_document(
            str(document),
            source="market_page_scrape",
            source_url=url,
        )
        if not parsed_rows:
            parsed_rows = extract_profile_refs_from_market_html(
                str(document),
                source="market_page_profile_link_scrape",
                source_url=url,
            )
        for row in parsed_rows:
            key = (
                str(row.get("event_slug") or ""),
                str(row.get("side") or ""),
                str(row.get("raw_ref") or row.get("profile_label") or ""),
                str(row.get("shares") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            if safe_limit and len([item for item in rows if item.get("raw_ref")]) >= safe_limit:
                return rows
    return rows


def extract_profile_refs_from_market_html(
    document: str,
    *,
    source: str = "market_page_profile_link_scrape",
    source_url: str | None = None,
) -> list[dict[str, Any]]:
    """Extract profile links from Polymarket market HTML or rendered DOM HTML."""

    parser = _ProfileAnchorParser()
    parser.feed(document or "")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in parser.anchors:
        parsed = _profile_ref_from_href(str(anchor.get("href") or ""))
        if not parsed:
            continue
        raw_ref = parsed["raw_ref"]
        if raw_ref in seen:
            continue
        seen.add(raw_ref)
        rows.append(
            {
                "source": source,
                "source_url": source_url,
                "event_slug": _event_slug_from_market_url(source_url),
                "raw_ref": raw_ref,
                "address": parsed.get("address"),
                "handle": parsed.get("handle"),
                "profile_label": str(anchor.get("text") or "").strip() or None,
                "profile_href": anchor.get("href"),
                "status": "discovered",
            }
        )
    return rows


def extract_top_holder_rows_from_market_document(
    document: str,
    *,
    source: str = "market_page_text_scrape",
    source_url: str | None = None,
) -> list[dict[str, Any]]:
    """Extract visible Up/Down holder rows from rendered HTML or copied page text."""

    text = _visible_text_from_html(document) if _looks_like_html(document) else str(document or "")
    return extract_top_holder_rows_from_text(text, source=source, source_url=source_url)


def extract_top_holder_rows_from_text(
    text: str,
    *,
    source: str = "market_page_text_scrape",
    source_url: str | None = None,
) -> list[dict[str, Any]]:
    """Parse a visible Top Holders text block into holder rows.

    The parser accepts both English and Portuguese labels and handles the minimum
    fallback shape the UI exposes: side, holder label/address, and share count.
    """

    lines = [_clean_visible_line(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    rows: list[dict[str, Any]] = []
    side: str | None = None
    rank = 0
    index = 0
    event_slug = _event_slug_from_market_url(source_url)
    while index < len(lines):
        line = lines[index]
        detected_side = _holder_side_header(line)
        if detected_side:
            side = detected_side
            rank = 0
            index += 1
            if index < len(lines) and _is_holder_shares_header(lines[index]):
                index += 1
            continue
        if side is None or _is_holder_shares_header(line):
            index += 1
            continue
        if _looks_like_non_holder_section(line):
            side = None
            index += 1
            continue
        label = line
        shares: float | None = None
        if index + 1 < len(lines) and _parse_holder_shares(lines[index + 1]) is not None:
            shares = _parse_holder_shares(lines[index + 1])
            index += 2
        else:
            index += 1
        rank += 1
        ref = _profile_ref_from_visible_label(label)
        rows.append(
            {
                "source": source,
                "source_url": source_url,
                "event_slug": event_slug,
                "side": side,
                "rank": rank,
                "profile_label": label,
                "raw_ref": ref.get("raw_ref"),
                "address": ref.get("address"),
                "handle": ref.get("handle"),
                "shares": shares,
                "status": "discovered" if ref.get("raw_ref") else "blocked",
                "blocker": None if ref.get("raw_ref") else "truncated_or_unusable_profile_label",
            }
        )
    return rows


def normalize_user_activity(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        timestamp = _parse_timestamp(record.get("timestamp"))
        title = str(record.get("title") or "")
        rows.append(
            {
                "proxy_wallet": record.get("proxyWallet"),
                "timestamp": timestamp,
                "condition_id": record.get("conditionId"),
                "type": record.get("type"),
                "side": record.get("side"),
                "outcome": record.get("outcome"),
                "asset": record.get("asset"),
                "size": _to_float(record.get("size")),
                "usdc_size": _to_float(record.get("usdcSize")),
                "price": _to_float(record.get("price")),
                "title": title,
                "slug": record.get("slug"),
                "event_slug": record.get("eventSlug"),
                "symbol": _symbol_from_title(title),
                "cadence": _cadence_from_slug(str(record.get("eventSlug") or record.get("slug") or "")),
                "raw_json": record,
            }
        )
    return pd.DataFrame(rows).sort_values("timestamp", ascending=False, kind="mergesort").reset_index(drop=True) if rows else pd.DataFrame()


def normalize_user_positions(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        title = str(record.get("title") or "")
        rows.append(
            {
                "proxy_wallet": record.get("proxyWallet"),
                "condition_id": record.get("conditionId"),
                "asset": record.get("asset"),
                "outcome": record.get("outcome"),
                "size": _to_float(record.get("size")),
                "avg_price": _to_float(record.get("avgPrice")),
                "current_price": _to_float(record.get("curPrice")),
                "initial_value": _to_float(record.get("initialValue")),
                "current_value": _to_float(record.get("currentValue")),
                "cash_pnl": _to_float(record.get("cashPnl")),
                "percent_pnl": _to_float(record.get("percentPnl")),
                "realized_pnl": _to_float(record.get("realizedPnl")),
                "title": title,
                "slug": record.get("slug"),
                "event_slug": record.get("eventSlug"),
                "symbol": _symbol_from_title(title),
                "cadence": _cadence_from_slug(str(record.get("eventSlug") or record.get("slug") or "")),
                "redeemable": bool(record.get("redeemable")),
                "mergeable": bool(record.get("mergeable")),
                "raw_json": record,
            }
        )
    return pd.DataFrame(rows).reset_index(drop=True) if rows else pd.DataFrame()


def analyze_polymarket_account(
    *,
    profile: dict[str, Any],
    activity_df: pd.DataFrame,
    positions_df: pd.DataFrame,
    activity_pages: int,
    positions_pages: int,
) -> dict[str, Any]:
    activity = activity_df.copy()
    positions = positions_df.copy()
    activity_summary = _activity_summary(activity)
    position_summary = _position_summary(positions)
    fingerprints = _strategy_fingerprints(activity, positions)
    return {
        "schema_version": "polymarket_crypto_account_research_v1",
        "status": "complete",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "name": profile.get("name"),
            "pseudonym": profile.get("pseudonym"),
            "proxy_wallet": profile.get("proxyWallet"),
            "created_at": profile.get("createdAt"),
            "verified_badge": profile.get("verifiedBadge"),
        },
        "fetch_scope": {
            "activity_pages": int(activity_pages),
            "positions_pages": int(positions_pages),
            "activity_rows": int(len(activity)),
            "position_rows": int(len(positions)),
        },
        "activity_summary": activity_summary,
        "position_summary": position_summary,
        "strategy_fingerprints": fingerprints,
        "engineering_takeaways": [
            "The visible edge target should be modeled as high-turnover execution plus selective no-trade, not one static prediction rule.",
            "Two-sided books and overlapping 5m/15m/4h windows should be first-class backtest objects.",
            "Near-certain 0.90+ entries must be tested with real bid/ask/depth and latency because small adverse moves erase many small wins.",
            "Account-level PnL cannot be reproduced from screenshots without full closed-position ledger, fees, deposits, withdrawals, and all sells/redeems.",
        ],
        "live_trading_authorized": False,
        "orders_allowed": False,
    }


def _fetch_data_api_pages(endpoint: str, address: str, *, pages: int, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    safe_limit = min(max(int(limit), 1), 500)
    for page in range(max(int(pages), 1)):
        query = urlencode({"user": str(address), "limit": safe_limit, "offset": page * safe_limit})
        request = Request(f"{POLYMARKET_DATA_API_BASE_URL}/{endpoint}?{query}", headers=PUBLIC_HEADERS)
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < safe_limit:
            break
    return rows


def _is_condition_id(value: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", value))


def _activity_summary(activity: pd.DataFrame) -> dict[str, Any]:
    if activity.empty:
        return {"status": "empty"}
    buys = activity[(activity["type"] == "TRADE") & (activity["side"] == "BUY")]
    sells = activity[(activity["type"] == "TRADE") & (activity["side"] == "SELL")]
    redeems = activity[activity["type"] == "REDEEM"]
    latest = activity["timestamp"].max()
    last_12m = activity[activity["timestamp"] >= latest - pd.Timedelta(minutes=12)] if pd.notna(latest) else activity.iloc[0:0]
    return {
        "first_activity_utc": activity["timestamp"].min(),
        "latest_activity_utc": latest,
        "rows": int(len(activity)),
        "buy_count": int(len(buys)),
        "sell_count": int(len(sells)),
        "redeem_count": int(len(redeems)),
        "buy_outlay": _safe_sum(buys["usdc_size"]),
        "sell_proceeds": _safe_sum(sells["usdc_size"]),
        "redeem_proceeds": _safe_sum(redeems["usdc_size"]),
        "near_certain_buy_count": int((buys["price"] >= 0.90).sum()),
        "longshot_buy_count": int((buys["price"] <= 0.10).sum()),
        "symbols": _value_counts(activity, "symbol"),
        "cadences": _value_counts(activity, "cadence"),
        "last_12m": {
            "rows": int(len(last_12m)),
            "buy_count": int(((last_12m["type"] == "TRADE") & (last_12m["side"] == "BUY")).sum()),
            "redeem_count": int((last_12m["type"] == "REDEEM").sum()),
            "buy_outlay": _safe_sum(last_12m.loc[(last_12m["type"] == "TRADE") & (last_12m["side"] == "BUY"), "usdc_size"]),
            "redeem_proceeds": _safe_sum(last_12m.loc[last_12m["type"] == "REDEEM", "usdc_size"]),
            "unique_markets": int(last_12m["condition_id"].nunique()),
        },
    }


def _position_summary(positions: pd.DataFrame) -> dict[str, Any]:
    if positions.empty:
        return {"status": "empty"}
    total_value = _safe_sum(positions["current_value"])
    largest = positions.sort_values("current_value", ascending=False).head(1)
    two_sided = positions.groupby("condition_id")["outcome"].nunique()
    return {
        "rows": int(len(positions)),
        "total_current_value": total_value,
        "total_initial_value": _safe_sum(positions["initial_value"]),
        "total_cash_pnl": _safe_sum(positions["cash_pnl"]),
        "total_realized_pnl_visible": _safe_sum(positions["realized_pnl"]),
        "largest_position": largest.to_dict(orient="records")[0] if not largest.empty else None,
        "largest_position_value_share": _safe_float(largest.iloc[0]["current_value"] / total_value) if not largest.empty and total_value else None,
        "two_sided_market_count": int((two_sided > 1).sum()),
        "symbols_by_value": _sum_by(positions, "symbol", "current_value"),
        "directions_by_value": _sum_by(positions, "outcome", "current_value"),
        "cadences_by_value": _sum_by(positions, "cadence", "current_value"),
    }


def _strategy_fingerprints(activity: pd.DataFrame, positions: pd.DataFrame) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    if not activity.empty:
        buys = activity[(activity["type"] == "TRADE") & (activity["side"] == "BUY")]
        fingerprints.append(
            {
                "name": "late_convergence_or_near_certain_accumulation",
                "evidence": {
                    "buy_count_at_90c_or_higher": int((buys["price"] >= 0.90).sum()),
                    "buy_notional_at_90c_or_higher": _safe_sum(buys.loc[buys["price"] >= 0.90, "usdc_size"]),
                },
                "backtest_requirement": "Must use executable ask, depth, fees, and latency; midpoint replay is not acceptable.",
            }
        )
        fingerprints.append(
            {
                "name": "cheap_tail_or_reversal_lottery_entries",
                "evidence": {
                    "buy_count_at_10c_or_lower": int((buys["price"] <= 0.10).sum()),
                    "buy_notional_at_10c_or_lower": _safe_sum(buys.loc[buys["price"] <= 0.10, "usdc_size"]),
                },
                "backtest_requirement": "Needs calibrated tail probability and strict max-loss streak tracking.",
            }
        )
        laddered = buys.groupby(["condition_id", "outcome"], dropna=False).size()
        fingerprints.append(
            {
                "name": "laddered_entries_same_market_side",
                "evidence": {
                    "max_buys_same_market_side": int(laddered.max()) if not laddered.empty else 0,
                    "market_side_groups_with_5_plus_buys": int((laddered >= 5).sum()) if not laddered.empty else 0,
                },
                "backtest_requirement": "Replay must support multiple entries per market, not one trade per event.",
            }
        )
    if not positions.empty:
        two_sided = positions.groupby("condition_id")["outcome"].nunique()
        fingerprints.append(
            {
                "name": "two_sided_inventory_or_hedged_window_books",
                "evidence": {
                    "active_two_sided_market_count": int((two_sided > 1).sum()),
                    "position_rows": int(len(positions)),
                },
                "backtest_requirement": "Position ledger must net Up/Down inventory and score portfolio-level PnL by market.",
            }
        )
        fingerprints.append(
            {
                "name": "overlapping_cadence_spread",
                "evidence": {
                    "active_cadences": _value_counts(positions, "cadence"),
                    "active_symbols": _value_counts(positions, "symbol"),
                },
                "backtest_requirement": "Engine should model 5m, 15m, hourly, and 4h contracts as correlated but distinct payoff books.",
            }
        )
    return fingerprints


def _parse_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    unit = "ms" if isinstance(value, (int, float)) and float(value) >= 10_000_000_000 else "s"
    try:
        parsed = pd.to_datetime(value, unit=unit, utc=True)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(parsed) else parsed


def _symbol_from_title(title: str) -> str | None:
    lowered = title.lower()
    if "bitcoin" in lowered or "btc" in lowered:
        return "BTC"
    if "ethereum" in lowered or "eth" in lowered:
        return "ETH"
    if "solana" in lowered or "sol" in lowered:
        return "SOL"
    if "xrp" in lowered:
        return "XRP"
    return None


def _cadence_from_slug(slug: str) -> str | None:
    match = re.search(r"updown-(\d+m|\d+h)-", slug)
    if match:
        return match.group(1)
    if re.search(r"\d+am-et|\d+pm-et|up-or-down-[a-z]+-\d+-\d{4}-\d+(?:am|pm)-et", slug):
        return "hourly_or_daily"
    return None


def _value_counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if column not in frame.columns:
        return {}
    return {str(key): int(value) for key, value in frame[column].fillna("unknown").value_counts().to_dict().items()}


def _sum_by(frame: pd.DataFrame, group_column: str, value_column: str) -> dict[str, float]:
    if group_column not in frame.columns or value_column not in frame.columns:
        return {}
    grouped = frame.groupby(frame[group_column].fillna("unknown"))[value_column].sum()
    return {str(key): _safe_float(value) or 0.0 for key, value in grouped.to_dict().items()}


def _safe_sum(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum())


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    return _to_float(value)


class _ProfileAnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._href_stack: list[str | None] = []
        self._text_stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        self._href_stack.append(href)
        self._text_stack.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href_stack:
            return
        href = self._href_stack.pop()
        text_parts = self._text_stack.pop() if self._text_stack else []
        self.anchors.append({"href": href or "", "text": _clean_visible_line(" ".join(text_parts))})

    def handle_data(self, data: str) -> None:
        if self._text_stack:
            self._text_stack[-1].append(data)


class _VisibleTextParser(HTMLParser):
    _BLOCK_TAGS = {"br", "div", "p", "li", "tr", "td", "th", "section", "article", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _market_page_url(event_slug_or_url: str) -> str:
    value = str(event_slug_or_url or "").strip()
    if not value:
        return f"{POLYMARKET_WEB_BASE_URL}/event/"
    if value.startswith(("http://", "https://")):
        return value
    cleaned = value.split("?", 1)[0].strip("/")
    if cleaned.startswith("@") or cleaned.startswith("profile/"):
        return f"{POLYMARKET_WEB_BASE_URL}/{cleaned}"
    if cleaned.startswith("event/"):
        return f"{POLYMARKET_WEB_BASE_URL}/{cleaned}"
    return f"{POLYMARKET_WEB_BASE_URL}/event/{cleaned}"


def _event_slug_from_market_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(str(url)).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[-2] == "event":
        return parts[-1]
    if len(parts) >= 3 and parts[-3] in {"pt", "en"} and parts[-2] == "event":
        return parts[-1]
    if "event" in parts:
        index = parts.index("event")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def _profile_ref_from_href(href: str) -> dict[str, str] | None:
    cleaned = str(href or "").split("?", 1)[0].strip()
    match = re.match(r"^/(?:[a-z]{2}/)?profile/(?P<address>0x[a-fA-F0-9]{40})/?$", cleaned)
    if match:
        address = match.group("address").lower()
        return {"raw_ref": f"profile/{address}", "address": address}
    match = re.match(r"^/(?:[a-z]{2}/)?@(?P<handle>[A-Za-z0-9_.-]+)/?$", cleaned)
    if match:
        handle = match.group("handle")
        return {"raw_ref": f"@{handle}", "handle": handle}
    return None


def _profile_ref_from_visible_label(label: str) -> dict[str, str | None]:
    cleaned = _clean_visible_line(label)
    if not cleaned:
        return {"raw_ref": None, "address": None, "handle": None}
    if re.fullmatch(r"0x[a-fA-F0-9]{40}", cleaned):
        address = cleaned.lower()
        return {"raw_ref": f"profile/{address}", "address": address, "handle": None}
    if re.fullmatch(r"0x[a-fA-F0-9]{4,}\.\.\.[a-fA-F0-9]{4,}", cleaned):
        return {"raw_ref": None, "address": None, "handle": None}
    if re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", cleaned):
        return {"raw_ref": f"@{cleaned}", "address": None, "handle": cleaned}
    return {"raw_ref": None, "address": None, "handle": None}


def _visible_text_from_html(document: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(document or "")
    return "\n".join(_clean_visible_line(line) for line in "".join(parser.parts).splitlines())


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<(?:html|body|div|a|script|section|span)\b", str(value or ""), flags=re.IGNORECASE))


def _clean_visible_line(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _holder_side_header(value: str) -> str | None:
    lowered = value.lower().strip()
    if lowered in {"up holders", "up titulares", "titulares up"}:
        return "Up"
    if lowered in {"down holders", "down titulares", "titulares down"}:
        return "Down"
    return None


def _is_holder_shares_header(value: str) -> bool:
    return value.lower().strip() in {"shares", "share", "quotas", "cotas"}


def _looks_like_non_holder_section(value: str) -> bool:
    lowered = value.lower().strip()
    return lowered in {
        "comments",
        "comentários",
        "positions",
        "cargos",
        "activity",
        "actividade",
        "rules",
        "regras",
        "market context",
        "contexto de mercado",
        "back to top",
        "voltar ao início",
    }


def _parse_holder_shares(value: str) -> float | None:
    cleaned = str(value or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", cleaned):
        return None
    if "." in cleaned and "," not in cleaned:
        left, right = cleaned.split(".", 1)
        if len(right) == 3 and len(left) <= 3:
            return float(f"{left}{right}")
    if "," in cleaned and "." not in cleaned:
        left, right = cleaned.split(",", 1)
        if len(right) == 3 and len(left) <= 3:
            return float(f"{left}{right}")
        return float(f"{left}.{right}")
    if "," in cleaned and "." in cleaned:
        return float(cleaned.replace(",", ""))
    return float(cleaned)


__all__ = [
    "analyze_polymarket_account",
    "build_polymarket_account_research_report",
    "extract_profile_refs_from_market_html",
    "extract_profile_seed_from_html",
    "extract_top_holder_rows_from_market_document",
    "extract_top_holder_rows_from_text",
    "fetch_market_page",
    "fetch_public_profile",
    "fetch_top_holders_for_markets",
    "fetch_user_activity",
    "fetch_user_closed_positions",
    "fetch_user_pnl_history",
    "fetch_user_positions",
    "fetch_user_trades",
    "normalize_user_activity",
    "normalize_user_positions",
    "resolve_polymarket_profile",
    "scrape_market_profile_refs",
]
