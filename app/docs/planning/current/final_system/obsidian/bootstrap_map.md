# Obsidian Bootstrap Map

Status: initial draft
Vault target: `C:\Users\lnoni\OneDrive\Documentos\Janus\Janus-Brain`

## Purpose

Obsidian is the curated knowledge layer for Janus. It should store reusable design rationale, strategy knowledge, profile studies, important game reviews, and cross-domain memory.

It is not live truth. Runtime truth remains direct CLOB, Janus DB/API, artifacts, handoffs, and repo contracts.

This vault should follow the LLM-wiki pattern from Karpathy's gist: immutable raw sources, LLM-maintained wiki pages, a schema file, an index, and an append-only log.

Reference: `https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f`

## Initial Folder Structure

| Folder | Purpose |
|---|---|
| `raw` | Immutable source summaries and source metadata. |
| `00_Janus_Control` | Master index, operating rules, active design state. |
| `10_System_Specs` | Curated system specs and design rationale. |
| `20_Trading_Knowledge` | Scenario taxonomy, risk engine, strategy principles. |
| `30_Game_Reviews` | High-value reviewed events, good/bad examples. |
| `40_Profile_Studies` | Polymarket profile analysis and lessons. |
| `90_Inbox` | Raw notes awaiting curation. |

## Required Control Files

| File | Purpose |
|---|---|
| `AGENTS.md` | Agent schema for maintaining the wiki. |
| `index.md` | Content-oriented catalog of all maintained notes. |
| `log.md` | Append-only chronological record of ingests, queries, and lint passes. |
| `00_Janus_Control/Obsidian Modular Curation Policy.md` | Edit-before-create and vault refactor policy. |

## First Notes To Populate

| Note | Source |
|---|---|
| `AGENTS.md` | LLM-wiki schema adapted for Janus. |
| `index.md` | Initial catalog for vault navigation. |
| `log.md` | Initial operation log. |
| `raw/sources/Karpathy LLM Wiki Gist 2026-05-17.md` | External LLM-wiki source. |
| `00_Janus_Control/Janus Master Index.md` | This bootstrap map and final-system README. |
| `00_Janus_Control/Janus Wiki Maintenance Runbook.md` | Janus-specific ingest/query/lint workflow. |
| `00_Janus_Control/Obsidian Modular Curation Policy.md` | `obsidian/modular_curation_policy.md`. |
| `00_Janus_Control/Issue Backlog Index.md` | Current canonical GitHub issues and duplicate issue hygiene. |
| `10_System_Specs/Premise Decisions 2026-05-17.md` | `premise_decisions_2026-05-17.md`. |
| `10_System_Specs/Janus Global Ego And Purpose.md` | `global_ego_and_purpose.md`. |
| `10_System_Specs/Source Of Truth Layering.md` | Repo/runtime/GitHub/Obsidian authority model. |
| `10_System_Specs/Controller And Queue Design.md` | Master controller and issue queue design. |
| `10_System_Specs/Market Domain And Scope Registry.md` | Market/domain axes and maturity ladder. |
| `10_System_Specs/Agent Persona Registry.md` | Persona roles, limits, and outputs. |
| `00_Janus_Control/Automation Anchor Map.md` | Stable automation anchors and mutable doc map. |
| `00_Janus_Control/Issue Taxonomy And Backlog Layers.md` | GitHub labels, backlog layers, and sprint readiness rules. |
| `10_System_Specs/GitHub Remote State 2026-05-17.md` | Remote/local issue and commit-state observation. |
| `20_Trading_Knowledge/Scenario Taxonomy S-A-B-C-D.md` | Scenario taxonomy report and premise decisions. |
| `20_Trading_Knowledge/Profit-Ratcheted Risk Engine.md` | Risk premise decisions and WNBA/CLE examples. |
| `30_Game_Reviews/CLE DET 2026-05-13.md` | CLE/DET final performance review. |
| `30_Game_Reviews/WNBA Lynx Wings 2026-05-14.md` | WNBA Lynx/Wings risk review. |
| `40_Profile_Studies/Polymarket Winning Profiles Overview.md` | Profile report context. |
| `40_Profile_Studies/Profile - aenews2.md` | Generalist profile analysis. |
| `40_Profile_Studies/Profile - car.md` | Generalist profile analysis. |
| `40_Profile_Studies/Profile - classified.md` | Generalist profile analysis. |
| `40_Profile_Studies/Profile - 0xb55fa Crypto.md` | Crypto profile analysis. |
| `40_Profile_Studies/Profile - pbot-6.md` | Crypto profile analysis. |
| `40_Profile_Studies/Profile - baloneigh.md` | Crypto profile analysis. |
| `40_Profile_Studies/Profile - mikeaddon.md` | Crypto profile analysis. |
| `40_Profile_Studies/Profile - predictfolio.md` | Generalist/global portfolio profile analysis. |
| `40_Profile_Studies/Profile - wuhuuuuuuli.md` | Crypto profile analysis. |

## Linking Rule

Repo docs should link to Obsidian note titles and paths when curated wisdom is required. Obsidian notes should link back to repo docs and runtime reports.

## Curation Rule

The vault is not an append-only memory dump. Every Obsidian maintenance pass must follow `app/docs/planning/current/final_system/obsidian/modular_curation_policy.md`.

Before creating a note, the agent must scan the relevant folder, `index.md`, and the parent overview note. Prefer updating, merging, splitting, relinking, or marking superseded notes before creating new notes.

## Health Check

The controller should periodically check:

- Are core Obsidian notes present?
- Are `AGENTS.md`, `index.md`, and `log.md` present?
- Is `00_Janus_Control/Obsidian Modular Curation Policy.md` present and linked?
- Do notes link back to repo source docs?
- Are raw chat/report insights stuck in `90_Inbox` without curation?
- Are obsolete assumptions marked as superseded?
- Are key winning/losing events captured as reusable examples?
- Are GitHub issue state changes reflected in the issue/backlog notes?
- Are duplicate notes being merged or superseded instead of allowed to accumulate?
