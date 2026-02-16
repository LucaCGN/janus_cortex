# Janus Cortex — Nodes Design

> Local: `app/data/nodes/nodes-design.md`  
> Objetivo: mapa técnico / documentação objetiva dos **nodes de dados** e seus componentes  
> Escopo: NBA (stats + live), Polymarket Gamma (NBA), Polymarket Blockchain (CLOB / portfolio / orderbook)

---

## 1. Visão Geral

Os **nodes** são blocos de dados autocontidos que:

1. **Buscam dados externos** (APIs da NBA, Polymarket, blockchain/CLOB, etc.)
2. **Normalizam para DataFrames** (`pandas`)
3. **Expõem modelos Pydantic** para uso em:
   - Pipelines de ingestão/atualização diária
   - Ferramentas de agentes (Crew / MCP / etc.)
   - Scripts analíticos (pesquisa, simulação, backtest)
4. **Persistem em banco** (SQLite local de teste, e futuramente Postgres/Prod)

Fluxo conceitual:

```mermaid
flowchart LR
    extNBA[NBA API (stats/live)] --> nbaNodes[NBA Nodes<br/>players/teams/live]
    extGamma[Polymarket Gamma API] --> gammaNodes[Gamma Nodes<br/>events/markets/metadata]
    extCLOB[Polymarket CLOB/Blockchain] --> bcNodes[Blockchain Nodes<br/>portfolio/orderbook]
    extJina[Jina AI API] --> jinaNodes[Jina Nodes<br/>reader/search]

    nbaNodes --> db[(DB local / prod)]
    gammaNodes --> db
    bcNodes --> db
    jinaNodes --> db

    db --> pipelines[Data Pipelines<br/>(daily_nba.py, etc.)]
    db --> agents[Agents & Crews<br/>(matchup_research.py, etc.)]
    db --> liveMonitor[Live Monitor<br/>(estratégia in-game)]
````

---

## 2. Estrutura de Pastas (Nodes)

```text
app/data/nodes
├── jinaai
│   ├── reader.py
│   └── search.py
├── nba
│   ├── live
│   │   ├── live_stats.py            # TODO
│   │   └── play_by_play.py          # TODO
│   ├── players
│   │   ├── leaguedash_player_advanced_season.py
│   │   ├── leaguedash_player_base_season.py
│   │   ├── leaguedash_player_usage_season.py
│   │   ├── player_quarter_splits_byperiod.py
│   │   └── tests/…                  # test_*_node.py
│   └── teams
│       ├── leaguedash_team_advanced_season.py
│       ├── leaguedash_team_base_season.py
│       ├── team_quarter_splits_byperiod.py
│       ├── team_recent_form_last5.py
│       └── tests/…                  # test_*_node.py
└── polymarket
    ├── blockchain
    │   ├── manage_portfolio.py      # Implementado (View Positions/Orders, Place Market/Limit, Cancel)
    │   └── stream_orderbook.py      # Implementado (Fetch Depth)
    └── gamma
        ├── gamma_client.py
        └── nba
            ├── events_node.py
            ├── markets_moneyline_node.py
            ├── sports_metadata.py
            ├── teams_node.py
            ├── tests/…              # test_*_node.py
    ├── hoopsstats
    │   ├── profile_scraper_beautiful_soup.py
    │   ├── tips_scraper_beautiful_soup.py
    │   ├── streaks_scraper_beautiful_soup.py
    │   ├── matchups_scraper_beautiful_soup.py
    │   └── tests/…                  # test_*_node.py
