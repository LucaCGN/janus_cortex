from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT

DEFAULT_ARTIFACT_ROOT = CENTRAL_ARTIFACT_ROOT
STOP_FOR_REVIEW_FILE = "stop_for_review_request.json"
NON_CRITICAL_PROFILE_SOURCE_QUOTE_BLOCKERS = {
    "missing_best_ask",
    "missing_spread",
    "missing_ask_size",
    "missing_depth_top3_ask_size",
}


def build_live_dashboard_state(*, artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT) -> dict[str, Any]:
    root = Path(artifact_root)
    automation_dir = root / "automation"
    validation_dir = root / "live-validation"
    reports_dir = root / "reports"
    active_state = _read_json(automation_dir / "signal_live_active_process.json")
    live_status = _read_json(automation_dir / "signal_live_status.json")
    review_request = _read_json(automation_dir / STOP_FOR_REVIEW_FILE)
    active_run_id = str(active_state.get("run_id") or live_status.get("run_id") or "")
    run_artifact = _read_json(validation_dir / f"{active_run_id}.json") if active_run_id else {}
    settlement = _read_json(reports_dir / f"{active_run_id}_settlement_performance.json") if active_run_id else {}
    audit = _read_json(reports_dir / f"{active_run_id}_order_audit.json") if active_run_id else {}
    display_active_state = _display_active_run_state(active_state, live_status, active_run_id, run_artifact, audit)
    event_artifacts = _event_artifact_summaries(validation_dir, active_run_id)
    strategy_rows = []
    if isinstance(run_artifact.get("strategy_rows"), list):
        strategy_rows = [row for row in run_artifact["strategy_rows"] if isinstance(row, dict)]
    elif event_artifacts:
        strategy_rows = _rows_from_event_artifacts(validation_dir, active_run_id)
    strategies = _strategy_state(strategy_rows, settlement)
    return {
        "schema_version": "crypto_options_live_dashboard_state_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "active_run": display_active_state,
        "live_status": live_status,
        "run_artifact_present": bool(run_artifact),
        "settlement_present": bool(settlement),
        "audit": _audit_summary(audit, strategy_rows=strategy_rows, active_run_id=active_run_id),
        "event_artifacts": event_artifacts,
        "strategies": strategies,
        "totals": _totals(strategies, settlement, run_artifact, live_status, active_run=display_active_state),
        "review_request": review_request or None,
        "manual_orders_avoided": True,
    }


