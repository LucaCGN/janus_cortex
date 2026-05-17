# Janus Premise Decisions - 2026-05-17

Status: normalized from operator-annotated premise register
Input: `app/docs/planning/current/janus_final_system_premise_register.md`

## Decision Legend

| Status | Meaning |
|---|---|
| `Accepted` | Use as a guiding premise. |
| `AcceptedWithEdits` | Accepted after applying the operator correction below. |
| `Rejected` | Do not use as originally written. Replace with corrected premise. |
| `NeedsDesign` | Must be resolved in the next design iteration. |
| `Mutable` | Current best premise, but expected to evolve with evidence. |

## High-Level Accepted Premises

- Current-state architecture mapping is accurate and accepted.
- Repo docs, runtime artifacts, handoffs, reports, Obsidian, GitHub issues, and chat must be separated by authority and purpose.
- Janus must run independently from Codex, but Codex remains one of the three control actors: User, Codex, Janus.
- Live-game development work should stop during games except for critical patching and safety repair.
- WNBA must move faster than originally framed because NBA playoff test windows are sparse.
- The final system should be framed as an autonomous and self-evolving expectation-markets trading system, not only an NBA/WNBA trading system.
- Basketball remains the immediate implementation domain, with NBA and WNBA first.
- Future crypto/geopolitics/economics modules remain backlog/foundation scope until basketball and core infrastructure are stable.

## Key Operator Corrections

### Authority And Control

- `P033` is accepted only with a distinction between authority and priority.
- Pregame research normally has context authority only.
- If Janus internal LLM is unavailable, Codex may temporarily act as strategy crafter with authority routed through Janus-compatible artifacts, endpoints, and reconciliation.
- Codex authority is not priority. Janus should still prefer app-owned deterministic/ML/LLM paths when healthy.

### Codex As Runtime Participant

- `P056` is rejected as written.
- Corrected premise: Codex should not be the required recurring executor for normal live operation, but Codex and the human operator can be recurring executors or parallel strategy actors during live games when they intentionally place or manage positions.
- Codex may identify a winning Janus strategy and enter a parallel position without interfering with Janus reasoning, as long as direct CLOB reconciliation and event inventory adoption are in place.

### User-Codex-Janus Triad

- The final system has three simultaneous actors:
  - User.
  - Codex.
  - Janus app/runtime.
- All three can observe, reason, and affect portfolio state.
- The system must detect, reconcile, and attribute actions from all three without breaking strategy logic.

### Automation Design

- The old multi-pinned-chat automation structure is not final.
- The final automation should be one stable recurring controller that spawns or routes work based on repo docs, Obsidian references, issues, time window, and live-event state.
- Codex should be used for reasoning and coding capability, not memory management.
- The automation prompt should be immutable or rarely changed.
- The flow it points to must be mutable through repo docs and Obsidian.
- A periodic health/review pipeline must inspect whether repo docs, Obsidian notes, issue state, and agent outputs are being used correctly.

### Runtime And Service Architecture

- Start from the current FastAPI modular monolith.
- Keep a single repository even if the system later grows multiple FastAPI apps, workers, Docker services, or microservices.
- Add Redis or external queueing only if the need becomes concrete.

### WNBA Priority

- `P126`, `P260`, and `P264` remain safety-valid, but WNBA should be promoted to urgent basketball priority after core cost/shutdown and ledger controls are safe enough.
- Because NBA playoff games are sparse, WNBA provides critical live-test surface.
- Minimal WNBA live tests may be allowed once algorithm/ML lanes are shadow-active, direct CLOB truth is clean, and the same safety controls used for NBA are available.

### CLOB And Manual Execution

- All planned live orders should pass through audited Janus endpoints or order-manager paths.
- Exception: if immediate manual/Codex intervention is necessary for stop-win, stop-loss, or urgent portfolio repair, the system must reconcile it after the fact and ingest it as first-class event inventory.
- Market orders should generally remain disabled, not only during testing.
- Only exception: realizing gains immediately after a sharp price spike when the strategy expects rapid reversal and the action is explicitly classified as urgent profit capture.
- Minimum order assumptions must be validated against direct CLOB and Polymarket UI behavior. The current `$1` minimum may be wrong for sub-cent or 0.1c markets.

