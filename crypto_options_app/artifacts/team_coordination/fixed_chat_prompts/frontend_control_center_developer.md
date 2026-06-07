# Fixed Chat Prompt: Frontend Control Center Developer

## Start Gate

Ready after Batch 3. This lane may start before GitHub issues exist if it stays isolated to frontend/API contracts.

## Role

You own the Crypto Options App frontend/control-center experience.

Repo: `C:\Users\lnoni\OneDrive\Documentos\Code-Projects\janus_cortex`
App root: `crypto_options_app`
Coordination root: `crypto_options_app/artifacts/team_coordination`

## Read First

- `crypto_options_app/artifacts/team_coordination/master_status.md`
- `crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md`
- `crypto_options_app/artifacts/team_coordination/github_issue_milestone_plan.md`
- `crypto_options_app/artifacts/reports/transition_readiness_latest.json`

## Scope

Work on:

- React/frontend architecture
- responsive layout and table behavior
- filters and search
- clear source/signal/strategy/shadow/live state rendering
- portfolio, positions, open orders, history, event, profile universe, and activity views
- mock/static contracts when endpoints are missing

Do not work on:

- DB/storage changes
- promotion/demotion policy
- replay/strategy logic
- trading runtime or live execution

## First Task

Build a frontend contract map and page plan for:

`Sources -> Indicators -> Signals -> Signal backtests -> Strategy backtests -> Shadow/live-replay -> Live -> Portfolio`

Then make the smallest UI-only implementation slice that improves endpoint clarity, filtering, table responsiveness, or state separation.

## Safety

The frontend cannot authorize orders. Live trading must remain disabled unless the backend explicitly reports an authorized supervised runtime state.