```

Documento atual: **`nodes-design.md`** → mapa de tudo acima.

---

## 3. Convenções Gerais de Nodes

### 3.1. Padrões de código

Cada node segue um padrão aproximado:

1. **Configuração e logging**

   * Usa `logging.getLogger(__name__)`
   * Mensagens consistentes no padrão `[context][step] ...`
2. **Modelos Pydantic**

   * `*Request`: parâmetros de entrada (season, filtros, etc.)
   * `*Stats` / `*Record`: linha normalizada de saída
3. **Funções core**

   * `fetch_*_df(request: RequestModel) -> pd.DataFrame`
   * Funções auxiliares para filtros (por team, player, slug, etc.)
4. **Persistência**

   * `upsert_*_to_sqlite(df, sqlite_path, table_name=...)`
   * Futuro: `upsert_*_to_postgres(engine, table_name=...)`
5. **Testes**

   * Scripts de teste integrados em `tests/test_*_node.py`
   * Cada teste:

     * Imprime header rico (com emojis, separadores)
     * Executa 2–4 casos (full season, filtro, pydantic, upsert)
     * Usa SQLite local em `app/data/databases/...`

### 3.2. Banco de dados local de teste

* **NBA players**: `app/data/databases/nba_test_players.db`

  * `nba_players_base_test`
  * `nba_players_advanced_test`
  * `nba_players_usage_test`
* **NBA teams**: `app/data/databases/nba.db`

  * `nba_teams_base_test`
  * `nba_teams_advanced_test`
  * `nba_teams_last5_test`
  * `nba_teams_quarter_splits_test`
* **Polymarket Gamma**: `app/data/databases/polymarket_nba_test.db`

  * `polymarket_nba_events`
  * `polymarket_nba_moneyline`
  * (futuro: `polymarket_nba_teams` caso o endpoint volte a responder dados)

---

## 4. Nodes NBA — Players

### 4.1. `leaguedash_player_base_season.py`

**Função**
Node de estatísticas **“base”** por jogador (médias de pontos, rebotes, arremessos, etc.).

**Principais componentes**

* `PlayerBaseRequest` (Pydantic)

  * `season: str` (ex.: `"2024-25"`)
  * `team_slugs: Optional[List[str]]`
  * `player_ids: Optional[List[int]]`
* `PlayerBaseStats` (Pydantic)

  * Campos normalizados:

    * `player_nba_id`, `player_name`, `team_id`, `team_slug`, `season`, `last_update`
    * `games_played`, `wins`, `losses`, `win_pct`
    * `age`, `avg_minutes`, `avg_points`, `avg_assist`, `avg_steals`, `avg_blocks`
    * `avg_turnover`, `avg_rebounds`, `off_reb`, `def_reb`
    * `fg_made`, `fg_attempted`, `fg_pct`
    * `fg3_made`, `fg3_attempted`, `fg3_pct`
    * `ft_made`, `ft_attempted`, `ft_pct`
    * `pf`, `pf_drawn`, `plus_minus`, `nba_fantasy_pts`, `double_doubles`, `triple_doubles`
* `fetch_player_base_df(request: PlayerBaseRequest) -> pd.DataFrame`
* `upsert_player_base_to_sqlite(df, sqlite_path, table_name="nba_players_base_test")`

**Uso típico**

* Alimentar tabela `players` base do projeto
* Ferramentas de agente para descrever perfil “geral” de um jogador num matchup

---

### 4.2. `leaguedash_player_advanced_season.py`

**Função**
Node de métricas **avançadas** por jogador (ratings, uso, PIE, etc.).

**Principais componentes**

* `PlayerAdvancedRequest`

  * Mesmo padrão de `season` / `team_slugs` / `player_ids`
* `PlayerAdvancedStats`

  * `games_played`, `wins`, `losses`, `win_pct`
  * `min`, `usage_pct`, `ts_pct`, `efg_pct`
  * `off_rating`, `def_rating`, `net_rating`
  * `ast_pct`, `reb_pct`, `oreb_pct`, `dreb_pct`, `tov_pct`, `pie`
* `fetch_player_advanced_df(request)`
* `upsert_player_advanced_to_sqlite(df, ...)`

**Uso**

* Dar contexto ao agente sobre impacto real do jogador (eficiência, rating, uso)
* Feeds para modelos que avaliam mismatchs individuais em matchup

---

### 4.3. `leaguedash_player_usage_season.py`

**Função**
Node de **perfil de uso** (tipos de arremesso, origem dos pontos).

**Principais componentes**

* `PlayerUsageRequest`
* `PlayerUsageStats`

  * `pct_fga_2pt`, `pct_fga_3pt`
  * `pct_pts_2pt`, `pct_pts_3pt`, `pct_pts_ft`
  * `pct_pts_fastbreak`, `pct_pts_paint`, `pct_pts_off_tov`
  * `pct_ast_2pm`, `pct_uast_2pm`
  * `pct_ast_3pm`, `pct_uast_3pm`
* `fetch_player_usage_df(request)`
* `upsert_player_usage_to_sqlite(df, ...)`

**Uso**

* Entender se o jogador depende mais de transição, garrafão, 3 pontos, etc.
* Complementar edges de matchup (ex.: time que defende mal transição vs scorers de fast-break)

---

### 4.4. `player_quarter_splits_byperiod.py`

**Função**
Node de **splits por quarto** para cada jogador.

**Principais componentes**

* `PlayerQuarterSplitsRequest`
* `PlayerQuarterSplitsStats`

  * `player_nba_id`, `player_name`, `team_id`, `team_slug`, `season`, `last_update`
  * `Q1_PTS`, `Q1_PLUS_MINUS`
  * `Q2_PTS`, `Q2_PLUS_MINUS`
  * `Q3_PTS`, `Q3_PLUS_MINUS`
  * `Q4_PTS`, `Q4_PLUS_MINUS`
* `fetch_player_quarter_splits_df(request)`

**Uso**

* Estratégias live:

  * Quem “começa quente” (Q1 alto)
  * Quem fecha jogos (Q4 PTS/PLUS_MINUS)
* Gatilhos para agentes: ex.: se mismatch forte no 3Q, ajustar posição live

---

## 5. Nodes NBA — Teams

### 5.1. `leaguedash_team_base_season.py`

**Função**
Node de estatísticas base por time.

**Principais componentes**

* `TeamBaseRequest`
* `TeamBaseStats`

  * `team_id`, `team_name`, `team_slug`, `season`, `last_update`
  * `games_played`, `wins`, `losses`, `season_win_rate`
  * `avg_points`, `avg_turnovers`, `avg_plus_minus`, `avg_rebounds`
  * `off_reb`, `def_reb`
  * `avg_assists`, `avg_steals`, `avg_blocks`
  * `fg_made`, `fg_attempted`, `fg_pct`
  * `fg3_made`, `fg3_attempted`, `fg3_pct`
  * `ft_made`, `ft_attempted`, `ft_pct`, `pf`
* `fetch_team_base_df(request)`
* `upsert_team_base_to_sqlite(df, sqlite_path, table_name="nba_teams_base_test")`

---

### 5.2. `leaguedash_team_advanced_season.py`

**Função**
Node de métricas avançadas por time.

**Principais componentes**

* `TeamAdvancedRequest`
* `TeamAdvancedStats`

  * `pace`, `off_rating`, `def_rating`, `net_rating`
  * `ast_pct`, `ast_tov`, `ast_ratio`
  * `oreb_pct`, `dreb_pct`, `reb_pct`
  * `tm_tov_pct`, `efg_pct`, `ts_pct`, `pie`
* `fetch_team_advanced_df(request)`
* `upsert_team_advanced_to_sqlite(df, table_name="nba_teams_advanced_test")`

**Uso**

* Perfil global do time (ritmo, eficiência, defesa/ataque)
* Combinar com odds para identificar spots de valor

---

### 5.3. `team_recent_form_last5.py`

**Função**
Node de **forma recente** (últimos 5 jogos).

**Principais componentes**

* `TeamLast5Request`
* `TeamLast5Stats`

  * `last_5_games_played`
  * `last_5_games_win_rate`
  * `last_5_avg_points`
  * `last_5_avg_turnover`
  * `last_5_avg_plus_minus`
* `compute_last5_metrics_df(request)`
* `upsert_team_last5_to_sqlite(df, table_name="nba_teams_last5_test")`

**Uso**

* Complementar análise de longo prazo (season) com momento recente
* Inputs para agentes que avaliam se “run recente” é sustentável vs pricing de mercado

---

### 5.4. `team_quarter_splits_byperiod.py`

**Função**
Node de splits por quarto para times.

**Principais componentes**

* `TeamQuarterSplitsRequest`
* `TeamQuarterSplitsStats`

  * `Q1_PTS`, `Q1_PLUS_MINUS`, …, `Q4_PTS`, `Q4_PLUS_MINUS`
* `fetch_quarter_splits_df(request)`
* `upsert_team_quarters_to_sqlite(df, table_name="nba_teams_quarter_splits_test")`

**Uso**

* Perfis de times por quarto (strong starters vs closers)
* Base para estratégia live focada em spreads por período

---

## 6. Nodes NBA — Live (TODO)

### 6.1. `live/live_stats.py` — Design

**Objetivo**

* Node para snapshot **live boxscore** de um jogo:

  * Placar, líderes de pontos/rebotes/assistências
  * Estatísticas agregadas por time ao longo do jogo

**Proposta de API**

* `LiveStatsRequest`

  * `game_id: str` (ID da NBA, ex.: `"0022500315"`)
  * `include_players: bool = True`
* `LiveTeamStats`

  * `team_id`, `team_slug`
  * `score`, `timeouts_remaining`, `fouls`, etc.
* `LivePlayerBoxScore`

  * `player_nba_id`, `player_name`, `team_id`
  * `minutes`, `points`, `rebounds`, `assists`, `fgm`, `fga`, `fg3m`, `fg3a`, `ftm`, `fta`
* Funções:

  * `fetch_live_stats(request: LiveStatsRequest) -> dict[Literal["home","away"], LiveTeamStats]`
  * `fetch_live_players_df(request) -> pd.DataFrame`

**Uso**

* Inputs para o **live monitor**
* Gatilhos como “jogador saiu com lesão” (minutos abruptamente baixos) etc.

---

### 6.2. `live/play_by_play.py` — Design

**Objetivo**

* Node de **play-by-play** live/pseudo-live:

  * Lista de eventos cronológicos (cestas, faltas, timeouts, runs…)

**Proposta de API**

* `PlayByPlayRequest`

  * `game_id: str`
  * `window_last_n_actions: Optional[int]` (ex.: 20)
* `PlayByPlayEvent`

  * `game_id`, `period`, `clock`, `event_type`, `description`
  * `team_id`, `player_nba_id`, `points_change`, `score_home`, `score_away`
* Funções:

  * `fetch_play_by_play_df(request) -> pd.DataFrame`
  * `compute_runs(df, lookback_actions=20) -> pd.DataFrame`
    (detectar “runs” tipo 12–0, 15–2 etc., usado nos seus testes de momentum)

**Uso**

* Live monitor:

  * Detectar corridas, mudança de momentum
  * Acionar agente Polymarket pra reavaliar posição

  * Acionar agente Polymarket pra reavaliar posição

---

## 7. Nodes HoopsStats (Scrapers)

### 7.1. Visão Geral

HoopsStats fornece dados complementares de "perfil", "dicas" (trends) e "streaks" que não estão disponíveis na API oficial da NBA.

### 7.2. `hoopsstats/profile_scraper_beautiful_soup.py`

**Função**
Coleta estatísticas agregadas de perfil (Ranking, Médias, Eficiência) para um time.

**Principais componentes**
* `scrape_data(team_slug, hs_id, season_code)`
* Retorna `ProfileData` contendo `TeamStatsAverages`.

**Uso**
* Enriquecer `nba_teams` com stats como `avg_efficiency`, `avg_rebounds` (fonte alternativa/validada).

### 7.3. `hoopsstats/tips_scraper_beautiful_soup.py`

**Função**
Coleta "Tips" (tendências de apostas) de um time.

**Principais componentes**
* `scrape_data(team_slug, hs_id, season_code)`
* Retorna lista de `TeamTip` (ex: "ATS Home: 10-5").

**Uso**
* Inserir insights textuais na tabela `nba_team_insights`.

### 7.4. `hoopsstats/streaks_scraper_beautiful_soup.py`

**Função**
Coleta "Streaks" (sequências de vitórias/derrotas/stats).

**Principais componentes**
* `scrape_data(team_slug, hs_id, season_code)`
* Retorna lista de `TeamStreak` (ex: "Won last 5 home games").

**Uso**
* Identificar momento (momentum) para estratégias.

---

## 7. Polymarket — Gamma (NBA)

### 7.1. `gamma/gamma_client.py`

**Função**

* Cliente HTTP central comum a todos os nodes Gamma.

**Principais componentes**

* `GammaClientSettings`

  * `base_url: str` (ex.: `https://gamma-api.polymarket.com`)
  * `timeout: float`
  * `retries: int`
