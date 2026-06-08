# Technical Issue Handoff Log

This log is for issues discovered by fixed chats or automations that are outside their allowed lane.

Rules:

- Do not use this log for ordinary stale-row cleanup decisions.
- Use it when a row review exposes a technical issue in replay, lifecycle, reconciliation, queue runtime, promotion/demotion enforcement, DB/storage, frontend endpoints, or shared source/indicator contracts.
- If GitHub access exists, comment on a relevant issue or open a new one, then link it here.
- If GitHub access is unavailable, write `GitHub issue: not_created_tool_unavailable`.
- Also append a matching `OPEN` item to `handoff_queue.jsonl`.

Template:

```markdown
## YYYY-MM-DDTHH:MM:SSZ - Short Title

- Severity: blocker | high | medium | low
- Owner lane: master | db_data | signal_strategy | frontend | promotion_runtime | replay_engine
- GitHub issue: #123 or not_created_tool_unavailable
- Affected row: signal/strategy id, variant, version
- Symptom:
- Evidence:
- Blocking impact:
- Suggested next action:
- Live/manual-order status: no live activity, manual orders avoided
```
