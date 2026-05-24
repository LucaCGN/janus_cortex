# Janus CI/CD Automation Roster

Status: active source-of-truth roster
Updated: 2026-05-24
Parent issue: https://github.com/LucaCGN/janus_cortex/issues/63

## Purpose

Define the automation lanes needed to run Janus as a full CI/CD and live-learning system.

This roster separates app-owned live execution, Codex development, oversight, research, portfolio management, postgame review, Obsidian curation, and GitHub Actions CI. It does not authorize live orders by itself.

## Current Codex App Automations

| Automation | Kind | Cadence | Status | Primary issues | Authority |
|---|---|---:|---|---|---|
| `janus-master-dev` (`janus-master-controller`) | cron | 15m | active | #61, #62, #63-#70, #55, #42, #44 | Issue-backed development/live-readiness executor. May patch code/docs/tests and update issues after queue claim. Janus live actions only through approved Janus StrategyPlan/live-worker gates. |
| `master-janus-manager` | cron | 1h | active | #73, #63, #71, #74 | Automation-system manager. Reviews last runs across all Codex lanes, detects prompt/memory/source-of-truth drift, and enforces GitHub issues, repo docs, and Obsidian as durable CI/CD truth. No trading or worker starts. |
| `oversight-devloop` | cron | 30m | active | #73, all active P0/P1 | Dev-loop oversight and anti-stagnation. Splits/closes/routes issues, checks dirty worktree and queue locks. No trading or worker starts. |
| `oversight-portfolio` (`janus-loop-oversight-console`) | cron | 1h | active | #56, #59 | Portfolio-manager oversight only. Reviews strategy/action/rationale drift. No orders, redemptions, or services. |
| `janus-portfolio-manager` | cron | 6h deep pass | active | #56, #59 | Active Codex global portfolio manager for non-Janus-covered portfolio slots. May trade only through approved portfolio-manager order gates. |
| `janus-obsidian-builder` | cron | 6h | active | #74 support | Curated memory and note hygiene. No execution authority. |
| `obsidian-backlog-ingestor` | cron | daily 07:30 BRT | active | #74 | Converts curated notes into idea/planned/issue-candidate backlog rows. No execution authority. |
| `nba-pregame-research` | cron | 6h | active | #72 | Structured optional NBA priors. Missing/stale output cannot block Janus liveness. |
| `wnba-pregame-research` | cron | 6h | active | #72 | Structured optional WNBA priors with WNBA source caveats. Missing/stale output cannot block Janus liveness. |
| `janus-postgame-signal-review` | cron | 3h | active | #70 | Postgame signal-performance artifact generator for fired/blocked/missed signals, sleeve PnL, latency, fillability, and config recommendations. |
| `janus-performance-review` | cron | daily 06:30 BRT | active | #71 | Project-chief loop. Converts live/postgame/portfolio/dev evidence into ranked next-day development and strategy-improvement plan. Read-only trading. |

Historical stale directories such as `janus-master-controller-2` are not active unless a valid `automation.toml` exists and the source-of-truth roster is updated.

## Pinned Chat Policy

Recurring Janus CI/CD lanes must run as cron-style Codex app automations (`kind = "cron"`) with explicit repo `cwds`. Do not run Janus oversight or manager lanes as pinned-chat heartbeat automations. The pinned heartbeat path has produced missing or unreliable automation memory/JSON artifacts, which breaks source-of-truth review. Manual pinned chats may still receive operator discussion, but durable automation state belongs in automation memory, runtime artifacts, repo docs, GitHub issues, and Obsidian.

## App-Owned Runtime Services

These are not Codex automations and should not be replaced by Codex heartbeats.

| Service | Cadence | Owner | Authority |
|---|---:|---|---|
| Janus FastAPI | service | Janus app | API, DB, catalog, StrategyPlan, portfolio/order-management, runtime gates. |
| Janus live strategy worker | game-window service | Janus app | Covered-market NBA/WNBA live evaluation/execution through approved StrategyPlan/live-worker gates only. |
| League data sync adapters | scheduled/service | Janus app | NBA/WNBA/future league data sync. |
| Live feed adapters | game-window service | Janus app | Scoreboard, play-by-play, stats, CLOB/orderbook/account snapshots with freshness metadata. |
| Signal aggregation runtime | app module/service | Janus app | Merge deterministic/ML/LLM/Codex/operator signals into event-scoped decisions. Target implementation: #65/#66/#69. |

Codex automations may inspect, patch, or configure these services only through issue-backed changes and runtime gates.

## GitHub Actions CI

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/janus-ci.yml` | pull request, push to `main` | Install dependencies, compile app/tool packages, run focused runtime/API/portfolio tests, and run `git diff --check`. |
| `.github/workflows/janus-nightly-replay.yml` | daily scheduled, manual dispatch | Compile replay/postgame surfaces and run replay-focused NBA/WNBA tests. Upload local replay artifacts when produced. |

GitHub Actions must not require live Polymarket credentials or live-money environment variables. Live trading validation remains local/runtime gated and issue-backed.

## Prompt Drift Policy

Codex app automation prompts should stay short and point to tracked repo contracts:

- `master_automation_system_prompt.md`
- `global_portfolio_manager_prompt.md`
- `global_portfolio_manager_contract.md`
- `live_signal_aggregation_contract.md`
- `janus_core_live_trading_runtime.md`
- `live_runtime_scope_map_2026-05-24.md`
- `backlog_layers.md`
- `modular_curation_policy.md`

If an automation needs new behavior, edit the repo contract first, then reduce the automation prompt to a stable pointer. Do not paste large changing policy blocks into the app prompt unless there is no tracked repo owner yet.

## Startup Order

1. Keep Janus API/runtime healthy and confirm `python codex_tool/janus_status.py`.
2. Ensure dirty worktree slices are owned by queue locks or committed/pushed.
3. Enable `janus-master-dev`, `master-janus-manager`, and `oversight-devloop`.
4. Enable portfolio lanes only after global portfolio risk/order gates are understood.
5. Enable pregame/postgame/performance-review lanes.
6. Keep Obsidian builder and backlog ingestor active after source-of-truth docs are current.
7. Use GitHub Actions for remote regression evidence; do not treat CI success as live-order permission.

## Hard Prohibitions

- Oversight, research, Obsidian, performance-review, and GitHub Actions lanes must not place, cancel, replace, submit, sign, broadcast, redeem, or start live-money workers.
- `janus-portfolio-manager` can trade only through approved global portfolio-manager gates.
- Janus covered-market NBA/WNBA live execution can occur only through app-owned StrategyPlan/evaluate/execute/live-worker paths.
- Obsidian and GitHub text are memory/backlog, not live trading truth.