def create_stop_for_review_request(
    message: str,
    *,
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
) -> dict[str, Any]:
    cleaned_message = message.strip()
    if not cleaned_message:
        raise ValueError("stop_for_review message is required")
    if len(cleaned_message) > 4000:
        raise ValueError("stop_for_review message must be 4000 characters or fewer")

    root = Path(artifact_root)
    automation_dir = root / "automation"
    automation_dir.mkdir(parents=True, exist_ok=True)
    state = build_live_dashboard_state(artifact_root=root)
    active_run = state.get("active_run") or {}
    payload = {
        "schema_version": "crypto_options_stop_for_review_request_v1",
        "status": "pending",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": "dashboard",
        "operator_message": cleaned_message,
        "run_id": active_run.get("run_id"),
        "stage": active_run.get("stage"),
        "active_status": active_run.get("status"),
        "manual_orders_avoided": True,
    }
    request_path = automation_dir / STOP_FOR_REVIEW_FILE
    request_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (automation_dir / "stop_for_review_requests.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def _display_active_run_state(
    active_state: dict[str, Any],
    live_status: dict[str, Any],
    active_run_id: str,
    run_artifact: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    state = dict(active_state) if active_state else {}
    if not state.get("run_id") and (live_status.get("run_id") or active_run_id):
        state["run_id"] = live_status.get("run_id") or active_run_id
    if not state.get("stage") and live_status.get("stage"):
        state["stage"] = live_status.get("stage")
    if not state.get("stage") and state.get("run_id"):
        state["stage"] = "latest completed run" if str(run_artifact.get("status") or "") == "validated" else "latest run"
    if not state.get("status"):
        state["status"] = run_artifact.get("status") or live_status.get("status")
    artifact_status = str(run_artifact.get("status") or "")
    audit_status = str(audit.get("status") or "")
    if state.get("status") in {"running", "finished", "stale"} and artifact_status in {"validated", "blocked", "stopped_on_breaker"}:
        state["status"] = artifact_status
        state["status_source"] = "run_artifact"
    elif state.get("status") == artifact_status and artifact_status:
        state["status_source"] = "run_artifact"
    if artifact_status == "validated" and audit_status == "matched":
        state["audit_status"] = "matched"
        state.pop("stale_reason", None)
        state.pop("stale_marked_at_utc", None)
    return state


def render_live_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Crypto Options Live Console</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #070a0e;
      --panel: #0e141b;
      --line: #1f2a35;
      --text: #e7eef7;
      --muted: #8795a6;
      --accent: #2ee59d;
      --danger: #ff5d6c;
      --warn: #f7bf4f;
      --cold: #78a6ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at top left, rgba(46,229,157,.12), transparent 34rem), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    main { width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 42px; }
    header { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; border-bottom: 1px solid var(--line); padding-bottom: 18px; }
    h1 { margin: 0; font-size: 30px; font-weight: 760; }
    .sub { color: var(--muted); margin-top: 6px; font-size: 13px; }
    .status { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    .pill { border: 1px solid var(--line); color: var(--muted); padding: 6px 10px; border-radius: 999px; font-size: 12px; background: rgba(14,20,27,.72); }
    .pill.ok { color: var(--accent); border-color: rgba(46,229,157,.4); }
    .pill.warn { color: var(--warn); border-color: rgba(247,191,79,.4); }
    .pill.bad { color: var(--danger); border-color: rgba(255,93,108,.45); }
    .pill.cold { color: var(--cold); border-color: rgba(120,166,255,.45); }
    .control { border: 1px solid var(--line); color: var(--text); background: rgba(14,20,27,.9); padding: 7px 12px; border-radius: 999px; font: inherit; font-size: 12px; cursor: pointer; }
    .control:hover { border-color: rgba(120,166,255,.55); }
    .control.danger { color: var(--danger); border-color: rgba(255,93,108,.48); }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); margin: 24px 0; }
    .metric { background: rgba(14,20,27,.92); padding: 16px; min-height: 82px; }
    .metric span { display:block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
    .metric strong { display:block; margin-top: 8px; font-size: 22px; font-weight: 720; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .section-title { display:flex; justify-content:space-between; align-items:center; margin: 26px 0 10px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    table { width: 100%; border-collapse: collapse; border: 1px solid var(--line); background: rgba(14,20,27,.78); }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 11px 12px; font-size: 13px; }
    th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; font-weight: 620; }
    tr:last-child td { border-bottom: 0; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .pos { color: var(--accent); }
    .neg { color: var(--danger); }
    .muted { color: var(--muted); }
    section { min-width: 0; }
    .grid { display:grid; grid-template-columns: minmax(0, 1.5fr) minmax(320px, .7fr); gap: 18px; align-items:start; min-width: 0; }
    .grid > section:first-child { overflow-x: auto; }
    .grid > section:first-child table { min-width: 780px; }
    .events { border: 1px solid var(--line); background: rgba(14,20,27,.72); }
    .event { display:grid; grid-template-columns: 44px minmax(0, 1fr) 96px; gap: 12px; padding: 12px; border-bottom: 1px solid var(--line); align-items:center; }
    .event:last-child { border-bottom: 0; }
    .idx { color: var(--cold); font-variant-numeric: tabular-nums; }
    .small { font-size: 12px; color: var(--muted); margin-top: 3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .cell-note { color: var(--muted); font-size: 11px; margin-top: 3px; white-space: nowrap; }
    .modal-backdrop { position: fixed; inset: 0; display: grid; place-items: center; background: rgba(0,0,0,.64); padding: 18px; z-index: 50; }
    .modal-backdrop[hidden] { display: none; }
    .modal { width: min(560px, 100%); background: #0d141b; border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: 0 18px 60px rgba(0,0,0,.35); }
    .modal h2 { margin: 0 0 12px; font-size: 18px; }
    .modal label { display:block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; }
    .modal textarea { width: 100%; min-height: 140px; resize: vertical; background: #080d12; color: var(--text); border: 1px solid var(--line); border-radius: 6px; padding: 10px; font: inherit; line-height: 1.4; }
    .modal-actions { display:flex; justify-content:flex-end; gap: 10px; margin-top: 14px; }
    .modal-status { color: var(--muted); min-height: 18px; margin-top: 10px; font-size: 12px; }
    @media (max-width: 900px) { .metrics { grid-template-columns: repeat(2, 1fr); } .grid { grid-template-columns: 1fr; } header { display:block; } .status { justify-content:flex-start; margin-top: 14px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Crypto Options Live Console</h1>
      <div class="sub" id="runLine">Loading run state</div>
    </div>
    <div class="status">
      <span class="pill" id="alivePill">state</span>
      <span class="pill" id="auditPill">audit</span>
      <span class="pill cold" id="reviewPill" hidden>review requested</span>
      <span class="pill ok">manual orders avoided</span>
      <button class="control danger" id="stopReviewButton" type="button">Stop for review</button>
    </div>
  </header>
  <section class="metrics">
    <div class="metric"><span>Spent</span><strong id="spent">$0.00</strong></div>
    <div class="metric"><span>Realized PnL</span><strong id="pnl">$0.00</strong></div>
    <div class="metric"><span>Open cost</span><strong id="openCost">$0.00</strong></div>
    <div class="metric"><span>Win rate</span><strong id="winRate">n/a</strong></div>
    <div class="metric"><span>Filled trades</span><strong id="trades">0</strong></div>
    <div class="metric"><span>Processed cycles</span><strong id="cycles">0</strong></div>
    <div class="metric"><span>5m windows</span><strong id="windows">0</strong></div>
    <div class="metric"><span>Started</span><strong id="started">--</strong></div>
    <div class="metric"><span>Updated</span><strong id="updated">--</strong></div>
  </section>
  <div class="grid">
    <section>
      <div class="section-title"><span>Strategies</span><span id="strategyCount">0 lanes</span></div>
      <table>
        <thead><tr><th>Strategy</th><th>Status</th><th class="num">Filled</th><th class="num">Win rate</th><th class="num">PnL</th><th class="num">Avg win</th><th class="num">Avg loss</th><th>Blockers</th></tr></thead>
        <tbody id="strategyRows"></tbody>
      </table>
    </section>
    <section>
      <div class="section-title"><span>Recent event cycles</span><span id="eventCount">0</span></div>
      <div class="events" id="events"></div>
    </section>
  </div>
</main>
<div class="modal-backdrop" id="reviewModal" hidden>
  <form class="modal" id="reviewForm">
    <h2>Stop for Review</h2>
    <label for="reviewText">Automation prompt</label>
    <textarea id="reviewText" maxlength="4000" required></textarea>
    <div class="modal-actions">
      <button class="control" id="cancelReview" type="button">Cancel</button>
      <button class="control danger" type="submit">Confirm stop</button>
    </div>
    <div class="modal-status" id="reviewStatus"></div>
  </form>
</div>
<script>
const money = value => value === null || value === undefined ? 'n/a' : `${Number(value) < 0 ? '-' : ''}$${Math.abs(Number(value)).toFixed(2)}`;
const rate = value => value === null || value === undefined ? 'n/a' : `${(Number(value) * 100).toFixed(1)}%`;
const cls = value => Number(value || 0) < 0 ? 'neg' : Number(value || 0) > 0 ? 'pos' : '';
function setPill(el, text, state) { el.textContent = text; el.className = `pill ${state || ''}`; }
function pnlCell(strategy) {
  const open = Number(strategy.active_cost_usd || 0);
  const unresolved = Number(strategy.unresolved_position_count || 0);
  const settled = strategy.settled_position_count ?? 0;
  const note = open > 0 || unresolved > 0 ? `<div class="cell-note">open ${money(open)} / ${settled} settled</div>` : '';
  return `${money(strategy.realized_pnl_usd)}${note}`;
}
async function refresh() {
  const res = await fetch('/v1/crypto-options-app/dashboard/state', {cache: 'no-store'});
  const data = await res.json();
  const active = data.active_run || {};
  const totals = data.totals || {};
  document.getElementById('runLine').textContent = `${active.stage || 'no active stage'} - ${active.run_id || 'no run'}`;
  setPill(document.getElementById('alivePill'), active.status || 'unknown', active.status === 'running' ? 'ok' : active.status === 'validated' ? 'ok' : 'warn');
  const auditStatus = (data.audit || {}).status || 'audit pending';
  setPill(document.getElementById('auditPill'), auditStatus, auditStatus === 'matched' ? 'ok' : auditStatus.includes('pending') ? 'cold' : 'warn');
  const reviewPill = document.getElementById('reviewPill');
  reviewPill.hidden = !(data.review_request && data.review_request.status === 'pending');
  document.getElementById('spent').textContent = money(totals.estimated_spent_usd);
  document.getElementById('pnl').textContent = money(totals.realized_pnl_usd);
  document.getElementById('pnl').className = cls(totals.realized_pnl_usd);
  document.getElementById('openCost').textContent = money(totals.active_cost_usd);
  document.getElementById('winRate').textContent = rate(totals.win_rate);
  document.getElementById('trades').textContent = totals.filled_position_count ?? 0;
  document.getElementById('cycles').textContent = totals.event_count ?? 0;
  document.getElementById('windows').textContent = totals.elapsed_5m_window_count ?? totals.event_window_count ?? 0;
  document.getElementById('started').textContent = totals.run_started_at_utc ? new Date(totals.run_started_at_utc).toLocaleTimeString() : '--';
  document.getElementById('updated').textContent = new Date(data.generated_at_utc).toLocaleTimeString();
  const strategies = data.strategies || [];
  document.getElementById('strategyCount').textContent = `${strategies.length} lanes`;
  document.getElementById('strategyRows').innerHTML = strategies.map(s => `<tr>
    <td>${s.strategy_id}</td><td><span class="pill ${s.status === 'live' ? 'ok' : s.status === 'blocked' ? 'bad' : s.status === 'waiting' || s.status === 'gated' ? 'cold' : 'warn'}">${s.status}</span></td>
    <td class="num">${s.filled_position_count ?? 0}</td><td class="num">${rate(s.win_rate)}</td>
    <td class="num ${cls(s.realized_pnl_usd)}">${pnlCell(s)}</td><td class="num pos">${money(s.avg_win_usd)}</td><td class="num neg">${money(s.avg_loss_usd)}</td>
    <td class="muted">${(s.top_blockers || []).map(b => `${b[0]}(${b[1]})`).join(', ')}</td>
  </tr>`).join('');
  const events = data.event_artifacts || [];
  document.getElementById('eventCount').textContent = `${events.length}`;
  document.getElementById('events').innerHTML = events.slice(-12).reverse().map(e => `<div class="event"><div class="idx">#${e.event_index}</div><div><div>${e.event_slug || 'unknown event'}</div><div class="small">${e.row_summary || ''}</div></div><div class="num">${money(e.estimated_spent_usd)}</div></div>`).join('');
}
const modal = document.getElementById('reviewModal');
const reviewText = document.getElementById('reviewText');
const reviewStatus = document.getElementById('reviewStatus');
document.getElementById('stopReviewButton').addEventListener('click', () => {
  reviewStatus.textContent = '';
  reviewText.value = '';
  modal.hidden = false;
  reviewText.focus();
});
document.getElementById('cancelReview').addEventListener('click', () => {
  modal.hidden = true;
});
document.getElementById('reviewForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = reviewText.value.trim();
  if (!message) {
    reviewStatus.textContent = 'Prompt is required.';
    return;
  }
  if (!window.confirm('Stop the supervised run for review?')) {
    return;
  }
  const res = await fetch('/v1/crypto-options-app/dashboard/stop-for-review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message})
  });
  if (!res.ok) {
    reviewStatus.textContent = 'Request failed.';
    return;
  }
  reviewStatus.textContent = 'Review request saved.';
  await refresh();
  setTimeout(() => { modal.hidden = true; }, 650);
});
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


def _strategy_state(strategy_rows: list[dict[str, Any]], settlement: dict[str, Any]) -> list[dict[str, Any]]:
    by_strategy = (settlement.get("summary") or {}).get("by_strategy") if isinstance(settlement.get("summary"), dict) else {}
    row_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "executed": 0, "unfilled": 0, "blocked": 0, "blockers": defaultdict(int)})
    for row in strategy_rows:
        strategy_id = str(row.get("strategy_id") or "unknown")
        stats = row_stats[strategy_id]
        stats["rows"] += 1
        status = str(row.get("status") or "")
        if status == "live_structural_executed":
            stats["executed"] += 1
        elif status == "live_structural_unfilled":
            stats["unfilled"] += 1
        elif status == "blocked":
            stats["blocked"] += 1
        for blocker in row.get("blockers") or []:
            stats["blockers"][str(blocker)] += 1
    strategy_ids = sorted(set(row_stats) | set(by_strategy or {}))
    output = []
    for strategy_id in strategy_ids:
        settled = (by_strategy or {}).get(strategy_id) or {}
        stats = row_stats.get(strategy_id, {})
        wins = _float(settled.get("wins"))
        realized = _float(settled.get("realized_pnl_usd"))
        positive_rows = _settlement_rows_for_strategy(settlement, strategy_id, positive=True)
        negative_rows = _settlement_rows_for_strategy(settlement, strategy_id, positive=False)
        output.append(
            {
                "strategy_id": strategy_id,
                "status": _strategy_display_status(stats),
                "rows": stats.get("rows", 0),
                "executed_rows": stats.get("executed", 0),
                "unfilled_rows": stats.get("unfilled", 0),
                "blocked_rows": stats.get("blocked", 0),
                "filled_position_count": settled.get("filled_position_count", stats.get("executed", 0)),
                "settled_position_count": settled.get("settled_position_count"),
                "unresolved_position_count": settled.get("unresolved_position_count"),
                "wins": wins,
                "losses": _float(settled.get("losses")),
                "win_rate": settled.get("win_rate"),
                "realized_pnl_usd": realized,
                "active_cost_usd": settled.get("active_cost_usd"),
                "avg_win_usd": _avg(positive_rows),
                "avg_loss_usd": _avg(negative_rows),
                "top_blockers": _display_blockers_for_strategy(stats),
            }
        )
    return output


def _display_blockers_for_strategy(stats: dict[str, Any]) -> list[tuple[str, int]]:
    blockers = dict(stats.get("blockers") or {})
    if int(stats.get("executed") or 0) > 0:
        for blocker in NON_CRITICAL_PROFILE_SOURCE_QUOTE_BLOCKERS:
            blockers.pop(blocker, None)
    return sorted(blockers.items(), key=lambda item: item[1], reverse=True)[:4]


def _strategy_display_status(stats: dict[str, Any]) -> str:
    if stats.get("executed", 0):
        return "live"
    if stats.get("unfilled", 0):
        return "probing"
    if not stats.get("blocked", 0):
        return "waiting"

    blockers = [str(blocker) for blocker in (stats.get("blockers") or {}).keys()]
    if blockers and all(_is_expected_waiting_blocker(blocker) for blocker in blockers):
        return "gated" if any(_is_valid_gate_blocker(blocker) for blocker in blockers) else "waiting"
    return "blocked"


def _is_expected_waiting_blocker(blocker: str) -> bool:
    return blocker.startswith("no_") and blocker.endswith("_signal") or _is_valid_gate_blocker(blocker)


def _is_valid_gate_blocker(blocker: str) -> bool:
    return blocker in {
        "s_tier_source_present",
        "strategy_budget_cap_reached",
        "strategy_budget_cap_would_be_exceeded",
        "lane_stop_gate_not_triggered",
    }


def _event_artifact_summaries(validation_dir: Path, run_id: str) -> list[dict[str, Any]]:
    if not run_id:
        return []
    summaries = []
    for path in sorted(validation_dir.glob(f"{run_id}_event_*.json")):
        payload = _read_json(path)
        rows = [row for row in payload.get("strategy_rows") or [] if isinstance(row, dict)]
        summaries.append(
            {
                "event_index": payload.get("event_index"),
                "event_slug": payload.get("event_slug"),
                "event_window_start_utc": payload.get("event_window_start_utc") or _event_window_start_utc(payload.get("event_slug")),
                "estimated_spent_usd": payload.get("estimated_spent_usd"),
                "runtime_blockers": payload.get("runtime_blockers") or [],
                "row_summary": "; ".join(f"{row.get('strategy_id')}:{row.get('status')}" for row in rows),
                "path": str(path),
            }
        )
    return summaries


def _rows_from_event_artifacts(validation_dir: Path, run_id: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(validation_dir.glob(f"{run_id}_event_*.json")):
        payload = _read_json(path)
        rows.extend(row for row in payload.get("strategy_rows") or [] if isinstance(row, dict))
    return rows


def _audit_summary(audit: dict[str, Any], *, strategy_rows: list[dict[str, Any]] | None = None, active_run_id: str = "") -> dict[str, Any]:
    detail = audit.get("audit") if isinstance(audit.get("audit"), dict) else {}
    if not audit and active_run_id:
        rows = strategy_rows or []
        recorded_successful_buy_count = sum(1 for row in rows if _row_has_recorded_exchange_buy(row))
        return {
            "status": "active_run_pending_final_audit",
            "blockers": [],
            "recorded_successful_buy_count": recorded_successful_buy_count,
            "strong_match_count": None,
            "unmatched_recorded_count": None,
            "unmatched_exchange_buy_count": None,
        }
    return {
        "status": audit.get("status") or detail.get("status"),
        "blockers": detail.get("blockers") or [],
        "recorded_successful_buy_count": detail.get("recorded_successful_buy_count"),
        "strong_match_count": detail.get("strong_match_count"),
        "unmatched_recorded_count": detail.get("unmatched_recorded_count"),
        "unmatched_exchange_buy_count": detail.get("unmatched_exchange_buy_count"),
    }


def _row_has_recorded_exchange_buy(row: dict[str, Any]) -> bool:
    if str(row.get("status") or "") != "live_structural_executed":
        return False
    order_id = row.get("exchange_order_id") or row.get("order_id")
    order_status = str(row.get("order_status") or row.get("status_detail") or "").lower()
    return bool(order_id) and (not order_status or order_status in {"filled", "matched", "success"})


def _totals(
    strategies: list[dict[str, Any]],
    settlement: dict[str, Any],
    run_artifact: dict[str, Any],
    live_status: dict[str, Any],
    *,
    active_run: dict[str, Any],
) -> dict[str, Any]:
    summary = settlement.get("summary") if isinstance(settlement.get("summary"), dict) else {}
    event_cycle_counts = run_artifact.get("event_cycle_counts") or live_status.get("event_cycle_counts") or {}
    run_started_at_utc = active_run.get("started_at_utc")
    return {
        "estimated_spent_usd": run_artifact.get("estimated_spent_usd", live_status.get("estimated_spent_usd")),
        "event_count": len(run_artifact.get("event_results") or live_status.get("event_results") or []),
        "event_window_count": len(event_cycle_counts) if isinstance(event_cycle_counts, dict) else 0,
        "elapsed_5m_window_count": _elapsed_5m_window_count(run_started_at_utc),
        "run_started_at_utc": run_started_at_utc,
        "filled_position_count": summary.get("filled_position_count", sum(int(item.get("filled_position_count") or 0) for item in strategies)),
        "settled_position_count": summary.get("settled_position_count", sum(int(item.get("settled_position_count") or 0) for item in strategies)),
        "unresolved_position_count": summary.get("unresolved_position_count", sum(int(item.get("unresolved_position_count") or 0) for item in strategies)),
        "active_cost_usd": summary.get("active_cost_usd", sum(float(item.get("active_cost_usd") or 0.0) for item in strategies)),
        "realized_pnl_usd": summary.get("realized_pnl_usd", sum(float(item.get("realized_pnl_usd") or 0.0) for item in strategies)),
        "win_rate": summary.get("win_rate"),
    }


def _settlement_rows_for_strategy(settlement: dict[str, Any], strategy_id: str, *, positive: bool) -> list[float]:
    values = []
    for row in settlement.get("rows") or []:
        if not isinstance(row, dict) or row.get("strategy_id") != strategy_id:
            continue
        pnl = _float(row.get("pnl_usd"))
        if pnl is None:
            continue
        if positive and pnl > 0:
            values.append(pnl)
        if not positive and pnl <= 0:
            values.append(pnl)
    return values


def _avg(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 6)


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_window_start_utc(event_slug: Any) -> str | None:
    if not event_slug:
        return None
    raw = str(event_slug).rsplit("-", 1)[-1]
    try:
        timestamp = int(raw)
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _elapsed_5m_window_count(started_at_utc: Any) -> int | None:
    if not started_at_utc:
        return None
    try:
        started = datetime.fromisoformat(str(started_at_utc).replace("Z", "+00:00"))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed_seconds = max(0.0, (datetime.now(UTC) - started.astimezone(UTC)).total_seconds())
    return int(elapsed_seconds // 300) + 1


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
