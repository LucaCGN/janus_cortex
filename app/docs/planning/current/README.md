# Current Planning

## Active Program Categories
- live execution integration for the locked NBA playoff controller
- decision logging and ML-ready dataset contracts around the locked controller
- read-only portfolio and route review for the locked controller pair
- season-continuity preparation for playoffs, preseason, and WNBA

## Current Planning Order
1. freeze the locked controller pair on `main`
2. wire the primary controller into a paper/live-safe Polymarket executor
3. log every candidate, route, skip, fill, and outcome into an ML-ready contract
4. surface the resulting live-review outputs in read-only tooling
5. keep season-continuity work isolated as a secondary lane

## Canonical Current Planning Files
- [../reference/master_execution_dependency_graph.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/master_execution_dependency_graph.md)
- [roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)
- [branch_strategy.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branch_strategy.md)
- [agent_operating_rules.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/agent_operating_rules.md)
- [nba_analysis_next_steps.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/nba_analysis_next_steps.md)

## Working Rule
- the roadmap explains milestone order
- the branch directory defines branch-by-branch subphases
- the next-steps doc is the short operational summary

## Local Companion Files
Keep branch-by-branch local tracking in:
- `JANUS_LOCAL_ROOT\\tracks\\planning\\current\\branch_register.md`
- `JANUS_LOCAL_ROOT\\tracks\\planning\\current\\session_notes.md`

Move finished local notes into `JANUS_LOCAL_ROOT\\tracks\\planning\\archive`.