### Ledger And Performance Review

- DB-origin performance is not enough.
- Global performance review must combine:
  - Janus DB.
  - Direct Polymarket records.
  - Direct CLOB truth.
  - Obsidian notes.
  - Repo/runtime reports.
  - Manual and Codex interventions.
- Future reporting must separate autonomous Janus, Codex-assisted Janus, user/manual, and external account activity.

### Scenario Taxonomy

- S/A/B/C/D taxonomy is accepted as current best framework.
- It is mutable and should evolve with new evidence.
- It must be treated as a living model, not a fixed dogma.

### LLM Authority

- `P209` is rejected as written.
- Corrected premise: internal LLM should not call raw exchange endpoints directly, but it may generate immediate executable actions, including buy/cancel/target/hedge/close recommendations, if those actions flow through Janus validators and order-manager execution.
- ML/PBP triggers may request fast LLM action. The LLM can propose an immediate order plus a StrategyPlanJSON update, but Janus still validates safety, sizing, freshness, and reconciliation.

### Model Cost Strategy

- Frontier model use is not economically viable at current bankroll/API scale.
- Prefer nano and mini for nearly all live operation until realized returns justify higher cost.
- The system should route based on OpenAI available balance and event budget, not only severity.
- If OpenAI API budget or credits fail, app state must expose that games need Codex-generated StrategyPlanJSON or Codex monitoring.
- Deterministic and ML lanes should keep running during internal LLM downtime.
- Codex fallback will be slower, possibly 5 to 10 minutes delayed, but is better than deterministic-only behavior in important regimes.

### Reporting And Chart Equivalents

- The system does not need visual chart reading.
- It needs analytical equivalents of chart insights:
  - Number of underdog/favorite inversions.
  - Price oscillation amplitude.
  - Spike frequency.
  - Smooth trend versus jagged trend.
  - Grid-scalp opportunity count.
  - Price/path regimes around PBP events.
- These should be available through endpoints/tools for fast live-monitor context.

### Future Domains

- Basketball is first, but Janus must be framed as expectation-markets infrastructure.
- Future modules should stay in backlog until WNBA is active and basketball core is stable.
- Crypto 15-minute modeling is a major future ML opportunity, but not an immediate implementation lane.

## New Missing Premises Added

- **NP001 [Anchor]** The recurring controller prompt must be stable and minimal; mutable behavior belongs in repo docs and Obsidian.
- **NP002 [Anchor]** The controller must periodically audit whether the documentation and memory system itself is healthy.
- **NP003 [Anchor]** Internal LLM model choice must consider available OpenAI budget and bankroll, not just reasoning severity.
- **NP004 [Anchor]** Janus must expose API/DB state saying "LLM unavailable, Codex strategy required" when internal LLM cannot operate.
- **NP005 [Anchor]** Codex fallback must be able to generate StrategyPlanJSON and monitoring instructions from current app state.
- **NP006 [Anchor]** If Codex is used as fallback strategy interface, Janus safety gates still apply unless the user explicitly performs manual external intervention.
- **NP007 [Anchor]** The system should explicitly model User, Codex, and Janus as separate actors in the ledger, event inventory, decision timeline, and postgame review.
- **NP008 [Anchor]** Repo, Obsidian, GitHub issues, and runtime local state must be designed together, not as independent memory systems.
- **NP009 [Anchor]** The immediate phase ends only when repo docs, Obsidian bootstrap, GitHub issue backlog, and a preliminary controller/queue loop are all working enough for review.
- **NP010 [Anchor]** The next final specs should be produced only after that preliminary repo/Obsidian/GitHub/queue loop has been exercised.

## Design Consequences For Next Iteration

1. Write the current-state architecture document before final architecture.
2. Build the docs/Obsidian/GitHub/queue control layer before deep implementation.
3. Convert the immediate backlog into GitHub issues.
4. Define a controller automation that reads mutable docs.
5. Add Codex fallback and OpenAI-budget-aware LLM routing to the P0 backlog.
6. Prioritize WNBA readiness sooner than the original P2 framing, after core safety controls are in place.
7. Treat the event review bundle, ledger cleanup, and direct CLOB manual assistant as infrastructure blockers.
8. Produce preliminary specs only after this organization loop proves usable.
