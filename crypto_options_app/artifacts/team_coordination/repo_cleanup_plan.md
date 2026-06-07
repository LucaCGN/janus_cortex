# Crypto Options Repo Cleanup Plan

Updated: 2026-06-07T04:40:00Z

## Rule

Do not delete old work.

## Target Reference Roots

- `wnba_nba_app_reference/`
- `global_app_reference/`

## Active Crypto Roots

- `crypto_options_app/`
- `tests/crypto_options_app/`
- crypto-specific scripts/tools/docs/specs
- crypto-specific GitHub issues/workflows

## Cleanup Order

1. Inventory non-crypto files and dirty tracked paths.
2. Classify each path as crypto active, WNBA/NBA reference, global reference, compatibility wrapper, or unknown.
3. Move only after inventory review.
4. Keep compatibility wrappers until imports/tests are updated.
5. Run crypto-only tests after each move batch.

## Current Inventory

Source artifact: `crypto_options_app/artifacts/reports/repo_cleanup_inventory_latest.json`
Batch artifact: `crypto_options_app/artifacts/reports/repo_cleanup_batches_latest.json`
Batch 0 staging artifact: `crypto_options_app/artifacts/reports/repo_baseline_staging_plan_latest.json`

- Dirty/status paths: 902
- Active crypto paths: 681
- Review-required paths: 219
- Crypto compatibility wrapper candidates: 131
- Global reference candidates: 69
- WNBA/NBA reference candidates: 3
- Local automation state review paths: 15
- Root config review paths: 1
- Unknown/root review paths: 0

Automatic moves remain disabled.

Current batch summary:

- Batch 0 active crypto baseline: 656 paths, branch `codex/crypto-transition-control-plane`
- Batch 1 local/root review: 16 paths, branch `codex/crypto-repo-local-state-cleanup`
- Batch 2 WNBA/NBA reference: 3 paths, branch `codex/crypto-repo-wnba-nba-reference`
- Batch 3 global reference: 69 paths, branch `codex/crypto-repo-global-reference`
- Batch 4 crypto compatibility wrapper decision: 131 paths, branch `codex/crypto-compatibility-wrapper-cutover`
- Batch 5 GitHub source-of-truth setup: 0 dirty paths, branch `codex/crypto-github-workflow-setup`

## Move Batch Plan

### Batch 0: No-Move Active Crypto Baseline

Purpose: establish a reviewable crypto baseline before moving legacy work.

- Keep `crypto_options_app/` and `tests/crypto_options_app/` as active roots.
- Do not move generated data, artifacts, or DB files.
- Run focused crypto transition tests.
- Stage only files marked `recommended_git_action=stage` in `repo_baseline_staging_plan_latest.json`.
- Hold files marked `recommended_git_action=hold`.

Suggested branch: `codex/crypto-transition-control-plane`.

Current staging split:

- Stage candidates: 295
- Hold paths: 386
- Manual review paths: 0

Batch 0 result:

- Branch: `codex/crypto-transition-control-plane`
- Commit: `d642702` (`Add crypto options transition control plane baseline`)
- Committed paths: 295 source-of-truth files
- Held paths: generated/data/runtime artifacts remain unstaged

### Batch 1: Local State And Root Config Review

Purpose: avoid accidentally committing local automation memory or broad dependency churn.

- Review `.codex_automation_memory/` and decide ignore/archive behavior.
- Review `requirements.txt` changes before any reference move.
- Add/update ignore rules only if needed after inspection.

Suggested branch: `codex/crypto-repo-local-state-cleanup`.

### Batch 2: WNBA/NBA Reference Move

Purpose: isolate old sports-bot context without deleting it.

- Move only reviewed `wnba_nba_reference_candidate` paths into `wnba_nba_app_reference/`.
- Keep README/context explaining that these are historical references for future modules.
- Run crypto-only tests after the move.

Suggested branch: `codex/crypto-repo-wnba-nba-reference`.

### Batch 3: Global Legacy Reference Move

Purpose: isolate non-crypto Janus runtime/tooling while keeping it searchable.

- Move reviewed `global_reference_candidate` paths into `global_app_reference/`.
- Preserve original relative path under the reference root.
- Do not move crypto compatibility wrappers in this batch.
- Run crypto-only tests after the move.

Suggested branch: `codex/crypto-repo-global-reference`.

### Batch 4: Crypto Compatibility Wrapper Decision

Purpose: avoid breaking current runtime routes while converging on `crypto_options_app/`.

- Review each `crypto_compatibility_wrapper_candidate`.
- Keep wrappers temporarily when current API/scripts/tests still import them.
- Migrate wrappers into `crypto_options_app/compatibility/` only after import paths and tests are adjusted.
- Prefer replacing wrappers with direct `crypto_options_app` entrypoints over keeping duplicate logic.

Suggested branch: `codex/crypto-compatibility-wrapper-cutover`.

### Batch 5: GitHub Source-Of-Truth Setup

Purpose: make issues and milestones safe to use as fixed-chat context.

- Create milestones from `github_issue_milestone_plan.md`.
- Create initial issues only after repo cleanup branches are reviewable.
- Link issues back to `team_coordination` artifacts and technical specs.

Suggested branch: `codex/crypto-github-workflow-setup`.

## Fixed-Chat Gate

Fixed chats remain gated until:

- Batch 0 has a clean baseline.
- The path-level inventory has been reviewed.
- GitHub milestone/issue source-of-truth is ready.
- `fixed_chat_bootstrap.md` is updated with the final branch/artifact references.

The fixed-chat prompts are ready, but starting the chats before this gate would create duplicate context and likely make cleanup harder.

## Acceptance Gate

- Git status is reviewable.
- Crypto-only tests run without old app concerns.
- GitHub issues/milestones can map cleanly to crypto work.
- Fixed chats and automations can read a clean project structure.
