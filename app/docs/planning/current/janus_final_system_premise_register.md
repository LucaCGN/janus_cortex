# Janus Final System Premise Register

Status: draft for operator review
Date: 2026-05-17
Scope: premise audit before final system redesign, backlog reshaping, risk-engine implementation, and new agent framework design.

## Purpose

This document extracts the premises implied by the current Janus repo, runtime handoffs, recent live tests, current architecture, and the operator's final-system brainstorm.

It is not a final architecture, backlog, or implementation plan. It is the review surface that should be approved, corrected, or expanded before writing the next generation specs.

## Research Basis

Repo and runtime sources inspected:

- `app/docs/planning/janus_agentic_backend_operating_plan.md`
- `app/docs/planning/codex_agents/shared_file_communication_contract.md`
- `app/docs/planning/llm_model_routing.md`
- `app/docs/planning/current/agent_operating_rules.md`
- `app/docs/planning/current/branch_strategy.md`
- `app/docs/planning/codex_agents/*/README.md`
- `local/shared/handoffs/daily-live-validation/status.md`
- `local/shared/handoffs/development-agent/status.md`
- `local/shared/handoffs/development-agent/master_queue.md`
- `local/shared/reports/daily-live-validation/cle_det_final_performance_review_2026-05-13.md`
- `local/shared/reports/daily-live-validation/scenario_taxonomy_close_game_microgrid_2026-05-14.md`
- `local/shared/reports/daily-live-validation/wnba_lynx_wings_risk_profile_review_2026-05-14.md`
- `local/shared/reports/daily-live-validation/account_trading_history_scenario_review_2026-05-15.md`
- `local/shared/reports/daily-live-validation/janus_db_origin_performance_review_2026-05-15.md`
- `app/modules/agentic/contracts.py`
- `app/modules/agentic/engine.py`
- `app/modules/agentic/llm_runtime.py`
- `app/modules/agentic/live_strategy_worker.py`
- `codex_tool/run_live_strategy_tick.py`
- NBA/WNBA analysis, replay, ML, LLM-lane, and passive-capture module layout
- Current `codex_tool/` and `tools/` surfaces

## Premise Types

- `Anchor`: a premise that should guide the design unless explicitly rejected.
- `Caveat`: a constraint, risk, or current limitation.
- `Implication`: a design consequence if the premise is accepted.
- `Validation`: something that must be verified before becoming a hard architecture rule.

## A. Scope And Process Premises

- **P001 [Anchor]** The final system should not be implemented as one large architecture rewrite.ðŸŸ¢
- **P002 [Anchor]** The next step is premise validation, not spec writing, coding, or backlog expansion.ðŸŸ¢
- **P003 [Anchor]** The current repo and runtime state must be mapped before reviewing the backlog or risk-return system.ðŸŸ¢
- **P004 [Anchor]** The user wants a source-of-truth system that survives long Codex sessions, agent handoffs, and parallel development.ðŸŸ¢
- **P005 [Anchor]** Chat history is useful context but cannot be the durable source of truth for system behavior.ðŸŸ¢
- **P006 [Anchor]** The system design must be decomposed into reviewable documents before implementation.ðŸŸ¢
- **P007 [Anchor]** Each future spec should be narrow enough to be validated independently.ðŸŸ¢
- **P008 [Anchor]** The first redesign artifact should capture assumptions and boundaries before proposing components.ðŸŸ¢
- **P009 [Caveat]** The current tree contains unrelated dirty work, so premise documentation must not rewrite existing contracts casually.ðŸŸ¢
- **P010 [Implication]** New planning documents should be added as standalone drafts until reviewed, not merged into core contracts immediately.ðŸŸ¢
- **P011 [Anchor]** The final docs should separate current-state description from target-state design.ðŸŸ¢
- **P012 [Anchor]** The user needs the current Janus architecture explained before committing to the next system design.ðŸŸ¢
- **P013 [Anchor]** Backlog lanes should be reviewed only after the current architecture and premises are explicit.ðŸŸ¢
- **P014 [Anchor]** Risk-return logic should be reviewed only after the current trading-flow logic is explicit.ðŸŸ¢
- **P015 [Anchor]** The new agent framework should be designed only after system architecture, file communication, and service boundaries are defined.ðŸŸ¢
- **P016 [Caveat]** The existing automation prompts are operationally useful but not a sufficient final orchestration framework.
- **P017 [Implication]** The final design needs a controller layer that decides which agent or service should run based on time, game state, backlog state, and live risk.ðŸŸ¢
- **P018 [Validation]** The exact split between repo docs, runtime docs, Obsidian, GitHub issues, and chat summaries must be approved before migration.ðŸŸ¢

## B. Source-Of-Truth And Authority Premises

- **P019 [Anchor]** Direct CLOB truth is the highest authority for live collateral, orders, fills, and positions.ðŸŸ¢
- **P020 [Anchor]** Janus DB/API is the authoritative application state for events, markets, outcome links, strategy plans, and recorded decisions.ðŸŸ¡
- **P021 [Anchor]** Runtime artifacts under `local/shared/artifacts` are machine-readable evidence, not durable architecture.ðŸŸ¡
- **P022 [Anchor]** Runtime handoffs under `local/shared/handoffs` are current operating status, not stable product contracts.ðŸŸ¡
- **P023 [Anchor]** Runtime reports under `local/shared/reports` are durable reasoning and postgame evidence.ðŸŸ¡
- **P024 [Anchor]** Tracked planning docs define standing contracts and expected behavior.ðŸŸ¢
- **P025 [Anchor]** Chat memory is lowest authority when file/API state exists.ðŸŸ¢->
- **P026 [Anchor]** If direct CLOB truth contradicts the portfolio mirror, direct CLOB wins.ðŸŸ¢
- **P027 [Caveat]** The current portfolio mirror can be stale and must not be used as live authority.ðŸŸ¢
- **P028 [Caveat]** The current Janus DB ledger does not yet fully reconstruct all account or Janus-origin PnL.ðŸŸ¢
- **P029 [Implication]** A future event-review bundle must aggregate direct CLOB, DB, plans, fills, LLM traces, PBP, and orderbook windows in one call.ðŸŸ¢->
- **P030 [Implication]** Any future agent must cite which authority level it used when reporting trade/order state.ðŸŸ¢
- **P031 [Anchor]** StrategyPlanJSON is executable strategy configuration only after schema validation and safety gates.ðŸŸ¢
- **P032 [Anchor]** LLM output is not order authority until adopted into a valid plan or routed through a validated position-management action.ðŸŸ¡->
- **P033 [Anchor]** Pregame research is context authority, not order authority.ðŸŸ¡->
- **P034 [Anchor]** Operator sizing policy overrides plan sizing metadata during live testing.ðŸŸ¢
- **P035 [Validation]** Obsidian can become a curated knowledge layer, but it should not outrank repo contracts or runtime truth without explicit linking rules.ðŸŸ¢->