* `GammaClient`

  * `_request(method, path, params=None, ...)`
  * `get_sports()`
  * (possíveis extensões: `get_events(...)`, `get_markets(...)` caso queira wrappers diretos)

**Uso**

* Injetado/instanciado dentro dos nodes:

  * `sports_metadata.py`
  * `events_node.py`
  * `markets_moneyline_node.py`
  * `teams_node.py`

---

### 7.2. `nba/sports_metadata.py`

**Função**

* Descobrir metadados da NBA dentro da Gamma.

**Principais componentes**

* `NBASportsMetadata` (Pydantic)

  * `sport: str = "nba"`
  * `tag_ids: List[int]` (ex.: `[1, 745, 100639]`)
  * `image: str`
  * `ordering: str` (ex.: `"away"`)
* `fetch_nba_sports_metadata(client: GammaClient) -> NBASportsMetadata`

**Uso**

* Fornece `tag_ids` e contexto para filtrar eventos/mercados da NBA.
* Ponto único de verdade para descobrir “como a Gamma marca NBA”.

---

### 7.3. `nba/events_node.py`

**Função**

* Node de **eventos NBA** na Gamma (jogos + prêmios).

**Principais componentes**

* `NBAEventType` (Enum)

  * `GAME`, `AWARD`, `OTHER`
