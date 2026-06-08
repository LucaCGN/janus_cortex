from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


DEFAULT_URL = "https://www.investing.com/indices/bitcoin-real-time-technical"
DEFAULT_OUTPUT_DIR = Path("crypto_options_app/artifacts/data-probes")


@dataclass(frozen=True)
class TechnicalSample:
    sequence: int
    requested_at_utc: str
    completed_at_utc: str
    elapsed_ms: int
    status_code: int | None
    bytes_read: int
    page_timestamp: str | None
    page_title: str | None
    summary: dict[str, Any]
    technical_indicators: list[dict[str, str]]
    moving_averages: list[dict[str, str]]
    pivots: list[dict[str, str]]
    error: str | None = None


def fetch_html(url: str, *, timeout_seconds: int) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return int(response.status), response.read().decode("utf-8", "replace")


def parse_technical_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    title = _text(soup.find("h1"))
    timestamp = _extract_latest_timestamp(soup.get_text(" ", strip=True))
    summary = _parse_summary(tables[0]) if tables else {}
    return {
        "page_title": title,
        "page_timestamp": timestamp,
        "summary": summary,
        "technical_indicators": _parse_indicator_table(tables[1]) if len(tables) > 1 else [],
        "moving_averages": _parse_moving_average_table(tables[2]) if len(tables) > 2 else [],
        "pivots": _parse_pivot_table(tables[3]) if len(tables) > 3 else [],
    }


