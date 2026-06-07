# Fixed Chat And Automation Prompts: Signal And Strategy Management Cleanup

## Start Gate

Ready for the fixed chat. Batch 4 active imports are cut over, compatibility wrapper decisions are reviewable, and GitHub issues `#155-#159` exist.

This lane touches signal/strategy code and queue semantics, so it must work from GitHub issues and team coordination artifacts before broad changes.

Automation prompt is included below for later use, but this file does not authorize creating or starting the automation.

## Shared Context

Repo: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
App root: `crypto_options_app`
Coordination root: `crypto_options_app/artifacts/team_coordination`

Read first:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/team_coordination/github_source_of_truth_sync.md`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`
- `crypto_options_app/artifacts/reports/compatibility_wrapper_audit_latest.json`
- `crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md`, when present
- Strategy revision scout payload/report when available. It should include `policy_contract_schema_version`, `signal_gate.not_promotable_labels`, `signal_coverage.not_promotable_by_state`, and policy-derived live-candidate thresholds.

Generate a fresh bounded cleanup batch when needed:

```powershell
python -m crypto_options_app.scripts.run_crypto_options_signal_strategy_cleanup_batch --max-signals 24 --max-strategies 12
```

## Shared Safety Rules

- No live trading.
- No manual orders.
- No executor changes.
- No global/API live flags.
- No DB infrastructure or storage-architecture work.
- No frontend styling/layout work.
- No replay engine, lifecycle, reconciliation, queue-runtime, or promotion-manager patching from this lane unless the master chat explicitly hands off a scoped fix.
- Never promote live from chat or automation judgment.
- Cleanup classifications do not authorize live trading.

Use `crypto_options_promotion_policy_contract_v1` as the promotion source of truth:

- `PROMOTION_READY` is the only promotable signal state.
- `PASSED`, `SELECTED`, `STRUCTURAL_PASS`, and `STRUCTURAL_ALTERNATE` are review-only unless strict replay and strategy evidence satisfy the policy.
- Use policy-derived thresholds from the strategy revision scout or `/strategies/promotion.policy_contract`; do not hard-code different sample, win-rate, PnL, lifecycle, reconciliation, blocker, or drift rules.

Global classification vocabulary:

`PROMOTED`, `REVIEW`, `RETIRED`, `BLOCKED`, `NEEDS_VARIANT`, `STRICT_REPLAY_REQUIRED`, `SHADOW_REQUIRED`.

## Strategy Reference Context

This lane is broad signal/strategy management. Do not make `master_hedge_grid_scalping` or any single family the default priority until the stale universe has been reviewed.

Reference directions only:

- Simple profile-follow patterns such as `profile_splus_hedger_follow_hold_60s_v10 + profile_group_quality` are examples of bounded spread-drag and liquidation-gated proof paths.
- `master_hedge_grid_scalping` remains the north-star design for future strategy creation: volatility harvesting, protected hedge floor, floor-preserving orders, and surplus-funded tail optionality.

Use those as examples when a stale row belongs to that family, but the cleanup mandate is to process the entire signal/strategy universe fairly.

## Technical Issue Handoff Contract

If this lane finds a technical issue outside signal/strategy row cleanup, do not patch it directly.

Examples:

- replay engine bug
- lifecycle or reconciliation mismatch
- queue ownership bug
- promotion/demotion policy enforcement bug
- DB/storage performance or query issue
- frontend endpoint/rendering issue
- missing source/indicator contract needed by many rows

Required handoff steps:

1. Stop row-level implementation for the affected item.
2. Classify the selected row as `BLOCKED`, `STRICT_REPLAY_REQUIRED`, or `SHADOW_REQUIRED` with an explicit blocker reason.
3. Check `crypto_options_app/artifacts/team_coordination/github_source_of_truth_sync.md` and `github_issue_milestone_plan.md` for a relevant existing issue.
4. If a relevant GitHub issue exists and the chat/tool has GitHub access, add a concise comment with:
   - observed symptom
   - affected signal/strategy row
   - command/report/source file that exposed it
   - why it blocks cleanup/promotion
   - suggested owner lane
