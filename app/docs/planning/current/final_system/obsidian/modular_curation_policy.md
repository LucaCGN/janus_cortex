# Janus Obsidian Modular Curation Policy

Status: active bootstrap policy
Created: 2026-05-18
Owner: `docs-memory-agent` / `obsidian-builder`

## Purpose

Prevent the Janus Obsidian vault from becoming an append-only dump.

The vault should become more navigable and more useful after each maintenance pass. A good pass may create no new notes. Editing, merging, splitting, linking, superseding, and indexing existing notes are first-class outcomes.

## Authority Boundary

Obsidian is curated synthesis. It is not live trading truth, execution authority, service readiness truth, or the durable backlog authority.

If an Obsidian insight changes behavior, it must be promoted into one of:

- tracked repo docs
- GitHub issue
- runtime contract or handoff
- code or test

## Curation Layers

| Layer | Path | Rule |
|---|---|---|
| Raw source | `raw/` | Preserve source summaries and metadata. Do not treat as curated truth. |
| Inbox | `90_Inbox/` | Temporary holding area for unprocessed material. Must not accumulate silently. |
| Control/index notes | `AGENTS.md`, `index.md`, `00_Janus_Control/` | Navigation, schema, maintenance policy, issue bridges, and operating maps. |
| Curated concept notes | `10_System_Specs/`, `20_Trading_Knowledge/` | Durable ideas, contracts, frameworks, and reusable lessons. |
| Case notes | `30_Game_Reviews/`, `40_Profile_Studies/` | Reviewed examples that link back to general concepts and issues. |
| Log | `log.md` | Append-only operation record. |

## Edit-Before-Create Gate

Before creating any note, the agent must answer these questions:

1. Does an existing note already cover this concept?
   - If yes, update the existing note.
2. Is this only raw evidence, a copied report, or a temporary chat synthesis?
   - If yes, put a source summary in `raw/` or `90_Inbox/`, then update curated notes only with synthesis.
3. Is this a durable concept, policy, case, profile, or domain that will be reused by future agents?
   - If yes, create a note only after linking it to a parent index and at least one related note.
4. Is an existing note too broad, mixed, or hard to maintain?
   - If yes, split it into smaller notes and leave explicit links.
5. Is a note stale or contradicted by newer repo/runtime/GitHub truth?
   - If yes, mark it superseded and link the replacement. Do not delete unless explicitly approved.

## Valid Maintenance Outcomes

Every Obsidian pass should classify its outcome as one or more of:

- `updated-existing-note`
- `merged-duplicate-note`
- `split-large-note`
- `created-durable-note`
- `moved-to-inbox`
- `promoted-to-repo-doc`
- `created-or-updated-issue`
- `marked-superseded`
- `index-only-refresh`
- `no-material-change`

Creating notes is only one acceptable outcome.

## New Note Acceptance Criteria

A new curated note is valid only when it has:

- one durable purpose
- a clear folder based on note type
- source references or repo/runtime/GitHub anchors
- at least one parent index link
- at least one lateral link to a related note when such a note exists
- a short statement of whether it is authority, synthesis, evidence, or hypothesis
- issue or backlog links when the note implies implementation work

Profile and game notes must also link to the relevant overview note, trading concept, and backlog issue when applicable.

## Anti-Dump Rules

Agents must not:

- paste full chat threads into curated notes
- paste full reports into curated notes when a summary and source link are enough
- create duplicate notes for the same concept with slightly different names
- create one-off notes for every automation pass
- link every new note to every existing hub
- add profile or game studies without updating the relevant overview note
- leave raw material in `90_Inbox/` without a next action or log entry

## Graph Hygiene

Dense graph regions are acceptable only when they encode real navigation.

Expected hubs:

- `Janus Master Index`
- `index`
- `Issue Backlog Index`
- `Automation Anchor Map`
- `Polymarket Winning Profiles Overview`
- domain overview notes

Individual notes should link to the minimum useful parents and siblings. Do not create broad backlinks just to make the graph look connected.

## Refactor Triggers

The docs-memory agent should refactor the vault when it detects:

- duplicate notes with overlapping purpose
- a concept note that has become a mixed backlog, source dump, and policy page
- an overview note that no longer links all child notes
- a profile/game note that contains reusable lessons not reflected in a trading concept note
- an issue/backlog note that is stale relative to GitHub
- an inbox item older than one maintenance cycle
- orphan notes without a clear reason to exist

## Required Obsidian Builder Behavior

The 24h Obsidian builder must:

1. Read `AGENTS.md`, `index.md`, `Janus Master Index`, this policy, and the repo final-system anchors.
2. Scan the target folder and existing note titles before creating notes.
3. Prefer updating existing notes over creating new notes.
4. Update overview notes when adding or changing case/profile notes.
5. Update `index.md` and `log.md` after material changes.
6. Report curation outcome classes in its final message or runtime artifact.
7. Create GitHub issues or repo-doc follow-ups when Obsidian reveals implementation work.

## Final Rule

The vault should get smaller, clearer, or more connected over time. More notes are not progress unless they make future reasoning and maintenance easier.