## C. Current Architecture Premises --> all bellow are ðŸŸ¢, this is an accurate description of the current application

- **P036 [Anchor]** Current Janus is already backend-first and FastAPI/API-tool driven.ðŸŸ¢
- **P037 [Anchor]** The current architecture has a primary agentic StrategyPlanJSON path.ðŸŸ¢
- **P038 [Caveat]** A legacy NBA controller path still exists and must be reconciled, wrapped, or retired in the final design.ðŸŸ¢
- **P039 [Anchor]** The agentic plan path uses `StrategyPlan`, `ActiveStrategy`, `OrderIntent`, `LLMRuntimeTrace`, and related contracts in `app/modules/agentic/contracts.py`.ðŸŸ¢
- **P040 [Anchor]** Strategy evaluation currently happens through `app/modules/agentic/engine.py`.ðŸŸ¢
- **P041 [Anchor]** Live worker control exists in `app/modules/agentic/live_strategy_worker.py`.ðŸŸ¢
- **P042 [Caveat]** The service-owned live worker currently shells into `codex_tool/run_live_strategy_tick.py`, which is a transitional architecture smell.ðŸŸ¢
- **P043 [Implication]** The final runtime should move critical tick logic from Codex tooling into app-owned service modules.ðŸŸ¢
- **P044 [Anchor]** `codex_tool/` is the current stable control layer used by Codex agents.ðŸŸ¢
- **P045 [Caveat]** Tools that started as Codex wrappers are now carrying too much live-runtime responsibility.ðŸŸ¢
- **P046 [Anchor]** The current live tick preflights integrity, live monitor, event context, NBA live/PBP sync, Polymarket orderbooks, watch persistence, direct CLOB state, pending intents, manual positions, LLM trace detection, shadow evaluation, and optional live execution.ðŸŸ¢
- **P047 [Anchor]** Current live execution uses audited `/v1/events/{event_id}/strategy-plan/execute` semantics, not arbitrary order placement.ðŸŸ¢
- **P048 [Anchor]** Current safety gates include plan expiry, shadow-only, orderbook freshness, scoreboard freshness, spread, score gap, period, clock, garbage time, player status shock, price band, pending intent, position limit, market-order disabled, minimum size, minimum buy notional, and low-price underdog protection.ðŸŸ¢
- **P049 [Caveat]** Current ML/PBP evidence passed into live LLM is still effectively placeholder-level for the live tick path.ðŸŸ¢
- **P050 [Caveat]** Current deterministic backtest and lane modules are richer than what is automatically generated live.ðŸŸ¢
- **P051 [Caveat]** Current LLM adoption is explicit/reviewed, not fully autonomous for conservative actions.ðŸŸ¢
- **P052 [Caveat]** The current system can support the workflow but cannot yet reproduce the best CLE/DET or WNBA results unattended.ðŸŸ¢
- **P053 [Implication]** The final design must distinguish "system supports operator/Codex success" from "system autonomously reproduces success."ðŸŸ¢
- **P054 [Implication]** The final design needs an architecture diagram that shows API, workers, DB, CLOB, data feeds, Codex, LLM, deterministic lanes, ML lanes, replay, and order manager as separate responsibilities.ðŸŸ¢

## D. Runtime Independence Premises

- **P055 [Anchor]** Janus must continue ingesting data, watching markets, evaluating active plans, reconciling portfolio truth, and preserving replay data when Codex is offline.ðŸŸ¢
- **P056 [Anchor]** Codex should not be the recurring executor during live games.ðŸ”´
- **P057 [Anchor]** The main app must always run independently from Codex.ðŸŸ¢
- **P058 [Anchor]** Codex should be able to inspect, start, stop, patch, revise, and review, but not be required for normal live operation.ðŸŸ¢
- **P059 [Anchor]** If internal LLM breaks, Janus should degrade instead of freezing the whole trading system.ðŸŸ¢
- **P060 [Anchor]** If Codex is unavailable, already-approved deterministic or ML strategies that pass safety gates should still run.ðŸŸ¡-> codex or LLM openai api* is the correct
- **P061 [Anchor]** If pregame research fails, proven deterministic/ML strategies should still be eligible when their data and profitability tests pass.ðŸŸ¢
- **P062 [Anchor]** If one scoreboard source fails, other valid sources should continue feeding the context builder.ðŸŸ¢
- **P063 [Anchor]** CLOB, NBA/hoopstats, WNBA, portfolio, replay, and strategy workers should be independently monitorable.ðŸŸ¢
- **P064 [Implication]** The final design should define service dependency levels rather than one global up/down state.ðŸŸ¢
- **P065 [Implication]** The system should support partial degradation states such as no LLM, no Codex, one stale sports feed, WNBA passive only, or CLOB read-only.ðŸŸ¢
- **P066 [Caveat]** Current worker startup still required Codex/operator action after pregame in recent tests.ðŸŸ¢
- **P067 [Caveat]** Current internal LLM dispatch created a cost incident and must remain blocked for live-money until controls exist.ðŸŸ¢
- **P068 [Validation]** The final queue or scheduler technology is not decided: internal FastAPI worker, DB task queue, Redis, APScheduler, external automation, or a hybrid are all still candidates.ðŸŸ¢/ðŸŸ¡ -> this is true, but that is why codex is running on the pro plan and is able to run every 5 minutes and assume as many personas and we need to define, from developer to live event analyst and trader, so even when the final queue is defined and "Automaded", codex is still one of the 3 main players. USER - CODEX - JANUS ---> this triad will always be in simultaneous control and observing events and porfolio.
- **P069 [Validation]** The final split between modular monolith and multiple FastAPI services is not decided.ðŸŸ¢ --> what we do have defined is having one single repo, even we if get to the point of having several different microservices and docker services running by necessity, we should still keep it one repo for the sake of our repo and codex worlflows.

