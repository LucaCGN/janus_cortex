# Janus LLM Model Routing

Janus uses model tiers by task cost and risk. The goal is to preserve full reasoning for high-impact decisions without spending frontier-model budget on extraction or routine summarization.

## Model Tiers

| Tier | Use |
|---|---|
| `gpt-5.4-nano` | Cheap extraction, play-by-play tagging, orderbook/tick compression, file/status summarization, repetitive report normalization. |
| `gpt-5.4-mini` | Routine pregame synthesis, routine StrategyPlanJSON drafting/revision, normal live-monitor reasoning, operator minimum-size/minimum-order live tests, postgame first-pass classification. |
| `gpt-5.5` | Critical reasoning only: final plan review under high uncertainty, material open-position stop/hedge decisions, manual-intervention reconciliation, halftime/late-game thesis revision, lane promotion/demotion, architecture or deep development decisions. Frontier is blocked for operator minimum-size/minimum-order live tests unless a separate cost/readiness review explicitly enables it. |

If a configured runtime does not expose one of these exact names, use the closest configured alias and record the alias in the run output. Do not silently upgrade every step to the expensive model.

## Authority Boundaries

- Codex Pregame Research provides context and proposed triggers only.
- Janus internal LLM converts approved context into structured strategy plans.
- Operator sizing policy controls order size, not Codex research and not the LLM plan.
- The order manager enforces mechanical safety and exchange/account constraints.

## Recommended Split By Agent

- Post Game System Review: `gpt-5.4-nano` for raw summaries, `gpt-5.4-mini` for per-game/per-lane review, `gpt-5.5` for next-day development priorities when PnL, missed opportunities, or live failures are material.
- Development Agent: `gpt-5.4-mini` for normal implementation, `gpt-5.5` for architecture, strategy redesign, ML/replay methodology, and bug clusters that affect live execution.
- Pregame Integrity Check: `gpt-5.4-nano` for status normalization, `gpt-5.4-mini` for readiness judgment, `gpt-5.5` only for ambiguous live-money safety failures.
- Pregame Research & Planning: `gpt-5.4-nano` for source extraction, `gpt-5.4-mini` for game thesis and trigger proposals, `gpt-5.5` only for high-stakes contradictions or last-minute critical lineup/market ambiguity.
- Live System Monitor: `gpt-5.4-nano` for tick summaries, `gpt-5.4-mini` for normal monitoring and operator minimum-size/minimum-order live tests, `gpt-5.5` only for material exposure, manual intervention, missing protection, stale-recovery decisions, or hedge/stop evaluation after frontier spend has been explicitly cleared.
