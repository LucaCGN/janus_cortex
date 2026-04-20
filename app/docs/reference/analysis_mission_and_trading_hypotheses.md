# Analysis Mission And Trading Hypotheses

## Mission
Build a research program that explains and exploits repeatable NBA odds movements on Polymarket.

The target is not abstract prediction quality by itself. The target is a profitable and defensible trading framework that can:
- explain why intragame prices move
- identify which price moves are normal possession-game noise versus actionable dislocation
- measure when a game is statistically defined versus still vulnerable to comeback paths
- compare multiple trading algorithms on the same historical substrate

## Core Market Thesis
Two forces matter at the same time:

1. Basketball is naturally volatile.
   - It is a possession game.
   - Even strong favorites usually experience stretches where they look vulnerable.
   - The same team can still win comfortably after multiple intragame drawdowns.

2. Odds are not purely rational.
   - Human expectation is partly emotional and narrative-driven.
   - Brand strength, recent headlines, superstar presence, and early-game swings can move prices faster than the underlying possession-level reality changes.

The research module exists to measure the overlap between those two forces.

## Guiding Examples

### Lakers
- strong offensive identity and star power
- often priced to win
- often allow opponent stretches that create cheap underdog entries
- example research implication:
  - favorite drawdown reversion
  - underdog scalp windows

### OKC
- elite team quality and strong third-quarter profile
- often opens at very high implied probability
- research implication:
  - when do overwhelming favorites still create tradeable drawdowns?
  - when does a game become statistically defined quickly enough that continuation beats reversion?

### Hawks
- started from weaker expectation bands
- produced better-than-expected outcomes and meaningful intragame repricing
- research implication:
  - expectation drift across the season
  - underdog continuation and comeback profile identification

## Stable Research Questions
- Which teams most often outperformed or underperformed opening expectation?
- Which teams and opening bands produce the largest intragame swings?
- Which teams cross the `50c` inversion line most often?
- Which game contexts create the highest comeback or reversion probability?
- Which price and score states imply that the winner is statistically defined?
- Which comeback classes still break those "game is defined" rules?
- How does team expectation drift across a season?
- Which player-presence or play-type signals matter enough to move the odds path meaningfully?

## Algorithm Program Goal
The target state is not one "best guess" model.

The target state is a controlled experiment set where multiple algorithm families can be backtested side by side, including:
- favorite drawdown reversion
- underdog inversion continuation
- winner-definition continuation
- quarter-context comeback reversion
- volatility scalping around opening-band and score-context regimes
- scoreboard-control mismatch fade or continuation rules

## Success Criteria
The program is successful when it can:
- run several strategies on the same mart and state-panel version
- compare them against naive baselines and slippage stress
- preserve visual and tabular evidence for why a strategy looks good or bad
- tell the difference between descriptive narrative findings and actual backtestable edge

## Non-Goals
- pure narrative betting without repeatable evidence
- unsupported injury-causality claims
- live automation before offline validation is trustworthy
- LLM-driven trade execution before structured mart, benchmark, and validation outputs are stable
