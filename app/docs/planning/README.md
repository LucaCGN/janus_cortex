# Planning Index

## Purpose
Separate active execution planning from archived execution history, while keeping stable product and data-contract docs outside the churn of branch-by-branch work.

Paired reference docs live under:
- [app/docs/reference/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/README.md)
- [app/docs/reference/master_execution_dependency_graph.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/master_execution_dependency_graph.md)

## Planning Layers

### Current Planning
Use these files for the next active work wave:
- [current/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/README.md)
- [current/roadmap_to_multi_algo_backtests.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/roadmap_to_multi_algo_backtests.md)
- [current/benchmark_integration_roadmap.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/benchmark_integration_roadmap.md)
- [current/branches/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branches/README.md)
- [current/branch_strategy.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/branch_strategy.md)
- [current/agent_operating_rules.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/agent_operating_rules.md)
- [current/nba_analysis_next_steps.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/current/nba_analysis_next_steps.md)

### Archived Planning
Use these files to understand why the codebase looks the way it does after a completed wave:
- [archive/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/planning/archive/README.md)

## Stable Product Docs
These stay authoritative even as branch plans change:
- [app/docs/reference/README.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/reference/README.md)
- [app/docs/nba_analysis_module_plan.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_module_plan.md)
- [app/docs/nba_analysis_data_products.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_data_products.md)
- [app/docs/nba_analysis_modeling_and_backtesting.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/nba_analysis_modeling_and_backtesting.md)
- [app/docs/scalable_db_schema_proposal.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/scalable_db_schema_proposal.md)
- [app/docs/scalable_api_routes_proposal.md](/C:/Users/lnoni/OneDrive/Documentos/Code-Projects/janus_cortex/app/docs/scalable_api_routes_proposal.md)

## Local Planning Ledger
Committed docs define the shared rules. Local branch-independent notes belong under:
- `JANUS_LOCAL_ROOT\tracks\planning\current`
- `JANUS_LOCAL_ROOT\tracks\planning\archive`

Use the local planning ledger for:
- branch registers
- session notes
- active checklists that should survive branch deletion
- archived rationale that is useful locally but should not clutter Git history

## Update Rule
- If a plan is guiding current work, keep it under `app/docs/planning/current`.
- If a plan explains a completed or superseded work wave, list it under `app/docs/planning/archive`.
- Do not overwrite the rationale for a completed wave with the next wave's plan.
- If a document is explaining system truth rather than next execution, move or index it under `app/docs/reference`.