* `NBAEvent` (Pydantic)

  * `event_id: int`
  * `slug: str`
  * `title: str`
  * `category: Optional[str]`
  * `subcategory: Optional[str]`
  * `event_type: NBAEventType`
  * `start_time`, `end_time` (`datetime`)
  * `closed: bool`
  * `enable_orderbook: bool`
  * `volume: float | None`
  * `liquidity: float | None`
  * `tags: List[int]`
  * `raw: dict`
* `NBAEventsRequest`

  * `include_closed: bool = False`
  * `limit: Optional[int]`
  * `event_types: Optional[List[NBAEventType]]`
* Funções:

  * `fetch_nba_events_df(request, client=None, metadata=None) -> pd.DataFrame`

    * Usa `sports_metadata` para filtrar tags da NBA
    * Normaliza para DataFrame com colunas:

      ```text
      event_id, slug, title, category, subcategory, event_type,
      start_time, end_time, closed, enable_orderbook,
      volume, liquidity, tags, raw
      ```

  * `upsert_nba_events_to_sqlite(df, sqlite_path, table_name="polymarket_nba_events")`

    * Serializa `raw` como JSON string
    * Garante compatibilidade com schema prévio (migrações leves)

**Uso**

* Popular tabela `events_nba` do projeto
* Descobrir:

  * Jogos do dia
  * Futures relevantes (MVP, ROY, etc.)