## E. Codex Agent And Automation Premises

- **P070 [Anchor]** Current Codex agents are external operators for postgame review, development, end phase, integrity, pregame planning, and live monitoring.ðŸŸ¢ -> Thats what we need on the very least for the final system, but they existed only in testing spans, and the reason why we are on this new system development spec/premisse is exaclty because they did not work / were not sufficient / the system was not built to support them, so while ðŸŸ¢ bellow means I validate the current scope as correct, keep in mind this should be the bare minimum for the last design, and MOST IMPORTANTLY: these were built as each its own pinned chat and autonatiom, so 1. we had the privilege of separating personas in codex 2. we had the privilege of chat memory. One thing that broke the testing loop was a internal codex bug where a .json was filed on the automation call which did not exist anymore and made it broke, as this was a Codex app error, no way to fuss about it, and that is why the new system will be ONE single automation and prompt which will spawn new chats. We will need a more complex and well though orchestration with obsidian and the repo /docs --> which contain every single .md file, from planning to spec to current tasks to backlog. By having the agent check the repo planning docs and act accordingly to its contents, the files it points to as per the time frame as context, and having pontual instructions to how to write/write/delete/edit obsidian across the flow, we will build a system which leverages codex best feature, its reasoning and coding capabilities, not memory management. I will say it again, from a development/devops/cd|ci perspective "we will build a system which leverages codex best feature, its reasoning and coding capabilities, not memory management."
- **P071 [Anchor]** Development Agent does not place orders.ðŸŸ¢
- **P072 [Anchor]** Development End Phase owns main-branch reconciliation and service-readiness verification.ðŸŸ¢
- **P073 [Anchor]** Pregame Integrity is the hard gate before pregame planning and later live readiness.ðŸŸ¢
- **P074 [Anchor]** Pregame Research supplies context, thesis, trigger design, and strategy suggestions, but not sizing or live order authority.ðŸŸ¢
- **P075 [Anchor]** Live Monitor verifies app-owned behavior and intervenes only on divergence, runtime failure, or explicitly reviewed revisions.ðŸŸ¢
- **P076 [Anchor]** Postgame Review converts completed games into system learning and development tasks.ðŸŸ¢
- **P077 [Caveat]** The current five-agent schedule is useful but too rigid for the final system.ðŸŸ¢
- **P078 [Anchor]** The final agent framework should choose agent/task based on timeframe, live events, closed events needing review, available development plan, current queue, and active running agents.ðŸŸ¢
- **P079 [Anchor]** A 5-minute master automation should act as controller/dispatcher, not as a duplicate live executor.ðŸŸ¢
- **P080 [Anchor]** The dispatcher must know whether games are live, pregame, postgame, or idle-development window.ðŸŸ¢
- **P081 [Anchor]** The dispatcher must know whether postgame review is done before starting development planning.ðŸŸ¢
- **P082 [Anchor]** The dispatcher must know whether the daily development plan exists before launching development work.ðŸŸ¢
- **P083 [Anchor]** The dispatcher must know how many agents are running and what they are working on.ðŸŸ¢
- **P084 [Anchor]** The dispatcher must avoid conflicting parallel work on the same files or live runtime scope.ðŸŸ¢
- **P085 [Anchor]** Parallel agents should be anchored by branches, worktrees, issues, or scoped handoff tasks.ðŸŸ¢
- **P086 [Caveat]** Current runtime files under `local/` are not source code and must remain untracked.ðŸŸ¢
- **P087 [Implication]** A future agent/task queue needs explicit locks or ownership fields for code, event, and runtime resources.ðŸŸ¢
- **P088 [Implication]** Live game monitoring should preempt lower-priority development work when active money or safety is at risk.ðŸŸ¡ --> correct but as in the last loop, this is part of the time frame logic of redirection of the agent "Ispect daily schedule with A. ours since last game B. hours until next game" and while A. is used to trigger post game reviews, B. is the guideline to wether we should be developing, closing developing and making integrity checks/ pregame planning, or monitoring live games, so during live games there should be no development at all of any tasks/backlog, just patching crucial bugs if needed.
- **P089 [Implication]** If a live issue is low impact, the system should create a GitHub issue instead of patching during the game.ðŸŸ¢
- **P090 [Validation]** The exact format of agent queue documents must be defined and tested before replacing current automations.ðŸŸ¢-> current automations are not defined, first we need a complete clean up of the repository and obsidian setup, and that only after we go after this premisses step.

## F. File System, Docs, And Memory Premises

- **P091 [Anchor]** The repo root is the tracked source and contract root.ðŸŸ¢
- **P092 [Anchor]** `local/` is runtime state and must remain untracked.ðŸŸ¢
- **P093 [Anchor]** `local/shared` is the current cross-agent communication bus.ðŸŸ¢
- **P094 [Anchor]** `daily-live-validation/status.md` is the current global operating bus.ðŸŸ¢
- **P095 [Anchor]** Dated reports are durable reasoning and should be readable without chat context.ðŸŸ¢
- **P096 [Anchor]** Machine-readable artifacts should be written before reports and handoffs.ðŸŸ¢
- **P097 [Anchor]** The end-of-pass write order should be artifacts, dated report, agent handoff, global status, then tracked docs only if contracts changed.ðŸŸ¢
- **P098 [Anchor]** The final system needs a single file/folder communication spec organized by file and folder, not by automation.ðŸŸ¢
- **P099 [Anchor]** The current shared file contract is a good starting point but not final.ðŸŸ¢
- **P100 [Anchor]** Stable product behavior belongs in committed repo docs.ðŸŸ¢
- **P101 [Anchor]** Active branch planning belongs under `app/docs/planning/current`.ðŸŸ¢
- **P102 [Anchor]** Historical execution rationale belongs under planning archive or dated reports.ðŸŸ¢
- **P103 [Anchor]** Local-only branch tracking belongs under the local runtime root.ðŸŸ¢
- **P104 [Anchor]** Obsidian should serve as curated second-brain memory for higher-level wisdom, design rationale, and cross-domain knowledge.ðŸŸ¢
- **P105 [Caveat]** The Karpathy-style "Facts, Working Memory, Wisdom" framing is useful but not directly sufficient for Janus.ðŸŸ¢
- **P106 [Implication]** Janus memory tiers should likely distinguish runtime truth, repo contracts, active plans, historical evidence, curated strategy knowledge, and operator preferences.ðŸŸ¢ --> somenthing like that, but also the idea is that after building the base framework and consequent nodes/relations in obsdians, we are able to build very specif documents such as include a deep analisys of an winning profile with high return and win percentage, or maybe get an nba event that we did REALLY good/bad and want to record so it is considered in case a situation like this repeat. So we need it segmented and connected as need, so we have specif modules crucial to the analys/planning/trading services, but can also be used independantly by other agents.
- **P107 [Implication]** Obsidian docs should be referenced by repo contracts or dispatcher instructions, not discovered ad hoc.ðŸŸ¢
- **P108 [Validation]** The final Obsidian folder taxonomy and sync relationship with repo docs must be designed before bulk migration.ðŸŸ¢
- **P109 [Validation]** The final prompt/docs dictionary for agents must be generated or maintained in a way that avoids stale links.ðŸŸ¢

