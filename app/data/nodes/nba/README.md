# NBA Data Nodes

**Path**: `app/data/nodes/nba`

## Overview
These nodes interface with the official NBA Stats API to fetch core basketball data: Teams, Players, Schedule, and Live scores.

## Modules

### 1. Teams (`nba/teams`)
- **Core Script**: `leaguedash_team_base_season.py`
- **Output**: Team statistics (wins, points, efficiency).
- **Status**: **Active**. Used in daily sync.

### 2. Players (`nba/players`)
- **Core Script**: `leaguedash_player_base_season.py`
- **Output**: Player statistics (averages, usage, splits).
- **Status**: **Active**. Used in daily sync.

### 3. Schedule (`nba/schedule`)
- **Core Script**: `season_schedule.py` (assumed loc)
- **Output**: Game list with IDs and status.
- **Status**: **Active**. Used in daily sync.

### 4. Live (`nba/live`)
- **Status**: **Pending/Partial**. Designed for `live_stats.py` and `play_by_play.py`. Currently used by the Live Monitor features.

## Database State
- **Connection**: `app/data/databases/janus_cortex.db`
- **Tables**: `nba_teams` (30 rows), `nba_players`, `nba_games`.

## Verification
- **Test Script**: `dev/tests/validate_nba.py`
- **Result**: **SUCCESS**. Successfully fetched live data from NBA API for 2024-25 season.
