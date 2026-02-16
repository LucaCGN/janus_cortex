# Source Temporal Coverage and Limitations (v0.1 baseline)

## Purpose
Track what each current endpoint/method can return for:
- past data
- current data
- future/scheduled data

This artifact is mandatory for `v0.1.*` node validation and must be updated when methods change.

## Validation date
- 2026-02-16

## Coverage matrix

### NBA

#### `app/data/nodes/nba/schedule/season_schedule.py::fetch_season_schedule_df`
- Source: `https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json`
- Expected by method signature: selectable season via `season` arg
- Observed behavior:
  - Returns a full current schedule window with past + upcoming games for one season snapshot.
  - Date range observed: `2025-10-02` to `2026-06-19`.
  - Row count observed: `1322`.
  - `season` argument currently does not change result (same output for `2023-24`, `2024-25`, `2025-26`).
- Coverage status:
  - Past season: `NOT AVAILABLE` with current endpoint/method implementation.
  - Current season past games: `AVAILABLE`.
  - Current season upcoming games: `AVAILABLE`.
- Limitation:
  - Method is effectively pinned to one season feed and ignores requested season scope.

#### `app/data/nodes/nba/live/play_by_play.py::fetch_play_by_play_df`
- Source: `nba_api.live.nba.endpoints.playbyplay`
- Observed behavior:
  - For completed game id (`0012500008`), returns rows (`631` observed).
  - For scheduled future game id (`0022500792`), returns a safe empty DataFrame path.
  - Node now emits deterministic normalized fields (`event_index`, score deltas, raw payload) and supports cursor/window slicing.
- Coverage status:
  - Past game pbp by game_id: `AVAILABLE` (at least recent completed games).
  - Current live game pbp: `AVAILABLE` (design intent; not deeply load-tested yet).
  - Future game pbp: `NOT AVAILABLE` (no pbp exists before game).
- Limitation:
  - Future/scheduled games still have no pbp by definition; ingestion must rely on repeated runtime sampling once game starts.

#### `app/data/nodes/nba/live/live_stats.py::fetch_live_scoreboard`
- Source: `nba_api.live.nba.endpoints.boxscore`
- Observed behavior:
  - Completed game id returns valid payload (`status=3 Final` observed).
  - Future scheduled game id returned empty dict/error path in test.
- Coverage status:
  - Past completed game summary by game_id: `PARTIALLY AVAILABLE`.
  - Current live game: `AVAILABLE`.
  - Future scheduled game details: `LIMITED/INCONSISTENT`.
- Limitation:
  - Future games may fail because live boxscore payload may not exist before tip-off.

### Polymarket (Gamma)

#### `app/data/nodes/polymarket/gamma/nba/events_node.py::fetch_nba_events_df`
- Source: `https://gamma-api.polymarket.com/events`
- Observed behavior:
  - Node now uses correct Gamma snake_case params (`tag_slug`/`tag_id`, `start_date_min/max`) and defaults to `tag_slug=nba` strategy with tag-id fallback.
  - Node normalizes API bounds to UTC day boundaries and accepts naive datetime inputs by coercing to UTC.
  - `2026-02-01` to `2026-03-01` returned `668` rows, min `2026-02-01`, max `2026-03-02`, with future rows present vs now (`future_cnt=129` in probe).
  - `now` to `now+30d` can still return `0` rows depending on provider semantics for `startDate` (provider-side field is not a stable game-schedule timestamp).
- Coverage status:
  - Past events: `AVAILABLE`.
  - Current/near-term events: `PARTIAL` (window-sensitive).
  - Future events: `PARTIAL / QUERY-SENSITIVE`.
- Limitations:
  - Coverage is sensitive to window anchor and `max_pages`; one window can show future rows while another nearby window can return none.
  - Single wide windows may not represent full temporal coverage due provider pagination/order behavior.