* Chave de ligação com markets moneyline (`event_id`, `slug`)

---

### 7.4. `nba/markets_moneyline_node.py`

**Função**

* Node de **mercados Moneyline** dos eventos NBA (quando houver dados).

**Principais componentes**

* `NBAMoneylineOutcome` (Pydantic)

  * `event_id`, `event_slug`
  * `market_id`, `market_slug`
  * `outcome` (ex.: `"home" / "away"`, ou outro label da Gamma)
  * `team_id: Optional[int]` (mapeamento interno futuro)
  * `team_abbr: Optional[str]`
  * `team_name: Optional[str]`
  * `last_price: float | None`
  * `implied_prob: float | None`
  * `token_id: Optional[str]`
  * `game_start_time: Optional[datetime]`
  * `closed: bool`
  * `enable_orderbook: bool`
  * `volume: float | None`
  * `liquidity: float | None`
  * `raw: dict`
* `NBAMoneylineRequest`

  * `include_closed: bool = False`
  * `event_ids: Optional[List[int]]`
  * `event_slugs: Optional[List[str]]`
* Funções:

  * `fetch_nba_moneyline_df(request, client=None, metadata=None) -> pd.DataFrame`
  * `upsert_nba_moneyline_to_sqlite(df, sqlite_path, table_name="polymarket_nba_moneyline")`

    * Lida com DF vazio sem quebrar (log de “nada a inserir”)

