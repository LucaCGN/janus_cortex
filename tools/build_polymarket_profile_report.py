"""Build a Polymarket profile rentability report from public APIs.

The output is intended as a compact but information-dense Markdown artifact.
It combines UI-visible profile metrics with derived period and event diagnostics.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
PNL_BASE = "https://user-pnl-api.polymarket.com/user-pnl"
REPORT_DATE = datetime(2026, 5, 17, tzinfo=UTC)

PROFILES = [
    {"handle": "predictfolio", "url": "https://polymarket.com/@predictfolio"},
    {"handle": "pbot-6", "url": "https://polymarket.com/@pbot-6"},
    {"handle": "baloneigh", "url": "https://polymarket.com/@baloneigh"},
    {"handle": "mikeaddon", "url": "https://polymarket.com/@mikeaddon"},
    {
        "handle": "0xb55fa1296e6ec55d0ce53d93b9237389f11764d4-1777575277609",
        "url": "https://polymarket.com/@0xb55fa1296e6ec55d0ce53d93b9237389f11764d4-1777575277609?tab=positions",
        "wallet": "0xb55fa1296e6ec55d0ce53d93b9237389f11764d4",
    },
    {"handle": "wuhuuuuuuli", "url": "https://polymarket.com/@wuhuuuuuuli?tab=activity"},
    {"handle": "classified", "url": "https://polymarket.com/@classified"},
    {"handle": "car", "url": "https://polymarket.com/@car"},
    {"handle": "aenews2", "url": "https://polymarket.com/@aenews2"},
]

TARGET_CATEGORIES = [
    "Politics",
    "Sports",
    "Crypto",
    "Esports",
    "Iran",
    "Finance",
    "Geopolitics",
    "Tech",
    "Culture",
    "Economy",
    "Weather",
    "Mentions",
    "Elections",
]

CATEGORY_ALIASES = {
    "politics": "Politics",
    "political": "Politics",
    "us politics": "Politics",
    "sports": "Sports",
    "sport": "Sports",
    "nba": "Sports",
    "nfl": "Sports",
    "ncaa": "Sports",
    "tennis": "Sports",
    "soccer": "Sports",
    "football": "Sports",
    "crypto": "Crypto",
    "cryptocurrency": "Crypto",
    "bitcoin": "Crypto",
    "ethereum": "Crypto",
    "solana": "Crypto",
    "esports": "Esports",
    "e-sports": "Esports",
    "iran": "Iran",
    "finance": "Finance",
    "financial": "Finance",
    "geopolitics": "Geopolitics",
    "geopolitical": "Geopolitics",
    "world": "Geopolitics",
    "tech": "Tech",
    "technology": "Tech",
    "culture": "Culture",
    "pop culture": "Culture",
    "music": "Culture",
    "awards": "Culture",
    "economy": "Economy",
    "economics": "Economy",
    "weather": "Weather",
    "mentions": "Mentions",
    "mention": "Mentions",
    "elections": "Elections",
    "election": "Elections",
}

KEYWORD_CATEGORIES = [
    ("Iran", ["iran", "khamenei", "pahlavi", "tehran"]),
    ("Crypto", ["bitcoin", "btc", "ethereum", "eth ", "solana", "sol ", "xrp", "crypto"]),
    ("Sports", ["nba", "nfl", "ncaa", "bulldogs", "illini", "state owls", "championship", "match"]),
    ("Culture", ["eurovision", "oscars", "grammy", "music", "movie", "song", "youtube"]),
    ("Elections", ["election", "presidential", "senate", "congress", "democrat", "republican"]),
    ("Politics", ["trump", "biden", "vance", "white house", "president", "government"]),
    ("Geopolitics", ["israel", "hamas", "ukraine", "russia", "lebanon", "military", "ceasefire", "war"]),
    ("Tech", ["openai", "apple", "tesla", "google", "ai ", "iphone"]),
    ("Finance", ["fed", "rate cut", "stocks", "s&p", "nasdaq", "dow"]),
    ("Economy", ["inflation", "recession", "tariff", "gdp", "unemployment"]),
    ("Weather", ["hurricane", "temperature", "weather", "rain", "snow"]),
]

PERIODS = [
    ("1D", timedelta(days=1)),
    ("1W", timedelta(days=7)),
    ("1M", timedelta(days=30)),
    ("1Y", timedelta(days=365)),
    ("YTD", "ytd"),
    ("ALL", None),
]


@dataclass(frozen=True)
class EventMeta:
    slug: str
    title: str
    primary_category: str
    subcategory: str
    tags: tuple[str, ...]
    source: str


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def clean_text(value: Any) -> str:
    text = str(value or "")
    if any(marker in text for marker in ("Ã", "â", "�")):
        try:
            text = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize_wallet(value: str) -> str:
    return value.strip().lower()


class PolymarketClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "janus-cortex-profile-report/1.0",
                "Accept": "application/json",
            }
        )
        self.event_cache: dict[str, EventMeta] = {}

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, timeout: int = 45) -> Any:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=timeout)
                if response.status_code == 404:
                    return None
                if response.status_code == 400 and "offset" in response.text.lower():
                    return {"__error__": "offset", "message": response.text}
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                response.encoding = "utf-8"
                return response.json()
            except Exception as exc:  # noqa: BLE001 - report builder should continue across flaky API calls.
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET failed: {url} params={params} error={last_error}") from last_error

    def resolve_profile(self, item: dict[str, str]) -> dict[str, Any]:
        if item.get("wallet"):
            wallet = normalize_wallet(item["wallet"])
            public_profile = self.public_profile(wallet)
            return {
                "handle": item["handle"],
                "input_url": item["url"],
                "wallet": wallet,
                "name": clean_text(public_profile.get("name") or item["handle"]),
                "pseudonym": clean_text(public_profile.get("pseudonym") or ""),
                "createdAt": public_profile.get("createdAt"),
                "verifiedBadge": bool(public_profile.get("verifiedBadge")),
            }

        handle = item["handle"]
        search = self.get_json(
            f"{GAMMA_BASE}/public-search",
            {
                "q": handle,
                "search_profiles": "true",
                "limit_per_type": 20,
                "cache": "false",
            },
        )
        profiles = (search or {}).get("profiles") or []
        if not profiles:
            raise RuntimeError(f"No Polymarket profile found for @{handle}")

        wanted = slugify(handle)
        chosen = None
        for candidate in profiles:
            name = str(candidate.get("name") or "")
            if slugify(name) == wanted:
                chosen = candidate
                break
        if chosen is None:
            chosen = profiles[0]

        wallet = normalize_wallet(str(chosen["proxyWallet"]))
        public_profile = self.public_profile(wallet)
        return {
            "handle": handle,
            "input_url": item["url"],
            "wallet": wallet,
            "name": clean_text(public_profile.get("name") or chosen.get("name") or handle),
            "pseudonym": clean_text(public_profile.get("pseudonym") or chosen.get("pseudonym") or ""),
            "createdAt": public_profile.get("createdAt") or chosen.get("createdAt"),
            "verifiedBadge": bool(public_profile.get("verifiedBadge")),
        }

    def public_profile(self, wallet: str) -> dict[str, Any]:
        return self.get_json(f"{GAMMA_BASE}/public-profile", {"address": wallet}) or {}

    def fetch_pnl(self, wallet: str, *, interval: str, fidelity: str) -> list[dict[str, Any]]:
        data = self.get_json(
            PNL_BASE,
            {"user_address": wallet, "interval": interval, "fidelity": fidelity},
            timeout=60,
        )
        if not isinstance(data, list):
            return []
        return sorted(data, key=lambda row: int(row.get("t") or 0))

    def fetch_paginated(
        self,
        endpoint: str,
        wallet: str,
        *,
        limit: int = 500,
        max_offset: int = 10000,
        extra: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        capped = False
        offset = 0
        while offset <= max_offset:
            params: dict[str, Any] = {"user": wallet, "limit": limit, "offset": offset}
            if extra:
                params.update(extra)
            page = self.get_json(f"{DATA_BASE}/{endpoint}", params)
            if page is None:
                break
            if isinstance(page, dict) and page.get("__error__") == "offset":
                capped = True
                break
            if not isinstance(page, list):
                raise RuntimeError(f"Unexpected {endpoint} response for {wallet}: {type(page)}")
            rows.extend(page)
            if len(page) < limit:
                break
            offset += limit
            if offset > max_offset:
                capped = True
                break
        return rows, capped

    def fetch_trades(self, wallet: str) -> tuple[list[dict[str, Any]], bool]:
        rows, capped = self.fetch_paginated("trades", wallet, limit=1000, max_offset=3000)
        return rows, capped

    def fetch_value(self, wallet: str) -> float:
        data = self.get_json(f"{DATA_BASE}/value", {"user": wallet})
        if isinstance(data, list) and data:
            return float(data[0].get("value") or 0)
        return 0.0

    def fetch_traded_count(self, wallet: str) -> int:
        data = self.get_json(f"{DATA_BASE}/traded", {"user": wallet})
        if isinstance(data, dict):
            return int(data.get("traded") or 0)
        return 0

    def event_meta(self, slug: str, fallback_title: str = "") -> EventMeta:
        if not slug:
            return infer_event_meta("", fallback_title, {}, "missing")
        if slug in self.event_cache:
            return self.event_cache[slug]

        encoded = quote(slug, safe="")
        payload = self.get_json(f"{GAMMA_BASE}/events/slug/{encoded}", {"includeMarkets": "false"})
        source = "gamma event"
        if payload is None:
            payload = self.get_json(f"{GAMMA_BASE}/markets/slug/{encoded}")
            source = "gamma market"
        meta = infer_event_meta(slug, fallback_title, payload or {}, source if payload else "heuristic")
        self.event_cache[slug] = meta
        return meta


def parse_timestamp(row: dict[str, Any]) -> int:
    try:
        return int(row.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def parse_iso_date(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        clean = value.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).strftime("%b %Y")
    except ValueError:
        return value[:10]


def money(value: float | int | None, *, compact: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if compact:
        if value >= 1_000_000:
            return f"{sign}${value / 1_000_000:.2f}M"
        if value >= 1_000:
            return f"{sign}${value / 1_000:.1f}K"
    return f"{sign}${value:,.2f}"


def number(value: float | int | None, digits: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if digits == 0:
        return f"{float(value):,.0f}"
    return f"{float(value):,.{digits}f}"


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:.{digits}f}%"


def safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def trade_cash(row: dict[str, Any]) -> float:
    return safe_float(row.get("size")) * safe_float(row.get("price"))


def closed_cost(row: dict[str, Any]) -> float:
    return safe_float(row.get("totalBought")) * safe_float(row.get("avgPrice"))


def period_start(label: str, delta: timedelta | str | None, now: datetime) -> int | None:
    if delta is None:
        return None
    if delta == "ytd":
        return int(datetime(now.year, 1, 1, tzinfo=UTC).timestamp())
    return int((now - delta).timestamp())


def latest_pnl(candles: list[dict[str, Any]]) -> float | None:
    if not candles:
        return None
    return safe_float(max(candles, key=lambda row: int(row.get("t") or 0)).get("p"))


def pnl_change(candles: list[dict[str, Any]], start_ts: int | None, *, all_time: bool = False) -> float | None:
    if not candles:
        return None
    latest = max(candles, key=lambda row: int(row.get("t") or 0))
    latest_value = safe_float(latest.get("p"))
    if all_time or start_ts is None:
        return latest_value

    before = [row for row in candles if int(row.get("t") or 0) <= start_ts]
    if before:
        baseline = max(before, key=lambda row: int(row.get("t") or 0))
    else:
        after = [row for row in candles if int(row.get("t") or 0) >= start_ts]
        baseline = min(after, key=lambda row: int(row.get("t") or 0)) if after else candles[0]
    return latest_value - safe_float(baseline.get("p"))


def rows_since(rows: list[dict[str, Any]], start_ts: int | None) -> list[dict[str, Any]]:
    if start_ts is None:
        return rows
    return [row for row in rows if parse_timestamp(row) >= start_ts]


def distinct_event_key(row: dict[str, Any]) -> str:
    return str(row.get("eventSlug") or row.get("slug") or row.get("conditionId") or "")


def closed_win_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nonzero = [row for row in rows if abs(safe_float(row.get("realizedPnl"))) > 1e-9]
    wins = [row for row in nonzero if safe_float(row.get("realizedPnl")) > 0]
    losses = [row for row in nonzero if safe_float(row.get("realizedPnl")) < 0]
    total_cost = sum(closed_cost(row) for row in rows)
    total_pnl = sum(safe_float(row.get("realizedPnl")) for row in rows)
    return {
        "closed": len(rows),
        "nonzero": len(nonzero),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(nonzero) * 100 if nonzero else None,
        "closed_cost": total_cost,
        "closed_pnl": total_pnl,
        "closed_return_pct": total_pnl / total_cost * 100 if total_cost else None,
    }


def period_metrics(
    label: str,
    start_ts: int | None,
    pnl_value: float | None,
    trades: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    period_trades = rows_since(trades, start_ts)
    period_closed = rows_since(closed_positions, start_ts)
    traded_events = {distinct_event_key(row) for row in period_trades if distinct_event_key(row)}
    closed_events = {distinct_event_key(row) for row in period_closed if distinct_event_key(row)}
    buy_cash = sum(trade_cash(row) for row in period_trades if str(row.get("side") or "").upper() == "BUY")
    stats = closed_win_stats(period_closed)
    return {
        "period": label,
        "pnl": pnl_value,
        "buy_cash": buy_cash,
        "pnl_on_buy_cash_pct": (pnl_value / buy_cash * 100) if buy_cash and pnl_value is not None else None,
        "win_rate": stats["win_rate"],
        "closed": stats["closed"],
        "closed_pnl": stats["closed_pnl"],
        "trade_count": len(period_trades),
        "traded_events": len(traded_events),
        "avg_trades_per_event": len(period_trades) / len(traded_events) if traded_events else None,
        "closed_events": len(closed_events),
        "closure_rate": len(closed_events) / len(traded_events) * 100 if traded_events else None,
    }


def infer_category_from_labels(labels: list[str]) -> tuple[str, str]:
    hits: list[str] = []
    for label in labels:
        key = label.strip().lower()
        mapped = CATEGORY_ALIASES.get(key)
        if mapped and mapped not in hits:
            hits.append(mapped)

    if "Iran" in hits:
        primary = "Iran"
    elif "Elections" in hits and "Politics" in hits:
        primary = "Politics"
    elif hits:
        primary = hits[0]
    else:
        primary = "Other"

    subcategory = ""
    for hit in hits:
        if hit != primary:
            subcategory = hit
            break
    return primary, subcategory


def infer_event_meta(slug: str, fallback_title: str, payload: dict[str, Any], source: str) -> EventMeta:
    title = clean_text(payload.get("title") or payload.get("question") or fallback_title or slug or "Unknown event")
    labels: list[str] = []
    for field in ("category", "subcategory"):
        if payload.get(field):
            labels.append(clean_text(payload[field]))
    for collection_name in ("categories", "tags"):
        for item in payload.get(collection_name) or []:
            if isinstance(item, dict):
                label = item.get("label") or item.get("slug")
                if label:
                    labels.append(clean_text(label))
            elif item:
                labels.append(clean_text(item))

    primary, subcategory = infer_category_from_labels(labels)
    text = f"{title} {slug}".lower()
    if primary == "Other":
        for category, keywords in KEYWORD_CATEGORIES:
            if any(keyword in text for keyword in keywords):
                primary = category
                break
    if not subcategory:
        for category, keywords in KEYWORD_CATEGORIES:
            if category != primary and any(keyword in text for keyword in keywords):
                subcategory = category
                break

    cleaned_tags = tuple(dict.fromkeys(clean_text(label).strip() for label in labels if label.strip()))
    return EventMeta(
        slug=slug,
        title=title,
        primary_category=primary,
        subcategory=subcategory,
        tags=cleaned_tags,
        source=source,
    )


def category_path(meta: EventMeta) -> str:
    if meta.subcategory and meta.subcategory != meta.primary_category:
        return f"{meta.primary_category} / {meta.subcategory}"
    return meta.primary_category


def aggregate_events(closed_positions: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trade_counts: Counter[str] = Counter()
    for trade in trades:
        key = distinct_event_key(trade)
        if key:
            trade_counts[key] += 1

    grouped: dict[str, dict[str, Any]] = {}
    for row in closed_positions:
        key = distinct_event_key(row)
        if not key:
            continue
        entry = grouped.setdefault(
            key,
            {
                "eventSlug": key,
                "title": clean_text(row.get("title") or key),
                "closed_positions": 0,
                "wins": 0,
                "losses": 0,
                "breakevens": 0,
                "realized_pnl": 0.0,
                "traded_cost": 0.0,
                "latest_ts": 0,
                "trade_count": 0,
            },
        )
        entry["closed_positions"] += 1
        pnl = safe_float(row.get("realizedPnl"))
        entry["realized_pnl"] += pnl
        entry["traded_cost"] += closed_cost(row)
        entry["latest_ts"] = max(entry["latest_ts"], parse_timestamp(row))
        if pnl > 1e-9:
            entry["wins"] += 1
        elif pnl < -1e-9:
            entry["losses"] += 1
        else:
            entry["breakevens"] += 1
    for key, entry in grouped.items():
        nonzero = entry["wins"] + entry["losses"]
        entry["win_rate"] = entry["wins"] / nonzero * 100 if nonzero else None
        entry["return_pct"] = (
            entry["realized_pnl"] / entry["traded_cost"] * 100 if entry["traded_cost"] else None
        )
        entry["trade_count"] = trade_counts.get(key, 0)
        entry["trades_per_closed_position"] = (
            entry["trade_count"] / entry["closed_positions"] if entry["closed_positions"] else None
        )
    return list(grouped.values())


def fetch_event_metadata(
    client: PolymarketClient,
    event_rows: list[dict[str, Any]],
    *,
    max_events: int = 800,
) -> tuple[dict[str, EventMeta], int, int]:
    ranked = sorted(
        event_rows,
        key=lambda row: (
            abs(safe_float(row.get("realized_pnl"))),
            safe_float(row.get("traded_cost")),
            safe_float(row.get("closed_positions")),
        ),
        reverse=True,
    )
    selected = ranked[:max_events]
    metas: dict[str, EventMeta] = {}

    def load(row: dict[str, Any]) -> tuple[str, EventMeta]:
        slug = str(row.get("eventSlug") or "")
        return slug, client.event_meta(slug, str(row.get("title") or ""))

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        for slug, meta in executor.map(load, selected):
            if slug:
                metas[slug] = meta
    return metas, len(selected), len(event_rows)


def category_mix(
    event_rows: list[dict[str, Any]],
    metas: dict[str, EventMeta],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "category": "",
            "closed_positions": 0,
            "events": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl": 0.0,
            "traded_cost": 0.0,
        }
    )
    for row in event_rows:
        slug = str(row.get("eventSlug") or "")
        meta = metas.get(slug) or infer_event_meta(slug, str(row.get("title") or ""), {}, "heuristic")
        category = category_path(meta)
        bucket = buckets[category]
        bucket["category"] = category
        bucket["events"] += 1
        bucket["closed_positions"] += int(row.get("closed_positions") or 0)
        bucket["wins"] += int(row.get("wins") or 0)
        bucket["losses"] += int(row.get("losses") or 0)
        bucket["realized_pnl"] += safe_float(row.get("realized_pnl"))
        bucket["traded_cost"] += safe_float(row.get("traded_cost"))

    rows = list(buckets.values())
    for row in rows:
        nonzero = row["wins"] + row["losses"]
        row["win_rate"] = row["wins"] / nonzero * 100 if nonzero else None
        row["return_pct"] = row["realized_pnl"] / row["traded_cost"] * 100 if row["traded_cost"] else None
    return sorted(rows, key=lambda row: abs(row["realized_pnl"]), reverse=True)


def specialty_label(mix_rows: list[dict[str, Any]]) -> str:
    if not mix_rows:
        return "Unknown"
    target_rows = [row for row in mix_rows if row["category"] != "Other"]
    if not target_rows:
        return "Generalist"
    total_cost = sum(max(0.0, safe_float(row.get("traded_cost"))) for row in target_rows)
    top = max(target_rows, key=lambda row: safe_float(row.get("traded_cost")))
    if len(target_rows) >= 4 and total_cost and safe_float(top.get("traded_cost")) / total_cost < 0.55:
        return "Generalist"
    return str(top["category"]).split(" / ")[0]


def top_events(event_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    positive = [row for row in event_rows if safe_float(row.get("realized_pnl")) > 0]
    returnable = [row for row in event_rows if row.get("return_pct") is not None and safe_float(row.get("traded_cost")) >= 5]
    by_pnl = sorted(positive, key=lambda row: safe_float(row.get("realized_pnl")), reverse=True)[:3]
    by_return = sorted(
        returnable,
        key=lambda row: (safe_float(row.get("return_pct")), safe_float(row.get("realized_pnl"))),
        reverse=True,
    )[:3]
    by_win_rate = sorted(
        [row for row in event_rows if row.get("win_rate") is not None and int(row.get("closed_positions") or 0) >= 1],
        key=lambda row: (
            safe_float(row.get("win_rate")),
            int(row.get("closed_positions") or 0),
            safe_float(row.get("realized_pnl")),
        ),
        reverse=True,
    )[:3]
    return {"biggest_pnl": by_pnl, "best_return_pct": by_return, "best_win_rate": by_win_rate}


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        rows = [["n/a" for _ in headers]]
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    header_line = "| " + " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * widths[idx] for idx in range(len(headers))) + " |"
    body = [
        "| " + " | ".join(str(cell).ljust(widths[idx]) for idx, cell in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line, *body])


def summarize_top_events(
    rows: list[dict[str, Any]],
    metas: dict[str, EventMeta],
) -> str:
    table_rows: list[list[str]] = []
    for row in rows:
        slug = str(row.get("eventSlug") or "")
        meta = metas.get(slug) or infer_event_meta(slug, str(row.get("title") or ""), {}, "heuristic")
        title = clean_text(row.get("title") or meta.title)
        if len(title) > 72:
            title = title[:69] + "..."
        table_rows.append(
            [
                title,
                category_path(meta),
                money(safe_float(row.get("realized_pnl"))),
                pct(row.get("return_pct"), 1),
                pct(row.get("win_rate"), 1),
                number(row.get("closed_positions")),
                number(row.get("trade_count")),
                (
                    f"{number(safe_float(row.get('trade_count')) / safe_float(row.get('closed_positions')), 2)} trades/closed"
                    if safe_float(row.get("closed_positions"))
                    else "n/a"
                ),
            ]
        )
    return markdown_table(
        ["Event", "Category", "P/L", "% return", "Win rate", "Closed", "Trade rows", "Rows/closed"],
        table_rows,
    )


def build_profile_report(client: PolymarketClient, item: dict[str, str], now: datetime) -> dict[str, Any]:
    profile = client.resolve_profile(item)
    wallet = profile["wallet"]
    print(f"Fetching @{profile['handle']} ({wallet})")

    all_pnl = client.fetch_pnl(wallet, interval="all", fidelity="1d")
    recent_pnl = client.fetch_pnl(wallet, interval="1m", fidelity="1h")
    closed_positions, closed_capped = client.fetch_paginated(
        "closed-positions",
        wallet,
        limit=50,
        extra={"sortBy": "TIMESTAMP", "sortDirection": "DESC"},
    )
    positions, positions_capped = client.fetch_paginated(
        "positions",
        wallet,
        limit=500,
        extra={"sizeThreshold": 0, "sortBy": "CURRENT", "sortDirection": "DESC"},
    )
    trades, trades_capped = client.fetch_trades(wallet)
    position_value = client.fetch_value(wallet)
    traded_count = client.fetch_traded_count(wallet)

    event_rows = aggregate_events(closed_positions, trades)
    metas, categorized_events, total_events = fetch_event_metadata(client, event_rows)
    mix_rows = category_mix(event_rows, metas)
    tops = top_events(event_rows)

    current_pnl = latest_pnl(all_pnl)
    period_rows = []
    for label, delta in PERIODS:
        start = period_start(label, delta, now)
        candles = recent_pnl if label in {"1D", "1W", "1M"} else all_pnl
        pnl_value = pnl_change(candles, start, all_time=(label == "ALL"))
        period_rows.append(period_metrics(label, start, pnl_value, trades, closed_positions))

    open_initial = sum(safe_float(row.get("initialValue")) for row in positions)
    open_current = sum(safe_float(row.get("currentValue")) for row in positions)
    open_cash_pnl = sum(safe_float(row.get("cashPnl")) for row in positions)
    open_pct = open_cash_pnl / open_initial * 100 if open_initial else None
    closed_stats = closed_win_stats(closed_positions)
    biggest_win = max((safe_float(row.get("realizedPnl")) for row in closed_positions), default=0.0)
    biggest_loss = min((safe_float(row.get("realizedPnl")) for row in closed_positions), default=0.0)

    return {
        "profile": profile,
        "position_value": position_value,
        "traded_count": traded_count,
        "current_pnl": current_pnl,
        "period_rows": period_rows,
        "closed_stats": closed_stats,
        "open": {
            "positions": len(positions),
            "initial": open_initial,
            "current": open_current,
            "cash_pnl": open_cash_pnl,
            "return_pct": open_pct,
            "capped": positions_capped,
        },
        "closed_count": len(closed_positions),
        "closed_capped": closed_capped,
        "trades_count": len(trades),
        "trades_capped": trades_capped,
        "biggest_win": biggest_win,
        "biggest_loss": biggest_loss,
        "event_rows": event_rows,
        "category_mix": mix_rows,
        "specialty": specialty_label(mix_rows),
        "tops": tops,
        "event_metas": {slug: meta.__dict__ for slug, meta in metas.items()},
        "categorized_events": categorized_events,
        "total_events": total_events,
    }


def render_profile_section(summary: dict[str, Any]) -> str:
    profile = summary["profile"]
    wallet = profile["wallet"]
    period_map = {row["period"]: row for row in summary["period_rows"]}
    one_year = period_map.get("1Y", {}).get("pnl")
    all_time = period_map.get("ALL", {}).get("pnl")

    lines = [
        f"## @{profile['handle']} - {profile['name']}",
        "",
        f"Source: [{profile['input_url']}]({profile['input_url']})",
        "",
        "### Snapshot",
        markdown_table(
            ["Metric", "Value"],
            [
                ["Wallet", f"`{wallet}`"],
                ["Profile type", summary["specialty"]],
                ["Joined", parse_iso_date(profile.get("createdAt"))],
                ["1Y return", money(one_year)],
                ["All-time return", money(all_time)],
                ["Position value", money(summary["position_value"], compact=True)],
                ["Open position P/L", f"{money(summary['open']['cash_pnl'])} ({pct(summary['open']['return_pct'], 1)})"],
                ["Biggest win / loss", f"{money(summary['biggest_win'])} / {money(summary['biggest_loss'])}"],
                ["Predictions / traded markets", number(summary["traded_count"])],
                ["Closed rows / trade rows", f"{number(summary['closed_count'])} / {number(summary['trades_count'])}"],
            ],
        ),
        "",
        "### Period Performance",
    ]

    period_rows = []
    for row in summary["period_rows"]:
        period_rows.append(
            [
                row["period"],
                money(row.get("pnl")),
                pct(row.get("pnl_on_buy_cash_pct"), 2),
                pct(row.get("win_rate"), 1),
                number(row.get("closed")),
                number(row.get("trade_count")),
                number(row.get("avg_trades_per_event"), 2),
                pct(row.get("closure_rate"), 1),
            ]
        )
    lines.append(
        markdown_table(
            [
                "Period",
                "P/L",
                "P/L on buys",
                "Win rate",
                "Closed",
                "Trades",
                "Trades/event",
                "Closure rate",
            ],
            period_rows,
        )
    )

    mix_rows = []
    for row in summary["category_mix"][:6]:
        mix_rows.append(
            [
                row["category"],
                money(row["realized_pnl"]),
                pct(row.get("return_pct"), 1),
                pct(row.get("win_rate"), 1),
                number(row["events"]),
                number(row["closed_positions"]),
            ]
        )
    lines.extend(
        [
            "",
            "### Category Mix",
            markdown_table(
                ["Category", "Closed P/L", "% return", "Win rate", "Events", "Closed"],
                mix_rows,
            ),
            f"Category metadata coverage: {summary['categorized_events']}/{summary['total_events']} closed event groups.",
            "",
            "### Top Events By P/L",
            summarize_top_events(
                summary["tops"]["biggest_pnl"],
                {slug: EventMeta(**meta) for slug, meta in summary["event_metas"].items()},
            ),
            "",
            "### Top Events By % Return",
            summarize_top_events(
                summary["tops"]["best_return_pct"],
                {slug: EventMeta(**meta) for slug, meta in summary["event_metas"].items()},
            ),
            "",
            "### Top Events By Win Rate",
            summarize_top_events(
                summary["tops"]["best_win_rate"],
                {slug: EventMeta(**meta) for slug, meta in summary["event_metas"].items()},
            ),
            "",
            "### Readout",
            build_readout(summary),
        ]
    )
    return "\n".join(lines)


def build_readout(summary: dict[str, Any]) -> str:
    closed = summary["closed_stats"]
    period_map = {row["period"]: row for row in summary["period_rows"]}
    month = period_map.get("1M", {})
    all_time = period_map.get("ALL", {})
    mix = summary["category_mix"][:3]
    category_text = ", ".join(f"{row['category']} ({money(row['realized_pnl'])})" for row in mix) or "n/a"
    warnings: list[str] = []
    if summary["closed_capped"]:
        warnings.append("closed-position data hit the public API pagination cap")
    if summary["trades_capped"]:
        warnings.append("trade data hit the public API pagination cap")
    if summary["open"]["capped"]:
        warnings.append("open-position data hit the public API pagination cap")
    warning_text = f" Data caveat: {'; '.join(warnings)}." if warnings else ""
    return (
        f"Overall closed-position win rate is {pct(closed.get('win_rate'), 1)} across "
        f"{number(closed.get('closed'))} closed rows, with closed-position P/L of "
        f"{money(closed.get('closed_pnl'))}. Last-month P/L is {money(month.get('pnl'))}; "
        f"all-time P/L is {money(all_time.get('pnl'))}. Dominant profitable buckets: "
        f"{category_text}.{warning_text}"
    )


def render_report(summaries: list[dict[str, Any]], now: datetime) -> str:
    ranked = sorted(summaries, key=lambda item: safe_float(item.get("current_pnl")), reverse=True)
    overview_rows = []
    for item in ranked:
        profile = item["profile"]
        period_map = {row["period"]: row for row in item["period_rows"]}
        overview_rows.append(
            [
                f"@{profile['handle']}",
                item["specialty"],
                parse_iso_date(profile.get("createdAt")),
                money(period_map.get("1M", {}).get("pnl")),
                money(period_map.get("1Y", {}).get("pnl")),
                money(period_map.get("ALL", {}).get("pnl")),
                pct(item["closed_stats"].get("win_rate"), 1),
                number(item["traded_count"]),
                money(item["position_value"], compact=True),
            ]
        )

    sources = [
        "[Polymarket Gamma API introduction](https://docs.polymarket.com/api-reference/introduction)",
        "[Polymarket public search](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles)",
        "[Polymarket public profile](https://docs.polymarket.com/api-reference/profiles/get-public-profile-by-wallet-address)",
        "[Polymarket positions](https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user)",
        "[Polymarket closed positions](https://docs.polymarket.com/api-reference/core/get-closed-positions-for-a-user)",
        "[Polymarket activity/trades](https://docs.polymarket.com/api-reference/core/get-user-activity)",
        "[Polymarket leaderboard](https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings)",
        "[Polymarket User PnL API rate-limit listing](https://docs.polymarket.com/api-reference/rate-limits)",
    ]

    body = [
        "# Polymarket Profile Rentability Report",
        "",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "This report combines public Polymarket profile APIs, Data API portfolio endpoints, Gamma event metadata, and the public User PnL candle endpoint. P/L values are USD-denominated Polymarket PnL where exposed by the public data layer.",
        "",
        "## Methodology",
        "",
        "- Period P/L uses cumulative PnL candles. `1D`, `1W`, and `1M` use hourly candles from the last-month endpoint; `1Y`, `YTD`, and `ALL` use daily all-history candles.",
        "- Win rate uses closed positions with nonzero realized PnL: wins are rows where `realizedPnl > 0`; losses are rows where `realizedPnl < 0`.",
        "- `% return` on closed events uses `realizedPnl / (avgPrice * totalBought)`. This matches the Web UI's closed-position P/L percentage behavior for sampled rows.",
        "- `Trades/event` is public trade-fill count divided by distinct event keys in the same period. `Closure rate` is distinct closed event keys divided by distinct traded event keys for that period.",
        "- High-activity accounts can hit public API history caps. When that happens, trade-row density and closure-rate columns are best read as sample diagnostics, not full lifetime fill counts.",
        "- Category and subcategory are taken from Gamma event metadata when available, with conservative title/slug inference as fallback.",
        "",
        "## Cross-Profile Overview",
        markdown_table(
            [
                "Profile",
                "Type",
                "Joined",
                "1M P/L",
                "1Y P/L",
                "All P/L",
                "Win rate",
                "Predictions",
                "Position value",
            ],
            overview_rows,
        ),
        "",
        "## Profile Reports",
        "",
    ]
    for summary in ranked:
        body.append(render_profile_section(summary))
        body.append("")

    body.extend(
        [
            "## Source Links",
            "",
            *[f"- {source}" for source in sources],
            "",
            "Profile URLs:",
            "",
            *[f"- [{item['profile']['handle']}]({item['profile']['input_url']})" for item in summaries],
        ]
    )
    return "\n".join(body).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--date", default=REPORT_DATE.strftime("%Y-%m-%d"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    # Use late UTC hours on the report date so the 1D window covers the visible current day.
    now = now.replace(hour=23, minute=59, second=59)
    client = PolymarketClient()

    summaries = [build_profile_report(client, item, now) for item in PROFILES]
    report = render_report(summaries, now)

    stamp = args.date.replace("-", "_")
    md_path = output_dir / f"polymarket_profile_rentability_report_{stamp}.md"
    json_path = output_dir / f"polymarket_profile_rentability_report_{stamp}.json"
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