def collect_sample(url: str, *, sequence: int, timeout_seconds: int) -> TechnicalSample:
    requested = datetime.now(UTC)
    started = time.monotonic()
    try:
        status, html = fetch_html(url, timeout_seconds=timeout_seconds)
        completed = datetime.now(UTC)
        parsed = parse_technical_html(html)
        return TechnicalSample(
            sequence=sequence,
            requested_at_utc=requested.isoformat(),
            completed_at_utc=completed.isoformat(),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            status_code=status,
            bytes_read=len(html.encode("utf-8", "replace")),
            page_timestamp=parsed["page_timestamp"],
            page_title=parsed["page_title"],
            summary=parsed["summary"],
            technical_indicators=parsed["technical_indicators"],
            moving_averages=parsed["moving_averages"],
            pivots=parsed["pivots"],
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        completed = datetime.now(UTC)
        return TechnicalSample(
            sequence=sequence,
            requested_at_utc=requested.isoformat(),
            completed_at_utc=completed.isoformat(),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            status_code=None,
            bytes_read=0,
            page_timestamp=None,
            page_title=None,
            summary={},
            technical_indicators=[],
            moving_averages=[],
            pivots=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def run_probe(
    *,
    url: str,
    output_dir: Path,
    slow_duration_seconds: int,
    slow_interval_seconds: int,
    fast_duration_seconds: int,
    fast_interval_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    samples: list[TechnicalSample] = []
    sequence = 1

    samples.extend(
        _sample_phase(
            url=url,
            sequence_start=sequence,
            phase="slow",
            duration_seconds=slow_duration_seconds,
            interval_seconds=slow_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    sequence += len(samples)
    slow_summary = summarize_samples(samples)

    fast_samples: list[TechnicalSample] = []
    if should_run_fast_phase(samples):
        fast_samples = _sample_phase(
            url=url,
            sequence_start=sequence,
            phase="fast",
            duration_seconds=fast_duration_seconds,
            interval_seconds=fast_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        samples.extend(fast_samples)

    summary = summarize_samples(samples)
    summary["slow_phase"] = slow_summary
    summary["fast_phase_ran"] = bool(fast_samples)
    summary["fast_phase_reason"] = (
        "slow_phase_values_or_page_timestamp_changed" if fast_samples else "slow_phase_did_not_show_sub_30s_refresh"
    )
    summary["url"] = url
    summary["created_at_utc"] = datetime.now(UTC).isoformat()

    run_id = datetime.now(UTC).strftime("investing-technical-refresh-%Y%m%dT%H%M%SZ")
    payload = {"run_id": run_id, "summary": summary, "samples": [asdict(sample) for sample in samples]}
    output_path = output_dir / f"{run_id}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    latest_path = output_dir / "latest_investing_technical_refresh_probe.json"
    latest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["output_path"] = str(output_path)
    payload["latest_path"] = str(latest_path)
    return payload


def summarize_samples(samples: list[TechnicalSample]) -> dict[str, Any]:
    valid = [sample for sample in samples if sample.error is None and sample.status_code == 200]
    keys = [_fingerprint(sample) for sample in valid]
    timestamp_keys = [sample.page_timestamp for sample in valid if sample.page_timestamp]
    intervals = _observed_change_intervals(valid)
    return {
        "sample_count": len(samples),
        "valid_sample_count": len(valid),
        "error_count": len(samples) - len(valid),
        "status_codes": sorted({sample.status_code for sample in samples if sample.status_code is not None}),
        "unique_page_timestamps": len(set(timestamp_keys)),
        "page_timestamps": sorted(set(timestamp_keys)),
        "unique_value_fingerprints": len(set(keys)),
        "first_fingerprint": keys[0] if keys else None,
        "last_fingerprint": keys[-1] if keys else None,
        "observed_change_intervals_seconds": intervals,
        "min_observed_change_interval_seconds": min(intervals) if intervals else None,
        "rate_limit_or_block_observed": any(sample.status_code in {403, 429} for sample in samples if sample.status_code),
        "timeout_or_network_errors": [sample.error for sample in samples if sample.error],
        "latest_summary": valid[-1].summary if valid else {},
        "latest_technical_indicators": valid[-1].technical_indicators if valid else [],
        "latest_moving_averages": valid[-1].moving_averages if valid else [],
        "latest_pivots": valid[-1].pivots if valid else [],
    }


def should_run_fast_phase(samples: list[TechnicalSample]) -> bool:
    valid = [sample for sample in samples if sample.error is None and sample.status_code == 200]
    if len(valid) < 2:
        return False
    fingerprints = [_fingerprint(sample) for sample in valid]
    timestamps = [sample.page_timestamp for sample in valid]
    return len(set(fingerprints)) > 1 or len(set(timestamps)) > 1


def _sample_phase(
    *,
    url: str,
    sequence_start: int,
    phase: str,
    duration_seconds: int,
    interval_seconds: int,
    timeout_seconds: int,
) -> list[TechnicalSample]:
    samples: list[TechnicalSample] = []
    deadline = time.monotonic() + max(duration_seconds, 0)
    sequence = sequence_start
    while True:
        sample = collect_sample(url, sequence=sequence, timeout_seconds=timeout_seconds)
        samples.append(sample)
        print(
            json.dumps(
                {
                    "phase": phase,
                    "sequence": sample.sequence,
                    "status_code": sample.status_code,
                    "elapsed_ms": sample.elapsed_ms,
                    "page_timestamp": sample.page_timestamp,
                    "fingerprint": _fingerprint(sample),
                    "error": sample.error,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        sequence += 1
        if time.monotonic() + interval_seconds > deadline:
            break
        time.sleep(interval_seconds)
    return samples


def _parse_summary(table: Any) -> dict[str, Any]:
    text = _text(table)
    result: dict[str, Any] = {}
    for label in ("Moving Averages", "Technical Indicators"):
        match = re.search(rf"{re.escape(label)}\s*:\s*([A-Za-z ]+?)\s+Buy\s*:\s*\(\s*(\d+)\s*\)\s+Sell\s*:\s*\(\s*(\d+)\s*\)", text)
        if match:
            result[_slug(label)] = {
                "summary": match.group(1).strip(),
                "buy": int(match.group(2)),
                "sell": int(match.group(3)),
            }
    return result


def _parse_indicator_table(table: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        cells = [_text(cell) for cell in tr.find_all(["td", "th"])]
        if len(cells) == 3 and cells[0] != "Name":
            rows.append({"name": cells[0], "value": cells[1], "action": cells[2]})
    return rows


def _parse_moving_average_table(table: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        cells = [_text(cell) for cell in tr.find_all(["td", "th"])]
        if len(cells) == 5 and cells[0] != "Name":
            rows.append(
                {
                    "name": cells[0],
                    "simple_value": _strip_action_suffix(cells[1]),
                    "simple_action": cells[2],
                    "exponential_value": _strip_action_suffix(cells[3]),
                    "exponential_action": cells[4],
                }
            )
    return rows


def _parse_pivot_table(table: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        cells = [_text(cell) for cell in tr.find_all(["td", "th"])]
        if len(cells) == 8 and cells[0] != "Name":
            rows.append(
                {
                    "name": cells[0],
                    "s3": cells[1],
                    "s2": cells[2],
                    "s1": cells[3],
                    "pivot": cells[4],
                    "r1": cells[5],
                    "r2": cells[6],
                    "r3": cells[7],
                }
            )
    return rows


def _extract_latest_timestamp(text: str) -> str | None:
    matches = re.findall(r"[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}(?:AM|PM)\s+GMT", text)
    return matches[-1] if matches else None


def _observed_change_intervals(samples: list[TechnicalSample]) -> list[int]:
    intervals: list[int] = []
    previous_sample: TechnicalSample | None = None
    previous_fingerprint: str | None = None
    for sample in samples:
        fingerprint = _fingerprint(sample)
        if previous_sample and previous_fingerprint != fingerprint:
            started = datetime.fromisoformat(previous_sample.completed_at_utc)
            ended = datetime.fromisoformat(sample.completed_at_utc)
            intervals.append(int((ended - started).total_seconds()))
        previous_sample = sample
        previous_fingerprint = fingerprint
    return intervals


def _fingerprint(sample: TechnicalSample) -> str:
    payload = {
        "page_timestamp": sample.page_timestamp,
        "summary": sample.summary,
        "technical_indicators": sample.technical_indicators,
        "moving_averages": sample.moving_averages,
        "pivots": sample.pivots,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _strip_action_suffix(value: str) -> str:
    return re.sub(r"\s+(Strong Sell|Strong Buy|Sell|Buy|Neutral)$", "", value).strip()


def _text(node: Any) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Investing.com technical-analysis HTML refresh cadence.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--slow-duration-seconds", type=int, default=300)
    parser.add_argument("--slow-interval-seconds", type=int, default=30)
    parser.add_argument("--fast-duration-seconds", type=int, default=90)
    parser.add_argument("--fast-interval-seconds", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_probe(
        url=args.url,
        output_dir=args.output_dir,
        slow_duration_seconds=args.slow_duration_seconds,
        slow_interval_seconds=args.slow_interval_seconds,
        fast_duration_seconds=args.fast_duration_seconds,
        fast_interval_seconds=args.fast_interval_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps({"output_path": payload["output_path"], "summary": payload["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