#### `app/data/nodes/polymarket/gamma/nba/markets_moneyline_node.py::fetch_nba_moneyline_df`
- Source: `https://gamma-api.polymarket.com/markets` with `sportsMarketType=moneyline`
- Observed behavior:
  - Query now uses correct snake_case params (`tag_id`, `start_date_min/max`) and returns rows for NBA game windows.
  - Probe window `2026-01-24` to `2026-01-26` returned 2 normalized outcome rows (1 game market x 2 outcomes) after client filtering.
  - Wider probe (`now-60d` to `now+30d`, `max_pages=20`) returned 58 rows.
  - Fallback path implemented: if `/markets` yields no NBA moneyline rows, node can pull nested markets from `/events` (`ingestion_source=events_fallback`).
- Coverage status:
  - Past/current/future moneyline: `PARTIAL AVAILABLE` (query-shape and date-interpretation sensitive).
- Limitation:
  - Provider `startDate` behavior in `/markets` is not a stable proxy for game date; node must apply client-side date filtering using event slug date when available.
  - Some windows still under-return and may require fallback or broader fetch + client filter.

#### `app/data/nodes/polymarket/gamma/nba/odds_history_node.py::fetch_nba_odds_history_df`
- Sources:
  - primary: `https://clob.polymarket.com/prices-history`
  - fallback: current snapshot from Gamma moneyline node (`last_price`) when direct history is empty.
- Observed behavior:
  - CLOB history requires `market=<token_id>` and a time component.
  - Valid time components are:
    - `startTs/endTs` + `fidelity`
    - or `interval` (`1m`, `1h`, `6h`, `1d`, `1w`) + `fidelity`.
  - Large `startTs/endTs` windows can return `400` (`interval is too long`); node falls back to interval query shape.
  - In live probes, `interval=1m` with `fidelity=10` yielded direct history points for some tokens, while other intervals/windows often returned empty arrays.
  - When direct history is empty, snapshot fallback can still emit a usable sample row tagged as `source=snapshot_fallback`.
- Coverage status:
  - Past odds ticks (recent lookback): `PARTIAL AVAILABLE` (token/window dependent).
  - Current/near-current odds ticks: `AVAILABLE` (especially via `interval=1m` path).
  - Future scheduled event odds history: `NOT APPLICABLE` for direct history (history timestamps are retrospective), `PARTIAL` via snapshot fallback by future event association.
- Limitations:
  - Direct history retention/granularity is token- and interval-sensitive.
  - Not all market tokens return historical points for the same query shape.
  - For wide windows, interval-based fallback is required to avoid filter rejection.

#### `app/data/nodes/polymarket/gamma/nba/fallback_stream_history_collector.py::collect_nba_fallback_stream_df`
- Source:
  - live moneyline snapshot polling via `fetch_nba_moneyline_df` (Gamma `/markets` with `/events` fallback when needed).
- Observed behavior:
  - Collector emits append-only rows tagged with `source=fallback_stream`, `ts`, and `sample_no`.
  - In dry-run combined probe (`sync_db.py --include-stream-fallback`), collector produced rows (`gamma_stream_rows=2`) even when direct history was sparse.
  - Standalone probe can still return zero rows in some windows due upstream market exposure/filter shape (`rows=0` observed in one sample run).
- Coverage status:
  - Past/current/future association: `PARTIAL AVAILABLE` through event/game timestamps on sampled rows.
  - Runtime accumulation: `AVAILABLE` (collector builds dataset progressively as app runs).
- Limitations:
  - Collector depends on live snapshot availability for selected window/tags.
  - A single short sample run may return empty; repeated sampling windows are required for robust coverage.
  - This collector captures snapshots (not true historical reconstruction) and should be treated as fallback provenance.

## Practical implications for v0.1 planning
1. NBA past season goal is not met with current schedule method; an alternate historical endpoint/source is required.
2. Current season past and upcoming games are available from schedule feed.
3. For odds history, rely on:
   - direct endpoints where available (moneyline + CLOB token history),
   - fallback stream-to-history persistence (`v0.1.4`) when direct windows under-return.
4. For future event availability, always validate provider exposure window in each run.
5. For Gamma events, use multiple narrower windows (past + future) instead of one broad range when validating temporal coverage.

## Required updates when this file changes
- active checkpoint file (currently `dev-checkpoint/v0.2.1.md`)
- `app/docs/development_guide.md`
- `app/docs/scalable_db_schema_proposal.md` (if schema implications change)
- `app/docs/scalable_api_routes_proposal.md` (if route readiness changes)