**Estado atual (observado nos testes)**

* A estrutura está pronta (colunas definidas, upsert ok).
* Em alguns runs recentes, não há linhas (`Shape: (0, 17)`), por falta de dados disponíveis da Gamma para NBA.

**Uso**

* Quando a Gamma estiver oferencendo moneylines NBA, esse node:

  * Alimenta tabela `markets_nba_moneyline`
  * Fornece os preços e `token_id` para integrar com blockchain/CLOB.

---

### 7.5. `nba/teams_node.py`

**Função**

* Node para metadados de **times** NBA via Gamma (quando o endpoint for útil).

**Principais componentes**

* `NBATeamGamma` (Pydantic)

  * `team_slug`, `team_name`, `tags`, etc. (dependendo do payload)
* `fetch_nba_teams_df(client, metadata) -> pd.DataFrame`
* `upsert_nba_teams_to_sqlite(df, ...)`

**Estado atual**

* Endpoint `/teams` tem retornado vazio para o filtro atual.
* Node tratado defensivamente:

  * DF vazio → não tenta criar tabela/insert (evita erro de schema)
  * Loga `WARN` e segue.

**Uso**

* Interessante como fonte secundária de metadados.
* **Fonte primária de times permanece sendo o módulo NBA “oficial”** (`leaguedash_team_*`).

---

## 8. Polymarket — Blockchain / CLOB

### 8.1. `blockchain/manage_portfolio.py`

**Objetivo**

* Node de alto nível para **visualizar e gerenciar posições** no Polymarket:

  * Ver posições abertas / fechadas (via Data-API)
  * Ver ordens abertas (via CLOB)
  * Colocar novas ordens (via CLOB)
  * Fechar/cancelar ordens (via CLOB)

**Componentes Implementados**

* `PolymarketCredentials` (Pydantic)

  * Gerencia chaves L1 (PK) e L2 (API Key/Secret/Passphrase)
  * Suporte a Proxy Wallet (funder) e tipos de assinatura (EOA/Magic/Browser)

* **Models (Pydantic)**

  * `OpenPosition`: Dados normalizados de posição (tamanho, preço médio, PnL)
  * `ClosedPosition`: Dados de posições encerradas
  * `OpenOrder`: Ordem no book (Limit)
  * `PlaceOrderRequest`: Payload para envio de ordem (market_id/token_id, side, size, price)
  * `PlaceOrderResult`: Retorno da operação (sucesso/falha + raw response)

* **Funções principais**

  * `view_open_positions(creds) -> List[OpenPosition]`
  * `view_closed_positions(creds) -> List[ClosedPosition]`
  * `view_orders(creds) -> List[OpenOrder]`
  * `place_new_order(creds, request) -> PlaceOrderResult`
  * `cancel_order(creds, order_id) -> PlaceOrderResult`

**Scripts de Teste (Exemplos)**

* `tests/test_simple_buy.py`: Compra simples (Limit) em mercado específico.
* `tests/test_simple_sell.py`: Venda simples (Limit) para encerrar posição.
* `tests/test_den_cha_buy.py`: Teste integrado de compra (NBA Hornets YES).
* `tests/test_den_cha_sell.py`: Teste integrado de venda (NBA Hornets YES).

**Uso**

* Camada base para:

  * Agents que operam o portfólio
  * CLI de inspeção e gestão
  * Live monitor (abrir/fechar posições baseado em triggers de jogo/odds)

---

### 8.2. `blockchain/stream_orderbook.py` — Design

**Objetivo**

* Node para **stream / polling** do orderbook (CLOB) de um mercado específico:

  * Obter snapshots de bid/ask e volume
  * Detectar mudanças relevantes (spread, desequilíbrio, walls, trades grandes)

