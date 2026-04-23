'use strict';

(function () {
  const DEFAULT_API_ROOT = '/v1/nba/live';
  const DEFAULT_RUN_ID = 'live-2026-04-23-v1';
  const DEFAULT_POLL_MS = 5000;

  const state = {
    apiRoot: DEFAULT_API_ROOT,
    runId: DEFAULT_RUN_ID,
    pollMs: DEFAULT_POLL_MS,
    connected: false,
    demo: true,
    timer: null,
    data: null,
    lastError: '',
  };

  function bootShell() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="app-shell">
        <header class="hero">
          <div>
            <p class="eyebrow">Janus Cortex / Live Control</p>
            <h1>Playoff execution monitor</h1>
            <p class="lede">Minimal operator-first surface for a single live run: status, three game cards, open positions, open orders, recent events, and slippage/fill summary.</p>
            <div class="hero-pills" id="hero-pills">
              <span class="pill">Disconnected</span>
              <span class="pill">Demo fallback ready</span>
              <span class="pill">Polling-friendly</span>
            </div>
          </div>
          <form id="connection-form" class="control-panel">
            <label><span>API Root</span><input id="api-root" name="api_root" placeholder="/v1/nba/live"></label>
            <label><span>Run ID</span><input id="run-id" name="run_id" placeholder="live-2026-04-23-v1"></label>
            <label><span>Poll Interval</span><input id="poll-ms" name="poll_ms" type="number" min="2000" step="1000" value="5000"></label>
            <label class="control-span"><span>Notes</span><input id="operator-notes" name="operator_notes" placeholder="Today s locked controller session"></label>
            <div class="control-actions">
              <button id="connect-button" type="submit">Connect</button>
              <button id="demo-button" type="button" class="ghost-button">Load demo</button>
              <button id="pause-button" type="button" class="ghost-button" disabled>Pause entries</button>
              <button id="resume-button" type="button" class="ghost-button" disabled>Resume entries</button>
              <button id="stop-button" type="button" class="ghost-button danger" disabled>Stop run</button>
            </div>
            <div class="status-row">
              <p id="status-line" class="status-line">Disconnected. Load demo or connect to a live run.</p>
              <p id="last-update" class="meta">Last update: waiting.</p>
            </div>
          </form>
        </header>

        <main class="dashboard">
          <section class="panel">
            <div class="section-header">
              <div>
                <p class="section-kicker">Run Status</p>
                <h2>Current control state</h2>
              </div>
              <p class="section-meta" id="run-summary-meta">No live run loaded.</p>
            </div>
            <div id="run-status-grid" class="metric-grid"></div>
          </section>

          <section class="panel">
            <div class="section-header">
              <div>
                <p class="section-kicker">Games</p>
                <h2>Three-game control board</h2>
              </div>
              <p class="section-meta">Built for today s slate and future live sessions.</p>
            </div>
            <div id="game-cards" class="game-grid"></div>
          </section>

          <section class="split-layout">
            <article class="panel">
              <div class="section-header">
                <div>
                  <p class="section-kicker">Positions</p>
                  <h2>Open positions</h2>
                </div>
                <p class="section-meta">Current exposure and live PnL.</p>
              </div>
              <div id="positions-table" class="table-shell"></div>
            </article>
            <article class="panel">
              <div class="section-header">
                <div>
                  <p class="section-kicker">Orders</p>
                  <h2>Open orders</h2>
                </div>
                <p class="section-meta">Pending entries, exits, and stop activity.</p>
              </div>
              <div id="orders-table" class="table-shell"></div>
            </article>
          </section>

          <section class="split-layout">
            <article class="panel">
              <div class="section-header">
                <div>
                  <p class="section-kicker">Events</p>
                  <h2>Recent events and errors</h2>
                </div>
                <p class="section-meta">Live feed for decisions, fills, cancellations, and failures.</p>
              </div>
              <div id="events-log" class="log-shell"></div>
            </article>
            <article class="panel">
              <div class="section-header">
                <div>
                  <p class="section-kicker">Execution</p>
                  <h2>Slippage and fill summary</h2>
                </div>
                <p class="section-meta">A quick read on fill quality and adverse movement.</p>
              </div>
              <div id="fill-summary" class="summary-shell"></div>
            </article>
          </section>
        </main>
      </div>
    `;
  }

  function q(id) {
    return document.getElementById(id);
  }

  function text(value, fallback = '-') {
    if (value === null || value === undefined || value === '') return fallback;
    return String(value);
  }

  function money(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `$${n.toFixed(2)}` : '-';
  }

  function pct(value, digits = 1) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(digits)}%` : '-';
  }

  function integer(value) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.round(n).toLocaleString() : '-';
  }

  function demoData() {
    return {
      run: {
        run_id: state.runId,
        status: 'demo',
        controller: 'controller_vnext_unified_v1 :: balanced',
        fallback: 'controller_vnext_deterministic_v1 :: tight',
        active_games: 3,
        open_orders: 2,
        open_positions: 1,
        current_bankroll: 11.24,
        starting_bankroll: 10,
        drawdown_pct: 8.2,
        drawdown_amount: 0.83,
      },
      games: [
        {
          game_id: '0042500123',
          matchup: 'New York at Atlanta',
          clock: 'Q1 08:41',
          controller: 'controller_vnext_unified_v1 :: balanced',
          family: 'winner_definition',
          confidence: 0.71,
          status: 'entry queued',
          action: 'buy limit',
          stop: '5c below entry',
          pnl: 0.18,
          bid: 0.43,
          ask: 0.45,
          open_orders: 1,
          open_positions: 1,
          last_event: 'Entry queued after live confidence threshold passed.',
          fill: 'partial',
        },
        {
          game_id: '0042500133',
          matchup: 'Cleveland at Toronto',
          clock: 'Q2 03:12',
          controller: 'controller_vnext_unified_v1 :: balanced',
          family: 'inversion',
          confidence: 0.64,
          status: 'monitoring',
          action: 'wait',
          stop: '5c below entry',
          pnl: -0.06,
          bid: 0.37,
          ask: 0.39,
          open_orders: 1,
          open_positions: 0,
          last_event: 'Live trigger met but spread was too wide; monitoring next cycle.',
          fill: 'pending',
        },
        {
          game_id: '0042500163',
          matchup: 'Denver at Minnesota',
          clock: 'Pregame',
          controller: 'controller_vnext_deterministic_v1 :: tight',
          family: 'skip',
          confidence: 0.32,
          status: 'skip',
          action: 'no trade',
          stop: '-',
          pnl: 0,
          bid: null,
          ask: null,
          open_orders: 0,
          open_positions: 0,
          last_event: 'No qualifying signal yet; staying flat.',
          fill: 'none',
        },
      ],
      positions: [
        { game: 'New York at Atlanta', market: 'NYK moneyline', side: 'buy', size: '$1.00', entry: '$0.44', mark: '$0.47', pnl: '+$0.03', status: 'open' },
        { game: 'Cleveland at Toronto', market: 'CLE moneyline', side: 'buy', size: '$1.00', entry: '$0.38', mark: '$0.36', pnl: '-$0.02', status: 'open' },
      ],
      orders: [
        { game: 'New York at Atlanta', market: 'NYK moneyline', type: 'limit buy', price: '$0.44', qty: '5 shares', status: 'working', age: '00:21' },
        { game: 'Cleveland at Toronto', market: 'CLE moneyline', type: 'stop-market sell', price: 'trigger @ $0.33', qty: '5 shares', status: 'armed', age: '00:06' },
      ],
      events: [
        { time: '20:01', level: 'info', title: 'Live run created', message: 'Controller booted with entries enabled and polling active.' },
        { time: '20:04', level: 'warn', title: 'Spread filter hit', message: 'One candidate skipped because best ask spread was wider than the live threshold.' },
        { time: '20:06', level: 'error', title: 'Order retry pending', message: 'A cancel or replace path is waiting on a fresh quote before the next cycle.' },
      ],
      fills: [
        { label: 'Fill rate', value: 67, suffix: '%' },
        { label: 'Avg slippage', value: 0.03, suffix: 'c', warn: true },
        { label: 'Median delay', value: 8, suffix: 's' },
        { label: 'Stop hits', value: 1, suffix: '' },
      ],
    };
  }

  function normalizeSnapshot(payload) {
    const root = payload && typeof payload === 'object' ? (payload.data || payload.run || payload.snapshot || payload) : {};
    const games = Array.isArray(root.games || payload.games) ? (root.games || payload.games) : [];
    return {
      run: {
        run_id: text(root.run_id || payload.run_id || state.runId),
        status: text(root.status || root.run_status || payload.status || 'idle'),
        controller: text(root.controller_name || root.controller || 'controller_vnext_unified_v1 :: balanced'),
        fallback: text(root.fallback_controller_name || root.fallback_controller || 'controller_vnext_deterministic_v1 :: tight'),
        active_games: Number(root.active_games ?? games.length) || games.length,
        open_orders: Number(root.open_orders ?? payload.open_orders ?? 0) || 0,
        open_positions: Number(root.open_positions ?? payload.open_positions ?? 0) || 0,
        current_bankroll: root.current_bankroll ?? payload.current_bankroll ?? 10,
        starting_bankroll: root.starting_bankroll ?? payload.starting_bankroll ?? 10,
        drawdown_pct: root.drawdown_pct ?? payload.drawdown_pct ?? null,
        drawdown_amount: root.drawdown_amount ?? payload.drawdown_amount ?? null,
      },
      games: games.slice(0, 3).map((game, index) => ({
        game_id: text(game.game_id || game.id || `game-${index + 1}`),
        matchup: text(game.matchup || game.label || game.title || `Game ${index + 1}`),
        clock: text(game.clock || game.status || game.game_clock || 'waiting'),
        controller: text(game.controller || game.controller_name || 'controller_vnext_unified_v1 :: balanced'),
        family: text(game.family || game.strategy_family || 'winner_definition'),
        confidence: game.confidence ?? game.selected_confidence ?? null,
        status: text(game.status_text || game.state || game.state_label || 'idle'),
        action: text(game.action || game.selected_action || 'monitor'),
        stop: text(game.stop || game.stop_price || '-'),
        pnl: game.pnl ?? game.realized_pnl ?? null,
        bid: game.bid ?? game.best_bid ?? null,
        ask: game.ask ?? game.best_ask ?? null,
        open_orders: game.open_orders ?? game.open_order_count ?? null,
        open_positions: game.open_positions ?? game.open_position_count ?? null,
        last_event: text(game.last_event || game.note || 'No live signal yet.'),
        fill: text(game.fill || game.fill_state || 'pending'),
      })),
      positions: Array.isArray(root.positions || payload.positions) ? (root.positions || payload.positions) : [],
      orders: Array.isArray(root.orders || payload.orders) ? (root.orders || payload.orders) : [],
      events: Array.isArray(root.events || payload.events) ? (root.events || payload.events) : [],
      fills: Array.isArray(root.fills || payload.fills) ? (root.fills || payload.fills) : [],
    };
  }

  function setHeaderState(connected, demo) {
    const pills = q('hero-pills');
    if (!pills) return;
    pills.innerHTML = '';
    const items = [
      connected ? (demo ? 'Demo connected' : 'Live connected') : 'Disconnected',
      demo ? 'Demo fallback ready' : 'Polling live endpoint',
      `Poll ${Math.round(state.pollMs / 1000)}s`,
    ];
    items.forEach((label, index) => {
      const span = document.createElement('span');
      span.className = `pill${index === 0 && connected ? ' live' : ''}`;
      span.textContent = label;
      pills.appendChild(span);
    });
  }

  function setStatus(message, isError = false) {
    const node = q('status-line');
    if (node) {
      node.textContent = message;
      node.style.color = isError ? '#ffd1d1' : '';
    }
  }

  function setLastUpdate(message) {
    const node = q('last-update');
    if (node) node.textContent = message;
  }

  function renderMetrics(run) {
    const grid = q('run-status-grid');
    grid.innerHTML = '';
    [
      ['Run', run.run_id || '-', run.status || 'idle'],
      ['Controller', run.controller || '-', run.fallback ? `Fallback: ${run.fallback}` : ''],
      ['Exposure', `${run.active_games || 0} games`, `${run.open_orders || 0} open orders / ${run.open_positions || 0} open positions`],
      ['Bankroll', money(run.current_bankroll ?? run.starting_bankroll), `Start ${money(run.starting_bankroll)} | DD ${pct(run.drawdown_pct, 1)} (${money(run.drawdown_amount)})`],
      ['Heartbeat', text(run.last_heartbeat_at, '-'), `Last good cycle ${text(run.last_successful_cycle_at, '-')}`],
      ['Cycle', integer(run.cycle_count), `Last duration ${run.last_cycle_duration_seconds !== null && run.last_cycle_duration_seconds !== undefined ? `${Number(run.last_cycle_duration_seconds).toFixed(2)}s` : '-'}`],
      ['Logs', text(run.run_root, '-'), run.log_paths ? `runtime.log and last_error.txt under run root` : ''],
      ['Last Error', text(run.last_error, 'none'), run.last_traceback ? 'See last_error.txt for traceback' : ''],
    ].forEach(([label, value, caption]) => {
      const card = document.createElement('article');
      card.className = 'metric-card';
      card.innerHTML = `<span class="metric-label">${label}</span><strong class="metric-value">${value}</strong>${caption ? `<span class="metric-caption">${caption}</span>` : ''}`;
      grid.appendChild(card);
    });
  }

  function renderGames(games) {
    const grid = q('game-cards');
    grid.innerHTML = '';
    if (!games.length) {
      grid.innerHTML = '<div class="empty-state">No games loaded yet.</div>';
      return;
    }
    games.forEach((game) => {
      const active = ['entry queued', 'monitoring', 'working'].includes(game.status);
      const warn = ['error', 'skip'].includes(game.status);
      const card = document.createElement('article');
      card.className = 'game-card';
      card.innerHTML = `
        <div class="game-head">
          <div>
            <h3 class="game-title">${game.matchup}</h3>
            <p class="card-meta">Game ${game.game_id}</p>
          </div>
          <div class="game-clock">${game.clock}</div>
        </div>
        <div class="status-chip ${active ? 'active' : warn ? 'warn' : 'idle'}"><span class="dot"></span><span>${game.status}</span></div>
        <div class="game-metrics">
          ${mini('Controller', game.controller)}
          ${mini('Family', game.family)}
          ${mini('Confidence', Number.isFinite(Number(game.confidence)) ? `${(Number(game.confidence) * 100).toFixed(0)}%` : '-')}
          ${mini('Action', game.action)}
          ${mini('Best Bid', money(game.bid))}
          ${mini('Best Ask', money(game.ask))}
          ${mini('Stop', game.stop)}
          ${mini('P&L', money(game.pnl))}
        </div>
        <p class="card-meta">Open orders: ${integer(game.open_orders)} | Open positions: ${integer(game.open_positions)} | Fill: ${game.fill}</p>
        <p class="summary-note">${game.last_event}</p>
      `;
      grid.appendChild(card);
    });
  }

  function mini(label, value) {
    return `<div class="mini-card"><span class="mini-label">${label}</span><span class="mini-value">${value}</span></div>`;
  }

  function renderTable(node, columns, rows) {
    node.innerHTML = '';
    if (!rows.length) {
      node.innerHTML = '<div class="empty-state">No rows available.</div>';
      return;
    }
    const table = document.createElement('table');
    table.className = 'table';
    table.innerHTML = `
      <thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join('')}</tr></thead>
      <tbody>
        ${rows.map((row) => `<tr>${columns.map((c) => `<td${c.mono ? ' class="mono"' : ''}>${formatCell(row[c.key], c.format)}</td>`).join('')}</tr>`).join('')}
      </tbody>
    `;
    node.appendChild(table);
  }

  function formatCell(value, format) {
    if (format === 'money') return money(value);
    if (format === 'pct') return pct(value, 1);
    if (format === 'int') return integer(value);
    return text(value);
  }

  function renderEvents(rows) {
    const node = q('events-log');
    node.innerHTML = '';
    if (!rows.length) {
      node.innerHTML = '<div class="empty-state">No recent events.</div>';
      return;
    }
    rows.slice(0, 12).forEach((row) => {
      const item = document.createElement('article');
      item.className = 'log-item';
      item.innerHTML = `
        <div class="log-top">
          <div>
            <h3 class="log-title">${text(row.level || 'info').toUpperCase()} | ${text(row.title || row.type || 'event')}</h3>
            <p class="log-message">${text(row.message || row.detail || '')}</p>
          </div>
          <div class="log-time">${text(row.time || row.created_at || row.timestamp || nowLabel())}</div>
        </div>
      `;
      node.appendChild(item);
    });
  }

  function renderSummary(run, fills) {
    const node = q('fill-summary');
    node.innerHTML = '';
    const byLabel = Object.fromEntries(fills.map((item) => [item.label, item]));
    const rows = [
      ['Fill rate', `${text(byLabel['Fill rate']?.value || '-')}${byLabel['Fill rate']?.suffix || ''}`],
      ['Avg slippage', `${text(byLabel['Avg slippage']?.value || '-')}${byLabel['Avg slippage']?.suffix || ''}`],
      ['Median delay', `${text(byLabel['Median delay']?.value || '-')}${byLabel['Median delay']?.suffix || ''}`],
      ['Stop hits', `${text(byLabel['Stop hits']?.value || '-')}${byLabel['Stop hits']?.suffix || ''}`],
    ];
    const grid = document.createElement('div');
    grid.className = 'summary-grid';
    rows.forEach(([label, value]) => {
      const card = document.createElement('article');
      card.className = 'summary-card';
      card.innerHTML = `<span class="card-label">${label}</span><strong>${value}</strong>`;
      grid.appendChild(card);
    });
    const note = document.createElement('p');
    note.className = 'summary-note';
    note.textContent = `Current bankroll ${money(run.current_bankroll ?? run.starting_bankroll)} against a ${money(run.starting_bankroll)} start.`;
    const list = document.createElement('div');
    list.className = 'bar-list';
    fills.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'bar-row';
      const width = Math.max(2, Math.min(100, Number(item.value) || 0));
      row.innerHTML = `
        <div class="bar-label">${item.label}</div>
        <div class="bar-track"><div class="bar-fill ${item.warn ? 'warn' : ''}" style="width:${width}%"></div></div>
        <div class="bar-value">${text(item.value)}${item.suffix || ''}</div>
      `;
      list.appendChild(row);
    });
    node.append(grid, note, list);
  }

  function renderAll(data) {
    state.data = data;
    renderMetrics(data.run);
    renderGames(data.games);
    renderTable(q('positions-table'), [
      { label: 'Game', key: 'game' },
      { label: 'Market', key: 'market' },
      { label: 'Side', key: 'side' },
      { label: 'Size', key: 'size' },
      { label: 'Entry', key: 'entry' },
      { label: 'Mark', key: 'mark' },
      { label: 'PnL', key: 'pnl', mono: true },
      { label: 'Status', key: 'status' },
    ], data.positions);
    renderTable(q('orders-table'), [
      { label: 'Game', key: 'game' },
      { label: 'Market', key: 'market' },
      { label: 'Type', key: 'type' },
      { label: 'Price', key: 'price' },
      { label: 'Qty', key: 'qty' },
      { label: 'Status', key: 'status' },
      { label: 'Age', key: 'age', mono: true },
    ], data.orders);
    renderEvents(data.events);
    renderSummary(data.run, data.fills);
    setLastUpdate(`Last update: ${nowLabel()}`);
    q('run-summary-meta').textContent = `${data.run.run_id} | ${integer(data.games.length)} games | ${integer(data.run.open_orders)} open orders`;
    if (state.lastError) {
      setStatus(`Polling issue: ${state.lastError}`, true);
    } else {
      setStatus(`${state.demo ? 'demo' : 'live'} connected | ${data.run.status || 'idle'} | ${data.run.controller || 'controller_vnext_unified_v1 :: balanced'}`);
    }
    setHeaderState(state.connected, state.demo);
  }

  async function fetchJson(path) {
    const response = await fetch(path, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function postJson(path) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function loadLive() {
    const root = state.apiRoot.replace(/\/$/, '');
    const runPath = `${root}/runs/${encodeURIComponent(state.runId)}`;
    const gamesPath = `${root}/runs/${encodeURIComponent(state.runId)}/games`;
    const ordersPath = `${root}/runs/${encodeURIComponent(state.runId)}/orders`;
    const eventsPath = `${root}/runs/${encodeURIComponent(state.runId)}/events`;
    const summaryPath = `${root}/runs/${encodeURIComponent(state.runId)}/summary`;

    const [run, games, orders, events, summary] = await Promise.allSettled([
      fetchJson(runPath),
      fetchJson(gamesPath),
      fetchJson(ordersPath),
      fetchJson(eventsPath),
      fetchJson(summaryPath),
    ]);

    return {
      ...normalizeSnapshot({
        ...(run.status === 'fulfilled' ? run.value : {}),
        games: games.status === 'fulfilled' ? (games.value.games || games.value.items || games.value) : undefined,
        positions: orders.status === 'fulfilled' ? (orders.value.positions || []) : undefined,
        orders: orders.status === 'fulfilled' ? (orders.value.orders || orders.value.items || orders.value) : undefined,
        events: events.status === 'fulfilled' ? (events.value.events || events.value.items || events.value) : undefined,
        fills: summary.status === 'fulfilled' ? (summary.value.fills || summary.value.items || summary.value) : undefined,
      }),
    };
  }

  async function tick() {
    if (!state.connected) return;
    try {
      const data = state.demo ? demoData() : await loadLive();
      state.lastError = '';
      renderAll(data);
    } catch (error) {
      state.lastError = error.message;
      setStatus(`Polling issue: ${error.message}`, true);
      renderAll(demoData());
    } finally {
      if (state.timer) clearTimeout(state.timer);
      state.timer = setTimeout(tick, state.pollMs);
    }
  }

  function connect(demo) {
    state.apiRoot = q('api-root').value.trim() || DEFAULT_API_ROOT;
    state.runId = q('run-id').value.trim() || DEFAULT_RUN_ID;
    state.pollMs = Math.max(2000, Number(q('poll-ms').value) || DEFAULT_POLL_MS);
    state.demo = demo;
    state.connected = true;
    state.lastError = '';
    setControlButtons(true);
    setHeaderState(true, demo);
    setStatus(demo ? 'Demo mode active.' : `Polling ${state.runId} at ${state.apiRoot}`);
    renderAll(demo ? demoData() : (state.data || demoData()));
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(tick, 0);
  }

  function initFormDefaults() {
    const params = new URLSearchParams(window.location.search);
    q('api-root').value = params.get('apiRoot') || params.get('api_root') || DEFAULT_API_ROOT;
    q('run-id').value = params.get('runId') || params.get('run_id') || DEFAULT_RUN_ID;
    q('poll-ms').value = params.get('pollMs') || params.get('poll_ms') || DEFAULT_POLL_MS;
    q('operator-notes').value = params.get('notes') || '';
  }

  function bindActions() {
    q('connection-form').addEventListener('submit', function (event) {
      event.preventDefault();
      connect(false);
    });
    q('demo-button').addEventListener('click', function () {
      connect(true);
    });
    q('pause-button').addEventListener('click', function () {
      const root = state.apiRoot.replace(/\/$/, '');
      postJson(`${root}/runs/${encodeURIComponent(state.runId)}/pause-entries`)
        .then(function () {
          setStatus('Pause entries requested from the live surface.');
          tick();
        })
        .catch(function (error) {
          setStatus(`Pause failed: ${error.message}`, true);
        });
    });
    q('resume-button').addEventListener('click', function () {
      const root = state.apiRoot.replace(/\/$/, '');
      postJson(`${root}/runs/${encodeURIComponent(state.runId)}/resume-entries`)
        .then(function () {
          setStatus('Resume entries requested from the live surface.');
          tick();
        })
        .catch(function (error) {
          setStatus(`Resume failed: ${error.message}`, true);
        });
    });
    q('stop-button').addEventListener('click', function () {
      const root = state.apiRoot.replace(/\/$/, '');
      postJson(`${root}/runs/${encodeURIComponent(state.runId)}/stop`)
        .then(function () {
          setStatus('Stop requested. Waiting for the backend runner to acknowledge.');
          tick();
        })
        .catch(function (error) {
          setStatus(`Stop failed: ${error.message}`, true);
        });
    });
  }

  function setControlButtons(enabled) {
    q('pause-button').disabled = !enabled;
    q('resume-button').disabled = !enabled;
    q('stop-button').disabled = !enabled;
  }

  function finishSetup() {
    initFormDefaults();
    bindActions();
    setControlButtons(false);
    setHeaderState(false, true);
    renderAll(demoData());
  }

  bootShell();
  finishSetup();
})();
