# Source Temporal Coverage and Limitations (v0.1 baseline)

## Purpose
Track what each current endpoint/method can return for:
- past data
- current data
- future/scheduled data

This artifact is mandatory for `v0.1.*` node validation and must be updated when methods change.

## Validation date
- 2026-03-14 (latest refresh)

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

### FastAPI portfolio and market-data service validation (`v0.6.1` - `v0.6.6`)

#### Date-aware live validation run
- Validation date: `2026-02-22` (UTC timestamp from run: `2026-02-22T03:01:31Z`).
- Observed scoreboard behavior from live API:
  - `scoreboard_games_total=6`
  - `scoreboard_status_counts={2:4,3:2}`
  - dominant slate date: `2026-02-21`
  - `/v1/nba/games?game_date=2026-02-21` returned `6`
  - `/v1/nba/games?status=2` returned `4`
  - `scoreboard_slug_missing_in_events=0` after `/v1/sync/polymarket/markets`

#### Market-data route coverage
- `/v1/events/{event_id}/odds/latest` returned deep outcome coverage for same-day NBA events (`event_odds_count=90` in probe run).
- `/v1/outcomes/{outcome_id}/prices/ticks` returned historical points for live outcomes (`outcome_ticks_count=150` in probe run).
- `/v1/sync/polymarket/orderbook` validated with persisted write path:
  - status `success`
  - `rows_read=30`, `rows_written=6` (sample run)
  - `/v1/outcomes/{outcome_id}/orderbook/history` then returned `count=1`.
- `/v1/sync/polymarket/prices` validated with persisted write path:
  - status `success`
  - `rows_read=128`, `rows_written=128` (sample run).

#### Portfolio route coverage
- `/v1/portfolio/accounts` and `/v1/portfolio/summary` operational with `portfolio.valuation_snapshots` active (`v0.6.2` migration).
- `/v1/portfolio/orders` `POST`/`DELETE` validated in dry-run mode:
  - manual place response `201`
  - manual cancel response `200`
  - lifecycle events persisted in `portfolio.order_events`.
- Risk/rate guards (`v0.6.5`) validated via endpoint tests:
  - oversize order request rejected (`422`)
  - repeated actions for same account over configured limit rejected (`429`).

#### DB state snapshot after v0.6 live validation
- `core.sync_runs=5`, `core.raw_payloads=17`
- `catalog.events=6`, `catalog.markets=271`, `catalog.outcomes=542`, `catalog.market_state_snapshots=542`
- `market_data.outcome_price_ticks=1566`, `market_data.orderbook_snapshots=1`, `market_data.orderbook_levels=6`
- `portfolio.trading_accounts=1`, `portfolio.orders=1`, `portfolio.order_events=2`
- `nba.nba_games=40`, `nba.nba_live_game_snapshots=8`, `nba.nba_play_by_play=1555`

#### Limitations (current)
- `portfolio.valuation_snapshots` is active but not yet fed by dedicated ingestion; summaries fall back to latest position snapshots when valuation rows are absent.
- `sync/polymarket/positions|orders|trades` use wallet-dependent Data API sync and require wallet availability in payload or environment.

### NBA module serving validation (`v0.7.1`)

#### `GET /v1/nba/*` read-route coverage (past/live/upcoming + context joins)
- Validation date: `2026-02-23` (UTC run window).
- Observed behavior from live validation flow:
  - scoreboard sample:
    - `scoreboard_games_total=11`
    - `scoreboard_status_counts={2:3,3:4,1:4}`
    - dominant slate date: `2026-02-22`
  - endpoint coverage:
    - `/v1/nba/games?finished_only=true` -> `29`
    - `/v1/nba/games?live_only=true` -> `3`
    - `/v1/nba/games?upcoming_only=true` -> `34`
    - `/v1/nba/games?game_date=2026-02-22` -> `11`
  - BOS/LAL event route linkage:
    - canonical slug `nba-bos-lal-2026-02-22` resolved in `/v1/events`
    - `/v1/events/{event_id}/odds/latest` returned `94` rows
    - selected moneyline market: `Celtics vs. Lakers: 1H Moneyline`
- Coverage status:
  - Past same-day games: `AVAILABLE`.
  - Current live games: `AVAILABLE`.
  - Upcoming same-day games: `AVAILABLE`.
  - Game-event link joins (`/v1/nba/games/{game_id}/event-links`): `AVAILABLE`.
  - Team/player/stat read routes seeded+validated in `tests/app/api/test_nba_read_routes_pytest.py`: `AVAILABLE`.
- Limitations:
  - context-pre/context-live endpoints remain phase-gated to `v0.7.4` and are not part of `v0.7.1`.