## G. Git, DevOps, And Work Management Premises

- **P110 [Anchor]** Main should remain clean, tested, and runnable.ðŸŸ¢
- **P111 [Anchor]** Development branches should be narrow and reconcilable.ðŸŸ¢
- **P112 [Anchor]** Development Agent should not merge to main.ðŸŸ¢
- **P113 [Anchor]** Development End Phase should merge safe work and defer unsafe or unrelated work.ðŸŸ¢
- **P114 [Anchor]** Branches should map to ownership lanes such as ops, data, analysis, season, docs, or frontend.ðŸŸ¢
- **P115 [Anchor]** GitHub issues should become the durable backlog for bugs, features, and detected problems.ðŸŸ¢
- **P116 [Anchor]** GitHub issues should reduce hallucination and long-session drift by anchoring tasks outside chat and local handoffs.ðŸŸ¢
- **P117 [Anchor]** GitHub issues should let the user interface with the system as a developer, not only as chat operator.ðŸŸ¢
- **P118 [Implication]** New features should create or update issues with scope, acceptance criteria, evidence links, and priority.ðŸŸ¢
- **P119 [Implication]** Future parallel agents should claim issues or worktree-scoped tasks before editing.ðŸŸ¢
- **P120 [Caveat]** Current local reports/handoffs are not enough for long-term product backlog governance.ðŸŸ¢
- **P121 [Validation]** The exact GitHub Projects, labels, issue templates, and automation relationship must be designed later.ðŸŸ¢
- **P122 [Validation]** The final branching/worktree strategy for parallel Codex agents must be tested with real concurrent slices before becoming policy.ðŸŸ¢

## H. Trading Domain Premises

- **P123 [Anchor]** Basketball live trading is the first mature Janus domain.ðŸŸ¢
- **P124 [Anchor]** NBA is the more mature implementation and should ground early service design.ðŸŸ¢
- **P125 [Anchor]** WNBA should reuse shared basketball market/replay concepts but remain separately calibrated.ðŸŸ¢
- **P126 [Anchor]** WNBA live money remains blocked until passive capture, replay, fillability, and sample-size evidence are sufficient.ðŸŸ¡ --> indeed blocked but should have the highest priority since we have very few games on sparse scheduled left for the nba playoffs, so since the system is basically the same, and the data layer handle whatever is different before it reachs the db and system, there is absolutely no reason for us not to implement it and test is with minimal 5shares orders and scale it as we win.
- **P127 [Anchor]** Crypto, economics, geopolitics, elections, and long-term portfolio opportunities are future Janus domains, not current core live sports execution.ðŸŸ¢
- **P128 [Anchor]** The final architecture should be generic enough to support sports, crypto options, geopolitics, and other Polymarket event categories.ðŸŸ¢
- **P129 [Caveat]** Generic market support must not weaken basketball-specific safety gates.ðŸŸ¢
- **P130 [Caveat]** Crypto high-frequency options require a different data cadence, backtest methodology, and risk model than basketball.ðŸŸ¢
- **P131 [Caveat]** Long-term geopolitics or culture positions require monitoring, target, rebuy, and portfolio allocation logic distinct from live-game trading.ðŸŸ¢
- **P132 [Implication]** Future domain modules should plug into shared CLOB, portfolio, watch, replay, and reporting infrastructure.ðŸŸ¢
- **P133 [Implication]** Domain-specific strategy engines should own their own data feeds, classifiers, and calibration.ðŸŸ¢
- **P134 [Validation]** The global Polymarket portfolio manager is a separate future product lane and should not be merged into live NBA/WNBA strategy logic too early.ðŸŸ¢

## I. Market Data, CLOB, And Execution Premises

