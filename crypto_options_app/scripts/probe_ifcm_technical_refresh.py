from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_PAGE_URL = "https://www.ifcm.co.uk/technicals/crypto-technical-analysis/btcusd"
DEFAULT_AJAX_URL = "https://www.ifcm.co.uk/technicals/ajax"
DEFAULT_OUTPUT_DIR = Path("crypto_options_app/artifacts/data-probes")
DEFAULT_PERIODS = ("1", "5", "15", "30", "60", "240", "1440", "10080")
PERIOD_LABELS = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "240": "4h",
    "1440": "1d",
    "10080": "1w",
}


@dataclass(frozen=True)
class IfcmIntervalPayload:
    period: str
    label: str
    status_code: int | None
    elapsed_ms: int
    bytes_read: int
    summary: dict[str, Any]
    indicators: list[dict[str, Any]]
    oscillators: list[dict[str, Any]]
    pivots: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class IfcmTechnicalSample:
    sequence: int
    requested_at_utc: str
    completed_at_utc: str
    elapsed_ms: int
    intervals: list[IfcmIntervalPayload]


def fetch_interval(
    *,
    ajax_url: str,
    page_url: str,
    period: str,
    instrument_id: str,
    group_id: str,
    timeout_seconds: int,
) -> IfcmIntervalPayload:
    started = time.monotonic()
    data = urllib.parse.urlencode({"period": period, "instrumentId": instrument_id, "groupId": group_id}).encode()
    request = urllib.request.Request(
        ajax_url,
        data=data,
        method="POST",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.ifcm.co.uk",
            "Referer": page_url,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", "replace")
            parsed = json.loads(body)
            indicators = _normalize_rows(parsed.get("indicators") or [])
            oscillators = _normalize_rows(parsed.get("oscillators") or [])
            return IfcmIntervalPayload(
                period=period,
                label=PERIOD_LABELS.get(period, period),
                status_code=int(response.status),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                bytes_read=len(body.encode("utf-8", "replace")),
                summary={
                    "moving_averages": _summarize_signals(indicators),
                    "oscillators": _summarize_signals(oscillators),
                    "combined": _summarize_signals(indicators + oscillators),
                },
                indicators=indicators,
                oscillators=oscillators,
                pivots=parsed.get("pivots") or {},
            )
    except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return IfcmIntervalPayload(
            period=period,
            label=PERIOD_LABELS.get(period, period),
            status_code=None,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            bytes_read=0,
            summary={},
            indicators=[],
            oscillators=[],
            pivots={},
            error=f"{type(exc).__name__}: {exc}",
        )


def collect_sample(
    *,
    sequence: int,
    ajax_url: str,
    page_url: str,
    periods: tuple[str, ...],
    instrument_id: str,
    group_id: str,
    timeout_seconds: int,
) -> IfcmTechnicalSample:
    requested = datetime.now(UTC)
    started = time.monotonic()
    intervals = [
        fetch_interval(
            ajax_url=ajax_url,
            page_url=page_url,
            period=period,
            instrument_id=instrument_id,
            group_id=group_id,
            timeout_seconds=timeout_seconds,
        )
        for period in periods
    ]
    completed = datetime.now(UTC)
    return IfcmTechnicalSample(
        sequence=sequence,
        requested_at_utc=requested.isoformat(),
        completed_at_utc=completed.isoformat(),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        intervals=intervals,
    )


def run_probe(
    *,
    page_url: str,
    ajax_url: str,
    output_dir: Path,
    periods: tuple[str, ...],
    instrument_id: str,
    group_id: str,
    slow_duration_seconds: int,
    slow_interval_seconds: int,
    fast_duration_seconds: int,
    fast_interval_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = _sample_phase(
        phase="slow",
        sequence_start=1,
        page_url=page_url,
        ajax_url=ajax_url,
        periods=periods,
        instrument_id=instrument_id,
        group_id=group_id,
        duration_seconds=slow_duration_seconds,
        interval_seconds=slow_interval_seconds,
        timeout_seconds=timeout_seconds,
    )
    slow_summary = summarize_samples(samples)
    fast_samples: list[IfcmTechnicalSample] = []
    if should_run_fast_phase(samples):
        fast_samples = _sample_phase(
            phase="fast",
            sequence_start=len(samples) + 1,
            page_url=page_url,
            ajax_url=ajax_url,
            periods=periods,
            instrument_id=instrument_id,
            group_id=group_id,
            duration_seconds=fast_duration_seconds,
            interval_seconds=fast_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        samples.extend(fast_samples)

    summary = summarize_samples(samples)
    summary["slow_phase"] = slow_summary
    summary["fast_phase_ran"] = bool(fast_samples)
    summary["fast_phase_reason"] = (
        "slow_phase_value_fingerprint_changed" if fast_samples else "slow_phase_did_not_show_sub_30s_refresh"
    )
    summary["page_url"] = page_url
    summary["ajax_url"] = ajax_url
    summary["instrument_id"] = instrument_id
    summary["group_id"] = group_id
    summary["created_at_utc"] = datetime.now(UTC).isoformat()

    run_id = datetime.now(UTC).strftime("ifcm-technical-refresh-%Y%m%dT%H%M%SZ")
    payload = {"run_id": run_id, "summary": summary, "samples": [asdict(sample) for sample in samples]}
    output_path = output_dir / f"{run_id}.json"
    latest_path = output_dir / "latest_ifcm_technical_refresh_probe.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["output_path"] = str(output_path)
    payload["latest_path"] = str(latest_path)
    return payload


def summarize_samples(samples: list[IfcmTechnicalSample]) -> dict[str, Any]:
    fingerprints = [_fingerprint(sample) for sample in samples]
    intervals = _observed_change_intervals(samples)
    latest = samples[-1] if samples else None
    interval_status = {
        interval.label: {
            "status_code": interval.status_code,
            "error": interval.error,
            "summary": interval.summary,
            "indicator_count": len(interval.indicators),
            "oscillator_count": len(interval.oscillators),
            "pivot_count": len(interval.pivots),
        }
        for interval in latest.intervals
    } if latest else {}
    return {
        "sample_count": len(samples),
        "intervals_per_sample": len(latest.intervals) if latest else 0,
        "status_codes": sorted(
            {
                interval.status_code
                for sample in samples
                for interval in sample.intervals
                if interval.status_code is not None
            }
        ),
        "error_count": sum(1 for sample in samples for interval in sample.intervals if interval.error),
        "rate_limit_or_block_observed": any(
            interval.status_code in {403, 429}
            for sample in samples
            for interval in sample.intervals
            if interval.status_code is not None
        ),
        "unique_value_fingerprints": len(set(fingerprints)),
        "first_fingerprint": fingerprints[0] if fingerprints else None,
        "last_fingerprint": fingerprints[-1] if fingerprints else None,
        "observed_change_intervals_seconds": intervals,
        "min_observed_change_interval_seconds": min(intervals) if intervals else None,
        "latest_interval_status": interval_status,
    }


def should_run_fast_phase(samples: list[IfcmTechnicalSample]) -> bool:
    return len({_fingerprint(sample) for sample in samples}) > 1


def _sample_phase(
    *,
    phase: str,
    sequence_start: int,
    page_url: str,
    ajax_url: str,
    periods: tuple[str, ...],
    instrument_id: str,
    group_id: str,
    duration_seconds: int,
    interval_seconds: int,
    timeout_seconds: int,
) -> list[IfcmTechnicalSample]:
    samples: list[IfcmTechnicalSample] = []
    deadline = time.monotonic() + max(duration_seconds, 0)
    sequence = sequence_start
    while True:
        sample = collect_sample(
            sequence=sequence,
            ajax_url=ajax_url,
            page_url=page_url,
            periods=periods,
            instrument_id=instrument_id,
            group_id=group_id,
            timeout_seconds=timeout_seconds,
        )
        samples.append(sample)
        print(
            json.dumps(
                {
                    "phase": phase,
                    "sequence": sample.sequence,
                    "elapsed_ms": sample.elapsed_ms,
                    "fingerprint": _fingerprint(sample),
                    "status_codes": [interval.status_code for interval in sample.intervals],
                    "errors": [interval.error for interval in sample.intervals if interval.error],
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


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict) or data.get("error"):
            normalized.append({"name": str(row.get("name", "")), "value": None, "signal": "ERROR", "raw": data})
            continue
        normalized.append(
            {
                "name": str(row.get("name", "")),
                "value": data.get("value"),
                "signal": str(data.get("signal", "")).upper(),
                "raw": data,
            }
        )
    return normalized


def _summarize_signals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "ERROR": 0}
    for row in rows:
        signal = str(row.get("signal") or "ERROR").upper()
        counts[signal if signal in counts else "ERROR"] += 1
    score = counts["BUY"] - counts["SELL"]
    if score >= 4:
        label = "Strong Buy"
    elif score > 0:
        label = "Buy"
    elif score <= -4:
        label = "Strong Sell"
    elif score < 0:
        label = "Sell"
    else:
        label = "Neutral"
    return {"label": label, "score": score, "counts": counts}


def _observed_change_intervals(samples: list[IfcmTechnicalSample]) -> list[int]:
    intervals: list[int] = []
    previous_sample: IfcmTechnicalSample | None = None
    previous_fingerprint: str | None = None
    for sample in samples:
        fingerprint = _fingerprint(sample)
        if previous_sample and fingerprint != previous_fingerprint:
            started = datetime.fromisoformat(previous_sample.completed_at_utc)
            ended = datetime.fromisoformat(sample.completed_at_utc)
            intervals.append(int((ended - started).total_seconds()))
        previous_sample = sample
        previous_fingerprint = fingerprint
    return intervals


def _fingerprint(sample: IfcmTechnicalSample) -> str:
    payload = [
        {
            "period": interval.period,
            "summary": interval.summary,
            "indicators": interval.indicators,
            "oscillators": interval.oscillators,
            "pivots": interval.pivots,
        }
        for interval in sample.intervals
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _parse_periods(value: str) -> tuple[str, ...]:
    periods = tuple(part.strip() for part in value.split(",") if part.strip())
    return periods or DEFAULT_PERIODS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe IFCM technical-analysis AJAX refresh cadence.")
    parser.add_argument("--page-url", default=DEFAULT_PAGE_URL)
    parser.add_argument("--ajax-url", default=DEFAULT_AJAX_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--periods", default=",".join(DEFAULT_PERIODS))
    parser.add_argument("--instrument-id", default="903")
    parser.add_argument("--group-id", default="22")
    parser.add_argument("--slow-duration-seconds", type=int, default=300)
    parser.add_argument("--slow-interval-seconds", type=int, default=30)
    parser.add_argument("--fast-duration-seconds", type=int, default=90)
    parser.add_argument("--fast-interval-seconds", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_probe(
        page_url=args.page_url,
        ajax_url=args.ajax_url,
        output_dir=args.output_dir,
        periods=_parse_periods(args.periods),
        instrument_id=args.instrument_id,
        group_id=args.group_id,
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