**Proposta de componentes**

* `OrderbookSide` (Enum)

  * `BID`, `ASK`
* `OrderbookLevel`

  * `price: float`
  * `size: float`
  * `num_orders: int | None`
* `OrderbookSnapshot`

  * `market_id`
  * `token_id`
  * `timestamp: datetime`
  * `bids: List[OrderbookLevel]`
  * `asks: List[OrderbookLevel]`
* `OrderbookStreamConfig`

  * `market_id`, `token_id`
  * `poll_interval_seconds: float`
  * `max_iterations: Optional[int]`
* Funções:

  * `fetch_orderbook_once(config, creds) -> OrderbookSnapshot`
  * `stream_orderbook(config, creds, callback: Callable[[OrderbookSnapshot], None])`

    * Loop de polling (por enquanto)
    * Futuro: suporte a WebSocket se a API oferecer

**Uso**

* Live monitor:

  * Detectar “crash” de odds, entrada de whale, secagem de liquidez
  * Alimentar lógica de:

    * Ajustar tamanho da posição
    * Rebalancear risco entre mercados correlacionados
  * Pode acionar o agente de análise para reavaliar fundamentos quando houver movimentos anômalos.

---

## 9. Nodes Jina AI

### 9.1. `jinaai/reader.py`

**Função**
Node para converter URLs em Markdown limpo usando a API Jina Reader.

**Principais componentes**

* `JinaReaderRequest`
  * `url: str`
* `JinaReaderResponse`
  * `url`, `content` (markdown), `title`, `description`
* `fetch_jina_reader(request) -> JinaReaderResponse`

**Uso**
* Extrair conteúdo de notícias, artigos ou páginas de estatísticas para enriquecer o contexto dos agentes.

### 9.2. `jinaai/search.py`

**Função**
Node para realizar buscas na web usando a API Jina Search.

**Principais componentes**

* `JinaSearchRequest`
  * `query: str`
  * `limit: int`
* `JinaSearchResult`
  * `title`, `url`, `description`, `content`
* `fetch_jina_search(request) -> JinaSearchResponse`

**Uso**
* Pesquisar notícias recentes sobre lesões, rumores de trocas ou análises de matchups.

---

## 10. Integração e Tabelas Lógicas

Mesmo usando SQLite nos testes, a visão final é ter **tabelas lógicas** estáveis, idealmente em Postgres em produção.

### 9.1. Tabelas-alvo (conceituais)

| Tabela lógica                | Populado por                           | Chaves principais                     |
| ---------------------------- | -------------------------------------- | ------------------------------------- |
| `nba_players_base`           | `leaguedash_player_base_season.py`     | `player_nba_id`, `season`             |
| `nba_players_advanced`       | `leaguedash_player_advanced_season.py` | `player_nba_id`, `season`             |
| `nba_players_usage`          | `leaguedash_player_usage_season.py`    | `player_nba_id`, `season`             |
| `nba_players_quarter_splits` | `player_quarter_splits_byperiod.py`    | `player_nba_id`, `season`             |
| `nba_teams_base`             | `leaguedash_team_base_season.py`       | `team_id`, `season`                   |
| `nba_teams_advanced`         | `leaguedash_team_advanced_season.py`   | `team_id`, `season`                   |
| `nba_teams_last5`            | `team_recent_form_last5.py`            | `team_id`, `season`                   |
| `nba_teams_quarter_splits`   | `team_quarter_splits_byperiod.py`      | `team_id`, `season`                   |
| `nba_live_stats`             | `live_stats.py`                        | `game_id`, `timestamp`                |
| `nba_play_by_play`           | `play_by_play.py`                      | `game_id`, `event_index` / `event_id` |
| `polymarket_nba_events`      | `events_node.py`                       | `event_id`                            |
| `polymarket_nba_moneyline`   | `markets_moneyline_node.py`            | `market_id`, `token_id`               |
| `polymarket_positions`       | `manage_portfolio.py`                  | `position_id`                         |
| `polymarket_orders`          | `manage_portfolio.py`                  | `order_id`                            |
| `polymarket_orderbook`       | `stream_orderbook.py`                  | `market_id`, `token_id`, `timestamp`  |