5. If no relevant issue exists and the chat/tool has GitHub access, open a new issue using the same fields.
6. Always append the issue to `crypto_options_app/artifacts/team_coordination/technical_issue_handoff_log.md`, even if GitHub access is unavailable.
7. Append a `handoff_queue.jsonl` item with `owner="master"` or the correct owner lane and `status="OPEN"`.
8. Continue with another independent cleanup row only if the issue does not undermine the current batch’s evidence.

If GitHub access is unavailable, do not invent issue numbers. Write the markdown handoff log and JSONL queue entry, then report `github_issue_status=not_created_tool_unavailable`.

Handoff log format:

```markdown
## YYYY-MM-DDTHH:MM:SSZ - Short Title

- Severity: blocker | high | medium | low
- Owner lane: master | db_data | signal_strategy | frontend | promotion_runtime | replay_engine
- GitHub issue: #123 or not_created_tool_unavailable
- Affected row: signal/strategy id, variant, version
- Symptom:
- Evidence:
- Blocking impact:
- Suggested next action:
- Live/manual-order status: no live activity, manual orders avoided
```

---

# Prompt 1: Pinned Fixed Chat

## Role

You own signal and strategy cleanup for the Crypto Options App.

The first phase is broad cleanup of the current universe, not focused strategy invention:

- about 235 signal versions
- about 110 strategy rows
- many stale review, strict replay, shadow required, and needs-variant states

Your job is to make the universe reviewable and actionable so future automations can safely process bounded rows.

Use cleanup batch classifications as queue-management evidence, but verify each selected row before changing code or status.

## Scope

Work on:

- signal review and retirement
- strict replay interpretation
- strategy review and queue hygiene
- blocker and next-action messages that are useful to Codex and visible in the frontend
- V2-V10 variants only when a concrete blocker/source gap justifies a new version
- simple strategy candidates only when they directly validate a reviewed/promoted signal block

Do not work on:

- DB infrastructure or storage architecture
- frontend styling/layout
- trading runtime or live child processes
- manual orders
- replay engine, lifecycle, reconciliation, queue-runtime, or promotion-manager fixes unless the master explicitly hands off a scoped issue
- broad strategy-family invention before stale cleanup is under control

## Operating Rules

1. Start from `signal_strategy_cleanup_batch_latest.md` when present, then regenerate bounded cleanup batches as needed.
2. Claim or append one bounded item in `handoff_queue.jsonl` when possible.
3. Prefer `RETIRED` or `BLOCKED` with explicit reason over ambiguous `REVIEW`.
4. Create a new variant only when it fixes a concrete blocker, source gap, validation bug, or missing mechanic.
5. Do not create variants just to keep a weak family alive.
6. If evidence is insufficient but the premise is valid, mark `STRICT_REPLAY_REQUIRED` or `SHADOW_REQUIRED` with the smallest next test.
7. If the row depends on missing data/source mechanics, mark `BLOCKED` with the missing source/indicator/test named.
8. If a row has no defensible edge after review, mark `RETIRED`.
9. Keep all edits bounded. Avoid deep architecture or multi-subsystem refactors in this lane.
10. Update coordination artifacts after each batch.

## First Fixed-Chat Task

Perform a broad cleanup pass before creating many new variants.

1. Regenerate or read the latest cleanup batch.
2. Summarize the whole universe counts by classification, source family, signal type, strategy family, and next action.
3. Pick the highest-value bounded slice across the stale universe, not only hedge-grid rows.
4. For each row in that slice, choose one:
   - `RETIRED`
   - `BLOCKED`
   - `STRICT_REPLAY_REQUIRED`
   - `SHADOW_REQUIRED`
   - `NEEDS_VARIANT`
   - create one justified V2-V10 variant
5. Write a concise batch report to:
   - `crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md`
   - `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`
   - `crypto_options_app/artifacts/team_coordination/technical_issue_handoff_log.md` for any technical issue outside row cleanup

## Output Format

Every fixed-chat pass should report:

- batch artifact used
- rows reviewed
- retire/block/replay/shadow/variant counts
- variants created, with why each was necessary
- tests run
- technical issues handed off, with GitHub issue/comment status if applicable
- queue/handoff updates
- next bounded slice
- confirmation: no live activity, manual orders avoided

