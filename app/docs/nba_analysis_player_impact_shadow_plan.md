# NBA Player Impact Shadow Plan

## Why / Revision Note
- Experimental A7 lane for offline player-impact exploration.
- This is a shadow artifact only and does not change core mart, report, backtest, or model behavior.

## Objective
- Explore player-impact signals that can be described from offline artifacts already available in the NBA analysis lane.
- Keep the lane clearly labeled as correlational and non-blocking.

## Inputs
- `app/data/pipelines/daily/nba/analysis/mart_state_panel.py` outputs
- `nba.nba_play_by_play`
- `nba.nba_player_stats_snapshots`

## Outputs
- `player_impact_shadow_summary.json`
- `player_impact_shadow.md`
- `player_impact_shadow_swing_state_events.csv`
- `player_impact_shadow_run_segments.csv`
- `player_impact_shadow_player_presence_summary.csv`
- `player_impact_shadow_absence_proxy_summary.csv`
- `player_impact_shadow_absence_proxy_deltas.csv`

## What This Lane Tries To Describe
- Scorer presence on swing states.
- Run-start and run-stop involvement.
- Optional absence-driven deltas when explicit proxy fields exist in player snapshot payloads.

## Claim Boundaries
- Correlational: yes.
- Causal: no.
- Injury causality: not supported.
- Missing proxy fields should result in an empty proxy section, not a fabricated claim.

## Artifact Rules
- Keep every output marked experimental or shadow.
- Prefer stable offline joins and deterministic ordering.
- Do not depend on dashboard code or live APIs.
- Do not override the canonical A1 universe classification.
