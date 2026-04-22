# NBA Analysis Next Steps

## End Goal
Turn the current offline NBA analysis stack into a repeatable trading-research system that can:
- validate research-ready games deterministically
- replay a small final option set under realistic execution friction
- compare deterministic routing with bounded LLM-assisted routing
- expose only the high-signal artifacts needed to tune controller quality

Detailed milestone and branch decomposition lives in:
- [roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)

## What Is Already Done
- `A0-A7` implementation wave completed
- release wave completed through `v1.5.0`
- postseason validation corpus fixed and loaded:
  - `6` play-in games
  - `14` playoff games
- Polymarket event history linked for the full postseason-final-20 corpus
- play-by-play loaded for the full postseason-final-20 corpus
- hostile-execution benchmark contract frozen as `v11`

## Frozen Current State

### Final Compared Options
- `winner_definition`
- `master_strategy_router_v1`
- `gpt-5.4 :: llm_hybrid_freedom_compact_v1`
- `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1`

### Underlying Controller Family Set
- routed core:
  - `winner_definition`
  - `inversion`
  - `underdog_liftoff`
- independent sleeves:
  - `q1_repricing`
  - `q4_clutch`

### Current Replay Contract
- start bankroll: `$10.00`
- position fraction: `0.20`
- max concurrent positions: `5`
- shared-cash equal split across overlapping positions
- random adverse slippage: `0-20c`

### Current Postseason Final-20 Result
- `master_strategy_router_v1`: `$3.97`
- `gpt-5.4-mini :: llm_hybrid_freedom_compact_v1`: `$3.71`
- `gpt-5.4 :: llm_hybrid_freedom_compact_v1`: `$3.62`
- `winner_definition`: `$2.68`

Interpretation:
- the hostile-execution contract is materially harsher than the earlier optimistic regular-season path tests
- the deterministic router is currently the best capital-preservation option
- the LLM lanes are still challengers, not the new default controller

## Immediate Critical Path

### 1. Deterministic Router Hardening
Branch:
- `codex/analysis-routing-allocation`

Why this is next:
- the kept option set is now small enough
- the main open question is controller quality under hostile execution, not more strategy proliferation

Target outputs:
- refined `master_strategy_router_v1` confidence weighting
- better use of the underlying route mix under the `v11` contract
- clearer payout-vs-growth tradeoff policies

### 2. Focused Dashboard For Finalists
Branch:
- `codex/frontend-analysis-portfolio-viz`

Why this is parallel-friendly:
- the contracts are now narrow and stable
- the dashboard only needs to support the final four compared options plus the route mix

Target outputs:
- one clear finalist comparison view
- route-mix and drawdown diagnostics
- explicit visibility into where the LLM differs from the deterministic router

### 3. Season Continuity And Fresh Postseason Data
Branch:
- `codex/season-playoffs-preseason`

Why this matters:
- we now have a fixed postseason validation corpus, but the data workflow still has to stay healthy as new games land

Target outputs:
- reliable post-regular-season sync steps
- clear refresh playbook for mart rebuilds and controller reruns

## Questions That Still Matter
- where exactly does the deterministic router lose most of its bankroll on the hostile postseason slice?
- is the LLM adding value by selecting better routes, by skipping bad trades, or only by trading more often?
- which route or sleeve decisions deserve a bounded LLM override queue later?
- what payout policy keeps the system alive under harsh fills while preserving growth potential?

## What We Are Not Prioritizing Right Now
- broad family expansion
- free-form LLM autonomy
- model-heavy residual approaches before the deterministic controller is harder
- generic frontend/operator surfaces that do not help tune the final four options