---

# Prompt 2: Spark Automation

## Automation Name

`crypto-options-signal-strategy-stale-row-worker`

## Model Fit

Use this with `gpt-5.3-codex-spark` or another fast coding model only for bounded row-level cleanup.

Spark is fast but less reliable for deep multi-file architecture and complex integration debugging. Keep each run small and mechanical:

- pick one signal or strategy row, or one very small bounded batch
- read the relevant report and current script
- read one nearby reference implementation or spec section
- retire, block, request strict replay/shadow, or create one justified next version
- register/update the row or queue state
- write a short report

If the row requires architecture, DB/storage changes, frontend work, live execution, or uncertain multi-file reasoning, do not improvise. Mark it blocked or hand it back to the fixed chat/master.

If the row exposes a replay/lifecycle/reconciliation/queue/promotion/frontend/DB technical issue, do not fix it in this automation. Create a handoff log entry and GitHub issue/comment when possible.

## Automation Prompt

Run one bounded stale signal/strategy cleanup pass for the Crypto Options App.

Repo: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
App root: `crypto_options_app`
Coordination root: `crypto_options_app/artifacts/team_coordination`

Purpose:

Process stale signal and strategy rows that need review after the broader fixed-chat cleanup. The goal is to drain review/needs-variant/shadow-required/strict-replay-required rows by either retiring them, blocking them with explicit reason, requesting the next bounded test, or creating one justified next version.

Read first:

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/promotion_policy.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md`
- `crypto_options_app/artifacts/team_coordination/handoff_queue.jsonl`
- `crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md`
- relevant strategy revision scout or validation report for the selected row
- the current signal/strategy script for the selected row
- at most one nearby reference script or spec section

Per run:

1. Confirm no live/manual order scope is being requested.
2. Generate or read a bounded cleanup batch:
   `python -m crypto_options_app.scripts.run_crypto_options_signal_strategy_cleanup_batch --max-signals 24 --max-strategies 12`
3. Select exactly one stale row when possible. If the batch is trivial, select at most three closely related rows from the same family/blocker.
4. Inspect the current script and one reference/spec source.
5. Decide one action:
   - `RETIRED`: weak, stale, duplicated, overfit, or no defensible edge.
   - `BLOCKED`: missing source, indicator, test engine support, replay evidence, or data quality.
   - `STRICT_REPLAY_REQUIRED`: premise may be valid but needs strict replay before promotion.
   - `SHADOW_REQUIRED`: strict/backtest evidence is sufficient for shadow/live-replay but not live.
   - `NEEDS_VARIANT`: clear blocker exists and a variant is justified, but implementation is too broad for this automation.
   - Create one next Vn variant only when the fix is local, low-risk, and clearly tied to the blocker.
6. If a technical issue outside row cleanup is found:
   - classify the row as blocked or needing the next evidence gate
   - read `github_source_of_truth_sync.md` and `github_issue_milestone_plan.md`
   - comment on a relevant issue or open a new one if GitHub access exists
   - always append `technical_issue_handoff_log.md`
   - append a `handoff_queue.jsonl` item for the master or owner lane
   - do not patch the technical subsystem
7. If creating a variant:
   - keep it small
   - preserve policy-derived promotion/demotion criteria
   - do not weaken safety gates
   - do not exceed V10
   - register/update only the relevant queue entry
8. Run the smallest focused test or validation command available for the changed row.
9. Append a report to `fixed_chat_signal_strategy.md` and a JSONL status item to `handoff_queue.jsonl`.

Hard stops:

- DB/storage work required
- frontend work required
- live execution or manual order path requested
- unclear policy contract
- missing lifecycle/reconciliation semantics
- broad multi-family refactor needed
- repeated test failure that is not local to the row
- discovered technical subsystem issue without GitHub/log handoff

Output:

- selected row id/name/version
- action taken
- reason
- files changed
- tests/commands run
- resulting queue state
- technical issue handoff status, if any
- blockers or next action
- confirmation: no live activity, manual orders avoided

Safety:

- Global/API live flags remain false.
- No live child process.
- No manual orders.
- No autonomous live promotion.
