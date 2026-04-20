const form = document.getElementById("snapshot-form");
const runForm = document.getElementById("run-form");
const explorerForm = document.getElementById("explorer-form");
const comparisonForm = document.getElementById("comparison-form");
const loadButton = document.getElementById("load-button");
const runButton = document.getElementById("run-button");
const explorerButton = document.getElementById("explorer-button");
const comparisonButton = document.getElementById("comparison-button");
const statusLine = document.getElementById("status-line");
const runStatusLine = document.getElementById("run-status-line");
const explorerStatusLine = document.getElementById("explorer-status-line");
const comparisonStatusLine = document.getElementById("comparison-status-line");
const errorLine = document.getElementById("error-line");
const metadataGrid = document.getElementById("metadata-grid");
const universeGrid = document.getElementById("universe-grid");
const controlGrid = document.getElementById("control-grid");
const strategyTable = document.getElementById("strategy-table");
const modelList = document.getElementById("model-list");
const reportSections = document.getElementById("report-sections");
const artifactGrid = document.getElementById("artifact-grid");
const latestValidation = document.getElementById("latest-validation");
const recentValidations = document.getElementById("recent-validations");
const runList = document.getElementById("run-list");
const versionOptions = document.getElementById("analysis-version-options");
const gameList = document.getElementById("game-list");
const gameDetail = document.getElementById("game-detail");
const comparisonFamilySelect = document.getElementById("comparison_family");
const comparisonIndex = document.getElementById("comparison-index");
const comparisonDetail = document.getElementById("comparison-detail");

let selectedGameId = null;
let selectedStrategyFamily = null;
let latestGameListPayload = { items: [] };
let latestStrategyRankings = [];
let latestBacktestIndexPayload = { families: [] };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatValue(value, digits = 3) {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(digits);
  }
  return String(value);
}

function setLoadingState(isLoading, message) {
  loadButton.disabled = isLoading;
  loadButton.textContent = isLoading ? "Loading..." : "Load snapshot";
  statusLine.textContent = message;
}

function setRunState(isLoading, message) {
  runButton.disabled = isLoading;
  runButton.textContent = isLoading ? "Starting..." : "Start run";
  runStatusLine.textContent = message;
}

function setExplorerState(isLoading, message) {
  explorerButton.disabled = isLoading;
  explorerButton.textContent = isLoading ? "Loading..." : "Load games";
  explorerStatusLine.textContent = message;
}

function setComparisonState(isLoading, message) {
  comparisonButton.disabled = isLoading;
  comparisonButton.textContent = isLoading ? "Loading..." : "Load family detail";
  comparisonFamilySelect.disabled = isLoading || !(latestBacktestIndexPayload.families || []).length;
  comparisonStatusLine.textContent = message;
}

function clearError() {
  errorLine.hidden = true;
  errorLine.textContent = "";
}

function showError(message) {
  errorLine.hidden = false;
  errorLine.textContent = message;
}

function metricCard(label, value, caption = "") {
  return `
    <article class="metric-card">
      <span class="label">${escapeHtml(label)}</span>
      <span class="value">${escapeHtml(value)}</span>
      ${caption ? `<span class="caption">${escapeHtml(caption)}</span>` : ""}
    </article>
  `;
}

function tag(text, isSignal = false) {
  const className = isSignal ? "tag signal" : "tag";
  return `<span class="${className}">${escapeHtml(text)}</span>`;
}

function currentLoaderContext() {
  return {
    season: document.getElementById("season").value.trim(),
    season_phase: document.getElementById("season_phase").value.trim(),
    analysis_version: document.getElementById("analysis_version").value.trim(),
    backtest_experiment_id: document.getElementById("backtest_experiment_id").value.trim(),
    output_root: document.getElementById("output_root").value.trim(),
  };
}

function currentExplorerFilters() {
  return {
    team_slug: document.getElementById("explorer_team_slug").value.trim(),
    coverage_status: document.getElementById("explorer_coverage_status").value.trim(),
    game_date: document.getElementById("explorer_game_date").value.trim(),
  };
}

function normalizeLimit(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function currentComparisonOptions() {
  return {
    strategy_family: comparisonFamilySelect.value.trim(),
    trade_limit: normalizeLimit(document.getElementById("comparison_trade_limit").value, 5),
    context_limit: normalizeLimit(document.getElementById("comparison_context_limit").value, 10),
    trace_limit: normalizeLimit(document.getElementById("comparison_trace_limit").value, 3),
  };
}

function buildQueryParams(context) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(context)) {
    if (value) {
      params.set(key, value);
    }
  }
  return params;
}

