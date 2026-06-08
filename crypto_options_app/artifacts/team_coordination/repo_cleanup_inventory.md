# Crypto Options Repo Cleanup Inventory

Generated: 2026-06-07T03:56:05Z
Latest readiness review: 2026-06-07T04:24:45Z

## Purpose

Prepare the repo for clean crypto-options commits, issues, milestones, fixed chats, and automations without deleting legacy work.

## Current Root Buckets

Active crypto:

- `crypto_options_app`
- `tests/crypto_options_app`

Likely WNBA/NBA or legacy Janus reference:

- `app`
- `codex_tool`
- `codex_tools`
- `tools`
- `tests/app`
- `tests/codex_tool`
- `tests/codex_tools`
- `tests/tools`

New reference roots:

- `wnba_nba_app_reference`
- `global_app_reference`

## Cleanup Rule

Move nothing until a path-level inventory is reviewed. Compatibility wrappers may remain temporarily.

## Next Action

Create a path-level move plan that classifies each non-crypto dirty path as:

- crypto active
- WNBA/NBA reference
- global reference
- compatibility wrapper
- unknown/manual review

Latest path-level automated classification summary:

- Artifact: `crypto_options_app/artifacts/reports/repo_cleanup_inventory_latest.json`
- Dirty/status paths: 877
- Review-required paths: 219
- Active crypto paths: 656
- Crypto compatibility wrapper candidates: 131
- Legacy move candidates: 72
- Unknown/root review paths: 0
- Automatic moves allowed: false
- Fixed chats start ready: false

Do not move files until this inventory is expanded into path-level actions and reviewed.

## Current Gates

- Repo move ready: false
- GitHub issue creation ready: false
- Fixed chats start gate: `blocked_until_repo_cleanup_and_github_milestones_are_reviewed`

## Required Review Before Moves

1. Keep `crypto_options_app/` and `tests/crypto_options_app/` as active crypto roots.
2. Review crypto compatibility wrapper candidates before moving them; some may be needed until runtime routes are fully cut over.
3. Move reviewed legacy/global files only in small, testable batches.
4. Do not delete any old work.
