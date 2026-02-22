# Source Temporal Coverage and Limitations (v0.1 baseline)

## Purpose
Track what each current endpoint/method can return for:
- past data
- current data
- future/scheduled data

This artifact is mandatory for `v0.1.*` node validation and must be updated when methods change.

## Validation date
- 2026-02-20

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

### Polymarket targeted event probes (`v0.3.6` extras 3.7-3.9)

#### Past NBA game (`nba-mem-den-2026-02-11`)
- URL: `https://polymarket.com/sports/nba/nba-mem-den-2026-02-11`
- Coverage observed:
  - Event/markets/outcomes payload available from Gamma slug endpoint.
  - Moneyline token history available from CLOB in a game-period-focused window.
  - Sampled history window persisted in DB: `2026-02-11T20:00:17+00:00` to `2026-02-12T05:50:17+00:00` (`120` rows).
- Practical note:
  - Game-period windows can be captured reliably when `gameStartTime` is available on market payload.

#### Upcoming NBA game (`nba-ind-was-2026-02-19`)
- URL: `https://polymarket.com/sports/nba/nba-ind-was-2026-02-19`
- Coverage observed:
  - Event/markets/outcomes are available before tip-off.
  - Recent pre-game odds history is available via CLOB token history (`1024` rows persisted in probe run).
- Practical note:
  - Upcoming-game validation can use rolling recent windows (e.g., last 7 days) to verify market activity availability.

#### Long-horizon non-sports event (`will-the-us-confirm-that-aliens-exist-before-2027`)
- URL: `https://polymarket.com/event/will-the-us-confirm-that-aliens-exist-before-2027`
- Coverage observed:
  - Event payload and binary outcomes available via Gamma slug endpoint.
  - CLOB token history available with interval-based retrieval (`1m`) and suitable for grid-style price-level analysis.
  - Probe run persisted `8912` rows with observed price range `0.085` to `0.915` and `12` distinct 1c price levels.
- Practical note:
  - Interval-based queries are preferable for long-horizon events to avoid window-limit errors and still obtain rich recent granularity.

### Polymarket live scoreboard-driven probes (`v0.4.1`)

#### `app/data/databases/seed_packs/polymarket_event_seed_pack.py::build_today_nba_event_probes_from_scoreboard`
- Source:
  - NBA live scoreboard endpoint (`nba_api.live.nba.endpoints.scoreboard.ScoreBoard`)
  - Gamma slug endpoint (`/events/slug/{slug}`) using deterministic slug derived from scoreboard tricode/date (`nba-<away>-<home>-<YYYY-MM-DD>`).
- Observed behavior:
  - Finished and live games from the current NBA slate resolved to valid Polymarket slugs (`HTTP 200` observed for all sampled games on `2026-02-20` UTC window).
  - Event ingestion remained idempotent on `catalog.events` while allowing append-only tick growth.
- Coverage status:
  - Past (finished today): `AVAILABLE` via `history_mode=game_period`.
  - Current live: `AVAILABLE` via `history_mode=rolling_recent` plus `fallback_stream` sampling.
  - Future (scheduled today): `OPTIONAL/PARTIAL` when `include_upcoming=True`.
- Limitations:
  - Live NBA scoreboard does not natively provide historical-day listings; this probe set targets current scoreboard scope only.
  - Live status transitions quickly (`2 -> 3`), so test selection can race during long runs.

#### `app/data/databases/seed_packs/polymarket_event_seed_pack.py::_seed_single_event` (live stream fallback branch)
- Sources:
  - primary stream path: `collect_nba_fallback_stream_df` (`/markets` with `/events` fallback, `source=fallback_stream`)
  - secondary fallback: repeated event-slug snapshots when primary stream polling returns zero rows.
- Observed behavior (`2026-02-20` run evidence):
  - Finished sample (`nba-hou-cha-2026-02-19`) produced direct history ticks (`clob_prices_history`).
  - Live sample (`nba-phx-sas-2026-02-19`) persisted both direct history and stream ticks (`fallback_stream`), with stream rows inserted in DB during runtime sampling.
- Coverage status:
  - Past game retrieval: `AVAILABLE`.
  - Live stream persistence: `AVAILABLE` (primary path or slug-snapshot fallback).
  - Future scheduled association: `PARTIAL` (depends on pre-game market quote availability).
- Limitations:
  - Primary stream collector can still under-return in some windows; slug-snapshot fallback is used to maintain ingestion continuity.

### NBA same-day validation (`v0.4.4`)

#### `app/data/pipelines/daily/nba/sync_postgres.py::run_nba_metadata_sync`
- Source:
  - schedule feed (`fetch_season_schedule_df`)
  - live scoreboard feed (`nba_api.live.nba.endpoints.scoreboard.ScoreBoard`)
  - live boxscore + play-by-play methods for ongoing games
- Observed behavior (`2026-02-20` run evidence):
  - command run: `python -m app.data.pipelines.daily.nba.sync_postgres --season 2025-26 --schedule-window-days 2`
  - summary: `ongoing_games=3`, `missing_today_detected=0`, `missing_today_inserted=0`.
  - verification query for scoreboard IDs returned `db_games_for_scoreboard=10` with `missing_games_today=0`.
- Coverage status:
  - Past (finished today): `AVAILABLE`.
  - Current live (ongoing): `AVAILABLE` (live snapshots and play-by-play rows persisted).
  - Future (scheduled same season): `PARTIAL/AVAILABLE` through schedule feed, still subject to source limits noted above for true past-season retrieval.