- **P135 [Anchor]** Polymarket direct CLOB is the execution and portfolio truth source.ðŸŸ¢
- **P136 [Anchor]** The web UI is not reliable enough for final-seconds or low-price tail execution.ðŸŸ¢
- **P137 [Anchor]** Direct CLOB tools must support preview, guardrails, and audited execution for manual/operator orders.ðŸŸ¢
- **P138 [Anchor]** All live orders must pass through audited Janus endpoints or order-manager paths.ðŸŸ¡ --> unless something requires imediate interence, be it for patching or due to stopwin/loss scenarios, in that case we have reconcilation loops that must be in place, specially on this first weeks of testing
- **P139 [Anchor]** No live order is valid without current StrategyPlanJSON, fresh CLOB, fresh scoreboard/PBP when relevant, reconciled direct CLOB state, and active safety gates.ðŸŸ¢
- **P140 [Anchor]** Market orders should remain disabled by default for live testing.ðŸŸ¡-> not only live testing, but to avoid slippage concerns, always disabled, always limit orders, market only in ONE scenario, which is realzing gains as soons as possible in scenario where price spiked and our interpreation is it will drop at any moment
- **P141 [Anchor]** Limit-only minimum-size testing is the current live-money policy.ðŸŸ¢
- **P142 [Anchor]** Current minimum live testing uses at least 5 shares and at least $1 buy notional.ðŸŸ¡ -> investigate later the real minimum, but it should be the minimum allowed, I was able to buy 100 shares on 0.1 cent, which makes a 10cent order, but the UI on polymarket appears to have a special trigger to allow decimals input and the minim to be stricly 5 shares, so its something we have to confirm if its polymarket webui specif or if we been this whole time working with wrong premisses
- **P143 [Anchor]** Direct open orders and positions for the current event must be included in every serious strategy review.
- **P144 [Anchor]** Manual/operator orders must be detected and adopted or rejected explicitly.ðŸŸ¢
- **P145 [Anchor]** Every live position needs a lifecycle: entry, target, stop/hedge, close/settlement, and attribution.ðŸŸ¢
- **P146 [Caveat]** Current DB linkage between Janus orders and direct CLOB fills is incomplete.ðŸŸ¢
- **P147 [Caveat]** Current `portfolio.trades` contains duplicated/unlinked account rows and cannot be treated as clean Janus performance truth.ðŸŸ¡ --> truth but since these orders might come from codex or human interference and since most of the post game analysis is made outside fastapi, specially what is llm dependant, we should treat this concept as a global performance review based on both sources as truth, internal db and polymarket record + onsidian and repo docs to full y analyse performance
- **P148 [Implication]** Direct CLOB order IDs, trade IDs, strategy plan versions, strategy IDs, intent IDs, origin actors, and parent order IDs must be linked durably.ðŸŸ¢
- **P149 [Implication]** Stale submitted rows should be treated as unresolved until reconciled, not as performance.ðŸŸ¢
- **P150 [Implication]** Direct CLOB manual assistant tooling needs hard max price, max notional, freshness, event, and side validation.ðŸŸ¢

## J. Scenario Taxonomy Premises ðŸŸ¢--> all correct but caveat -> this is what we observed so far, and also insights we have construcuted so far, this should be mutable and build upon as we go

- **P151 [Anchor]** The S/A/B/C/D scenario taxonomy is strongly supported by account-level reconstruction.ðŸŸ¢
- **P152 [Anchor]** `S` full expectation inversion is the return accelerator.ðŸŸ¢
- **P153 [Anchor]** `A` close-game stable oscillation is the core backbone.ðŸŸ¢
- **P154 [Anchor]** `B` slow underdog descent with spikes is a selective scalp lane.ðŸŸ¢
- **P155 [Anchor]** `C` favorite floor rebound is a support lane.ðŸŸ¢
- **P156 [Anchor]** `D` unexpected blowout or falling knife is an avoid/shutdown state.ðŸŸ¢
- **P157 [Anchor]** Unclassified bad states should be treated like `D` until instrumented.ðŸŸ¢
- **P158 [Anchor]** The account-wide reconstruction showed tradable S/A/B/C profiles with much stronger performance than D/U profiles.ðŸŸ¢
- **P159 [Anchor]** Close games create repeated 10 percent to 20 percent repricing windows even when the final winner is uncertain.ðŸŸ¢
- **P160 [Anchor]** Expectation inversions create the largest return opportunities but are less clean and more intervention-prone today.ðŸŸ¢
- **P161 [Anchor]** Slow underdog descents can be tradable only when spikes and target room exist.ðŸŸ¢
- **P162 [Anchor]** Unexpected blowouts are the most destructive profile and require mechanical shutdown.ðŸŸ¢
- **P163 [Anchor]** A team does not need to become likely to win for an underdog scalp to be profitable.ðŸŸ¢
- **P164 [Anchor]** The core trade is often expectation repricing, not final-outcome prediction.ðŸŸ¢
- **P165 [Anchor]** OT is its own live regime and should not be treated as ordinary Q4.ðŸŸ¢
- **P166 [Anchor]** Q4 can be clutch or garbage time, so quarter alone is not enough.ðŸŸ¢
- **P167 [Anchor]** Garbage time can occur before Q4.ðŸŸ¢
- **P168 [Anchor]** Comebacks can happen at any point, but the comeback path must be structurally live.ðŸŸ¢
- **P169 [Caveat]** Low price alone is not value.ðŸŸ¢
- **P170 [Implication]** The final system needs a regime classifier before broadening live execution.ðŸŸ¢
- **P171 [Implication]** Strategy sleeves should be scenario-bound modules, not static pregame-only choices.ðŸŸ¢
- **P172 [Implication]** The engine must auto-generate or activate candidate sleeves when regimes change.ðŸŸ¢
- **P173 [Validation]** Scenario thresholds must be backtested by league, quarter, score gap, liquidity, and market depth.ðŸŸ¢

## K. Strategy And Sleeve PremisesðŸŸ¢

- **P174 [Anchor]** Active strategies should be represented as sleeves with identity, family, side, rules, and role.ðŸŸ¢
- **P175 [Anchor]** A sleeve may be active, shadow-only, disabled, or candidate pending review.ðŸŸ¢
- **P176 [Anchor]** Sleeve identity must survive evaluation, blockers, order intents, direct CLOB protection, LLM reviews, and postgame attribution.ðŸŸ¢
- **P177 [Anchor]** Current supported strategy families include underdog range scalp, favorite floor rebound, quarter open reprice, micro momentum continuation, inversion, panic fade, lead fragility, Q4 clutch, Q1 repricing, underdog liftoff, halftime gap fill, winner definition, price stability micro-grid, and resistance-band variants.ðŸŸ¢
- **P178 [Caveat]** Current live plans are too static relative to how games actually change.ðŸŸ¢
- **P179 [Caveat]** The engine did not create the DET Q4 and OT sleeves in time during CLE/DET without Codex/operator intervention.
- **P180 [Anchor]** Micro-grid should not be restricted to ultra-low prices.ðŸŸ¢
- **P181 [Anchor]** Micro-grid targets should scale by price band, commonly `entry + max(1c, 10 percent of entry)`.ðŸŸ¢
- **P182 [Anchor]** High-confidence small targets can accumulate meaningful returns.ðŸŸ¢
- **P183 [Anchor]** Strategy generation should include both-team positions and hedge-grid contexts when the game warrants it.ðŸŸ¢
- **P184 [Anchor]** The system should not assume a single active side per event.ðŸŸ¢
- **P185 [Caveat]** Multiple simultaneous sleeves can silently exceed risk if not governed by portfolio-level caps.ðŸŸ¢
- **P186 [Implication]** Sleeve dependency graphs are required so strategies can coordinate instead of collide.ðŸŸ¢
- **P187 [Implication]** Opposite-side hedge entries must be validated as risk-reducing or expected-return-positive after spread and lifecycle costs.ðŸŸ¢
- **P188 [Implication]** Shadow-first fallback is required when live authority is missing.ðŸŸ¢
- **P189 [Validation]** The final sleeve taxonomy should be reconciled with actual module family names and current plan schemas.ðŸŸ¢

