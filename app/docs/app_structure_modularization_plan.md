# App Structure Modularization Plan (Pre-v0.3 Gate)

## Why this is required now
Current `app/data/nodes/*` mixes:
- provider/source integrations (`polymarket`, `nba`, `hoopsstats`, `jinaai`)
- domain/module intent (`nba` as a trading/event domain)

For scale, we need explicit separation between:
1. provider connectors,
2. event categories/domains,
3. strategy modules.

NBA must remain only one module/category, not the root organizing axis.

Checkpoint tracking:
- `v0.2.7`: structure refactoring gate
- `v0.2.8`: pytest topology and regression gate
- `v0.2.9`: full documentation synchronization gate

Status:
- `v0.2.7` completed (package boundaries + compatibility wrappers created)
- `v0.2.8` completed (new mirrored pytest topology for wrappers + regression validation)
- `v0.2.9` completed (docs/checkpoint synchronization)

## Target architecture (category-aware, provider-agnostic)

```text
app/
  domain/
    events/
      categories/
        sports/
          nba/
        politics/
        macro/
        crypto/
      canonical/
        models.py
        id_rules.py
        information_profiles.py
        quality_gates.py
  providers/
    polymarket/
      gamma/
      clob/
    nba/
      cdn/
      live/
    hoopsstats/
    jinaai/
  ingestion/
    mappings/
      canonical/
        adapters/
    pipelines/
      sports_nba/
      politics/
      macro/
      crypto/
  modules/
    nba/
      context/
      serving/
    portfolio/
    risk/
```

Notes:
- `domain/events/categories/*` defines category contracts and mapping rules.
- `providers/*` handles raw fetch/extract only (no category business rules).
- `ingestion/mappings/canonical/*` converts provider payloads into canonical contracts.
- `modules/*` is where module-specific orchestration/serving lives.

## Mapping from current structure

### Keep behavior, move responsibility
- `app/data/nodes/polymarket/gamma/*`
  - target responsibility: `app/providers/polymarket/gamma/*`
- `app/data/nodes/polymarket/blockchain/*`
  - target responsibility: `app/providers/polymarket/clob/*`
- `app/data/nodes/nba/*`
  - split:
    - source connectors -> `app/providers/nba/*`
    - category logic -> `app/domain/events/categories/sports/nba/*`
- `app/data/nodes/hoopsstats/*`
  - source connectors -> `app/providers/hoopsstats/*`
- `app/data/pipelines/canonical/*`
  - canonical mapping domain -> `app/domain/events/canonical/*`
  - adapters stay in ingestion mapping layer -> `app/ingestion/mappings/canonical/adapters/*`
- `app/data/pipelines/daily/nba/sync_db.py`
  - category pipeline -> `app/ingestion/pipelines/sports_nba/sync_db.py`

## Recommended incremental migration (non-breaking)

### Step 1: establish package boundaries (no moves yet)
- add new top-level packages:
  - `app/domain/`
  - `app/providers/`
  - `app/ingestion/`
  - `app/modules/`
- keep current imports working.

### Step 2: alias imports and bridge layer
- add thin wrappers in new locations that import existing implementations.
- start new code only in new structure.

### Step 3: relocate canonical mapping first
- move `app/data/pipelines/canonical/*` into:
  - `app/domain/events/canonical/*` (contracts/rules/scoring/gates)
  - `app/ingestion/mappings/canonical/adapters/*` (provider adapters)
- keep compatibility imports for one phase.

### Step 4: relocate provider connectors
- move node files by provider responsibility.
- keep function signatures stable.

### Step 5: relocate category pipelines
- replace category-specific paths (`daily/nba`) by event-category paths (`sports_nba`).

### Step 6: remove legacy paths after one stable phase
- delete compatibility shims only after full pytest green and route/schema sync.

## Rules for new work
- New integrations must land under `providers/*` first.
- New event category logic must land under `domain/events/categories/*`.
- New strategy/control logic must land under `modules/*`.
- Canonical contract changes must remain provider-agnostic.

## Definition of complete structure gate
Before continuing `v0.3` migration implementation:
1. new package boundaries exist,
2. canonical layer has target home and compatibility imports,
3. no new code is added in legacy `app/data/nodes/*` paths except controlled shims,
4. pytest remains green.