#### Live place/cancel and leave-open validation (`/v1/portfolio/orders`) on BOS/LAL request
- Validation date: `2026-02-23`.
- Event: `https://polymarket.com/sports/nba/nba-bos-lal-2026-02-22`.
- Observed behavior:
  - first attempt with `size=2` failed at CLOB with minimum-size constraint (`minimum: 5`).
  - adjusted notional plan to `$1` with `price=0.2`, `size=5`.
  - cancel probe succeeded:
    - submitted order -> canceled (`manual_cancel_submitted`).
  - two submitted orders left open as requested:
    - Celtics outcome: submitted (`limit_price=0.2`, `size=5`)
    - Lakers outcome: submitted (`limit_price=0.2`, `size=5`)
- Limitation:
  - per-market minimum size constraints can invalidate notional plans if `size < min_size`; route-level validation should surface this earlier in later phases.

#### Closed-position consolidation and event-conclusion validation (`v0.7.2` extension)
- Validation date: `2026-02-23` (post-refresh run).
- Validation path:
  - `POST /v1/events/import-url` for `nba-bos-lal-2026-02-22`
  - `POST /v1/sync/polymarket/positions|orders|trades` with proxy wallet
  - `POST /v1/sync/polymarket/closed-positions/consolidate` with primary wallet payload
- Observed behavior:
  - wallet resolution now maps primary payload to configured/account proxy for Data-API mirror (`consolidate_wallet=0x7d2F...` in response).
  - mirror sync succeeds in current run (`rows_read=406`, `rows_written=8`).
  - BOS/LAL moneyline trades are present for both outcomes after mirror:
    - Celtics buy trades: present.
    - Lakers buy trades: present.
  - event conclusion markers remain unresolved upstream:
    - event `status=open` while `end_time=2026-02-22T23:30:00Z` is in the past.
    - moneyline outcomes still `is_winner=null`.
  - consolidation route now flags stale conclusion candidates from account exposure graph:
    - `stale_conclusion_candidates=2`
    - stale samples include `event_slug=nba-bos-lal-2026-02-22`.
- Coverage status:
  - closed-position normalization route behavior: `AVAILABLE`.
  - stale-finished-event detection over mirrored exposure data: `AVAILABLE`.
  - final winner resolution state from upstream market payload: `PARTIAL / PROVIDER-LAG SENSITIVE`.

### NBA module serving validation (`v0.7.3` to `v0.7.6`)

#### Game-scoped live sync, play-by-play, and context routes
- Validation date: `2026-03-14`.
- Refresh evidence:
  - final audited DB state:
    - `nba.nba_games=1322` (`updated_at=2026-03-14T03:39:56Z`)
    - `nba.nba_live_game_snapshots=23` (`captured_at=2026-03-14T03:39:56Z`)
    - `nba.nba_play_by_play=1571`
    - `nba.nba_context_cache=12` (`generated_at=2026-03-14T03:39:58Z`)
    - `market_data.outcome_price_ticks=1513` (`ts=2026-03-14T03:32:12Z`)
    - `core.sync_runs=26` (`started_at=2026-03-14T03:39:56Z`)
  - current-season status distribution after refresh:
    - upcoming: `246`
    - live: `3`
    - finished: `1073`
- Route validation pack:
  - `POST /v1/sync/nba/live/{game_id}`
  - `GET /v1/nba/games/{game_id}/live`
  - `GET /v1/nba/games/{game_id}/play-by-play`
  - `GET /v1/nba/games/{game_id}/context/pre`
  - `GET /v1/nba/games/{game_id}/context/live`
- Observed behavior from selected-game live validation:
  - finished game sample (`0022500964`):
    - live snapshots persisted and retrievable.
    - play-by-play returned populated rows (`200` in validation window).
    - pre/live contexts returned coherent payloads.
  - live game sample (`0022500968`):
    - repeated sync appends live snapshots.
    - play-by-play returned populated rows (`200` in validation window).
    - live context includes latest score-state and linked event preview.
  - upcoming game sample (`0042500407`):
    - schedule record exists and pre-context is available.
    - live snapshots and play-by-play return safe empty results before tip-off.
    - live context returns a limited payload with no live-state section populated.
- Coverage status:
  - Current season past games: `AVAILABLE`.
  - Current season live games: `AVAILABLE`.
  - Current season upcoming games: `AVAILABLE` for schedule/pre-context, `LIMITED` for live payloads before game start.
  - Current-season DB rehydrate via validated routes/pipelines: `AVAILABLE`.
- Limitations:
  - NBA schedule source is still effectively current-season scoped; past-season archive retrieval remains unavailable through current method.
  - Future scheduled games do not expose live boxscore or play-by-play before tip-off.