## L. Risk And Exposure Premises ðŸŸ¢--> weith of each premisse can change as we gather new insights that might infer in new design or features, but this is a excelent base

- **P190 [Anchor]** The final risk engine should protect base bankroll, scalp with base, speculate with realized profit, and use tail risk only with locked gains.ðŸŸ¢
- **P191 [Anchor]** Protected portfolio and realized event/day profit are separate capital sources.ðŸŸ¢
- **P192 [Anchor]** Open unrealized profit should inform decisions but should not unlock additional risk.ðŸŸ¢
- **P193 [Anchor]** Low, medium, and high risk sleeves need separate budgets.ðŸŸ¢
- **P194 [Anchor]** Base bankroll exposure should not scale aggressively just because the system is up on the day.ðŸŸ¢
- **P195 [Anchor]** Increased risk should primarily come from realized profit.ðŸŸ¢
- **P196 [Anchor]** Tail-risk trades should be funded only from realized profit.ðŸŸ¢
- **P197 [Anchor]** Tail-risk trades require hard max price, max notional, fresh book, and plausible path.ðŸŸ¢
- **P198 [Anchor]** A tiny final-possession tail trade can be rational when payout asymmetry is extreme and risk is capped.ðŸŸ¢
- **P199 [Caveat]** The WNBA Dallas final-possession UI error shows that uncapped web-UI execution can destroy a profitable session.ðŸŸ¢
- **P200 [Anchor]** The system should not close a losing underdog in a close, non-garbage game unless comeback path is virtually dead or truth is unsafe.ðŸŸ¢
- **P201 [Anchor]** Before realizing a loss, the system should compare hold, target reduction, hedge, add-down, and close.ðŸŸ¢
- **P202 [Anchor]** Stop-loss is important, but it should not be the default in live close-game profiles.ðŸŸ¢
- **P203 [Anchor]** Virtual-dead classifiers are required before mechanical loss exits.ðŸŸ¢
- **P204 [Anchor]** Risk profile should adapt by realized return state, game profile, and strategy confidence.ðŸŸ¢
- **P205 [Implication]** The final risk engine needs ledgers for base exposure, event realized PnL, day realized PnL, open unrealized PnL, sleeve budgets, unresolved inventory, and tail-risk budget.ðŸŸ¢
- **P206 [Implication]** The final trade scorer should combine expected return, confidence, liquidity, latency, drawdown, and unresolved-inventory penalties.ðŸŸ¢
- **P207 [Implication]** Human/operator risk upgrades should be represented as reviewed state, not hidden in chat.ðŸŸ¢
- **P208 [Validation]** The exact realized-return ladder thresholds are examples and must be calibrated against account and DB histories.ðŸŸ¢

## M. LLM Runtime Premises

- **P209 [Anchor]** The internal LLM should not place, cancel, or replace orders directly.ðŸ”´
- **P210 [Anchor]** The internal LLM should output structured plan revisions and reconciliation actions.ðŸŸ¢
- **P211 [Anchor]** Janus validates LLM output before execution.ðŸŸ¢
- **P212 [Anchor]** LLM routing currently defines nano, mini, and frontier tiers.ðŸŸ¡
- **P213 [Anchor]** Nano is intended for extraction, tagging, compression, and repetitive summaries.ðŸŸ¢
- **P214 [Anchor]** Mini is intended for routine pregame synthesis, routine revisions, normal monitoring, and first-pass postgame classification.ðŸŸ¡
- **P215 [Anchor]** Frontier is intended for critical reasoning, open exposure, manual intervention, high uncertainty, material failures, lane promotion, and architecture.ðŸŸ¡
- **P216 [Caveat]** Recent live LLM cost was too high relative to $1 to $10 testing exposure.ðŸŸ¢
- **P217 [Caveat]** The May 13 cost incident was mainly repeated postgame dispatch from stale/final evidence.ðŸŸ¢
- **P218 [Anchor]** Live LLM dispatch should remain blocked until cost and shutdown controls exist.ðŸŸ¢
- **P219 [Anchor]** Required LLM controls include repeated-trigger dedup, cooldowns, per-event budgets, model-call caps, prompt compression, final/flat shutdown, and cost telemetry.ðŸŸ¢
- **P220 [Anchor]** LLM calls should happen only when classified regime or portfolio state requires judgment.ðŸŸ¢
- **P221 [Anchor]** Deterministic and ML lanes should handle fast repeatable game-state interpretation before LLM escalation.ðŸŸ¢
- **P222 [Anchor]** LLM prompts should depend on scenario, quarter, risk state, sleeve type, and open inventory.ðŸŸ¢-> among others and anything that can make mini perform better without the need of frontier, lets scale in structurecontex, not reasoning capabilities
- **P223 [Anchor]** LLM output should include executable plan revision or explicit no-action with reason and invalidation conditions.ðŸŸ¢
- **P224 [Anchor]** Conservative actions such as pause, no-new-entry, cancel stale order, adopt known position, target, or position-management-only should be candidates for safe automatic adoption.ðŸŸ¢
- **P225 [Caveat]** New exposure, larger risk, tail-risk allocation, and ambiguous hedges should remain human-reviewed until policy is proven.ðŸŸ¢
- **P226 [Implication]** LLM usefulness must be tracked by whether the call changed plan/order behavior.ðŸŸ¢
- **P227 [Implication]** Every LLM trace should record model, selected tier, trigger, tokens, estimated cost, response status, adoption status, and resulting behavior.ðŸŸ¢
- **P228 [Validation]** The exact Codex-as-fallback-LLM flow must be designed so it does not bypass Janus safety gates.ðŸŸ¡ --> it must be designed so it considers the same safety factors when needing to interferes, point is if codex need explicly to interfers it can have any kind of gate stopping it, since that ocurring is already rare and necessary by design

