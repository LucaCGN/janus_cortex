const form = document.getElementById("snapshot-form");
const loadButton = document.getElementById("load-button");
const statusLine = document.getElementById("status-line");
const errorLine = document.getElementById("error-line");
const metadataGrid = document.getElementById("metadata-grid");
const universeGrid = document.getElementById("universe-grid");
const strategyTable = document.getElementById("strategy-table");
const modelList = document.getElementById("model-list");
const reportSections = document.getElementById("report-sections");
const artifactGrid = document.getElementById("artifact-grid");

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
        <span class="meta">${escapeHtml(section.key)} · rows ${escapeHtml(section.row_count)}</span>
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

async function loadSnapshot() {
  clearError();
  const formData = new FormData(form);
  const params = new URLSearchParams();
  for (const [key, value] of formData.entries()) {
    const trimmed = String(value).trim();
    if (trimmed) {
      params.set(key, trimmed);
    }
  }

  setLoadingState(true, "Resolving the latest consumer snapshot...");
  try {
    const response = await fetch(`/v1/analysis/studio/snapshot?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "The snapshot request failed.");
    }
    renderMetadata(payload);
    renderUniverse(payload);
    renderStrategies(payload);
    renderModels(payload);
    renderReportSections(payload);
    renderArtifacts(payload);
    setLoadingState(false, `Loaded ${payload.analysis_version} from ${payload.output_dir}`);
  } catch (error) {
    showError(error.message || "The snapshot request failed.");
    setLoadingState(false, "Snapshot load failed. Check the error banner and adjust the request.");
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadSnapshot();
});

loadSnapshot();
