'use strict';

(function () {
  const API_ROOT = '/v1/analysis/studio';
  const FINALIST_LIMIT = 6;

  const els = {
    form: document.getElementById('snapshot-form'),
    season: document.getElementById('season'),
    seasonPhase: document.getElementById('season_phase'),
    analysisVersion: document.getElementById('analysis_version'),
    outputRoot: document.getElementById('output_root'),
    loadButton: document.getElementById('load-button'),
    statusLine: document.getElementById('status-line'),
    errorLine: document.getElementById('error-line'),
    meta: document.getElementById('snapshot-meta'),
    chart: document.getElementById('strategy-line-chart'),
    legend: document.getElementById('strategy-line-legend'),
    rankingTable: document.getElementById('finalist-ranking-table'),
    masterSummary: document.getElementById('master-router-summary'),
    routeBandTable: document.getElementById('route-band-table'),
    llmVariantTable: document.getElementById('llm-variant-table'),
    comparisonForm: document.getElementById('comparison-form'),
    comparisonFamily: document.getElementById('comparison_family'),
    comparisonTradeLimit: document.getElementById('comparison_trade_limit'),
    comparisonContextLimit: document.getElementById('comparison_context_limit'),
    comparisonTraceLimit: document.getElementById('comparison_trace_limit'),
    comparisonButton: document.getElementById('comparison-button'),
    comparisonStatusLine: document.getElementById('comparison-status-line'),
    comparisonIndex: document.getElementById('comparison-index'),
    comparisonDetail: document.getElementById('comparison-detail'),
  };

  const state = {
    snapshot: null,
    finalists: [],
    selectedFamily: '',
  };

  function toNumber(value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatNumber(value, digits = 2) {
    const parsed = toNumber(value);
    return parsed === null ? '—' : parsed.toFixed(digits);
  }

  function formatInteger(value) {
    const parsed = toNumber(value);
    return parsed === null ? '—' : Math.round(parsed).toLocaleString();
  }

  function formatPercent(value, digits = 1) {
    const parsed = toNumber(value);
    return parsed === null ? '—' : `${(parsed * 100).toFixed(digits)}%`;
  }

  function formatMetric(value, kind) {
    if (kind === 'percent') {
      return formatPercent(value);
    }
    if (kind === 'money') {
      return formatNumber(value, 2);
    }
    if (kind === 'integer') {
      return formatInteger(value);
    }
    return value === null || value === undefined || value === '' ? '—' : String(value);
  }

  function cleanString(value) {
    if (value === null || value === undefined) {
      return '';
    }
    return String(value).trim();
  }

  function isTruthy(value) {
    if (typeof value === 'boolean') return value;
    const text = cleanString(value).toLowerCase();
    return text === '1' || text === 'true' || text === 'yes' || text === 'y';
  }

  function setStatus(message, isError = false) {
    if (els.statusLine) {
      els.statusLine.textContent = message;
    }
    if (els.errorLine) {
      els.errorLine.hidden = !isError;
      els.errorLine.textContent = isError ? message : '';
    }
  }

  function buildQuery(params) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== null && value !== undefined && String(value).trim() !== '') {
        query.set(key, String(value));
      }
    });
    const queryString = query.toString();
    return queryString ? `?${queryString}` : '';
  }

  async function fetchJson(path) {
    const response = await fetch(path, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = payload?.detail || payload?.message || detail;
      } catch (error) {
        // ignore json parse fallback
      }
      throw new Error(detail || `Request failed with status ${response.status}`);
    }
    return response.json();
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function getBenchmark(snapshot) {
    return snapshot?.benchmark || {};
  }

  function getStudio(snapshot) {
    return getBenchmark(snapshot)?.studio_dashboard || {};
  }

  function getFinalists(snapshot) {
    const studio = getStudio(snapshot);
    const finalists = asArray(studio.finalists);
    if (finalists.length > 0) {
      return finalists.slice(0, FINALIST_LIMIT);
    }

    const benchmark = getBenchmark(snapshot);
    const sources = [
      asArray(benchmark.individual_strategy_rankings),
      asArray(benchmark.portfolio_rankings),
      asArray(benchmark.strategy_rankings),
    ];
    const merged = [];
    const seen = new Set();
    for (const source of sources) {
      for (const row of source) {
        const key = cleanString(row.finalist_id || row.strategy_family || row.lane_name || row.portfolio_scope);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        merged.push(row);
      }
    }
    return merged.slice(0, FINALIST_LIMIT);
  }

  function detailCapableFamilies(snapshot) {
    const rows = asArray(getBenchmark(snapshot)?.strategy_rankings);
    const names = new Set();
    rows.forEach((row) => {
      const family = cleanString(row.strategy_family);
      if (family) {
        names.add(family);
      }
    });
    return names;
  }

  function isDetailCapableFamily(snapshot, family) {
    const target = cleanString(family);
    return target !== '' && detailCapableFamilies(snapshot).has(target);
  }

  function getSeriesRows(snapshot) {
    const studio = getStudio(snapshot);
    const rows = asArray(studio.finalist_series);
    return rows.map((entry, index) => {
      const family = cleanString(entry.strategy_family || entry.lane_name || entry.finalist_id);
      const points = asArray(entry.rows).map((row, pointIndex) => ({
        raw: row,
        x: row.x ?? row.x_index ?? row.state_index ?? pointIndex,
        y: row.y ?? row.value ?? row.ending_bankroll ?? row.compounded_return ?? row.bankroll ?? row.score,
        label: row.label ?? row.x_label ?? row.event_at ?? row.game_date ?? `${pointIndex + 1}`,
      }));
      return {
        finalist_id: cleanString(entry.finalist_id || family || index),
        strategy_family: family,
        family_type: cleanString(entry.family_type || entry.source_name || ''),
        source_name: cleanString(entry.source_name || ''),
        points,
      };
    });
  }

  function finalistLabel(row) {
    return cleanString(
      row.lane_name ||
        row.variant_name ||
        row.controller_name ||
        row.strategy_family ||
        row.family_name ||
        row.portfolio_scope ||
        row.finalist_id ||
        'unknown'
    );
  }

  function getFinalistSeriesByFamily(seriesRows, strategyFamily) {
    return seriesRows.find((entry) => {
      const matchesFamily = cleanString(entry.strategy_family) === cleanString(strategyFamily);
      const matchesId = cleanString(entry.finalist_id) === cleanString(strategyFamily);
      return matchesFamily || matchesId;
    }) || null;
  }

  function createCard(label, value, caption, tone = '') {
    const card = document.createElement('article');
    card.className = `metric-card ${tone}`.trim();
    const labelNode = document.createElement('span');
    labelNode.className = 'metric-label';
    labelNode.textContent = label;
    const valueNode = document.createElement('strong');
    valueNode.className = 'metric-value';
    valueNode.textContent = value;
    card.append(labelNode, valueNode);
    if (caption) {
      const captionNode = document.createElement('span');
      captionNode.className = 'metric-caption';
      captionNode.textContent = caption;
      card.appendChild(captionNode);
    }
    return card;
  }

  function renderEmpty(container, text) {
    container.className = `${container.className.replace(/\bempty-state\b/g, '').trim()} empty-state`;
    container.textContent = text;
  }

  function renderTable(container, columns, rows, options = {}) {
    const { dense = true, selectableKey = null, selectedValue = null } = options;
    container.innerHTML = '';

    if (!rows.length) {
      renderEmpty(container, options.emptyText || 'No rows available.');
      return;
    }

    container.className = 'table-wrap';
    const table = document.createElement('table');
    if (dense) {
      table.className = 'dense-table';
    }

    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    columns.forEach((column) => {
      const th = document.createElement('th');
      th.textContent = column.label;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    const tbody = document.createElement('tbody');
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      if (selectableKey && selectedValue !== null && cleanString(row[selectableKey]) === cleanString(selectedValue)) {
        tr.classList.add('selected-row');
      }
      columns.forEach((column) => {
        const td = document.createElement('td');
        const value = column.getter ? column.getter(row) : row[column.key];
        td.textContent = formatMetric(value, column.kind);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });

    table.append(thead, tbody);
    container.appendChild(table);
  }

  function renderLegend(legend, finalists, selectedFamily) {
    legend.innerHTML = '';
    finalists.forEach((row, index) => {
      const chip = document.createElement('span');
      chip.className = 'legend-chip';
      if (cleanString(row.strategy_family) === cleanString(selectedFamily)) {
        chip.classList.add('selected-card');
      }
      const score = row.score ?? row.mean_ending_bankroll ?? row.ending_bankroll ?? row.compounded_return;
      chip.textContent = `${index + 1}. ${finalistLabel(row)} · ${formatNumber(score, 2)}`;
      legend.appendChild(chip);
    });
  }

  function renderFinalistsTable(rows, selectedFamily) {
    const columns = [
      { label: 'Rank', getter: (row) => row.rank ?? '—', kind: 'integer' },
      { label: 'Finalist', getter: finalistLabel },
      { label: 'Score', getter: (row) => row.score ?? row.mean_ending_bankroll ?? row.ending_bankroll, kind: 'money' },
      { label: 'Mean Bankroll', getter: (row) => row.mean_ending_bankroll ?? row.ending_bankroll, kind: 'money' },
      { label: 'Positive Seeds', getter: (row) => row.positive_seed_rate, kind: 'percent' },
      { label: 'Drawdown', getter: (row) => row.max_drawdown_pct, kind: 'percent' },
      { label: 'Label', getter: (row) => row.candidate_label || row.robustness_label || row.label_reason || '—' },
    ];
    renderTable(els.rankingTable, columns, rows, {
      selectedValue: selectedFamily,
      selectableKey: 'strategy_family',
      emptyText: 'The finalists table will appear after loading a snapshot.',
    });
  }

  function renderMasterRouter(masterRouter, llmVariants) {
    els.masterSummary.className = 'metric-grid small-grid';
    els.masterSummary.innerHTML = '';
    const comparisonRows = asArray(masterRouter.comparison_rows);
    const routeRows = asArray(masterRouter.route_summary);
    const lowConfidenceRows = asArray(masterRouter.low_confidence_summary);
    const coreFamilies = asArray(masterRouter.core_families);
    const extraFamilies = asArray(masterRouter.extra_families);

    const cards = [
      createCard('Router family', cleanString(masterRouter.family_name || '—'), cleanString(masterRouter.selection_sample_name || 'selection sample')),
      createCard('Core families', formatInteger(coreFamilies.length), coreFamilies.join(' · ') || 'No core families reported'),
      createCard('Extra families', formatInteger(extraFamilies.length), extraFamilies.join(' · ') || 'No extra families reported'),
      createCard('Comparison rows', formatInteger(comparisonRows.length), 'Full sample, time validation, and holdout comparisons'),
    ];
    if (routeRows.length || lowConfidenceRows.length) {
      cards.push(createCard('Routing rows', formatInteger(routeRows.length), `Low-confidence cases: ${formatInteger(lowConfidenceRows.reduce((total, row) => total + (toNumber(row.decision_count) || 0), 0))}`));
    }
    cards.forEach((card) => els.masterSummary.appendChild(card));

    renderTable(
      els.routeBandTable,
      [
        { label: 'Sample', key: 'sample_name' },
        { label: 'Family', key: 'strategy_family' },
        { label: 'Scope', key: 'portfolio_scope' },
        { label: 'Bankroll', key: 'ending_bankroll', kind: 'money' },
        { label: 'Return', key: 'compounded_return', kind: 'percent' },
        { label: 'Drawdown', key: 'max_drawdown_pct', kind: 'percent' },
        { label: 'Trades', key: 'executed_trade_count', kind: 'integer' },
      ],
      comparisonRows,
      {
        emptyText: 'Master-router comparisons will appear here after loading a snapshot.',
      }
    );

    renderTable(
      els.llmVariantTable,
      [
        { label: 'Rank', key: 'rank', kind: 'integer' },
        { label: 'Variant', getter: finalistLabel },
        { label: 'Score', key: 'score', kind: 'money' },
        { label: 'Mean Bankroll', key: 'mean_ending_bankroll', kind: 'money' },
        { label: 'Positive Seeds', key: 'positive_seed_rate', kind: 'percent' },
        { label: 'Drawdown', key: 'max_drawdown_pct', kind: 'percent' },
      ],
      asArray(llmVariants),
      {
        emptyText: 'LLM variant summary will appear here after loading a snapshot.',
      }
    );
  }

  function getPointDomain(seriesRows) {
    let min = Infinity;
    let max = -Infinity;
    let maxPoints = 0;
    seriesRows.forEach((series) => {
      maxPoints = Math.max(maxPoints, series.points.length);
      series.points.forEach((point, index) => {
        const xValue = toNumber(point.x);
        const numeric = xValue === null ? index : xValue;
        if (numeric < min) min = numeric;
        if (numeric > max) max = numeric;
      });
    });
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      min = 0;
      max = Math.max(maxPoints - 1, 1);
    }
    if (min === max) {
      max = min + 1;
    }
    return { min, max, maxPoints };
  }

  function chartCoordinate(value, domainMin, domainMax, span, offset) {
    return offset + ((value - domainMin) / (domainMax - domainMin)) * span;
  }

  function renderChart(snapshot) {
    const seriesRows = getSeriesRows(snapshot);
    els.chart.innerHTML = '';
    if (!seriesRows.length) {
      renderEmpty(els.chart, 'No finalist series were found in the current snapshot.');
      els.legend.innerHTML = '';
      return;
    }

    els.chart.className = 'chart-shell';
    const width = 1200;
    const height = 440;
    const padding = { top: 18, right: 24, bottom: 42, left: 56 };
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    const { min, max, maxPoints } = getPointDomain(seriesRows);

    let yMin = Infinity;
    let yMax = -Infinity;
    seriesRows.forEach((series) => {
      series.points.forEach((point) => {
        const value = toNumber(point.y);
        if (value === null) return;
        if (value < yMin) yMin = value;
        if (value > yMax) yMax = value;
      });
    });
    if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) {
      yMin = 0;
      yMax = 1;
    }
    if (yMin === yMax) {
      yMax = yMin + 1;
    }
    const yPadding = (yMax - yMin) * 0.1 || 1;
    yMin -= yPadding;
    yMax += yPadding;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Strategy evolution chart');

    const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    background.setAttribute('x', '0');
    background.setAttribute('y', '0');
    background.setAttribute('width', width);
    background.setAttribute('height', height);
    background.setAttribute('fill', 'transparent');
    svg.appendChild(background);

    for (let index = 0; index <= 5; index += 1) {
      const y = padding.top + (innerHeight / 5) * index;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', padding.left);
      line.setAttribute('x2', width - padding.right);
      line.setAttribute('y1', y);
      line.setAttribute('y2', y);
      line.setAttribute('class', 'chart-grid-line');
      svg.appendChild(line);

      const value = yMax - ((yMax - yMin) / 5) * index;
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', 10);
      label.setAttribute('y', y + 4);
      label.setAttribute('class', 'chart-axis-label');
      label.textContent = formatNumber(value, 1);
      svg.appendChild(label);
    }

    for (let index = 0; index <= Math.max(maxPoints - 1, 1); index += 1) {
      const x = padding.left + (innerWidth / Math.max(maxPoints - 1, 1)) * index;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('y1', padding.top);
      line.setAttribute('y2', height - padding.bottom);
      line.setAttribute('x1', x);
      line.setAttribute('x2', x);
      line.setAttribute('class', 'chart-grid-line chart-grid-line-vertical');
      svg.appendChild(line);
    }

    const palette = ['#f7f7f7', '#ff3b3b', '#b9c0ca', '#f0f0f0', '#ff7373', '#8b96a8'];
    seriesRows.forEach((series, seriesIndex) => {
      const color = palette[seriesIndex % palette.length];
      const points = series.points
        .map((point, pointIndex) => {
          const xBase = toNumber(point.x);
          const xValue = xBase === null ? pointIndex : xBase;
          const yValue = toNumber(point.y);
          if (yValue === null) return null;
          const x = chartCoordinate(xValue, min, max, innerWidth, padding.left);
          const y = padding.top + innerHeight - ((yValue - yMin) / (yMax - yMin)) * innerHeight;
          return { x, y, label: point.label };
        })
        .filter(Boolean);
      if (!points.length) {
        return;
      }
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute(
        'd',
        points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ')
      );
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', color);
      path.setAttribute('stroke-width', seriesIndex === 0 ? '2.8' : '1.8');
      path.setAttribute('stroke-linecap', 'round');
      path.setAttribute('stroke-linejoin', 'round');
      path.setAttribute('class', 'chart-series-line');
      svg.appendChild(path);

      const lastPoint = points[points.length - 1];
      const marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      marker.setAttribute('cx', lastPoint.x.toFixed(2));
      marker.setAttribute('cy', lastPoint.y.toFixed(2));
      marker.setAttribute('r', seriesIndex === 0 ? '4' : '3');
      marker.setAttribute('fill', color);
      marker.setAttribute('class', 'chart-point');
      svg.appendChild(marker);

      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', lastPoint.x + 8);
      label.setAttribute('y', lastPoint.y - 8);
      label.setAttribute('class', 'chart-series-label');
      label.textContent = finalistLabel(series);
      svg.appendChild(label);
    });

    const xLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    xLabel.setAttribute('x', width - padding.right);
    xLabel.setAttribute('y', height - 10);
    xLabel.setAttribute('text-anchor', 'end');
    xLabel.setAttribute('class', 'chart-axis-label');
    xLabel.textContent = 'benchmark progression';
    svg.appendChild(xLabel);

    els.chart.appendChild(svg);
    renderLegend(els.legend, state.finalists, state.selectedFamily);
  }

  function renderSnapshot(snapshot) {
    state.snapshot = snapshot;
    state.finalists = getFinalists(snapshot).map((row, index) => ({
      ...row,
      rank: row.rank ?? index + 1,
    }));
    const detailFirst = state.finalists.find((row) => isDetailCapableFamily(snapshot, row.strategy_family));
    state.selectedFamily = cleanString(detailFirst?.strategy_family || '');

    const benchmark = getBenchmark(snapshot);
    const studio = getStudio(snapshot);
    const metadataBits = [
      snapshot.season,
      snapshot.season_phase,
      snapshot.analysis_version,
      `${state.finalists.length} finalists`,
      `${asArray(benchmark.master_router?.comparison_rows).length || 0} comparisons`,
    ].filter(Boolean);
    els.meta.textContent = metadataBits.join(' · ');

    renderFinalistsTable(state.finalists, state.selectedFamily);
    renderMasterRouter(benchmark.master_router || {}, studio.llm_variant_rankings || benchmark.llm_variant_summary || []);
    renderChart(snapshot);
    renderComparisonIndex(studio);
    populateComparisonFamilySelect();
    if (state.selectedFamily) {
      els.comparisonFamily.value = state.selectedFamily;
    }
  }

  function populateComparisonFamilySelect() {
    els.comparisonFamily.innerHTML = '';
    const detailFamilies = state.finalists.filter((row) => isDetailCapableFamily(state.snapshot, row.strategy_family));
    if (!detailFamilies.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'no direct family detail for finalists';
      els.comparisonFamily.appendChild(option);
      els.comparisonFamily.disabled = true;
      return;
    }

    detailFamilies.forEach((row) => {
      const option = document.createElement('option');
      option.value = cleanString(row.strategy_family || row.finalist_id);
      option.textContent = finalistLabel(row);
      els.comparisonFamily.appendChild(option);
    });
    els.comparisonFamily.disabled = false;
  }

  function renderComparisonIndex(studio) {
    els.comparisonIndex.className = 'stack-list';
    els.comparisonIndex.innerHTML = '';

    const finalists = state.finalists.length ? state.finalists : getFinalists({ benchmark: { studio_dashboard: studio } });
    if (!finalists.length) {
      renderEmpty(els.comparisonIndex, 'No finalists are available for comparison.');
      return;
    }

    finalists.forEach((row, index) => {
      const card = document.createElement('article');
      card.className = 'stack-item';
      if (cleanString(row.strategy_family) === cleanString(state.selectedFamily)) {
        card.classList.add('selected-card');
      }

      const head = document.createElement('div');
      head.className = 'stack-head';
      const titleWrap = document.createElement('div');
      const kicker = document.createElement('p');
      kicker.className = 'section-kicker';
      kicker.textContent = `Finalist ${index + 1}`;
      const title = document.createElement('h4');
      title.style.margin = '0';
      title.textContent = finalistLabel(row);
      titleWrap.append(kicker, title);

      const scorePill = document.createElement('span');
      scorePill.className = 'pill';
      scorePill.textContent = formatNumber(row.score ?? row.mean_ending_bankroll ?? row.ending_bankroll, 2);
      head.append(titleWrap, scorePill);

      const meta = document.createElement('p');
      meta.className = 'meta';
      meta.textContent = [
        row.family_type || 'strategy',
        row.source_name || 'deterministic',
        row.candidate_label || row.robustness_label || row.label_reason || 'no label',
      ]
        .filter(Boolean)
        .join(' · ');

      const tags = document.createElement('div');
      tags.className = 'tag-row';
      const primaryTags = [
        `Rank ${row.rank ?? index + 1}`,
        `Mean ${formatNumber(row.mean_ending_bankroll ?? row.ending_bankroll, 2)}`,
        `Seeds ${formatPercent(row.positive_seed_rate)}`,
        `DD ${formatPercent(row.max_drawdown_pct)}`,
      ];
      primaryTags.forEach((text) => {
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = text;
        tags.appendChild(tag);
      });

      card.append(head, meta, tags);
      els.comparisonIndex.appendChild(card);
    });
  }

  function renderMetricGrid(container, rows) {
    const grid = document.createElement('div');
    grid.className = 'metric-grid small-grid';
    rows.forEach((row) => {
      grid.appendChild(createCard(row.label, row.value, row.caption, row.tone || ''));
    });
    container.appendChild(grid);
  }

  function renderNamedTable(container, title, rows, columns, emptyText) {
    const block = document.createElement('article');
    block.className = 'stack-item';
    const header = document.createElement('div');
    header.className = 'subpanel-header';
    const labelWrap = document.createElement('div');
    const kicker = document.createElement('p');
    kicker.className = 'section-kicker';
    kicker.textContent = title;
    const heading = document.createElement('h4');
    heading.style.margin = '0';
    heading.textContent = title;
    labelWrap.append(kicker, heading);
    header.appendChild(labelWrap);
    block.appendChild(header);

    const wrap = document.createElement('div');
    block.appendChild(wrap);
    renderTable(wrap, columns, rows, { emptyText: emptyText || `No ${title.toLowerCase()} rows.` });
    container.appendChild(block);
  }

  function renderFamilyDetail(detail) {
    els.comparisonDetail.className = 'stack-list';
    els.comparisonDetail.innerHTML = '';

    if (!detail) {
      renderEmpty(els.comparisonDetail, 'Choose a finalist to inspect the bounded family detail.');
      return;
    }

    const summary = detail.summary || {};
    const candidate = detail.candidate_freeze || {};
    const ranking = detail.individual_ranking || {};
    const summaryBlock = document.createElement('article');
    summaryBlock.className = 'stack-item selected-card';
    const header = document.createElement('div');
    header.className = 'stack-head';
    const titleWrap = document.createElement('div');
    const kicker = document.createElement('p');
    kicker.className = 'section-kicker';
    kicker.textContent = 'Selected finalist';
    const title = document.createElement('h4');
    title.style.margin = '0';
    title.textContent = finalistLabel(summary) || cleanString(detail.strategy_family);
    titleWrap.append(kicker, title);
    const label = document.createElement('span');
    label.className = 'pill';
    label.textContent = summary.candidate_label || candidate.candidate_label || ranking.robustness_label || 'analysis ready';
    header.append(titleWrap, label);

    const meta = document.createElement('p');
    meta.className = 'meta';
    meta.textContent = [
      summary.strategy_family || detail.strategy_family,
      summary.portfolio_scope || ranking.portfolio_scope,
      summary.label_reason || candidate.label_reason,
    ]
      .filter(Boolean)
      .join(' · ');

    summaryBlock.append(header, meta);
    els.comparisonDetail.appendChild(summaryBlock);

    renderMetricGrid(els.comparisonDetail, [
      {
        label: 'Mean ending bankroll',
        value: formatNumber(summary.mean_ending_bankroll ?? summary.ending_bankroll, 2),
        caption: 'Benchmark selector value',
      },
      {
        label: 'Positive seed rate',
        value: formatPercent(summary.positive_seed_rate ?? ranking.positive_seed_rate),
        caption: 'Robustness check',
      },
      {
        label: 'Max drawdown',
        value: formatPercent(summary.max_drawdown_pct ?? ranking.max_drawdown_pct),
        caption: 'Observed risk',
      },
      {
        label: 'Trade count',
        value: formatInteger(summary.trade_count ?? ranking.executed_trade_count),
        caption: 'Filtered family sample',
      },
    ]);

    renderNamedTable(
      els.comparisonDetail,
      'Best trades',
      asArray(detail.best_trades),
      [
        { label: 'Game', key: 'game_id' },
        { label: 'Entry', key: 'entry_at' },
        { label: 'Exit', key: 'exit_at' },
        { label: 'Return', key: 'gross_return_with_slippage', kind: 'percent' },
        { label: 'Hold', key: 'hold_time_seconds', kind: 'integer' },
      ],
      'No best-trade rows were returned.'
    );

    renderNamedTable(
      els.comparisonDetail,
      'Worst trades',
      asArray(detail.worst_trades),
      [
        { label: 'Game', key: 'game_id' },
        { label: 'Entry', key: 'entry_at' },
        { label: 'Exit', key: 'exit_at' },
        { label: 'Return', key: 'gross_return_with_slippage', kind: 'percent' },
        { label: 'Hold', key: 'hold_time_seconds', kind: 'integer' },
      ],
      'No worst-trade rows were returned.'
    );

    renderNamedTable(
      els.comparisonDetail,
      'Context summary',
      asArray(detail.context_summary),
      [
        { label: 'Context', key: 'context_bucket' },
        { label: 'Trades', key: 'trade_count', kind: 'integer' },
        { label: 'Win rate', key: 'win_rate', kind: 'percent' },
        { label: 'Avg return', key: 'avg_gross_return_with_slippage', kind: 'percent' },
        { label: 'Drawdown', key: 'max_drawdown_pct', kind: 'percent' },
      ],
      'No context rows were returned.'
    );
  }

  async function loadSnapshot() {
    const season = cleanString(els.season.value);
    const seasonPhase = cleanString(els.seasonPhase.value);
    const analysisVersion = cleanString(els.analysisVersion.value);
    const outputRoot = cleanString(els.outputRoot.value);
    const url =
      `${API_ROOT}/snapshot` +
      buildQuery({
        season,
        season_phase: seasonPhase,
        analysis_version: analysisVersion,
        output_root: outputRoot,
      });

    setStatus('Loading benchmark snapshot...');
    els.loadButton.disabled = true;
    try {
      const snapshot = await fetchJson(url);
      renderSnapshot(snapshot);
      setStatus(`Loaded ${snapshot.season} ${snapshot.season_phase} · ${snapshot.analysis_version}`);
      if (state.selectedFamily && isDetailCapableFamily(snapshot, state.selectedFamily)) {
        loadFamilyDetail(state.selectedFamily).catch(() => {});
      }
    } catch (error) {
      setStatus(error.message || 'Failed to load snapshot.', true);
      els.chart.className = 'chart-shell empty-state';
      els.chart.textContent = 'Snapshot load failed.';
      els.legend.innerHTML = '';
      els.rankingTable.innerHTML = '';
      els.masterSummary.innerHTML = '';
      els.routeBandTable.innerHTML = '';
      els.llmVariantTable.innerHTML = '';
      els.comparisonIndex.innerHTML = '';
      els.comparisonDetail.innerHTML = '';
      throw error;
    } finally {
      els.loadButton.disabled = false;
    }
  }

  async function loadFamilyDetail(strategyFamily) {
    const family = cleanString(strategyFamily);
    if (!family) {
      renderFamilyDetail(null);
      return;
    }
    if (!state.snapshot) {
      return;
    }
    if (!isDetailCapableFamily(state.snapshot, family)) {
      renderFamilyDetail({
        summary: { strategy_family: family, note: 'Bounded family detail is only available for concrete strategy families.' },
        best_trades: [],
        worst_trades: [],
        context_summary: [],
      });
      return;
    }

    const season = cleanString(els.season.value);
    const seasonPhase = cleanString(els.seasonPhase.value);
    const analysisVersion = cleanString(els.analysisVersion.value);
    const outputRoot = cleanString(els.outputRoot.value);
    const tradeLimit = cleanString(els.comparisonTradeLimit.value) || '5';
    const contextLimit = cleanString(els.comparisonContextLimit.value) || '10';
    const traceLimit = cleanString(els.comparisonTraceLimit.value) || '3';
    const url =
      `${API_ROOT}/backtests/${encodeURIComponent(family)}` +
      buildQuery({
        season,
        season_phase: seasonPhase,
        analysis_version: analysisVersion,
        output_root: outputRoot,
        trade_limit: tradeLimit,
        context_limit: contextLimit,
        trace_limit: traceLimit,
      });

    setStatus(`Loading ${family} detail...`);
    els.comparisonButton.disabled = true;
    try {
      const detail = await fetchJson(url);
      renderFamilyDetail(detail);
      setStatus(`Loaded ${family} detail`);
    } catch (error) {
      setStatus(error.message || `Failed to load ${family} detail.`, true);
      renderFamilyDetail(null);
      throw error;
    } finally {
      els.comparisonButton.disabled = false;
    }
  }

  els.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await loadSnapshot();
    } catch (error) {
      // error already surfaced
    }
  });

  els.comparisonForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await loadFamilyDetail(els.comparisonFamily.value);
    } catch (error) {
      // error already surfaced
    }
  });

  els.comparisonFamily.addEventListener('change', () => {
    state.selectedFamily = cleanString(els.comparisonFamily.value);
    renderFinalistsTable(state.finalists, state.selectedFamily);
    renderLegend(els.legend, state.finalists, state.selectedFamily);
    if (isDetailCapableFamily(state.snapshot, state.selectedFamily)) {
      loadFamilyDetail(state.selectedFamily).catch(() => {});
    }
  });

  document.addEventListener('click', (event) => {
    const card = event.target.closest?.('.stack-item');
    if (!card || !els.comparisonIndex.contains(card)) {
      return;
    }
    const cards = Array.from(els.comparisonIndex.querySelectorAll('.stack-item'));
    const index = cards.indexOf(card);
    const finalist = state.finalists[index];
    if (!finalist) {
      return;
    }
    state.selectedFamily = cleanString(finalist.strategy_family || finalist.finalist_id);
    if (isDetailCapableFamily(state.snapshot, state.selectedFamily)) {
      els.comparisonFamily.value = state.selectedFamily;
    }
    renderFinalistsTable(state.finalists, state.selectedFamily);
    renderComparisonIndex(getStudio(state.snapshot || {}));
    renderLegend(els.legend, state.finalists, state.selectedFamily);
    if (isDetailCapableFamily(state.snapshot, state.selectedFamily)) {
      loadFamilyDetail(state.selectedFamily).catch(() => {});
    } else {
      renderFamilyDetail({
        summary: { strategy_family: state.selectedFamily, note: 'This finalist is a router or LLM lane. Use the chart and finalist tables for comparison.' },
        best_trades: [],
        worst_trades: [],
        context_summary: [],
      });
    }
  });

  if (state.finalists.length === 0) {
    renderEmpty(els.chart, 'Load a snapshot to render the finalists and the master-router comparison.');
    renderEmpty(els.rankingTable, 'Load a snapshot to inspect the finalists ranking.');
    renderEmpty(els.masterSummary, 'Master-router cards appear here after loading.');
    renderEmpty(els.routeBandTable, 'Opening-band routing appears here after loading.');
    renderEmpty(els.llmVariantTable, 'LLM variant summary appears here after loading.');
    renderEmpty(els.comparisonIndex, 'No strategy families loaded yet.');
    renderEmpty(els.comparisonDetail, 'Choose a family to inspect detail.');
  }

  loadSnapshot().catch(() => {});
})();