## N. Deterministic And ML Lane Premises --> perfect, but this is exclusive for NBA, a big part of future ML model in next categorys inclusion will be crypto 15-minute modelling, since getting this write would be the equivalent of a infinite money glitch

- **P229 [Anchor]** Deterministic lanes are the right place for fast, repeatable game-state interpretation.ðŸŸ¢
- **P230 [Anchor]** ML lanes should support valuation, confidence, and trigger quality, not directly bypass order safety.ðŸŸ¢
- **P231 [Anchor]** Quarter-based expectations should be a first-class development lane.ðŸŸ¢
- **P232 [Anchor]** Quarter intelligence should model team/player performance by quarter, matchup, lead/deficit, home/away, and rotations.ðŸŸ¢
- **P233 [Anchor]** PBP features should tag shot, turnover, foul, substitution, timeout, run, player role, star event, bench event, and score impact.ðŸŸ¢
- **P234 [Anchor]** Player role weights should distinguish star creator, defensive anchor, bench scorer, role player, and similar roles.
- **P235 [Anchor]** Before/after CLOB windows are required to label short-horizon price impact.ðŸŸ¢
- **P236 [Anchor]** ML/PBP should trigger LLM only when it sees high-confidence under/overvaluation or ambiguity.ðŸŸ¢
- **P237 [Caveat]** Current live ML/PBP evidence is not yet first-class in the tick path.ðŸŸ¢
- **P238 [Caveat]** Current WNBA ML is blocked by insufficient distinct linked games and labeled rows.ðŸŸ¢
- **P239 [Implication]** Backtests must include slippage, fillability, latency, and queue/depth assumptions.ðŸŸ¢
- **P240 [Implication]** ML promotion requires live/shadow evidence, not narrative success from one game.ðŸŸ¢
- **P241 [Implication]** Quarter/PBP feature contracts should be shared across NBA and WNBA with league-specific calibration.ðŸŸ¢
- **P242 [Validation]** The final ML stack choices should be based on dataset size and evidence, not model preference.ðŸŸ¢

## O. Replay, Reporting, And Performance Premises

- **P243 [Anchor]** If screenshots are required for postgame reconstruction, the system lacks required reporting tools.ðŸŸ¢ --> we just need to replace insights provided by screenshotsm e.g how many price inversions we have in the sense of how many times did the underdog became favorite and viceversa. That is a good example of a piece of visual data that I can interpret and act instantly. Other example is observing in a down or up trend, how many variation there is in the trend, is a smooth line or there is a lot of spikes which suggest room for grid scalping? We do not need visual analysis of chart, we just need equivalent analytical tools built in system logic and also as a endpoint for codex fast and efficient context gathering for the livemonitor loop.
- **P244 [Anchor]** A one-call event review bundle is a P0 requirement.ðŸŸ¢
- **P245 [Anchor]** The bundle should include PnL, fills, orders, positions, strategy plan versions, LLM traces, deterministic and ML decisions, PBP, CLOB windows, blockers, missed opportunities, and token/cost timeline.ðŸŸ¢
- **P246 [Anchor]** Account-scoped fill ledger is required before DB-origin performance can be trusted.ðŸŸ¢
- **P247 [Anchor]** Decision timeline artifacts are required for per-quarter and per-event review.ðŸŸ¢
- **P248 [Anchor]** Postgame reports should use bundle output instead of grep, screenshots, manual artifact parsing, and stale mirror judgment.ðŸŸ¢
- **P249 [Anchor]** Missed opportunity detection should compare shadow/live signals against observed price windows and direct fillability.ðŸŸ¢
- **P250 [Anchor]** Latency impact reports should quantify missed windows due to Codex, LLM, worker, API, CLOB, or UI delay.ðŸŸ¢
- **P251 [Anchor]** CLOB tick replay must use captured bid/ask/depth/trade cadence, not only panel snapshots.ðŸŸ¢
- **P252 [Caveat]** Current DB-linked Janus performance is negative and incomplete.ðŸŸ¢
- **P253 [Caveat]** Account-wide scenario performance is positive after excluding destructive D/U states, but not fully attributable to autonomous Janus.ðŸŸ¢
- **P254 [Implication]** Future reports must separate autonomous Janus, Codex-assisted Janus, and manual/operator performance.ðŸŸ¢
- **P255 [Implication]** The system must map strategy versions and interventions to event/outcome lifecycle groups before claiming profitability.ðŸŸ¢
- **P256 [Validation]** The exact event bundle schema should be designed before implementing more postgame report logic.ðŸŸ¢

## P. NBA/WNBA Portability Premises

- **P257 [Anchor]** NBA and WNBA should share basketball event, market, PBP, orderbook, replay, and report contracts where possible.ðŸŸ¢
- **P258 [Anchor]** WNBA must maintain separate liquidity, spread, player, quarter, and market-microstructure calibration.ðŸŸ¢
- **P259 [Anchor]** WNBA passive capture is valid and useful before live trading.ðŸŸ¢
- **P260 [Anchor]** WNBA orders must remain disabled until replay and fillability evidence supports a minimum-size test.ðŸŸ¢
- **P261 [Anchor]** WNBA closed-market price history and passive CLOB streams should feed shadow backtests first.ðŸŸ¢
- **P262 [Caveat]** WNBA Polymarket volume and UI/orderbook behavior may differ materially from NBA.ðŸŸ¢
- **P263 [Implication]** Shared schema should avoid NBA-specific assumptions in field names and period/clock logic.ðŸŸ¢
- **P264 [Implication]** WNBA Data Agent should remain P1 shadow/data/replay unless NBA P0 safety and ledger work are stable.ðŸŸ¢
- **P265 [Validation]** WNBA API/data-tier limitations must be validated before promising full past-season backfills.ðŸŸ¢

## Q. Future Domain Premises --> future modules beside basketball, specifically wnba & nba, will only be touched on when wnba is up and running, so as long as the system is built so it can accomodate their inclusion and they are in the backlog of tasks, we have all we need for now, the most important thing is to stop framing janus as a nba/wnba trading system to a fully autonomous and selevolvign expectation markets trading system

