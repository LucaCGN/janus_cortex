# HoopsStats Node

**Path**: `app/data/nodes/hoopsstats`

## Overview
This node scrapes valid/complementary stats from [HoopsStats.com](http://www.hoopsstats.com/). It specifically fetches:
- **Profile**: Aggregated efficiencies, rebounds, and shooting splits verified against fantasy scoring.
- **Tips**: Historical trends (e.g., "When Team has higher FG% -> 14-2").
- **Streaks**: Winning/Losing streaks and momentum indicators.
- **Quarters**: Scoring breakdowns by quarter to identify starters/finishers.

## Scripts & Components

**Primary Implementation**: `BeautifulSoup` (currently active in `daily/nba/sync_db.py`).
**Alternative**: `Crawl4AI` versions exist but are not currently used in the daily pipeline.

### 1. Profile Scraper
- **File**: `profile_scraper_beautiful_soup.py`
- **Function**: `scrape_data(team_slug, team_id, season_code)`
- **Output Model**: `ProfilePayload`
- **Target Table**: `nba_teams` (enriches columns like `avg_efficiency`, `avg_rebounds`, etc.)

### 2. Tips Scraper
- **File**: `tips_scraper_beautiful_soup.py`
- **Function**: `scrape_data`
- **Output Model**: `TipsPayload` (list of tips)
- **Target Table**: `nba_team_insights` (type='tip')

### 3. Streaks Scraper
- **File**: `streaks_scraper_beautiful_soup.py`
- **Function**: `scrape_data`
- **Output Model**: `StreaksPayload`
- **Target Table**: `nba_team_insights` (type='streak')

### 4. Quarters Scraper
- **File**: `quarters_scraper_beautiful_soup.py`
- **Function**: `scrape_data`
- **Output Model**: Contains `scoring_by_quarter` and `quarter_tips`
- **Target Table**: 
    - `nba_teams` (via `hoopsstats_quarters_json` column)
    - `nba_team_insights` (type='quarter_tip')

## Database State
- **Connection**: `app/data/databases/janus_cortex.db`
- **Validated Tables**:
    - `nba_teams`: 30 rows (Active). Contains fields like `avg_efficiency`, `hoopsstats_quarters_json`.
    - `nba_team_insights`: ~1973 rows (Active). Contains textual trends.

## Verification
- **Test Script**: `dev/tests/validate_hoopsstats.py`
- **Validation Results**: Successfully scrapes live profile data from HoopsStats.
