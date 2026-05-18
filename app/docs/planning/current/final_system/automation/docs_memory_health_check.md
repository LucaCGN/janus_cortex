# Janus Docs And Memory Health Check

Status: initial controller checklist
Owner: `docs-memory-agent` / `master-controller`

## Purpose

Verify that Janus source-of-truth layers remain usable as the system moves from chat-driven work into repo, Obsidian, GitHub, and runtime-state driven operation.

This check does not validate live trading readiness. It validates whether agents can find the correct instructions, issues, context, and durable memory without relying on chat history.

## Authority Boundaries

| Layer | Health Question |
|---|---|
| Repo docs | Are current contracts and queue files present, readable, and linked? |
| GitHub issues | Do issue-backed tasks exist for immediate work, and are their states reflected in local queue/status artifacts? |
| Obsidian | Are curated notes present and linked back to repo/runtime sources? |
| Runtime handoffs | Do handoffs summarize current safety/readiness and point to latest reports? |
| Runtime artifacts | Are machine-readable artifacts available for the current domain when needed? |

## Required Checks

1. Read `app/docs/planning/current/final_system/README.md`.
2. Read `app/docs/planning/current/final_system/automation/master_controller_contract.md`.
3. Confirm `app/docs/planning/current/final_system/backlog/immediate_issue_seed_2026-05-17.md` lists GitHub issue URLs.
4. Query GitHub for open `JANUS-P0-*` and `JANUS-P1-*` issues.
5. Confirm Obsidian index exists at `C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain\00_Janus_Control\Janus Master Index.md`.
6. Confirm LLM-wiki control files exist:
   - `AGENTS.md`
   - `index.md`
   - `log.md`
   - `raw\README.md`
7. Confirm the first curated notes exist:
   - `00_Janus_Control\Issue Backlog Index.md`
   - `00_Janus_Control\Janus Wiki Maintenance Runbook.md`
   - `10_System_Specs\Premise Decisions 2026-05-17.md`
   - `10_System_Specs\Source Of Truth Layering.md`
   - `10_System_Specs\Controller And Queue Design.md`
   - `10_System_Specs\GitHub Remote State 2026-05-17.md`
   - `20_Trading_Knowledge\Scenario Taxonomy S-A-B-C-D.md`
   - `20_Trading_Knowledge\Profit-Ratcheted Risk Engine.md`
   - `30_Game_Reviews\CLE DET 2026-05-13.md`
   - `30_Game_Reviews\WNBA Lynx Wings 2026-05-14.md`
   - `40_Profile_Studies\Polymarket Winning Profiles Overview.md`
8. Confirm `local/shared/handoffs/daily-live-validation/status.md` includes the latest controller pass.
9. Confirm unresolved live-safety gates are explicit before any pregame/live mode.

## Output

A controller health-check pass should write a short runtime artifact under:

`local/shared/artifacts/final-system-controller/YYYY-MM-DD/`

The artifact should include:

- Timestamp UTC and BRT.
- Selected controller mode.
- GitHub issue state summary.
- Obsidian note presence summary.
- Janus API/service state if checked.
- Live-safety gate summary.
- Next recommended issue-backed task.

For repeated no-change passes, follow `master_controller_contract.md` no-change compression. Do not emit a fresh artifact every recurring pass unless a material state change occurred or at least 60 minutes passed since the last artifact.

## Failure Handling

| Failure | Controller Behavior |
|---|---|
| Missing repo contract | `NOTIFY`; controller cannot safely route work. |
| Missing issue seed after bootstrap | `NOTIFY`; backlog is not durable. |
| Missing Obsidian note | `DONT_NOTIFY` unless repeated or blocking; add repair task. |
| GitHub unavailable | `DONT_NOTIFY` once; retry next pass, unless queue work depends on it. |
| Janus API down | If expected by cost-safety gate, record only. If unexpected near live window, `NOTIFY`. |
| Live-money gate unclear | `NOTIFY`; block live mode. |

## Completion Criteria For `JANUS-P0-007`

`JANUS-P0-007` can move to review when:

- Repo final-system docs exist and are linked.
- Obsidian bootstrap notes exist.
- Obsidian includes the LLM-wiki control files: `AGENTS.md`, `index.md`, `log.md`, and `raw/`.
- GitHub issues are created and linked from the issue seed.
- Controller automation exists and points to repo docs.
- This health-check procedure exists.
- At least one controller pass writes a runtime artifact and updates the daily status handoff.
