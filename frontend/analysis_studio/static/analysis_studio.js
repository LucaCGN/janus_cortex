'use strict';

(function () {
  const API_ROOT = '/v1/analysis/studio/benchmark-dashboard';

  const els = {
    form: document.getElementById('dashboard-form'),
    season: document.getElementById('season'),
    replayArtifactName: document.getElementById('replay_artifact_name'),
    finalistLimit: document.getElementById('finalist_limit'),
    sharedRoot: document.getElementById('shared_root'),
    loadButton: document.getElementById('load-button'),
    statusLine: document.getElementById('status-line'),
    errorLine: document.getElementById('error-line'),
    snapshotMeta: document.getElementById('snapshot-meta'),
    summaryCards: document.getElementById('summary-cards'),
    modeCards: document.getElementById('mode-cards'),
    dailyLiveList: document.getElementById('daily-live-list'),
    laneStatusTable: document.getElementById('lane-status-table'),
    laneRankingTable: document.getElementById('lane-ranking-table'),
    compareReadyTable: document.getElementById('compare-ready-table'),
    baselineTable: document.getElementById('baseline-table'),
    hfTable: document.getElementById('hf-table'),
    hfShadowTable: document.getElementById('hf-shadow-table'),
    hfBenchTable: document.getElementById('hf-bench-table'),
    mlTable: document.getElementById('ml-table'),
    llmTable: document.getElementById('llm-table'),
    promotedStackNote: document.getElementById('promoted-stack-note'),
    liveReadyTable: document.getElementById('live-ready-table'),
    liveProbeTable: document.getElementById('live-probe-table'),
    finalistTable: document.getElementById('finalist-table'),
    shadowOnlyTable: document.getElementById('shadow-only-table'),
    benchOnlyTable: document.getElementById('bench-only-table'),
    divergenceTable: document.getElementById('divergence-table'),
    gameGapTable: document.getElementById('game-gap-table'),
    mergePlanList: document.getElementById('merge-plan-list'),
    criteriaList: document.getElementById('criteria-list'),
    submissionExampleList: document.getElementById('submission-example-list'),
  };

  function cleanString(value) {
    if (value === null || value === undefined) return '';
    return String(value).trim();
  }

  function toNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function fallbackText(value) {
    const text = cleanString(value);
    return text || '-';
  }

  function formatNumber(value, digits = 2) {
    const parsed = toNumber(value);
    return parsed === null ? '-' : parsed.toFixed(digits);
  }

  function formatInteger(value) {
    const parsed = toNumber(value);
    return parsed === null ? '-' : Math.round(parsed).toLocaleString();
  }

  function formatPercent(value, digits = 1) {
    const parsed = toNumber(value);
    return parsed === null ? '-' : `${(parsed * 100).toFixed(digits)}%`;
  }

  function formatMetric(value, kind) {
    if (kind === 'integer') return formatInteger(value);
    if (kind === 'percent') return formatPercent(value);
    if (kind === 'money') return formatNumber(value, 2);
    return fallbackText(value);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function buildQuery(params) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== null && value !== undefined && cleanString(value) !== '') {
        query.set(key, cleanString(value));
      }
    });
    const queryString = query.toString();
    return queryString ? `?${queryString}` : '';
  }

  async function fetchJson(url) {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = payload?.error?.message || payload?.detail || payload?.message || detail;
      } catch (_error) {
        // ignore fallback
      }
      throw new Error(detail || `Request failed with status ${response.status}`);
    }
    return response.json();
  }

  function setStatus(message, isError = false) {
    els.statusLine.textContent = message;
    els.errorLine.hidden = !isError;
    els.errorLine.textContent = isError ? message : '';
  }

  function setEmptyState(container, message) {
    container.innerHTML = '';
    container.className = 'empty-state';
    container.textContent = message;
  }

  function renderTable(container, columns, rows, options = {}) {
    container.innerHTML = '';
    if (!rows.length) {
      setEmptyState(container, options.emptyText || 'No rows available.');
      return;
    }

    container.className = 'table-wrap';
    const table = document.createElement('table');
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
      if (row.baseline_locked_flag || row.comparison_ready_flag) {
        tr.classList.add('selected-row');
      }
      columns.forEach((column) => {
        const td = document.createElement('td');
        const rawValue = column.getter ? column.getter(row) : row[column.key];
        td.textContent = formatMetric(rawValue, column.kind);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });

    table.append(thead, tbody);
    container.appendChild(table);
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
    const captionNode = document.createElement('span');
    captionNode.className = 'metric-caption';
    captionNode.textContent = caption;
    card.append(labelNode, valueNode, captionNode);
    return card;
  }

  function renderStackItems(container, rows, options = {}) {
    container.innerHTML = '';
    if (!rows.length) {
      setEmptyState(container, options.emptyText || 'No rows available.');
      return;
    }

    container.className = 'stack-list';
    rows.forEach((row) => {
      const card = document.createElement('article');
      card.className = 'stack-item';
      const kicker = document.createElement('p');
      kicker.className = 'section-kicker';
      kicker.textContent = row.title || 'item';
      const body = document.createElement('p');
      body.className = 'meta';
      body.textContent = cleanString(row.body) || '-';
      card.append(kicker, body);
      container.appendChild(card);
    });
  }

  function liveState(row) {
    return (row.live_observed_result || {}).live_observed_flag ? 'observed' : 'not observed';
  }

  function comparisonColumns(options = {}) {
    const columns = [];
    if (options.includeRank) {
      columns.push({
        label: 'Rank',
        getter: (row) => row[options.rankKey || 'challenger_rank'],
        kind: 'integer',
      });
    }
    columns.push(
      { label: 'Candidate', getter: (row) => row.display_name || row.candidate_id }
    );
    if (options.includeLane) {
      columns.push({ label: 'Lane', getter: (row) => row.lane_label || row.lane_id });
    }
    if (options.includeBucket) {
      columns.push({ label: 'Bucket', getter: (row) => row.visibility_bucket || row.lane_bucket });
    }
    if (options.includePromotion) {
      columns.push({ label: 'Promotion', getter: (row) => row.promotion_bucket || '-' });
      columns.push({ label: 'Today', getter: (row) => row.today_execution_mode || '-' });
    }
    columns.push(
      { label: 'Std trades', key: 'standard_trade_count', kind: 'integer' },
      { label: 'Replay trades', key: 'replay_trade_count', kind: 'integer' },
      { label: 'Exec rate', key: 'execution_rate', kind: 'percent' },
      { label: 'Realism gap', key: 'realism_gap_trade_rate', kind: 'percent' },
      { label: 'Replay bankroll', key: 'replay_ending_bankroll', kind: 'money' },
      { label: 'Replay DD', key: 'replay_max_drawdown_pct', kind: 'percent' },
      { label: 'Live state', getter: (row) => liveState(row) },
      { label: 'Live trades', key: 'live_trade_count', kind: 'integer' },
      { label: 'Stale count', key: 'stale_signal_suppressed_count', kind: 'integer' },
      { label: 'Stale rate', key: 'stale_signal_suppression_rate', kind: 'percent' }
    );
    if (options.includeMissing) {
      columns.push({
        label: 'Missing',
        getter: (row) => asArray((row.compare_ready_checks || {}).missing_requirements).join(', ') || '-',
      });
    }
    if (options.includeReason) {
      columns.push({
        label: 'Reason',
        getter: (row) => row.visibility_bucket_reason || row.finalist_reason || '-',
      });
    } else if (!options.includeMissing) {
      columns.push({ label: 'Top no-trade', key: 'top_no_trade_reason' });
    }
    return columns;
  }

  function renderSummary(snapshot) {
    const summary = snapshot.summary || {};
    const replay = snapshot.replay_contract || {};

    els.summaryCards.innerHTML = '';
    els.summaryCards.className = 'metric-grid';
    [
      createCard('Published lanes', formatInteger(summary.published_lane_count), `Compare-ready lanes: ${formatInteger(summary.compare_ready_lane_count)}`, 'warning'),
      createCard('Published candidates', formatInteger(summary.published_candidate_count), `Compare-ready candidates: ${formatInteger(summary.compare_ready_candidate_count)}`),
      createCard('Live-ready', formatInteger(summary.live_ready_candidate_count), `Live-probe: ${formatInteger(summary.live_probe_candidate_count)}`, 'positive'),
      createCard('Replay challengers', formatInteger(summary.replay_compare_ready_challenger_count), `Replay pending: ${formatInteger(summary.replay_pending_candidate_count)}`),
      createCard('Finished postseason games', formatInteger(replay.finished_game_count), `State-panel games: ${formatInteger(replay.state_panel_game_count)}`),
      createCard('Mean execution rate', formatPercent(summary.mean_execution_rate), `Mean realism gap: ${formatPercent(summary.mean_realism_gap_trade_rate)}`, 'positive'),
      createCard('Mean stale suppression', formatPercent(summary.mean_stale_signal_suppression_rate), `Shadow-only candidates: ${formatInteger(summary.shadow_only_candidate_count)}`),
      createCard('Bench-only candidates', formatInteger(summary.bench_only_candidate_count), `Live observed candidates: ${formatInteger(summary.live_observed_candidate_count)}`),
      createCard('Daily live', fallbackText(summary.daily_live_status), `Session ${fallbackText(summary.daily_live_session_date)}`)
    ].forEach((card) => els.summaryCards.appendChild(card));
  }

  function renderResultModes(resultModes) {
    els.modeCards.innerHTML = '';
    const rows = asArray(resultModes);
    if (!rows.length) {
      setEmptyState(els.modeCards, 'Result mode summary unavailable.');
      return;
    }
    els.modeCards.className = 'metric-grid';
    rows.forEach((row, index) => {
      const tone = index === 1 ? 'positive' : '';
      els.modeCards.appendChild(
        createCard(
          row.label || row.id || 'mode',
          row.headline || '-',
          row.description || '',
          tone
        )
      );
    });
  }

  function renderDailyLiveValidation(dailyLiveValidation) {
    const summary = dailyLiveValidation.summary || {};
    const control = summary.control || {};
    const harness = summary.harness_capabilities || {};
    const currentTruth = summary.current_live_truth || {};
    const plannedProbes = asArray(summary.planned_probes).map((row) => ({
      title: `probe / ${row.candidate_id || '-'}`,
      body: [
        cleanString(row.today_execution),
        cleanString(row.compare_ready_state),
        cleanString(row.reason),
      ].filter(Boolean).join(' | '),
    }));

    const rows = [
      {
        title: 'session',
        body: [
          cleanString(dailyLiveValidation.session_date),
          cleanString(dailyLiveValidation.status),
          cleanString(dailyLiveValidation.snapshot_published_at),
        ].filter(Boolean).join(' | '),
      },
      {
        title: 'control',
        body: [
          `primary ${fallbackText(control.primary_controller)}`,
          `fallback ${fallbackText(control.fallback_controller)}`,
          `mode ${fallbackText(control.mode)}`,
        ].join(' | '),
      },
      {
        title: 'harness',
        body: [
          `probe routing ${fallbackText(harness.supports_standalone_probe_candidates)}`,
          `ML sidecar ${fallbackText(harness.supports_ml_sidecar_live_routing)}`,
          `LLM sidecar ${fallbackText(harness.supports_llm_sidecar_live_routing)}`,
        ].join(' | '),
      },
      {
        title: 'current live truth',
        body: [
          `run ${fallbackText(currentTruth.run_status)}`,
          `cycles ${formatInteger(currentTruth.cycle_count)}`,
          `open orders ${formatInteger(currentTruth.open_orders)}`,
          `open positions ${formatInteger(currentTruth.open_positions)}`,
        ].join(' | '),
      },
      ...plannedProbes,
    ];

    renderStackItems(els.dailyLiveList, rows, {
      emptyText: 'Daily live validation details were not published yet.',
    });
  }

  function renderLaneStatuses(rows) {
    renderTable(
      els.laneStatusTable,
      [
        { label: 'Lane', getter: (row) => row.lane_label || row.lane_id },
        { label: 'State', key: 'publication_state' },
        { label: 'Published', key: 'published_subject_count', kind: 'integer' },
        { label: 'Criteria-ready', key: 'criteria_ready_subject_count', kind: 'integer' },
        { label: 'Compare-ready', key: 'compare_ready_subject_count', kind: 'integer' },
        { label: 'Live-ready', key: 'live_ready_subject_count', kind: 'integer' },
        { label: 'Live-probe', key: 'live_probe_subject_count', kind: 'integer' },
        { label: 'Shadow-only', key: 'shadow_only_subject_count', kind: 'integer' },
        { label: 'Bench-only', key: 'bench_only_subject_count', kind: 'integer' },
        { label: 'Bucket', key: 'lane_bucket' },
        { label: 'Notes', getter: (row) => asArray(row.notes).join(' | ') || '-' },
      ],
      rows,
      { emptyText: 'No lane status rows were returned.' }
    );
  }

  function renderLaneRankings(rows) {
    renderTable(
      els.laneRankingTable,
      [
        { label: 'Rank', key: 'lane_rank', kind: 'integer' },
        { label: 'Lane', getter: (row) => row.lane_label || row.lane_id },
        { label: 'Bucket', key: 'lane_bucket' },
        { label: 'Top candidate', key: 'top_candidate_name' },
        { label: 'Top promotion', key: 'top_candidate_promotion_bucket' },
        { label: 'Replay bankroll', key: 'top_candidate_replay_ending_bankroll', kind: 'money' },
        { label: 'Exec rate', key: 'top_candidate_execution_rate', kind: 'percent' },
        { label: 'Realism gap', key: 'top_candidate_realism_gap_trade_rate', kind: 'percent' },
        { label: 'Compare-ready', key: 'compare_ready_subject_count', kind: 'integer' },
        { label: 'Live-ready', key: 'live_ready_subject_count', kind: 'integer' },
        { label: 'Live-probe', key: 'live_probe_subject_count', kind: 'integer' },
        { label: 'Shadow-only', key: 'shadow_only_subject_count', kind: 'integer' },
        { label: 'Bench-only', key: 'bench_only_subject_count', kind: 'integer' },
      ],
      rows,
      { emptyText: 'No lane ranking rows were returned.' }
    );
  }

  function renderCompareReadyRanking(rows) {
    renderTable(
      els.compareReadyTable,
      comparisonColumns({
        includeRank: true,
        rankKey: 'global_rank',
        includeLane: true,
        includePromotion: true,
        includeReason: true,
      }),
      rows,
      { emptyText: 'No compare-ready ranking rows were returned.' }
    );
  }

  function renderCandidates(snapshot) {
    renderTable(els.baselineTable, comparisonColumns({ includePromotion: true }), asArray(snapshot.baseline_controllers), {
      emptyText: 'No locked baseline rows were returned.',
    });
    renderTable(els.hfTable, comparisonColumns({ includeRank: true, includePromotion: true }), asArray(snapshot.deterministic_hf_compare_ready), {
      emptyText: 'No replay compare-ready challengers were returned.',
    });
    renderTable(els.hfShadowTable, comparisonColumns({ includePromotion: true, includeReason: true }), asArray(snapshot.deterministic_hf_shadow_only), {
      emptyText: 'No replay shadow-only candidates were returned.',
    });
    renderTable(els.hfBenchTable, comparisonColumns({ includePromotion: true, includeMissing: true, includeReason: true }), asArray(snapshot.deterministic_hf_bench_only), {
      emptyText: 'No replay bench-only candidates were returned.',
    });
    renderTable(els.mlTable, comparisonColumns({ includeBucket: true, includePromotion: true, includeReason: true }), asArray(snapshot.ml_candidates), {
      emptyText: 'The ML lane has not published a strict compare-ready submission yet.',
    });
    renderTable(els.llmTable, comparisonColumns({ includeBucket: true, includePromotion: true, includeReason: true }), asArray(snapshot.llm_candidates), {
      emptyText: 'The LLM lane has not published a strict compare-ready submission yet.',
    });
  }

  function renderPromotedStack(promotedStack) {
    const rows = [];
    if (cleanString(promotedStack.operator_note)) {
      rows.push({ title: 'operator note', body: cleanString(promotedStack.operator_note) });
    }
    if (asArray(promotedStack.live_ready).length) {
      rows.push({
        title: 'live-ready',
        body: asArray(promotedStack.live_ready).map((row) => row.candidate_id || row.display_name).join(' | '),
      });
    }
    if (asArray(promotedStack.live_probe).length) {
      rows.push({
        title: 'live-probe',
        body: asArray(promotedStack.live_probe).map((row) => row.candidate_id || row.display_name).join(' | '),
      });
    }
    if (asArray(promotedStack.shadow_only).length) {
      rows.push({
        title: 'shadow-only',
        body: asArray(promotedStack.shadow_only).slice(0, 8).map((row) => row.candidate_id || row.display_name).join(' | '),
      });
    }
    renderStackItems(els.promotedStackNote, rows, {
      emptyText: 'The promoted stack note is unavailable.',
    });
  }

  function renderPromotionBuckets(snapshot) {
    renderTable(
      els.liveReadyTable,
      comparisonColumns({ includePromotion: true, includeReason: true }),
      asArray(snapshot.live_ready_ranking),
      { emptyText: 'No live-ready candidates were returned.' }
    );
    renderTable(
      els.liveProbeTable,
      comparisonColumns({ includePromotion: true, includeReason: true }),
      asArray(snapshot.live_probe_ranking),
      { emptyText: 'No live-probe candidates were returned.' }
    );
    renderTable(
      els.shadowOnlyTable,
      comparisonColumns({ includeLane: true, includePromotion: true, includeReason: true }),
      asArray(snapshot.promotion_shadow_only_ranking || snapshot.shadow_only_candidates),
      { emptyText: 'No shadow-only candidates were returned.' }
    );
    renderTable(
      els.benchOnlyTable,
      comparisonColumns({ includeLane: true, includePromotion: true, includeMissing: true, includeReason: true }),
      asArray(snapshot.promotion_bench_only_ranking || snapshot.bench_only_candidates),
      { emptyText: 'No bench-only candidates were returned.' }
    );
  }

  function renderFinalists(rows) {
    renderTable(
      els.finalistTable,
      [
        { label: 'Rank', key: 'finalist_rank', kind: 'integer' },
        { label: 'Candidate', getter: (row) => row.display_name || row.candidate_id },
        { label: 'Promotion', key: 'promotion_bucket' },
        { label: 'Reason', key: 'finalist_reason' },
        { label: 'Replay bankroll', key: 'replay_ending_bankroll', kind: 'money' },
        { label: 'Execution', key: 'execution_rate', kind: 'percent' },
        { label: 'Stale rate', key: 'stale_signal_suppression_rate', kind: 'percent' },
        { label: 'Replay DD', key: 'replay_max_drawdown_pct', kind: 'percent' },
      ],
      rows,
      { emptyText: 'No compare-ready finalists are available yet.' }
    );
  }

  function renderDivergence(rows) {
    renderTable(
      els.divergenceTable,
      [
        { label: 'Candidate', key: 'subject_name' },
        { label: 'Type', key: 'subject_type' },
        { label: 'No-trade reason', key: 'no_trade_reason' },
        { label: 'Signals', key: 'signal_count', kind: 'integer' },
      ],
      rows,
      { emptyText: 'No divergence summary was returned.' }
    );
  }

  function renderGameGaps(rows) {
    renderTable(
      els.gameGapTable,
      [
        { label: 'Candidate', key: 'subject_name' },
        { label: 'Game', key: 'game_id' },
        { label: 'Std trades', key: 'standard_trade_count', kind: 'integer' },
        { label: 'Replay trades', key: 'replay_trade_count', kind: 'integer' },
        { label: 'Gap', key: 'trade_gap', kind: 'integer' },
        { label: 'Reason', key: 'top_no_trade_reason' },
      ],
      rows,
      { emptyText: 'No finalist game-gap rows were returned.' }
    );
  }

  function renderCriteria(criteria) {
    els.criteriaList.innerHTML = '';
    const candidateRequirements = asArray(criteria.candidate_requirements);
    if (!candidateRequirements.length) {
      setEmptyState(els.criteriaList, 'No compare-ready criteria were returned.');
      return;
    }

    els.criteriaList.className = 'stack-list';
    [
      { title: 'Lane requirements', rows: asArray(criteria.lane_requirements) },
      { title: 'Candidate requirements', rows: candidateRequirements },
      { title: 'Finalist rule', rows: asArray(criteria.finalist_rule).map((value) => ({ description: value })) },
    ].forEach((section) => {
      const card = document.createElement('article');
      card.className = 'stack-item';
      const kicker = document.createElement('p');
      kicker.className = 'section-kicker';
      kicker.textContent = section.title;
      const body = document.createElement('p');
      body.className = 'meta';
      body.textContent = section.rows.map((row) => cleanString(row.description)).join(' | ');
      card.append(kicker, body);
      els.criteriaList.appendChild(card);
    });
  }

  function renderSubmissionExamples(submissionExamples) {
    els.submissionExampleList.innerHTML = '';
    const examplePaths = submissionExamples.example_file_paths || {};
    const examples = submissionExamples.examples || {};
    if (!Object.keys(examplePaths).length) {
      setEmptyState(els.submissionExampleList, 'No submission example files were returned.');
      return;
    }

    els.submissionExampleList.className = 'stack-list';
    Object.entries(examplePaths).forEach(([laneId, path]) => {
      const example = examples[laneId] || {};
      const firstSubject = asArray(example.subjects)[0] || {};
      const card = document.createElement('article');
      card.className = 'stack-item';
      const kicker = document.createElement('p');
      kicker.className = 'section-kicker';
      kicker.textContent = laneId;
      const body = document.createElement('p');
      body.className = 'meta';
      body.textContent = [
        cleanString(path),
        cleanString(firstSubject.candidate_id || firstSubject.display_name),
        cleanString(example.schema_version),
      ].filter(Boolean).join(' | ');
      card.append(kicker, body);
      els.submissionExampleList.appendChild(card);
    });
  }

  function renderMergeRecommendations(mergeRecommendation) {
    els.mergePlanList.innerHTML = '';
    const mergeOrder = asArray(mergeRecommendation.merge_order);
    const mergeNow = asArray(mergeRecommendation.merge_now);
    const waitRows = asArray(mergeRecommendation.wait);
    const rows = [
      ...mergeOrder.map((row) => ({ ...row, section: `Order #${fallbackText(row.priority)}` })),
      ...mergeNow.map((row) => ({ ...row, section: 'Merge now' })),
      ...waitRows.map((row) => ({ ...row, section: 'Wait' })),
    ];
    if (!rows.length) {
      setEmptyState(els.mergePlanList, 'No merge recommendations were returned.');
      return;
    }

    els.mergePlanList.className = 'stack-list';
    rows.forEach((row) => {
      const card = document.createElement('article');
      card.className = 'stack-item';
      const kicker = document.createElement('p');
      kicker.className = 'section-kicker';
      kicker.textContent = `${row.section} / ${row.lane_id || 'lane'}`;
      const body = document.createElement('p');
      body.className = 'meta';
      body.textContent = [
        cleanString(row.recommendation),
        cleanString(row.status),
        cleanString(row.scope),
        cleanString(row.rationale || row.reason),
      ].filter(Boolean).join(' | ');
      card.append(kicker, body);
      els.mergePlanList.appendChild(card);
    });
  }

  function renderSnapshot(snapshot) {
    const replay = snapshot.replay_contract || {};
    const live = snapshot.daily_live_validation || {};
    els.snapshotMeta.textContent = [
      cleanString(snapshot.season),
      cleanString(snapshot.schema_version),
      cleanString(replay.replay_contract_maturity),
      cleanString(live.session_date),
      cleanString(live.status),
      `${formatInteger(replay.finished_game_count)} finished games`,
    ].filter(Boolean).join(' | ');

    renderSummary(snapshot);
    renderResultModes(snapshot.result_modes);
    renderDailyLiveValidation(snapshot.daily_live_validation || {});
    renderLaneStatuses(asArray(snapshot.lane_statuses));
    renderLaneRankings(asArray(snapshot.lane_rankings));
    renderCompareReadyRanking(asArray(snapshot.compare_ready_ranking));
    renderCandidates(snapshot);
    renderPromotedStack(snapshot.current_promoted_stack || {});
    renderPromotionBuckets(snapshot);
    renderFinalists(asArray(snapshot.finalists));
    renderDivergence(asArray(snapshot.divergence_summary));
    renderGameGaps(asArray(snapshot.game_gap_summary));
    renderMergeRecommendations(snapshot.merge_recommendation || {});
    renderCriteria(snapshot.compare_ready_criteria || {});
    renderSubmissionExamples(snapshot.submission_examples || {});
  }

  function renderFailure() {
    setEmptyState(els.summaryCards, 'Unified benchmark load failed.');
    setEmptyState(els.modeCards, 'Result mode summary unavailable.');
    setEmptyState(els.dailyLiveList, 'Daily live validation unavailable.');
    setEmptyState(els.laneStatusTable, 'Lane readiness unavailable.');
    setEmptyState(els.laneRankingTable, 'Lane ranking unavailable.');
    setEmptyState(els.compareReadyTable, 'Compare-ready ranking unavailable.');
    setEmptyState(els.baselineTable, 'Baseline controller data unavailable.');
    setEmptyState(els.hfTable, 'Replay compare-ready challenger data unavailable.');
    setEmptyState(els.hfShadowTable, 'Replay shadow-only candidate data unavailable.');
    setEmptyState(els.hfBenchTable, 'Replay bench-only candidate data unavailable.');
    setEmptyState(els.mlTable, 'ML lane data unavailable.');
    setEmptyState(els.llmTable, 'LLM lane data unavailable.');
    setEmptyState(els.promotedStackNote, 'Promoted stack note unavailable.');
    setEmptyState(els.liveReadyTable, 'Live-ready candidates unavailable.');
    setEmptyState(els.liveProbeTable, 'Live-probe candidates unavailable.');
    setEmptyState(els.finalistTable, 'Compare-ready finalists unavailable.');
    setEmptyState(els.shadowOnlyTable, 'Shadow-only candidates unavailable.');
    setEmptyState(els.benchOnlyTable, 'Bench-only candidates unavailable.');
    setEmptyState(els.divergenceTable, 'Divergence summary unavailable.');
    setEmptyState(els.gameGapTable, 'Game gap summary unavailable.');
    setEmptyState(els.mergePlanList, 'Merge recommendations unavailable.');
    setEmptyState(els.criteriaList, 'Compare-ready criteria unavailable.');
    setEmptyState(els.submissionExampleList, 'Submission example files unavailable.');
  }

  async function loadUnifiedBenchmarkDashboard() {
    const url = API_ROOT + buildQuery({
      season: els.season.value,
      replay_artifact_name: els.replayArtifactName.value,
      finalist_limit: els.finalistLimit.value,
      shared_root: els.sharedRoot.value,
    });

    setStatus('Loading unified benchmark dashboard...');
    els.loadButton.disabled = true;
    try {
      const snapshot = await fetchJson(url);
      renderSnapshot(snapshot);
      setStatus(`Loaded shared benchmark snapshot for ${snapshot.season}`);
    } catch (error) {
      renderFailure();
      setStatus(error.message || 'Failed to load unified benchmark dashboard.', true);
    } finally {
      els.loadButton.disabled = false;
    }
  }

  els.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    await loadUnifiedBenchmarkDashboard();
  });

  renderFailure();
  loadUnifiedBenchmarkDashboard().catch(() => {});
})();
