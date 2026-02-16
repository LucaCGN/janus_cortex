# Polymarket Gamma Node

**Path**: `app/data/nodes/polymarket/gamma`

## Overview
This node interfaces with the Polymarket Gamma API to fetch NBA-related events, markets (Moneyline), and sports metadata. It serves as the primary ingestion point for Polymarket betting data.

## Scripts & Components

### 1. Events Node
- **File**: `nba/events_node.py`
- **Function**: `fetch_nba_events_df`, `upsert_nba_events_to_sqlite`
- **Purpose**: Fetches generic NBA events (Games, Awards).
- **Output Model**: `NBAEvent`
- **Target Table**: `polymarket_events` (Schema) / `polymarket_nba_events` (Default Code)
- **Status**: **Active**. Data present in DB (`polymarket_events`).

### 2. Moneyline Markets Node
- **File**: `nba/markets_moneyline_node.py`
- **Function**: `fetch_nba_moneyline_df`
- **Purpose**: Fetches moneyline market details (outcomes, prices) for events.
- **Output Model**: `NBAMoneylineOutcome`
- **Target Table**: `polymarket_nba_moneyline` (Design)
- **Status**: **Pending/Inactive**. Table `polymarket_nba_moneyline` does not exist in `janus_cortex.db` yet.

### 3. Sports Metadata
- **File**: `nba/sports_metadata.py`
- **Function**: `fetch_nba_sport_metadata`
- **Purpose**: Retrieves Polymarket internal tags for "NBA" to filter events.

## Database State
- **Connection**: `app/data/databases/janus_cortex.db` (SQLite)
- **Validated Tables**:
    - `polymarket_events`: 28 rows (as of Dec 14).
    - `polymarket_nba_moneyline`: **MISSING**.

## Verification
- **Test Script**: `dev/tests/validate_gamma_events.py`
- **Validation**:
    - Code logic runs successfully.
    - Output filtering might return 0 events if no strictly "NBA" tagged markets are live/found with current tags.