---

## 11. Testes e Runner Integrado

### 10.1. Scripts de teste por node

* Cada pasta `tests/` contém `test_*_node.py` no formato CLI:

  * Prints ricos (headers, preview de DataFrame, contagens)
  * Saída “OK” / “FAIL” clara
  * Foco em:

    * Fetch básico
    * Filtros (team_slugs, player_id, event_type, etc.)
    * Pydantic round-trip (model → DF)
    * Upsert em SQLite

### 10.2. `run_all_tests.py`

* Runner integrado com `argparse`:

  * `--module nba` → roda apenas testes de nodes NBA
  * `--module polymarket` → roda apenas nodes Polymarket
  * `--module all` → todos
  * `--print-output`:

    * `none`   → só resumo
    * `full`   → stdout/stderr completo no terminal

* Agrupa logs em:

  ```text
  logs/tests/run_all_tests_<module>_<timestamp>.log
  ```

* Implementa:

  * Execução via `subprocess.run`
  * Coleta de `rc` e resumo final (Total / OK / FAIL)

---

## 12. Como Estender (Novos Nodes / Novos Esportes / Novos Mercados)

Para adicionar novos nodes mantendo consistência:

1. **Escolher o domínio**

   * `nba` (novo tipo de stat)
   * `polymarket/gamma` (novo mercado/filtro)
   * `polymarket/blockchain` (nova operação de portfolio)

2. **Criar arquivo em local adequado**

   * `app/data/nodes/nba/...`
   * `app/data/nodes/polymarket/gamma/...`
   * `app/data/nodes/polymarket/blockchain/...`

3. **Definir Pydantic models**

   * `Request` com parâmetros de entrada
   * `Stats` / `Record` com saída normalizada

4. **Implementar fetch → DataFrame**

   * Usar client dedicado (NBA API, GammaClient, CLOB client)
   * Garantir colunas estáveis e tipos corretos

5. **Implementar upsert**

   * Função `upsert_*_to_sqlite` (e, futuramente, Postgres)
   * Serializar campos complexos (`raw`, listas) como JSON string

6. **Criar teste integrado**

   * Em `tests/test_<nome>_node.py`
   * Seguir o formato dos testes existentes:

     * Header claro
     * `TEST 1, TEST 2, ...`
     * Preview de shapes/colunas
     * Upsert testando inserção real

7. **Registrar no runner**

   * Garantir que o padrão `test_*.py` seja respeitado
   * Usar `run_all_tests.py --module ...` para validar integração

---

## 12. Status Atual e Próximos Passos

### 12.1. Status atual (conforme último run de testes)

* **NBA nodes (players + teams)** → ✅ Todos passando (4 + 4 testes)
* **Polymarket Gamma NBA nodes** → ✅ Todos passando (events, moneyline, sports_metadata, teams, client)

  * `events_node` já puxa eventos NBA (`GAME`, `AWARD`) com sucesso
  * `markets_moneyline_node` está pronto, mas pode receber DF vazio dependendo da oferta atual da Gamma
  * `teams_node` configurado defensivamente para DF vazio
* **Runner integrado (`run_all_tests.py`)** → ✅ cobrindo `module=all`, `nba`, `polymarket`

### 12.2. Próximos passos recomendados

1. **Implementar nodes live NBA**

   * `live_stats.py`
   * `play_by_play.py`
   * Conectar com lógica de momentum / runs que você já vem testando

2. **Implementar nodes blockchain / CLOB**

   * `manage_portfolio.py`
   * `stream_orderbook.py`
   * Garantir que os `token_id` / `market_id` usados aqui sejam exatamente os retornados por `markets_moneyline_node.py`

3. **Criar um “Matchup View Node” (futuro)**

   * Ex: `app/data/nodes/nba/matchup_view.py`
   * Função: dado um game_id ou slug Polymarket:

     * Puxa:

       * Stats de times/jogadores (NBA nodes)
       * Evento e mercados na Gamma (events + moneyline nodes)
     * Retorna um `MatchupSnapshot` Pydantic unificado para o agente.

---