function renderMetadata(snapshot) {
  metadataGrid.innerHTML = [
    metricCard("Season", snapshot.season),
    metricCard("Phase", snapshot.season_phase),
    metricCard("Version", snapshot.analysis_version),
    metricCard("Output Dir", "Resolved", snapshot.output_dir),
  ].join("");
}

function renderUniverse(snapshot) {
  const universe = snapshot.report?.universe || {};
  const counts = universe.coverage_status_counts || {};
  universeGrid.innerHTML = [
    metricCard("Finished Games", universe.games_total ?? "n/a"),
    metricCard("Research Ready", universe.research_ready_games ?? "n/a"),
    metricCard("Descriptive Only", universe.descriptive_only_games ?? "n/a"),
    metricCard("Excluded", universe.excluded_games ?? "n/a"),
    ...Object.entries(counts).map(([label, value]) => metricCard(label, value)),
  ].join("");
}

function syncComparisonFamilySelect(families) {
  if (!families.length) {
    comparisonFamilySelect.innerHTML = '<option value="">load a snapshot first</option>';
    comparisonFamilySelect.disabled = true;
    return;
  }

  const options = families
    .map(
      (family) => `
        <option value="${escapeHtml(family.strategy_family)}">
          ${escapeHtml(family.strategy_family)}
        </option>
      `,
    )
    .join("");
  comparisonFamilySelect.innerHTML = options;
  comparisonFamilySelect.disabled = false;

  const preferredFamily = families.some((family) => family.strategy_family === selectedStrategyFamily)
    ? selectedStrategyFamily
    : families[0].strategy_family;
  comparisonFamilySelect.value = preferredFamily;
  selectedStrategyFamily = preferredFamily;
}

function bindStrategyFamilyButtons(container) {
  Array.from(container.querySelectorAll("[data-strategy-family]")).forEach((button) => {
    button.addEventListener("click", () => {
      loadBacktestDetail(button.getAttribute("data-strategy-family")).catch(() => {});
    });
  });
}

