const form = document.getElementById("snapshot-form");
const runForm = document.getElementById("run-form");
const explorerForm = document.getElementById("explorer-form");
const loadButton = document.getElementById("load-button");
const runButton = document.getElementById("run-button");
const explorerButton = document.getElementById("explorer-button");
const statusLine = document.getElementById("status-line");
const runStatusLine = document.getElementById("run-status-line");
const explorerStatusLine = document.getElementById("explorer-status-line");
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

let selectedGameId = null;
let latestGameListPayload = { items: [] };

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

function renderStrategies(snapshot) {
  const rows = snapshot.benchmark?.strategy_rankings || [];
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
          <th>Label</th>
          <th>Rule</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .slice(0, 8)
          .map(
            (row) => `
              <tr>
                <td>${escapeHtml(row.rank)}</td>
                <td><strong>${escapeHtml(row.strategy_family)}</strong></td>
                <td>${escapeHtml(row.avg_gross_return_with_slippage ?? "n/a")}</td>
                <td>${escapeHtml(row.trade_count ?? "n/a")}</td>
                <td>${escapeHtml(row.candidate_label ?? "n/a")}</td>
                <td>${escapeHtml(row.entry_rule ?? "n/a")}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
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
    renderMetadata(payload);
    renderUniverse(payload);
    renderStrategies(payload);
    renderModels(payload);
    renderReportSections(payload);
    renderArtifacts(payload);
    setLoadingState(false, `Loaded ${payload.analysis_version} from ${payload.output_dir}`);
    await loadControlPlane();
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

loadControlPlane()
  .then(() => loadSnapshot())
  .catch((error) => {
    showError(error.message || "Failed to initialize the analysis studio.");
  });

window.setInterval(() => {
  loadControlPlane().catch(() => {});
}, 5000);