- Limitations:
  - Same as base schedule limitation: endpoint is still current-season-scoped and not a full historical archive.

### Polymarket same-day gap closure (`v0.4.2` + `v0.4.6`)

#### `app/data/pipelines/daily/polymarket/sync_markets.py` and `app/data/pipelines/daily/polymarket/backfill_retry.py`
- Source:
  - today NBA slug builder from live scoreboard
  - Gamma slug event payloads and CLOB history endpoints
- Observed behavior (`2026-02-20` run evidence):
  - initial verification after partial ingestion showed `scoreboard_games=10`, `db_catalog_events_for_scoreboard_slugs=6`, `missing_event_slugs_today=4`.
  - running missing-only sync across full same-day caps:
    - `python -m app.data.pipelines.daily.polymarket.sync_markets --probe-set today_nba --max-finished 20 --max-live 20 --max-upcoming 20 --include-upcoming --missing-only`
  - follow-up verification: `db_catalog_events_for_scoreboard_slugs=10`, `missing_event_slugs_today=0`.
  - backfill orchestration run:
    - `python -m app.data.pipelines.daily.polymarket.backfill_retry --max-finished 2 --max-live 2 --max-upcoming 2 --include-upcoming --candle-timeframe 1m --candle-lookback-hours 48`
    - summary: `missing_today_before=0`, `missing_today_after=0`, `candles_upserted=2384`.
- Coverage status:
  - Past (finished today): `AVAILABLE`.
  - Current live: `AVAILABLE` with direct history + fallback stream rows.
  - Future (upcoming today): `PARTIAL/AVAILABLE` when scoreboard provides scheduled games and Gamma slug exists.
- Limitations:
  - Temporal completeness depends on running with sufficiently high `max-finished/max-live/max-upcoming` caps when full same-day closure is required.

### Canonical Mapping Layer (v0.2)

#### `app/data/pipelines/canonical/mapping_service.py::build_canonical_mapping_result`
- Sources:
  - Gamma event/moneyline normalized node outputs
  - NBA schedule normalized node output
  - deterministic fixture pack under `app/data/pipelines/canonical/fixtures/*.json`
  - compatibility wrappers under `app/domain/events/canonical/*` and `app/ingestion/mappings/canonical/*`
- Observed behavior:
  - Canonical mapping preserves mixed temporal windows when source rows contain them.
  - Fixture-backed integration pack contains past events (`2025-12-20`), near-current/past events (`2026-02-02`), and future events (`2026-03-15`).
  - Mapping output retains timezone-aware timestamps and deterministic ids for all windows.
- Coverage status:
  - Past events in canonical bundle: `AVAILABLE`.
  - Current/near-current events in canonical bundle: `AVAILABLE` when source rows include them.
  - Future/scheduled events in canonical bundle: `AVAILABLE`.
- Limitations:
  - Canonical layer is a transform/enrichment stage and cannot create missing temporal coverage not present in upstream node sources.
  - Live temporal completeness remains constrained by upstream provider endpoints listed above.

### FastAPI endpoint validation coverage (`v0.5.6`)

#### `POST /v1/sync/nba/schedule` + `GET /v1/nba/games`
- Validation date: `2026-02-21` (UTC timestamp from run: `2026-02-21T03:49:37Z`).
- Observed behavior:
  - scoreboard sample size: `9` games.
  - status distribution: `{2: 2, 3: 7}`.
  - date-level endpoint check:
    - `/v1/nba/games?game_date=2026-02-20` returned `9` rows, matching scoreboard count.
  - live endpoint check:
    - scoreboard live games (`status=2`): `2`
    - `/v1/nba/games?status=2` returned `3` rows (includes additional in-progress record from ingestion window).
- Coverage status:
  - Past/today completed games via endpoint: `AVAILABLE`.
  - Current live games via endpoint: `AVAILABLE`.
  - Future scheduled games via endpoint: `AVAILABLE` when already synced into `nba.nba_games`.

#### `POST /v1/sync/polymarket/markets` + `GET /v1/events`
- Validation date: `2026-02-21`.
- Observed behavior:
  - scoreboard-derived slug set size: `9`.
  - after sync call with high caps (`max_finished/max_live/max_upcoming=20`), `/v1/events?canonical_slug=<slug>` resolved all `9` slugs.
  - missing scoreboard slugs in events endpoint after sync: `0`.
- Coverage status:
  - Past/today event slugs via endpoint: `AVAILABLE`.
  - Current live event slugs via endpoint: `AVAILABLE`.
  - Future upcoming same-day slugs: `PARTIAL/AVAILABLE`, provider exposure dependent.

## Practical implications for v0.1 planning
1. NBA past season goal is not met with current schedule method; an alternate historical endpoint/source is required.
2. Current season past and upcoming games are available from schedule feed.
3. For odds history, rely on:
   - direct endpoints where available (moneyline + CLOB token history),
   - fallback stream-to-history persistence (`v0.1.4`) when direct windows under-return.
4. For future event availability, always validate provider exposure window in each run.
5. For Gamma events, use multiple narrower windows (past + future) instead of one broad range when validating temporal coverage.

## Required updates when this file changes
- active checkpoint file (currently `dev-checkpoint/v0.6.1.md`)
- `app/docs/development_guide.md`
- `app/docs/scalable_db_schema_proposal.md` (if schema implications change)
- `app/docs/scalable_api_routes_proposal.md` (if route readiness changes)