- **P266 [Anchor]** Crypto options are a future Janus domain with high-frequency, algorithmic, extensively backtested requirements.ðŸŸ¢
- **P267 [Anchor]** Economics and geopolitics can reuse generic market watch/replay/portfolio foundations but need domain-specific signal sources.ðŸŸ¢
- **P268 [Anchor]** The global portfolio manager should eventually monitor all Polymarket positions, not only Janus sports events.ðŸŸ¢
- **P269 [Anchor]** Long-term underpriced markets such as NBA futures, geopolitics, elections, or culture events need target/rebuy monitors.ðŸŸ¢
- **P270 [Caveat]** Future profile-report inspiration is not proof that Janus can copy those profiles or strategies.ðŸŸ¢
- **P271 [Caveat]** Profile reports have API caps and sample caveats that must not be ignored.ðŸŸ¢
- **P272 [Implication]** Future domains should be added only after core CLOB, ledger, risk, and review infrastructure is stable.ðŸŸ¢
- **P273 [Implication]** Future global portfolio manager should have separate risk budgets from live sports testing.ðŸŸ¢
- **P274 [Validation]** Direct Codex/MCP Polymarket management outside Janus needs its own safety review and permission model.ðŸŸ¢

## R. Safety And Shutdown Premises

- **P275 [Anchor]** Cost incidents require immediate runtime shutdown and incident reporting.ðŸŸ¢
- **P276 [Anchor]** Live LLM dispatch must fail closed on missing credentials, client failure, schema failure, or adoption failure.ðŸŸ¢
- **P277 [Anchor]** Final/flat event shutdown is required to prevent repeated postgame LLM calls.ðŸŸ¢
- **P278 [Anchor]** Repeated-trigger dedup is required before live LLM dispatch returns.ðŸŸ¢
- **P279 [Anchor]** Per-event token budget and cost telemetry are required before live LLM dispatch returns.ðŸŸ¢
- **P280 [Anchor]** Current-event uncovered positions must be protected or reconciled before new exposure.ðŸŸ¢
- **P281 [Anchor]** Duplicate pending intents must block new buys.ðŸŸ¢
- **P282 [Anchor]** Stale feed or inconsistent feed state must block or force review.ðŸŸ¢
- **P283 [Anchor]** Direct CLOB non-flat outside active scope can be classified as out-of-scope, but current-event scope must remain strict.ðŸŸ¢
- **P284 [Anchor]** Live-money readiness requires current plan, market/outcome/token mapping, fresh books, fresh game state, worker heartbeat, execution evidence, direct CLOB truth, order sizing policy, and unblocked integrity gate.ðŸŸ¢
- **P285 [Caveat]** Open out-of-scope positions should not block every development action, but must be snapshotted before service restarts.ðŸŸ¢
- **P286 [Implication]** Integrity Check must block live LLM dispatch if cost controls, cooldowns, or final shutdown controls are missing.ðŸŸ¢
- **P287 [Implication]** Live Monitor must treat stopped worker with current plan as RED, not as a benign status.ðŸŸ¢
- **P288 [Implication]** Any final architecture must define fail-closed semantics per service and per risk class.ðŸŸ¢

## S. Open Design Questions To Resolve Later

- **P289 [Validation]** What is the final service architecture: modular monolith, multiple FastAPI apps, DB-backed queue, Redis-backed workers, or hybrid? -> since the current system is adjustments always from working with the fastapi, lets build from it, with for now a modular monolith, evolving into multiole fastapi apps backed by the same integrated db and system. Redis only if it arrises in the future as a necessit
- **P290 [Validation]** What is the final master-controller decision tree for every time window and game state? that somethign you have to design and infer for all past interactions, but we need decision tree both at system levels that require it as in our codex live monitor analyst agent
- **P291 [Validation]** What is the final agent/task queue schema? As in the last question, this is object of your next goals for the next iteration
- **P292 [Validation]** What is the final GitHub issue/project structure? As in the last question, this is object of your next goals for the next iteration
- **P293 [Validation]** What is the final Obsidian taxonomy and repo-linking method? As in the last question, this is object of your next goals for the next iteration
- **P294 [Validation]** What current legacy NBA controller components should be retired versus wrapped? As in the last question, this is object of your next goals for the next iteration
- **P295 [Validation]** What StrategyPlanJSON fields should remain advisory versus executable in the final risk engine? As in the last question, this is object of your next goals for the next iteration
- **P296 [Validation]** Which conservative LLM actions should be auto-adoptable on day one? As in the last question, this is object of your next goals for the next iteration
- **P297 [Validation]** What exact scenarios qualify as virtual-dead across NBA and WNBA? As in the last question, this is object of your next goals for the next iteration
- **P298 [Validation]** What exact tail-risk order constraints should be allowed by realized profit state?As in the last question, this is object of your next goals for the next iteration
- **P299 [Validation]** What data quality must be required before WNBA minimum-size live money starts? Simply by our algo and ml lanes being active in shadow to build context, since as game profiles are similar to nba and llm/codex are already sufficient (but expensive/not always realiable) to run it
- **P300 [Validation]** What Codex fallback capabilities should exist if internal LLM credits fail? As much as we can, adding as we go as new features are added
- **P301 [Validation]** What direct CLOB manual-order assistant interface is safest: CLI, API endpoint, MCP, or all three? As in the lasts questions, this is object of your next goals for the next iteration, but keep in mind we do not have any MCP yet, if it proves necessary, once you set it up, I need to expose it to a ngrok endpoin for a clean connection loop with codex after we start the ci/cd loop
- **P302 [Validation]** What event bundle schema will become the source for all future postgame reviews? As in the lasts questions, this is object of your next goals for the next iteration
- **P303 [Validation]** What account ledger cleanup is needed before claiming system performance from DB state? As in the lasts questions, this is object of your next goals for the next iteration
- **P304 [Validation]** What minimum test suite must run before any live-money readiness gate can turn green? As in the lasts questions, this is object of your next goals for the next iteration

## Review Notes

This register intentionally captures more premises than a final spec should contain. The next step is to mark each premise as one of:

- Accepted.
- Accepted with edits.
- Rejected.
- Needs evidence.
- Move to later domain scope.

"Only after that review should Janus produce the final architecture, agent framework, risk engine, and backlog documents."

=== ===

- build the obsidian from all that is avaliable in this chat history and was discussed
- build the /docs source of truth for application specs, tasks, automation instructions, obsidian references, etc...
