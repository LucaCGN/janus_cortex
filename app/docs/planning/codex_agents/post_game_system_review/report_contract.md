# Postgame Report Contract

Every postgame report must use this structure.

## Header

- reviewed session date
- reviewed at UTC and BRT
- account id
- source artifacts
- direct CLOB state
- whether orders were placed by the review agent

## Executive Summary

- realized portfolio impact
- whether Janus was flat or exposed at end of review
- largest win/loss drivers
- biggest strategy insight
- biggest system failure
- one-sentence readiness for next live slate

## Game Reviews

One section per game:

- final score and market result
- pregame thesis versus actual game
- live market path and major price bands
- key player, rotation, injury, foul, pace, or run context
- manual interventions
- what Janus did
- what Janus should have done
- whether the game creates a new hypothesis for backtest/live shadow

## Per-Algorithm Performance

Use one table per game or one combined table.

Required columns:

- strategy/component
- role: live, live-probe, shadow, bench, ML, LLM, manual-adopted
- signal count
- order intent count
- fills
- skips/blockers
- best missed opportunity or false positive
- final read: right, wrong, late, early, overfiltered, underfiltered, untestable
- action: promote, keep, demote, redesign, instrument

If no data exists, write `missing artifact` and route it as an observability issue.

## Operational Integrity

Required checks:

- direct CLOB collateral, open orders, open positions, closed positions
- fill ledger completeness
- stale local mirror status
- orderbook freshness and spread
- scoreboard/play-by-play freshness
- watch-session tick persistence
- strategy decision persistence
- replay-session creation
- manual intervention adoption
- missing target/stop/hedge coverage
- API/provider errors

## Postgame Research Findings

Explain final-game context using local data and web research when needed:

- decisive game dynamics
- market overreaction or underreaction
- contextual factors Janus did not model
- price bands worth testing
- strategy families that should have captured the move

## Development Handoff

Write exact tasks:

- priority
- owner lane
- problem
- evidence path
- expected behavior
- likely code/docs/artifacts to inspect
- test or replay requirement

## Status Update Text

End with a concise block suitable for copying into:

`local\shared\handoffs\daily-live-validation\status.md`
