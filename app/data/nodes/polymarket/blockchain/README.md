# Polymarket Blockchain Node

**Path**: `app/data/nodes/polymarket/blockchain`

## Overview
This node handles interactions with the Polymarket CLOB (Central Limit Order Book) for trading and Portfolio management for reading positions using the Data-API.

## Status: ⚠️ Missing Dependency
**Critical**: The module `py_clob_client` is missing from the environment.
- Run: `pip install py-clob-client` (or similar) to enable this node.

## Scripts & Components

### 1. Manage Portfolio
- **File**: `manage_portfolio.py`
- **Classes**: `PolymarketCredentials`, `OpenPosition`, `ClosedPosition`, `OpenOrder`.
- **Functions**:
    - `view_open_positions`: Fetches positions via Data-API.
    - `view_orders`: Fetches open orders via CLOB API.
    - `place_new_order`: Sends signed Limit orders to CLOB.

## Database State
- **Connection**: `app/data/databases/janus_cortex.db`
- **Target Tables**:
    - `polymarket_positions`: Stores open positions.
    - `polymarket_trades`: Stores historical trades.
    - `polymarket_orders`: Stores open CLOB orders.

## Verification
- **Test Script**: `dev/tests/validate_blockchain.py`
- **Result**: **FAILED**. `ModuleNotFoundError: No module named 'py_clob_client'`.
