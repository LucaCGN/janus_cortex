from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.modules.agentic.basketball_logic import build_profit_ratcheted_risk_state


DEFAULT_SOURCE_DIR = REPO_ROOT / "local" / "shared" / "artifacts" / "account-reconstruction" / "2026-05-15"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "local" / "shared" / "artifacts" / "risk-ladder-calibration" / "2026-05-18"
DEFAULT_REPORT_PATH = (
    REPO_ROOT / "local" / "shared" / "reports" / "daily-live-validation" / "risk_ladder_calibration_2026-05-18.md"
)

TRADABLE_SCENARIOS = ("A", "B", "C", "S")
DESTRUCTIVE_SCENARIOS = ("D", "U")
SAMPLE_REALIZED_PROFITS = (0.0, 5.0, 10.0, 20.0, 50.0, 100.0)


def build_calibration(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    portfolio_value: float = 100.0,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scenario_stats = _load_json(source_dir / "scenario_stats.json")
    db_performance = _load_json(source_dir / "janus_db_system_performance.json")
    event_rows = _load_csv(source_dir / "basketball_event_trade_summary.csv")

    scenario_rows = [_scenario_row(level, scenario_stats.get(level) or {}) for level in sorted(scenario_stats)]
    tradable = _combine_scenarios(scenario_stats, TRADABLE_SCENARIOS)
    destructive = _combine_scenarios(scenario_stats, DESTRUCTIVE_SCENARIOS)
    context_coverage = _coverage_counts(event_rows, "context_coverage")

    ladder_samples = [
        {
            "realized_profit_usd": profit,
            "scenario_a": build_profit_ratcheted_risk_state(
                portfolio_value=portfolio_value,
                realized_event_pnl=profit,
                realized_day_pnl=0.0,
                scenario_level="A",
                confidence=0.8,
                liquidity_score=1.0,
            ),
            "scenario_s_tail_candidate": build_profit_ratcheted_risk_state(
                portfolio_value=portfolio_value,
                realized_event_pnl=profit,
                realized_day_pnl=0.0,
                scenario_level="S",
                confidence=0.85,
                liquidity_score=1.0,
            ),
            "scenario_d_blocked": build_profit_ratcheted_risk_state(
                portfolio_value=portfolio_value,
                realized_event_pnl=profit,
                realized_day_pnl=0.0,
                scenario_level="D",
                confidence=0.4,
                liquidity_score=1.0,
            ),
        }
        for profit in SAMPLE_REALIZED_PROFITS
    ]

    linked_fills = _safe_int(db_performance.get("linked_fills_total"))
    janus_strict = dict(db_performance.get("janus_strict") or {})
    janus_plus_codex = dict(db_performance.get("janus_plus_codex_assisted") or {})
    db_blockers = []
    if linked_fills < 30:
        db_blockers.append(
            {
                "reason": "sparse_janus_linked_fills",
                "linked_fills_total": linked_fills,
                "minimum_for_calibration": 30,
            }
        )
    if _safe_float(janus_strict.get("weighted_return")) < 0:
        db_blockers.append(
            {
                "reason": "autonomous_janus_sample_negative",
                "weighted_return": _round_or_none(janus_strict.get("weighted_return")),
            }
        )
    if destructive["weighted_return"] is not None and destructive["weighted_return"] < -0.25:
        db_blockers.append(
            {
                "reason": "destructive_d_u_profile",
                "weighted_return": _round_or_none(destructive["weighted_return"]),
            }
        )

    recommendation = {
        "status": "calibrated_conservative_defaults_only",
        "trading_authority": "review_only_no_risk_promotion",
        "portfolio_value_usd": round(portfolio_value, 6),
        "base_rule": "Protect base bankroll; do not scale from open unrealized profit.",
        "default_ladder": [
            {
                "realized_return_band": "0-20%",
                "max_base_risk_pct": 3.0,
                "max_realized_profit_risk_pct": 10.0,
                "high_or_tail_sleeve": "disabled",
            },
            {
                "realized_return_band": "20-50%",
                "max_base_risk_pct": 5.0,
                "max_realized_profit_risk_pct": 25.0,
                "high_or_tail_sleeve": "tiny_operator_review_only",
            },
            {
                "realized_return_band": "50-100%",
                "max_base_risk_pct": 8.0,
                "max_realized_profit_risk_pct": 40.0,
                "high_or_tail_sleeve": "operator_review_only",
            },
            {
                "realized_return_band": ">100%",
                "max_base_risk_pct": 10.0,
                "max_realized_profit_risk_pct": 50.0,
                "high_or_tail_sleeve": "operator_review_only_until_fillability_proven",
            },
        ],
        "tail_rule": "Tail sleeves require realized-profit funding, no unresolved inventory, non-D/U scenario, fresh direct CLOB book, max price, max notional, and operator review.",
        "promotion_blockers": db_blockers,
    }

    return {
        "schema_version": "risk_ladder_calibration_v1",
        "generated_at_utc": generated_at,
        "issue": "#44",
        "source_dir": str(source_dir),
        "sources": [
            "scenario_stats.json",
            "basketball_event_trade_summary.csv",
            "janus_db_system_performance.json",
            "app.modules.agentic.basketball_logic.build_profit_ratcheted_risk_state",
        ],
        "portfolio_value_usd": round(portfolio_value, 6),
        "context_coverage": context_coverage,
        "scenario_rows": scenario_rows,
        "tradable_profile": tradable,
        "destructive_profile": destructive,
        "janus_db_performance": {
            "scope": db_performance.get("db_scope"),
            "orders_total": _safe_int(db_performance.get("orders_total")),
            "linked_fills_total": linked_fills,
            "janus_strict": _performance_summary(janus_strict),
            "janus_plus_codex_assisted": _performance_summary(janus_plus_codex),
        },
        "ladder_samples": ladder_samples,
        "recommendation": recommendation,
        "live_order_impact": "none",
        "execution_authorized": False,
    }


def render_report(calibration: dict[str, Any], *, artifact_path: Path | None = None) -> str:
    tradable = calibration["tradable_profile"]
    destructive = calibration["destructive_profile"]
    db_perf = calibration["janus_db_performance"]
    rec = calibration["recommendation"]
    lines = [
        "# Profit-Ratcheted Risk Ladder Calibration - 2026-05-18",
        "",
        f"- timestamp_utc: `{calibration['generated_at_utc']}`",
        "- automation: `janus-master-controller`",
        "- GitHub issue: `#44`",
        "- persona: `risk-ledger-agent`",
        "- live-order impact: none. No live-money path ran and no orders were placed, cancelled, replaced, submitted, prepared, or executed.",
    ]
    if artifact_path is not None:
        lines.append(f"- artifact: `{_repo_relative(artifact_path)}`")
    lines.extend(
        [
            "",
            "## Source Evidence",
            "",
            f"- account reconstruction source: `{calibration['source_dir']}`",
            f"- context coverage: `{_compact_json(calibration['context_coverage'])}`",
            f"- tradable account profile `A/B/C/S`: {tradable['events']} events, buy notional `${tradable['buy_notional']:.2f}`, net `${tradable['net']:.2f}`, weighted return `{_pct(tradable['weighted_return'])}`.",
            f"- destructive `D/U` profile: {destructive['events']} events, buy notional `${destructive['buy_notional']:.2f}`, net `${destructive['net']:.2f}`, weighted return `{_pct(destructive['weighted_return'])}`.",
            f"- Janus DB linked-fill sample: {db_perf['linked_fills_total']} linked fills; strict Janus weighted return `{_pct(db_perf['janus_strict']['weighted_return'])}`; Janus+Codex weighted return `{_pct(db_perf['janus_plus_codex_assisted']['weighted_return'])}`.",
            "",
            "## Calibration Decision",
            "",
            f"- status: `{rec['status']}`",
            f"- trading authority: `{rec['trading_authority']}`",
            f"- portfolio scale modeled: `${calibration['portfolio_value_usd']:.2f}`",
            "- base bankroll remains protected; open unrealized profit does not unlock risk.",
            "- high/tail sleeves remain operator-review only and require realized-profit funding plus fresh direct CLOB evidence.",
            "- `D/U` profiles stay mechanically blocked for tail-risk allocation.",
            "",
            "## Default Ladder Snapshot",
            "",
            "| Realized return | Max base risk | Max realized-profit risk | High/tail sleeve |",
            "|---|---:|---:|---|",
        ]
    )
    for row in rec["default_ladder"]:
        lines.append(
            f"| {row['realized_return_band']} | {row['max_base_risk_pct']:.1f}% | "
            f"{row['max_realized_profit_risk_pct']:.1f}% | {row['high_or_tail_sleeve']} |"
        )
    lines.extend(
        [
            "",
            "## Promotion Blockers",
            "",
        ]
    )
    for blocker in rec["promotion_blockers"]:
        lines.append(f"- `{blocker['reason']}`: `{_compact_json(blocker)}`")
    lines.extend(
        [
            "",
            "## Next Safe Action",
            "",
            "Keep `#44` open for account/DB lifecycle joining, direct CLOB fillability, and drawdown-calibrated ladder validation. Use this report as conservative review evidence only.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(calibration: dict[str, Any], *, output_dir: Path, report_path: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(calibration["generated_at_utc"])
    artifact_path = output_dir / f"issue44_risk_ladder_calibration_{stamp}.json"
    artifact_path.write_text(json.dumps(calibration, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(calibration, artifact_path=artifact_path), encoding="utf-8")
    return {"artifact_path": str(artifact_path), "report_path": str(report_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only Janus profit-ratcheted risk calibration report.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--portfolio-value", type=float, default=100.0)
    parser.add_argument("--generated-at-utc", default=None)
    parser.add_argument("--json-only", action="store_true", help="Print calibration JSON without writing files.")
    args = parser.parse_args()

    calibration = build_calibration(
        source_dir=args.source_dir,
        portfolio_value=args.portfolio_value,
        generated_at_utc=args.generated_at_utc,
    )
    if args.json_only:
        print(json.dumps(calibration, indent=2, sort_keys=True))
        return 0
    paths = write_outputs(calibration, output_dir=args.output_dir, report_path=args.report_path)
    print(json.dumps({"ok": True, **paths}, indent=2, sort_keys=True))
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON at {path}")
    return payload


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _scenario_row(level: str, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_level": level,
        "events": _safe_int(stats.get("events")),
        "wins": _safe_int(stats.get("wins")),
        "win_rate": _round_or_none(stats.get("win_rate")),
        "buy_notional": _round_or_none(stats.get("buy_notional")),
        "net": _round_or_none(stats.get("net")),
        "weighted_return": _round_or_none(stats.get("weighted_return")),
    }


def _combine_scenarios(stats_by_level: dict[str, Any], levels: tuple[str, ...]) -> dict[str, Any]:
    events = 0
    wins = 0
    buy_notional = 0.0
    net = 0.0
    included = []
    for level in levels:
        stats = stats_by_level.get(level) or {}
        included.append(level)
        events += _safe_int(stats.get("events"))
        wins += _safe_int(stats.get("wins"))
        buy_notional += _safe_float(stats.get("buy_notional"))
        net += _safe_float(stats.get("net"))
    weighted_return = net / buy_notional if buy_notional > 0 else None
    return {
        "levels": included,
        "events": events,
        "wins": wins,
        "win_rate": round(wins / events, 6) if events else None,
        "buy_notional": round(buy_notional, 6),
        "net": round(net, 6),
        "weighted_return": round(weighted_return, 6) if weighted_return is not None else None,
    }


def _coverage_counts(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "unknown").strip() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _performance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "fill_rows": _safe_int(payload.get("fill_rows")),
        "buy_notional": _round_or_none(payload.get("buy_notional")),
        "sell_notional": _round_or_none(payload.get("sell_notional")),
        "net_cashflow": _round_or_none(payload.get("net_cashflow")),
        "weighted_return": _round_or_none(payload.get("weighted_return")),
    }


def _safe_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _round_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return round(_safe_float(value), 6)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{_safe_float(value) * 100:.1f}%"


def _compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _stamp(value: str) -> str:
    return (
        value.replace(":", "")
        .replace("-", "")
        .replace(".", "")
        .replace("+0000", "Z")
        .replace("+00:00", "Z")
    )


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