function renderStrategies(rows = latestStrategyRankings) {
  if (!rows.length) {
    strategyTable.className = "table-wrap empty-state";
    strategyTable.textContent = "No ranked strategies are available in this snapshot.";
    return;
  }

  strategyTable.className = "table-wrap";
  strategyTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Rank</th>
          <th>Family</th>
          <th>Return With Slippage</th>
          <th>Trades</th>
          <th>Win Rate</th>
          <th>Label</th>
          <th>Inspect</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .slice(0, 8)
          .map((row) => {
            const isSelected = row.strategy_family === selectedStrategyFamily;
            return `
              <tr class="${isSelected ? "selected-row" : ""}">
                <td>${escapeHtml(row.rank)}</td>
                <td><strong>${escapeHtml(row.strategy_family)}</strong></td>
                <td>${escapeHtml(row.avg_gross_return_with_slippage ?? "n/a")}</td>
                <td>${escapeHtml(row.trade_count ?? "n/a")}</td>
                <td>${escapeHtml(formatValue(row.win_rate))}</td>
                <td>${escapeHtml(row.candidate_label ?? "n/a")}</td>
                <td>
                  <button
                    type="button"
                    class="ghost-button"
                    data-strategy-family="${escapeHtml(row.strategy_family)}"
                  >
                    Inspect
                  </button>
                </td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;

  bindStrategyFamilyButtons(strategyTable);
}

function renderModels(snapshot) {
  const tracks = snapshot.models?.tracks || [];
  if (!tracks.length) {
    modelList.className = "stack-list empty-state";
    modelList.textContent = "No model tracks are available in this snapshot.";
    return;
  }

  modelList.className = "stack-list";
  modelList.innerHTML = tracks
    .map((track) => {
      const tags = [
        tag(`family: ${track.model_family || "n/a"}`),
        tag(`status: ${track.status || "n/a"}`),
        tag(`train: ${track.train_rows ?? "n/a"}`),
        tag(`validation: ${track.validation_rows ?? "n/a"}`),
      ].join("");
      return `
        <article class="stack-item">
          <strong>${escapeHtml(track.track_name)}</strong>
          <span class="meta">${escapeHtml(JSON.stringify(track.metrics || track.naive_comparison || {}))}</span>
          <div class="tag-row">${tags}</div>
        </article>
      `;
    })
    .join("");
}

function renderReportSections(snapshot) {
  const sections = snapshot.report?.sections || [];
  if (!sections.length) {
    reportSections.className = "stack-list empty-state";
    reportSections.textContent = "No report sections are available in this snapshot.";
    return;
  }

  reportSections.className = "stack-list";
  reportSections.innerHTML = sections
    .map((section) => `
      <article class="stack-item">
        <strong>${escapeHtml(section.title)}</strong>
        <span class="meta">${escapeHtml(section.key)} / rows ${escapeHtml(section.row_count)}</span>
        <div class="tag-row">
          ${(section.columns || []).slice(0, 6).map((column) => tag(column)).join("")}
        </div>
      </article>
    `)
    .join("");
}

function renderArtifacts(snapshot) {
  const artifacts = snapshot.artifacts || {};
  const cards = Object.entries(artifacts).map(([group, payload]) => `
    <article class="artifact-card">
      <span class="label">${escapeHtml(group)}</span>
      <code>${escapeHtml(JSON.stringify(payload, null, 2))}</code>
    </article>
  `);
  artifactGrid.className = cards.length ? "artifact-grid" : "artifact-grid empty-state";
  artifactGrid.innerHTML = cards.length ? cards.join("") : "No normalized artifact paths are available.";
}

function orderedColumns(rows, preferredColumns = []) {
  if (!rows.length) {
    return [];
  }
  const availableColumns = Object.keys(rows[0] || {});
  const preferred = preferredColumns.filter((column) => availableColumns.includes(column));
  const extras = availableColumns
    .filter((column) => !preferred.includes(column))
    .slice(0, Math.max(0, 7 - preferred.length));
  return preferred.concat(extras);
}

function renderRecordTable(title, rows, preferredColumns, emptyMessage) {
  if (!rows.length) {
    return `
      <article class="stack-item">
        <strong>${escapeHtml(title)}</strong>
        <span class="meta">${escapeHtml(emptyMessage)}</span>
      </article>
    `;
  }

  const columns = orderedColumns(rows, preferredColumns);
  return `
    <article class="stack-item">
      <strong>${escapeHtml(title)}</strong>
      <div class="table-wrap">
        <table class="dense-table">
          <thead>
            <tr>
              ${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (row) => `
                  <tr>
                    ${columns.map((column) => `<td>${escapeHtml(formatValue(row[column]))}</td>`).join("")}
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    </article>
  `;
}

function renderTradeTraces(traces) {
  if (!traces.length) {
    return `
      <article class="stack-item">
        <strong>Trade traces</strong>
        <span class="meta">No bounded trade traces are available for this family.</span>
      </article>
    `;
  }

  return `
    <article class="stack-item">
      <strong>Trade traces</strong>
      <div class="stack-list">
        ${traces
          .map((trace) => {
            const states = Array.isArray(trace.states) ? trace.states : [];
            return `
              <article class="stack-item detail-card">
                <strong>${escapeHtml(trace.game_id || "trace")}</strong>
                <span class="meta">${escapeHtml(trace.team_slug || "n/a")} / ${escapeHtml(trace.entry_context_bucket || "n/a")}</span>
                <div class="tag-row">
                  ${tag(`entry_price: ${formatValue(trace.entry_price)}`)}
                  ${tag(`exit_price: ${formatValue(trace.exit_price)}`)}
                  ${tag(`return: ${formatValue(trace.gross_return_with_slippage)}`)}
                  ${tag(`states: ${formatValue(states.length, 0)}`)}
                </div>
                ${
                  states.length
                    ? `
                      <div class="table-wrap">
                        <table class="dense-table">
                          <thead>
                            <tr>
                              <th>Period</th>
                              <th>Clock</th>
                              <th>Price</th>
                              <th>Diff</th>
                            </tr>
                          </thead>
                          <tbody>
                            ${states
                              .slice(0, 6)
                              .map(
                                (state) => `
                                  <tr>
                                    <td>${escapeHtml(formatValue(state.period, 0))}</td>
                                    <td>${escapeHtml(state.clock || "n/a")}</td>
                                    <td>${escapeHtml(formatValue(state.team_price))}</td>
                                    <td>${escapeHtml(formatValue(state.score_diff, 0))}</td>
                                  </tr>
                                `,
                              )
                              .join("")}
                          </tbody>
                        </table>
                      </div>
                    `
                    : ""
                }
              </article>
            `;
          })
          .join("")}
      </div>
    </article>
  `;
}

function renderBacktestIndex(payload) {
  const families = payload.families || [];
  latestBacktestIndexPayload = payload;
  syncComparisonFamilySelect(families);

  if (!families.length) {
    comparisonIndex.className = "stack-list empty-state";
    comparisonIndex.textContent = "No strategy families are available in the current backtest snapshot.";
    return;
  }

  comparisonIndex.className = "stack-list";
  comparisonIndex.innerHTML = families
    .map((family) => {
      const summary = family.summary || {};
      const isSelected = family.strategy_family === selectedStrategyFamily;
      return `
        <article class="stack-item game-item${isSelected ? " selected" : ""}">
          <strong>${escapeHtml(family.strategy_family)}</strong>
          <span class="meta">${escapeHtml(summary.label_reason || "No candidate rationale available.")}</span>
          <div class="tag-row">
            ${tag(`trades: ${formatValue(summary.trade_count, 0)}`)}
            ${tag(`win_rate: ${formatValue(summary.win_rate)}`)}
            ${tag(`return: ${formatValue(summary.avg_gross_return_with_slippage)}`)}
            ${tag(`label: ${summary.candidate_label || "n/a"}`, summary.candidate_label !== "keep")}
          </div>
          <div class="item-actions">
            <button
              type="button"
              class="ghost-button"
              data-strategy-family="${escapeHtml(family.strategy_family)}"
            >
              Inspect
            </button>
          </div>
        </article>
      `;
    })
    .join("");

  bindStrategyFamilyButtons(comparisonIndex);
}

function renderBacktestDetail(payload) {
  if (!payload || !payload.strategy_family) {
    comparisonDetail.className = "stack-list empty-state";
    comparisonDetail.textContent = "Choose a strategy family to inspect its bounded detail preview.";
    return;
  }

  const summary = payload.summary || {};
  const candidateFreeze = payload.candidate_freeze || {};
  comparisonDetail.className = "stack-list";
  comparisonDetail.innerHTML = `
    <div class="metric-grid comparison-metrics">
      ${metricCard("Family", payload.strategy_family)}
      ${metricCard("Trades", formatValue(summary.trade_count, 0))}
      ${metricCard("Return With Slippage", formatValue(summary.avg_gross_return_with_slippage))}
      ${metricCard("Win Rate", formatValue(summary.win_rate))}
    </div>
    <article class="stack-item">
      <strong>${escapeHtml(payload.strategy_family)} summary</strong>
      <span class="meta">${escapeHtml(candidateFreeze.label_reason || summary.label_reason || "No candidate-freeze rationale is available.")}</span>
      <div class="tag-row">
        ${tag(`candidate: ${candidateFreeze.candidate_label || summary.candidate_label || "n/a"}`, (candidateFreeze.candidate_label || summary.candidate_label) !== "keep")}
        ${tag(`entry_rule: ${summary.entry_rule || "n/a"}`)}
        ${tag(`hold_time: ${formatValue(summary.avg_hold_time_seconds, 0)}`)}
        ${tag(`mfe: ${formatValue(summary.avg_mfe_after_entry)}`)}
        ${tag(`mae: ${formatValue(summary.avg_mae_after_entry)}`)}
      </div>
    </article>
    ${renderRecordTable(
      "Best trades",
      payload.best_trades || [],
      ["game_id", "team_slug", "entry_price", "exit_price", "gross_return_with_slippage", "hold_time_seconds"],
      "No bounded best-trade preview is available.",
    )}
    ${renderRecordTable(
      "Worst trades",
      payload.worst_trades || [],
      ["game_id", "team_slug", "entry_price", "exit_price", "gross_return_with_slippage", "hold_time_seconds"],
      "No bounded worst-trade preview is available.",
    )}
    ${renderRecordTable(
      "Context summary",
      payload.context_summary || [],
      ["context_bucket", "trade_count", "win_rate", "avg_gross_return_with_slippage"],
      "No bounded context summary is available.",
    )}
    ${renderTradeTraces(payload.trade_traces || [])}
  `;
}

function renderControlPlane(payload) {
  controlGrid.innerHTML = [
    metricCard("Local Root", "Resolved", payload.local_root),
    metricCard("Default Output Root", "Resolved", payload.default_output_root),
    metricCard(
      "Known Versions",
      payload.available_analysis_versions.length,
      payload.available_analysis_versions.join(", ") || "none",
    ),
    metricCard(
      "Tracked Studio Runs",
      payload.recent_runs.length,
      payload.latest_analysis_output_dir || "no output directory discovered yet",
    ),
  ].join("");

  const versions = payload.available_analysis_versions || [];
  versionOptions.innerHTML = versions.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
  const analysisVersionInput = document.getElementById("analysis_version");
  if (!analysisVersionInput.value.trim() && versions.length) {
    analysisVersionInput.value = versions[versions.length - 1];
  }

  const latest = payload.latest_validation;
  if (!latest) {
    latestValidation.className = "stack-list empty-state";
    latestValidation.textContent = "No validation summary found yet.";
  } else {
    latestValidation.className = "stack-list";
    latestValidation.innerHTML = `
      <article class="stack-item">
        <strong>${escapeHtml(latest.run_label)} / ${escapeHtml(latest.target || "unknown target")}</strong>
        <span class="meta">${escapeHtml(latest.summary_json)}</span>
        <div class="tag-row">
          ${tag(`version: ${latest.analysis_version || "n/a"}`)}
          ${tag(`commands: ${latest.command_count || 0}`)}
          ${tag(`all_ok: ${latest.all_commands_ok}`, !latest.all_commands_ok)}
          ${tag(`research_ready: ${latest.universe?.research_ready_games ?? "n/a"}`)}
        </div>
      </article>
    `;
  }

  const validations = payload.recent_validations || [];
  if (!validations.length) {
    recentValidations.className = "stack-list empty-state";
    recentValidations.textContent = "No validation history found.";
  } else {
    recentValidations.className = "stack-list";
    recentValidations.innerHTML = validations
      .map(
        (row) => `
          <article class="stack-item">
            <strong>${escapeHtml(row.run_label)}</strong>
            <span class="meta">${escapeHtml(row.summary_markdown)}</span>
            <div class="tag-row">
              ${tag(`target: ${row.target || "n/a"}`)}
              ${tag(`version: ${row.analysis_version || "n/a"}`)}
              ${tag(`all_ok: ${row.all_commands_ok}`, !row.all_commands_ok)}
            </div>
          </article>
        `,
      )
      .join("");
  }

  const runs = payload.recent_runs || [];
  if (!runs.length) {
    runList.className = "stack-list empty-state";
    runList.textContent = "No studio runs launched yet.";
  } else {
    runList.className = "stack-list";
    runList.innerHTML = runs
      .map(
        (run) => `
          <article class="stack-item">
            <strong>${escapeHtml(run.action)}</strong>
            <span class="meta">${escapeHtml(run.status)} / ${escapeHtml(run.created_at)}</span>
            <span class="meta">${escapeHtml(run.stdout_path || "")}</span>
            <div class="tag-row">
              ${tag(`season: ${run.season}`)}
              ${tag(`phase: ${run.season_phase}`)}
              ${tag(`version: ${run.analysis_version}`)}
              ${tag(`return: ${run.return_code ?? "pending"}`, run.status === "failed")}
            </div>
          </article>
        `,
      )
      .join("");
  }
}

function renderGameList(payload) {
  const items = payload.items || [];
  if (!items.length) {
    gameList.className = "stack-list empty-state";
    gameList.textContent = "No finished games matched the current filters.";
    return;
  }

  gameList.className = "stack-list";
  gameList.innerHTML = items
    .map((item) => {
      const isSelected = item.game_id === selectedGameId;
      return `
        <article class="stack-item game-item${isSelected ? " selected" : ""}">
          <strong>${escapeHtml(item.matchup || item.game_id)}</strong>
          <span class="meta">${escapeHtml(item.game_date || "n/a")} / ${escapeHtml(item.game_start_time || "n/a")}</span>
          <div class="tag-row">
            ${tag(`coverage: ${(item.coverage_statuses || []).join(", ") || "n/a"}`, !item.research_ready_game_flag)}
            ${tag(`research_ready: ${item.research_ready_game_flag}`, !item.research_ready_game_flag)}
            ${tag(`winner: ${item.winner_team_slug || "n/a"}`)}
          </div>
          <div class="tag-row">
            ${tag(`home swing: ${formatValue(item.home?.total_swing)}`)}
            ${tag(`away swing: ${formatValue(item.away?.total_swing)}`)}
          </div>
          <div class="item-actions">
            <button type="button" class="ghost-button" data-game-id="${escapeHtml(item.game_id)}">Inspect</button>
          </div>
        </article>
      `;
    })
    .join("");

  Array.from(gameList.querySelectorAll("[data-game-id]")).forEach((button) => {
    button.addEventListener("click", () => {
      loadGameDetail(button.getAttribute("data-game-id")).catch(() => {});
    });
  });
}

function renderProfileCard(label, profile) {
  if (!profile) {
    return `
      <article class="stack-item detail-card">
        <strong>${escapeHtml(label)}</strong>
        <span class="meta">No profile row is available for this side.</span>
      </article>
    `;
  }
  return `
    <article class="stack-item detail-card">
      <strong>${escapeHtml(label)} / ${escapeHtml(profile.team_slug || "n/a")}</strong>
      <span class="meta">${escapeHtml(profile.opponent_team_slug || "n/a")} opponent / ${escapeHtml(profile.coverage_status || "n/a")}</span>
      <div class="tag-row">
        ${tag(`opening: ${formatValue(profile.opening_price)}`)}
        ${tag(`closing: ${formatValue(profile.closing_price)}`)}
        ${tag(`swing: ${formatValue(profile.total_swing)}`)}
        ${tag(`inversions: ${formatValue(profile.inversion_count, 0)}`)}
      </div>
      <div class="tag-row">
        ${tag(`price_path_ok: ${profile.price_path_reconciled_flag}`)}
        ${tag(`winner: ${profile.final_winner_flag}`)}
        ${tag(`stable80: ${formatValue(profile.winner_stable_80_clock_elapsed_seconds, 0)}`)}
      </div>
    </article>
  `;
}

function renderStatePanelSide(label, payload) {
  const summary = payload?.summary || {};
  const rows = payload?.rows || [];
  const table = rows.length
    ? `
      <div class="table-wrap">
        <table class="dense-table">
          <thead>
            <tr>
              <th>Idx</th>
              <th>Period</th>
              <th>Clock</th>
              <th>Score</th>
              <th>Diff</th>
              <th>Context</th>
              <th>Price</th>
              <th>MFE</th>
              <th>MAE</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (row) => `
                  <tr>
                    <td>${escapeHtml(formatValue(row.state_index, 0))}</td>
                    <td>${escapeHtml(formatValue(row.period, 0))}</td>
                    <td>${escapeHtml(row.clock || "n/a")}</td>
                    <td>${escapeHtml(`${formatValue(row.score_for, 0)}-${formatValue(row.score_against, 0)}`)}</td>
                    <td>${escapeHtml(formatValue(row.score_diff, 0))}</td>
                    <td>${escapeHtml(row.context_bucket || "n/a")}</td>
                    <td>${escapeHtml(formatValue(row.team_price))}</td>
                    <td>${escapeHtml(formatValue(row.mfe_from_state))}</td>
                    <td>${escapeHtml(formatValue(row.mae_from_state))}</td>
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : '<div class="empty-state">No state rows are available for this side.</div>';

  return `
    <div>
      <article class="stack-item detail-card">
        <strong>${escapeHtml(label)} state window</strong>
        <span class="meta">${escapeHtml(formatValue(summary.state_count, 0))} available states / latest idx ${escapeHtml(formatValue(summary.latest_state_index, 0))}</span>
        <div class="tag-row">
          ${tag(`price_min: ${formatValue(summary.price_min)}`)}
          ${tag(`price_max: ${formatValue(summary.price_max)}`)}
          ${(summary.top_context_buckets || [])
            .slice(0, 3)
            .map((bucket) => tag(`${bucket.context_bucket}: ${formatValue(bucket.state_count, 0)}`))
            .join("")}
        </div>
      </article>
      ${table}
    </div>
  `;
}

function renderGameDetail(payload) {
  const game = payload.game || {};
  gameDetail.className = "stack-list";
  gameDetail.innerHTML = `
    <article class="stack-item">
      <strong>${escapeHtml(game.matchup || game.game_id || "Selected game")}</strong>
      <span class="meta">${escapeHtml(game.game_date || "n/a")} / ${escapeHtml(payload.analysis_version || "n/a")}</span>
      <div class="tag-row">
        ${tag(`coverage: ${(game.coverage_statuses || []).join(", ") || "n/a"}`, !game.research_ready_game_flag)}
        ${tag(`research_ready: ${game.research_ready_game_flag}`, !game.research_ready_game_flag)}
        ${tag(`winner: ${game.winner_team_slug || "n/a"}`)}
      </div>
    </article>
    <div class="detail-grid">
      ${renderProfileCard("Home", payload.profiles?.home)}
      ${renderProfileCard("Away", payload.profiles?.away)}
    </div>
    <div class="dual-panel">
      ${renderStatePanelSide("Home", payload.state_panel?.home)}
      ${renderStatePanelSide("Away", payload.state_panel?.away)}
    </div>
  `;
}

async function loadControlPlane() {
  const context = currentLoaderContext();
  const params = new URLSearchParams();
  params.set("season", context.season || "2025-26");
  params.set("season_phase", context.season_phase || "regular_season");
  const response = await fetch(`/v1/analysis/studio/control?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error?.message || "Failed to load the analysis studio control plane.");
  }
  renderControlPlane(payload);
}

async function loadBacktestDetail(strategyFamily, options = {}) {
  const { silent = false } = options;
  const loaderContext = currentLoaderContext();
  const comparisonOptions = currentComparisonOptions();
  const resolvedFamily = String(strategyFamily || comparisonOptions.strategy_family || "").trim();
  if (!resolvedFamily) {
    renderBacktestDetail(null);
    setComparisonState(false, "Select a strategy family first.");
    return null;
  }

  const params = buildQueryParams({
    season: loaderContext.season || "2025-26",
    season_phase: loaderContext.season_phase || "regular_season",
    analysis_version: loaderContext.analysis_version,
    backtest_experiment_id: loaderContext.backtest_experiment_id,
    output_root: loaderContext.output_root,
    trade_limit: comparisonOptions.trade_limit,
    context_limit: comparisonOptions.context_limit,
    trace_limit: comparisonOptions.trace_limit,
  });

  if (!silent) {
    clearError();
    setComparisonState(true, `Loading ${resolvedFamily} detail...`);
  }
  try {
    const response = await fetch(
      `/v1/analysis/studio/backtests/${encodeURIComponent(resolvedFamily)}?${params.toString()}`,
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || payload.detail || "The backtest family detail request failed.");
    }
    selectedStrategyFamily = resolvedFamily;
    if (comparisonFamilySelect.options.length) {
      comparisonFamilySelect.value = resolvedFamily;
    }
    renderStrategies();
    renderBacktestIndex(latestBacktestIndexPayload);
    renderBacktestDetail(payload);
    if (!silent) {
      setComparisonState(false, `Loaded ${resolvedFamily} trade, context, and trace previews.`);
    }
    return payload;
  } catch (error) {
    if (!silent) {
      showError(error.message || "The backtest family detail request failed.");
      setComparisonState(false, "Backtest family detail load failed. Check the error banner.");
    }
    throw error;
  }
}

async function loadBacktestIndex(preferredStrategyFamily = selectedStrategyFamily) {
  const loaderContext = currentLoaderContext();
  const params = buildQueryParams({
    season: loaderContext.season || "2025-26",
    season_phase: loaderContext.season_phase || "regular_season",
    analysis_version: loaderContext.analysis_version,
    backtest_experiment_id: loaderContext.backtest_experiment_id,
    output_root: loaderContext.output_root,
  });

  clearError();
  setComparisonState(true, "Loading ranked backtest families...");
  try {
    const response = await fetch(`/v1/analysis/studio/backtests?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || payload.detail || "The backtest family index request failed.");
    }

    latestBacktestIndexPayload = payload;
    latestStrategyRankings = payload.benchmark?.strategy_rankings || latestStrategyRankings;
    renderStrategies();
    renderBacktestIndex(payload);

    const families = payload.families || [];
    if (!families.length) {
      selectedStrategyFamily = null;
      renderBacktestDetail(null);
      setComparisonState(false, "No strategy families were found in this backtest snapshot.");
      return payload;
    }

    const nextFamily = families.some((family) => family.strategy_family === preferredStrategyFamily)
      ? preferredStrategyFamily
      : families[0].strategy_family;
    selectedStrategyFamily = nextFamily;
    await loadBacktestDetail(nextFamily, { silent: true });
    renderStrategies();
    renderBacktestIndex(payload);
    setComparisonState(false, `Loaded ${families.length} ranked strategy families from ${payload.analysis_version}.`);
    return payload;
  } catch (error) {
    showError(error.message || "The backtest family index request failed.");
    setComparisonState(false, "Backtest family index load failed. Check the error banner.");
    throw error;
  }
}

async function loadGameDetail(gameId, options = {}) {
  const { silent = false } = options;
  const loaderContext = currentLoaderContext();
  const params = buildQueryParams({
    season: loaderContext.season || "2025-26",
    season_phase: loaderContext.season_phase || "regular_season",
    analysis_version: loaderContext.analysis_version,
    output_root: loaderContext.output_root,
  });
  if (!silent) {
    clearError();
    setExplorerState(true, `Loading ${gameId}...`);
  }
  try {
    const response = await fetch(`/v1/analysis/studio/games/${encodeURIComponent(gameId)}?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || payload.detail || "Failed to load the selected analysis game.");
    }
    selectedGameId = gameId;
    renderGameDetail(payload);
    if (!silent) {
      renderGameList(latestGameListPayload);
      setExplorerState(false, `Loaded ${payload.game?.matchup || gameId}.`);
    }
    return payload;
  } catch (error) {
    if (!silent) {
      showError(error.message || "Failed to load the selected analysis game.");
      setExplorerState(false, "Game detail load failed. Check the error banner.");
    }
    throw error;
  }
}

async function loadGameExplorer(preferredGameId = selectedGameId) {
  const loaderContext = currentLoaderContext();
  const filters = currentExplorerFilters();
  const params = buildQueryParams({
    season: loaderContext.season || "2025-26",
    season_phase: loaderContext.season_phase || "regular_season",
    analysis_version: loaderContext.analysis_version,
    output_root: loaderContext.output_root,
    team_slug: filters.team_slug,
    coverage_status: filters.coverage_status,
    game_date: filters.game_date,
  });

  clearError();
  setExplorerState(true, "Loading finished-game explorer...");
  try {
    const response = await fetch(`/v1/analysis/studio/games?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || payload.detail || "The game explorer request failed.");
    }
    const items = payload.items || [];
    latestGameListPayload = payload;
    if (!items.length) {
      selectedGameId = null;
      renderGameList(payload);
      gameDetail.className = "stack-list empty-state";
      gameDetail.textContent = "No finished games matched the current filters.";
      setExplorerState(false, "No finished games matched the current filters.");
      return;
    }

    const nextGameId = items.some((item) => item.game_id === preferredGameId)
      ? preferredGameId
      : items[0].game_id;
    selectedGameId = nextGameId;
    renderGameList(payload);
    await loadGameDetail(nextGameId, { silent: true });
    renderGameList(payload);
    setExplorerState(
      false,
      `Loaded ${payload.returned_games} of ${payload.total_games} finished games from ${payload.analysis_version}.`,
    );
  } catch (error) {
    showError(error.message || "The game explorer request failed.");
    setExplorerState(false, "Game explorer load failed. Check the error banner.");
  }
}

