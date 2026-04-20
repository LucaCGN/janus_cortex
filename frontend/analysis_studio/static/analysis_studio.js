const form = document.getElementById("snapshot-form");
const runForm = document.getElementById("run-form");
const loadButton = document.getElementById("load-button");
const runButton = document.getElementById("run-button");
const statusLine = document.getElementById("status-line");
const runStatusLine = document.getElementById("run-status-line");
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function currentLoaderContext() {
  return {
    season: document.getElementById("season").value.trim(),
    season_phase: document.getElementById("season_phase").value.trim(),
    analysis_version: document.getElementById("analysis_version").value.trim(),
    backtest_experiment_id: document.getElementById("backtest_experiment_id").value.trim(),
    output_root: document.getElementById("output_root").value.trim(),
  };
}

function renderControlPlane(payload) {
  controlGrid.innerHTML = [
    metricCard("Local Root", "Resolved", payload.local_root),
    metricCard("Default Output Root", "Resolved", payload.default_output_root),
    metricCard("Known Versions", payload.available_analysis_versions.length, payload.available_analysis_versions.join(", ") || "none"),
    metricCard("Tracked Studio Runs", payload.recent_runs.length, payload.latest_analysis_output_dir || "no output directory discovered yet"),
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

async function loadSnapshot() {
  clearError();
  const context = currentLoaderContext();
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(context)) {
    if (value) {
      params.set(key, value);
    }
  }

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

loadControlPlane()
  .then(() => loadSnapshot())
  .catch((error) => {
    showError(error.message || "Failed to initialize the analysis studio.");
  });

window.setInterval(() => {
  loadControlPlane().catch(() => {});
}, 5000);