#### Final historical feasibility report (`v0.7.6`)
- Validation date: `2026-03-14`.
- Tests executed:
  - `$env:JANUS_RUN_LIVE_TESTS='1'; python -m pytest -q tests/app/data/nodes/test_temporal_coverage_live_pytest.py`
- Result:
  - `5 passed, 2 skipped`.
- Confirmed feasible today:
  - NBA current-season past games.
  - NBA current-season live games.
  - NBA current-season upcoming games in schedule tables.
  - recent or current Polymarket odds history windows with interval-based or snapshot-fallback retrieval.
  - runtime-accumulated fallback stream history.
- Confirmed not fully feasible today:
  - NBA past-season schedule or game archive through current source endpoint.
  - pre-tip live boxscore or play-by-play for future scheduled NBA games.
  - one-shot Gamma validation that reliably returns both past and future windows in every sampled run.
  - arbitrary deep Polymarket historical price recovery for every token or window pair.
- Provider-sensitive evidence:
  - skipped: Gamma split-window coverage because provider did not expose one of the sampled windows.
  - skipped: mixed-window odds-history coverage because provider did not expose both past and future games in the sampled history window.

### 2025-26 season strategy audit gate (pre-`v0.8.1`)

#### Full-season play-by-play feasibility for lead-change analytics
- Validation date: `2026-03-14`.
- Execution path:
  - `python -m app.data.pipelines.daily.nba.season_strategy_audit --season 2025-26 --pbp-max-workers 8 --moneyline-window-days 14 --moneyline-max-pages 30 --history-sample-events-per-month 3`
- Observed behavior:
  - finished NBA games checked: `1076`
  - finished games with non-empty play-by-play: `1076`
  - play-by-play coverage: `100.0%`
  - average play-by-play rows per finished game: `577.41`
  - lead-change summary transform is now available in `app/data/nodes/nba/live/play_by_play.py::compute_lead_change_summary`
- Strategy-relevant evidence:
  - Lakers (`LAL`) from full finished-game audit:
    - `games_with_pbp=72`
    - `wins=42`, `losses=30`
    - `avg_lead_changes=6.75`
    - `avg_losing_segments=3.79`
    - `avg_largest_lead_in_losses=6.03`
    - `losses_after_leading=25`
  - Hornets (`CHA`) from full finished-game audit:
    - `games_with_pbp=72`
    - `wins=36`, `losses=36`
    - `avg_lead_changes=7.61`
    - `avg_losing_segments=4.32`
    - `avg_largest_lead_in_losses=6.19`
    - `losses_after_leading=29`
- Coverage status:
  - full-season finished-game play-by-play fetch: `AVAILABLE`
  - lead-change derivation from play-by-play: `AVAILABLE`

#### Full-season Polymarket moneyline and in-game odds feasibility
- Validation date: `2026-03-14`.
- Observed behavior:
  - unique season event slugs discovered from October 2025 to March 14, 2026: `655`
  - finished NBA schedule games covered by season moneyline fetch path: `583 / 1076`
  - finished-game moneyline coverage: `54.18%`
  - month-stratified history sample:
    - sampled events: `18`
    - sampled outcomes: `36`
    - outcomes with any direct history points: `36 / 36`
    - outcomes with both pre-game and in-game points: `36 / 36`
- Important implementation constraint:
  - Gamma/Polymarket `game_start_time` is not reliable enough to define the game-period history window.
  - Robust pre-game/in-game odds validation requires anchoring the history request to NBA schedule start time via slug mapping.
- Coverage status:
  - full-season moneyline event discovery: `PARTIAL`
  - direct odds-history on covered games when schedule-anchored: `AVAILABLE` in sampled validation
  - full-league full-season odds-history backfill from current provider path: `NOT FULLY AVAILABLE`
- Limitation:
  - the current provider path does not expose a complete season of NBA moneyline events for all finished games, so league-wide season odds analytics remain coverage-limited even though covered games have usable in-game history.

## Practical implications for v0.1 planning
1. NBA past season goal is not met with current schedule method; an alternate historical endpoint/source is required.
2. Current season past and upcoming games are available from schedule feed.
3. For odds history, rely on:
   - direct endpoints where available (moneyline + CLOB token history),
   - fallback stream-to-history persistence (`v0.1.4`) when direct windows under-return.
4. For future event availability, always validate provider exposure window in each run.
5. For Gamma events, use multiple narrower windows (past + future) instead of one broad range when validating temporal coverage.

## Required updates when this file changes
- active checkpoint file (currently `dev-checkpoint/v0.8.1.md`)
- `app/docs/development_guide.md`
- `app/docs/scalable_db_schema_proposal.md` (if schema implications change)
- `app/docs/scalable_api_routes_proposal.md` (if route readiness changes)