async function loadSnapshot() {
  clearError();
  const context = currentLoaderContext();
  const params = buildQueryParams(context);

  setLoadingState(true, "Resolving the latest consumer snapshot...");
  try {
    const response = await fetch(`/v1/analysis/studio/snapshot?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || payload.detail || "The snapshot request failed.");
    }
    latestStrategyRankings = payload.benchmark?.strategy_rankings || [];
    renderMetadata(payload);
    renderUniverse(payload);
    renderStrategies();
    renderModels(payload);
    renderReportSections(payload);
    renderArtifacts(payload);
    setLoadingState(false, `Loaded ${payload.analysis_version} from ${payload.output_dir}`);
    await loadControlPlane();
    try {
      await loadBacktestIndex(selectedStrategyFamily);
    } catch (_) {
      // Keep the snapshot visible even if the comparison panel fails independently.
    }
    await loadGameExplorer(selectedGameId);
  } catch (error) {
    showError(error.message || "The snapshot request failed.");
    setLoadingState(false, "Snapshot load failed. Check the error banner and adjust the request.");
  }
}

async function startRun() {
  clearError();
  const context = currentLoaderContext();
  const payload = {
    action: document.getElementById("run_action").value,
    validation_target: document.getElementById("validation_target").value,
    rebuild: document.getElementById("rebuild").checked,
    season: context.season || "2025-26",
    season_phase: context.season_phase || "regular_season",
    analysis_version: context.analysis_version || "v1_0_1",
  };

  setRunState(true, "Launching the local analysis command...");
  try {
    const response = await fetch("/v1/analysis/studio/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error?.message || "Failed to launch the analysis studio run.");
    }
    setRunState(false, `Run ${data.run_id} launched for ${data.action}.`);
    await loadControlPlane();
  } catch (error) {
    showError(error.message || "Failed to launch the analysis studio run.");
    setRunState(false, "Run launch failed. Check the error banner.");
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadSnapshot();
});

runForm.addEventListener("submit", (event) => {
  event.preventDefault();
  startRun();
});

explorerForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadGameExplorer();
});

comparisonForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadBacktestDetail(currentComparisonOptions().strategy_family).catch(() => {});
});

loadControlPlane()
  .then(() => loadSnapshot())
  .catch((error) => {
    showError(error.message || "Failed to initialize the analysis studio.");
  });

window.setInterval(() => {
  loadControlPlane().catch(() => {});
}, 5000);
