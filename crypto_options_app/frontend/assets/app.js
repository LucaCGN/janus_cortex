const API = window.CRYPTO_OPTIONS_API_BASE || "/v1/crypto-options-app";

const state = {
  control: null,
  dashboard: null,
  health: null,
  selection: null,
  strategies: null,
  signalBacktests: null,
  signalLiveReplay: null,
  strategyBacktests: null,
  strategyLiveReplay: null,
  endpointStatus: {},
  activeView: initialView(),
  selectedEventKey: null,
  expandedSignalKey: null,
  signalFilters: { type: "", tier: "", text: "" },
  strategyFilters: { state: "", family: "", track: "", text: "" },
  flowFilters: { ledger: "flow", positions: "grouped" },
};

const $ = (id) => document.getElementById(id);

function initialView() {
  const path = window.location.pathname.toLowerCase();
  if (path.includes("/signals/")) return "signals";
  if (path.includes("/strategies/")) return "strategies";
  return "overview";
}

async function fetchJSON(path, timeoutMs = 18000) {
  if (typeof window.fetch !== "function") {
    return xhrJSON(path, timeoutMs);
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      const error = new Error(`${response.status} ${response.statusText}`);
      error.status = response.status;
      error.path = path;
      error.kind = response.status === 404 ? "unavailable" : "http";
      throw error;
    }
    return response.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      error.kind = "timeout";
      error.path = path;
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function xhrJSON(path, timeoutMs = 18000) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("GET", `${API}${path}`, true);
    request.timeout = timeoutMs;
    request.setRequestHeader("Accept", "application/json");
    request.onreadystatechange = () => {
      if (request.readyState !== 4) return;
      if (request.status >= 200 && request.status < 300) {
        try {
          resolve(JSON.parse(request.responseText));
        } catch (error) {
          reject(error);
        }
      } else {
        const error = new Error(`${request.status} ${request.statusText || "request failed"}`);
        error.status = request.status;
        error.path = path;
        error.kind = request.status === 404 ? "unavailable" : "http";
        reject(error);
      }
    };
    request.ontimeout = () => {
      const error = new Error("request timeout");
      error.kind = "timeout";
      error.path = path;
      reject(error);
    };
    request.onerror = () => {
      const error = new Error("request failed");
      error.kind = "network";
      error.path = path;
      reject(error);
    };
    request.send();
  });
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

function toNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function money(value) {
  const parsed = toNumber(value);
  if (parsed === null) return "n/a";
  return `${parsed < 0 ? "-" : ""}$${Math.abs(parsed).toFixed(2)}`;
}

function cents(value) {
  const parsed = toNumber(value);
  if (parsed === null) return "n/a";
  return `${Math.round(parsed * 100)}c`;
}

function pct(value) {
  const parsed = toNumber(value);
  if (parsed === null) return "n/a";
  return `${(parsed * 100).toFixed(1)}%`;
}

function number(value) {
  const parsed = toNumber(value);
  if (parsed === null) return "--";
  return Math.abs(parsed) >= 1000 ? parsed.toLocaleString() : String(parsed);
}

function shortTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString();
}

function shortDateTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
}

function toneClass(value) {
  const parsed = toNumber(value);
  if (parsed === null || parsed === 0) return "";
  return parsed > 0 ? "pos" : "neg";
}

function slug(value) {
  return String(value || "unknown").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

function displayStatus(value) {
  const raw = String(value || "unknown");
  const upper = raw.toUpperCase();
  const labels = {
    NEEDS_V2_REVIEW: "NEEDS REVISION",
    STRUCTURAL_PASS: "STRUCTURAL PASS",
    PROMOTION_READY: "PROMOTION READY",
    SHADOW_REVIEW: "SHADOW REVIEW",
    LIVE_RUNNING: "LIVE RUNNING",
    DEMOTED_TO_SHADOW: "DEMOTED TO SHADOW",
    REVIEW_BLOCKED: "REVIEW BLOCKED",
  };
  return labels[upper] || raw.replaceAll("_", " ");
}

function setPillTone(id, tone) {
  const node = $(id);
  if (!node) return;
  node.classList.remove("safe", "warn", "bad");
  if (tone) node.classList.add(tone);
}

function describeRuntime(control, dashboard, active, audit) {
  const runtime = String(active.status || "read_only");
  const runtimeLower = runtime.toLowerCase();
  const auditStatus = String(audit.status || "audit_pending");
  const auditLower = auditStatus.toLowerCase();
  const ordersAllowed = Boolean(control.orders_allowed);
  const liveTradingAuthorized = Boolean(control.live_trading_authorized);
  const manualOrdersAvoided = Boolean(control.manual_orders_avoided || active.manual_orders_avoided);
  const apiFlagsRemainFalse = active.global_api_live_flags_remain_false !== false;
  const gatedReadOnly =
    runtimeLower === "blocked" &&
    !ordersAllowed &&
    !liveTradingAuthorized &&
    manualOrdersAvoided &&
    apiFlagsRemainFalse;

  if (gatedReadOnly) {
    return {
      runtimeLabel: "Gated read only",
      runtimeTone: "warn",
      railLabel: "Gated read-only runtime",
      auditLabel: auditLower === "matched"
        ? "Audit matched. Latest supervised run stopped under gating; UI order auth stays disabled."
        : `Audit ${displayStatus(auditStatus)}. Latest supervised run stopped under gating; UI order auth stays disabled.`,
      auditTone: auditLower === "matched" ? "safe" : "warn",
    };
  }

  const runtimeTone = ["running", "validated", "matched"].includes(runtimeLower)
    ? "safe"
    : ["read_only", "read only", "gated", "review"].includes(runtimeLower)
      ? "warn"
      : ["blocked", "failed", "error"].includes(runtimeLower)
        ? "bad"
        : "";
  const auditTone = auditLower === "matched" ? "safe" : auditLower.includes("blocked") ? "bad" : "";
  return {
    runtimeLabel: displayStatus(runtime),
    runtimeTone,
    railLabel: displayStatus(runtime),
    auditLabel: `Audit: ${displayStatus(auditStatus)}`,
    auditTone,
  };
}

function showToast(message, tone = "info") {
  const toast = $("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast is-visible ${tone}`;
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove("is-visible"), 3600);
}

function setEndpointStatus(key, info) {
  state.endpointStatus[key] = info;
}

function getEndpointStatus(key) {
  return state.endpointStatus[key] || { state: "idle", detail: "" };
}

function endpointEmptyState(key, emptyMessage, unavailableMessage) {
  const info = getEndpointStatus(key);
  if (info.state === "unavailable") {
    return unavailableMessage || "This backend does not expose that control-center route yet.";
  }
  if (info.state === "error") {
    return `Endpoint load failed${info.detail ? `: ${info.detail}` : ""}.`;
  }
  if (info.state === "loading") {
    return "Loading data...";
  }
  return emptyMessage;
}

function activateView(view) {
  state.activeView = view;
  for (const button of document.querySelectorAll(".nav-tab")) {
    button.classList.toggle("is-active", button.dataset.view === view);
  }
  for (const panel of document.querySelectorAll(".view-panel")) {
    panel.classList.toggle("is-active", panel.dataset.panel === view);
  }
}

function signalRows() {
  return state.selection?.signals || [];
}

function strategyRows() {
  return state.strategies?.strategies || [];
}

function promotionPolicyContract() {
  return state.strategies?.policy_contract || {};
}

function eventRows() {
  return state.control?.events || [];
}

function selectedEvent() {
  const rows = eventRows();
  if (!rows.length) return null;
  return rows.find((row) => row.event_path_stats_key === state.selectedEventKey) || rows[0];
}

function filteredSignals() {
  const text = state.signalFilters.text.trim().toLowerCase();
  return signalRows().filter((row) => {
    if (state.signalFilters.type && row.signal_type !== state.signalFilters.type) return false;
    if (state.signalFilters.tier && row.selection_tier !== state.signalFilters.tier) return false;
    if (!text) return true;
    const haystack = [
      row.signal_id,
      row.family,
      row.signal_type,
      row.variant,
      row.purpose,
      (row.sources || []).join(" "),
      row.next_action,
    ].join(" ").toLowerCase();
    return haystack.includes(text);
  });
}

function signalRowKey(row) {
  return [
    row.signal_id,
    row.signal_type,
    row.variant,
    row.selection_reason,
    row.version || "v1",
  ].filter(Boolean).join("::") || "signal::unknown";
}

function asList(value) {
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && item !== "");
  if (value === null || value === undefined || value === "") return [];
  return [value];
}

function signalEvidenceLabel(row) {
  const hit = row.live_shadow_hit_rate ?? row.latest_phase_hit_rate ?? row.hit_rate;
  const samples = row.live_shadow_sample_count ?? row.latest_phase_sample_count ?? row.sample_count;
  const events = row.distinct_event_count ?? row.event_count;
  const strict = asList(row.strict_review_reasons || row.strict_review).length;
  return `${pct(hit)} hit, ${number(samples)} samples, ${number(events)} events, ${number(strict)} strict flags`;
}

function signalEvidenceMetrics(row) {
  const metrics = [
    ["Selection tier", displayStatus(row.selection_tier || "unknown")],
    ["Queue status", displayStatus(row.queue_status || row.status || "unknown")],
    ["Promotion state", displayStatus(row.promotion_state || "none")],
    ["Signal id", row.signal_id || row.id || "n/a"],
    ["Purpose", row.purpose || "n/a"],
    ["Family", row.family || "n/a"],
    ["Sources", asList(row.sources).join(", ") || "n/a"],
    ["Source blocks", asList(row.source_blocks || row.required_data_blocks).join(" / ") || "n/a"],
    ["Selection reason", row.selection_reason || "n/a"],
    ["Action state", displayStatus(row.action_state || "VALIDATION_REQUIRED")],
    ["Next action", row.next_action || row.selection_next_action || "review evidence"],
    ["Impact if degraded", row.impact_if_degraded || row.impact || "n/a"],
    ["Hit rate", pct(row.live_shadow_hit_rate ?? row.latest_phase_hit_rate ?? row.hit_rate)],
    ["Sample count", number(row.live_shadow_sample_count ?? row.latest_phase_sample_count ?? row.sample_count)],
    ["Distinct events", number(row.distinct_event_count ?? row.event_count)],
    ["Forward return", pct(row.forward_return_mean ?? row.avg_forward_return ?? row.latest_phase_forward_return_mean)],
    ["Blocked count", number(row.failed_or_blocked_count ?? row.blocked_count)],
  ];
  return metrics.filter(([, value]) => value !== "n/a" && value !== "--");
}

function signalEvidenceLists(row) {
  return [
    ["Strict review flags", asList(row.strict_review_reasons || row.strict_review)],
    ["Structural blockers", asList(row.structural_blockers || row.blockers)],
    ["Coverage warnings", asList(row.coverage_warnings)],
    ["Validation notes", asList(row.validation_notes || row.review_notes || row.notes)],
  ].filter(([, items]) => items.length);
}

function signalEvidencePanel(row) {
  const metrics = signalEvidenceMetrics(row);
  const lists = signalEvidenceLists(row);
  return `
    <div class="signal-evidence">
      <div class="signal-evidence-summary">
        <strong>${escapeHtml(row.variant || row.signal_id || "signal")}</strong>
        <span>${escapeHtml(signalEvidenceLabel(row))}</span>
      </div>
      <div class="signal-evidence-grid">
        ${metrics.map(([label, value]) => `
          <div class="signal-evidence-cell">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
          </div>
        `).join("")}
      </div>
      ${lists.map(([label, items]) => `
        <div class="signal-evidence-list">
          <strong>${escapeHtml(label)}</strong>
          <div>${items.map((item) => `<span>${escapeHtml(String(item))}</span>`).join("")}</div>
        </div>
      `).join("")}
    </div>
  `;
}

function strategyTrack(row) {
  const evidence = row?.evidence || {};
  if ((toNumber(evidence.supervised_live_pass_count) ?? 0) > 0) return "live_proof";
  if ((toNumber(evidence.live_replay_pass_count) ?? 0) > 0 || (toNumber(evidence.recent_shadow_live_economic_sample_count) ?? 0) > 0) return "shadow";
  if ((toNumber(evidence.historical_replay_pass_count) ?? 0) > 0) return "historical";
  return "unproven";
}

function displayTrack(track) {
  const labels = {
    live_proof: "Live proof",
    shadow: "Shadow evidence",
    historical: "Historical only",
    unproven: "No replay evidence",
  };
  return labels[track] || displayStatus(track);
}

function filteredStrategies() {
  const text = state.strategyFilters.text.trim().toLowerCase();
  return strategyRows().filter((row) => {
    const promotionState = row.promotion_state || row.state || "";
    const family = row.strategy_family || "";
    const blockers = Array.isArray(row.blockers) ? row.blockers : [];
    const track = strategyTrack(row);
    if (state.strategyFilters.state && promotionState !== state.strategyFilters.state) return false;
    if (state.strategyFilters.family && family !== state.strategyFilters.family) return false;
    if (state.strategyFilters.track && track !== state.strategyFilters.track) return false;
    if (!text) return true;
    const haystack = [
      row.strategy_id,
      family,
      promotionState,
      row.next_action,
      blockers.join(" "),
      track,
    ].join(" ").toLowerCase();
    return haystack.includes(text);
  });
}

function renderStatus() {
  const control = state.control || {};
  const dashboard = state.dashboard || control.live_dashboard || {};
  const active = dashboard.active_run || control.live_dashboard?.active_run || {};
  const audit = dashboard.audit || control.live_dashboard?.audit || {};
  const generated = control.generated_at_utc || state.dashboard?.generated_at_utc || state.health?.generated_at_utc;
  const runtimeView = describeRuntime(control, dashboard, active, audit);
  const auditStatus = audit.status || "audit pending";

  setText("railRuntime", runtimeView.railLabel);
  setText("railAudit", runtimeView.auditLabel);
  setText("runtimeStatus", runtimeView.runtimeLabel);
  setText("auditStatus", displayStatus(auditStatus));
  setText("lastUpdated", generated ? shortTime(generated) : "loading");
  setText("orphanRuns", `orphan runs ${state.health?.db?.signal_validation?.orphaned_run_count ?? "--"}`);

  setPillTone("runtimeStatus", runtimeView.runtimeTone);
  setPillTone("auditStatus", runtimeView.auditTone);
}

function renderMetrics() {
  const portfolio = state.control?.portfolio || {};
  const selection = state.selection || {};
  const strategies = state.strategies || {};
  setText("metricCash", money(portfolio.recorded_cash_balance_usd));
  setText("metricSpend", money(portfolio.validation_notional_filled_usd));
  setText("metricPnl", money(portfolio.validation_realized_pnl_usd));
  setText("metricOpenCost", money(portfolio.validation_open_cost_usd));
  setText("metricSignals", number(selection.signal_count));
  setText("metricStrategies", number(strategies.strategy_count || strategyRows().length));
  $("metricPnl")?.classList.toggle("pos", toNumber(portfolio.validation_realized_pnl_usd) > 0);
  $("metricPnl")?.classList.toggle("neg", toNumber(portfolio.validation_realized_pnl_usd) < 0);
}

function renderLoop() {
  const modules = state.control?.modules || [];
  const moduleBlocks = state.control?.module_blocks || {};
  const coverage = state.selection?.coverage || {};
  const promotion = state.strategies?.by_promotion_state || {};
  const liveRows = state.strategyLiveReplay?.rows || [];
  const blockRows = ["A", "B", "C"].map((block) => moduleSummaryForBlock(block, modules, moduleBlocks[block]));
  const readyBlocks = blockRows.filter((row) => row.status === "ready").length;
  const degradedBlocks = blockRows.filter((row) => row.status === "degraded").length;
  const missingBlocks = blockRows.filter((row) => row.status === "missing").length;
  let moduleStatus = `${readyBlocks}/3 ready`;
  if (degradedBlocks) moduleStatus += `, ${degradedBlocks} degraded`;
  if (missingBlocks) moduleStatus += `, ${missingBlocks} missing`;
  setText("sourceLoopState", `A/B/C ${coverage.A ?? "--"}/${coverage.B ?? "--"}/${coverage.C ?? "--"}`);
  setText("indicatorLoopState", moduleStatus);
  setText("signalLoopState", `${state.selection?.selected_count ?? "--"} selected`);
  setText("shadowLoopState", `${liveRows.length} replay rows`);
  setText("liveLoopState", `${promotion.LIVE_RUNNING ?? 0} live running`);
}

function renderModules() {
  const grid = $("moduleGrid");
  if (!grid) return;
  const modules = state.control?.modules || [];
  const moduleBlocks = state.control?.module_blocks || {};
  const cryptoStatus = moduleSummaryForBlock("A", modules, moduleBlocks.A);
  const profileStatus = moduleSummaryForBlock("B", modules, moduleBlocks.B);
  const optionStatus = moduleSummaryForBlock("C", modules, moduleBlocks.C);
  const base = [
    {
      title: "A Crypto",
      status: cryptoStatus.status,
      detail: `${state.control?.crypto_indicators?.latest_prices?.length || 0} price rows, ${state.control?.crypto_indicators?.technicals?.length || 0} observer rows; ${cryptoStatus.detail}`,
    },
    {
      title: "B Profiles",
      status: profileStatus.status,
      detail: `${state.control?.profile_distributions?.length || 0} distribution snapshots; ${profileStatus.detail}`,
    },
    {
      title: "C Options",
      status: optionStatus.status,
      detail: `${eventRows().length} event path rows; ${optionStatus.detail}`,
    },
    {
      title: "Signals",
      status: state.selection?.review_count ? "review" : "ready",
      detail: `${state.selection?.selected_count ?? 0} selected, ${state.selection?.revision_candidate_count ?? 0} revision candidates, ${state.selection?.review_count ?? 0} pending review`,
    },
    {
      title: "Strategies",
      status: state.strategies?.orders_allowed ? "unsafe" : "gated",
      detail: `${state.strategies?.strategy_count ?? 0} registered, ${state.strategies?.by_promotion_state?.LIVE_RUNNING ?? 0} live-running labels`,
    },
    {
      title: "Trading",
      status: state.control?.orders_allowed ? "enabled" : "disabled",
      detail: "UI cannot authorize orders; supervised runtime only.",
    },
  ];
  grid.innerHTML = base.map((item) => `
    <article class="module-row">
      <span class="status-dot ${slug(item.status)}"></span>
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.detail)}</p>
      </div>
      <em>${escapeHtml(displayStatus(item.status))}</em>
    </article>
  `).join("");
}

function modulesForBlock(block, modules) {
  return moduleSummaryForBlock(block, modules).status;
}

function moduleSummaryForBlock(block, modules, summary) {
  if (summary && typeof summary === "object") {
    return {
      status: String(summary.status || "unknown"),
      detail: String(summary.detail || "no readiness row"),
    };
  }
  const rows = modules.filter((row) => row.data_block === block);
  if (!rows.length) return { status: "missing", detail: "no readiness row" };
  const preferredRows = rows.filter((row) => row.source !== "service_status_file");
  const detailRows = preferredRows.length ? preferredRows : rows;
  const statuses = rows.map((row) => String(row.status || "unknown").toLowerCase());
  const blockers = detailRows.flatMap((row) => Array.isArray(row.blockers) ? row.blockers : []);
  let status = "ready";
  if (statuses.some((value) => ["failed", "unsafe"].includes(value))) {
    status = "failed";
  } else if (statuses.some((value) => ["degraded", "stale", "missing", "unknown"].includes(value))) {
    status = "degraded";
  } else if (statuses.every((value) => ["healthy", "ready", "complete"].includes(value))) {
    status = "ready";
  }
  const symbols = detailRows
    .map((row) => row.symbol)
    .filter((value) => value && value !== "unknown")
    .slice(0, 4)
    .join("/");
  const statusText = `${detailRows.length} module${detailRows.length === 1 ? "" : "s"} ${symbols ? `(${symbols})` : ""}`.trim();
  const blockerText = blockers.length ? `; blockers: ${blockers.slice(0, 3).join(", ")}` : "";
  return { status, detail: `${statusText}${blockerText}` };
}

function renderOverviewEvent() {
  const node = $("overviewEvent");
  if (!node) return;
  const row = eventRows()[0];
  setText("eventCountPill", `${eventRows().length} events`);
  if (!row) {
    node.innerHTML = `<div class="empty-state">${escapeHtml(endpointEmptyState("control", "No captured event path rows yet."))}</div>`;
    return;
  }
  node.innerHTML = `
    <div class="event-title">
      <strong>${escapeHtml(row.event_slug || row.event_key || "unknown event")}</strong>
      <span>${escapeHtml(row.symbol || "crypto")} - ${escapeHtml(row.path_direction || "unknown path")}</span>
    </div>
    ${sparkline(row)}
    <div class="stat-grid tight">
      ${statCell("Snapshots", row.snapshot_count)}
      ${statCell("Range", pct(row.up_range))}
      ${statCell("Move/min", pct(row.up_abs_move_per_minute))}
      ${statCell("Swing avg", pct(row.avg_swing_distance))}
      ${statCell("Rebounds", row.rebound_direction_flip_count)}
      ${statCell("Latency", `${number(row.avg_source_latency_ms)} ms`)}
    </div>
  `;
}

function renderProfilePressure() {
  const node = $("profilePressurePanel");
  if (!node) return;
  const row = state.control?.profile_distributions?.[0];
  if (!row) {
    node.innerHTML = `<div class="empty-state">${escapeHtml(endpointEmptyState("control", "No profile distribution snapshot available."))}</div>`;
    return;
  }
  const up = toNumber(row.up_weight) ?? 0;
  const down = toNumber(row.down_weight) ?? 0;
  const total = Math.max(up + down, 0.000001);
  const upPct = toNumber(row.up_pressure_ratio) ?? up / total;
  const downPct = toNumber(row.down_pressure_ratio) ?? 1 - upPct;
  const delta = toNumber(row.pressure_delta);
  const deltaSide = delta === null || Math.abs(delta) < 0.0001 ? "balanced" : delta > 0 ? "Up tilt" : "Down tilt";
  const upPrice = row.up_reconstructed_profile_price;
  const downPrice = row.down_reconstructed_profile_price;
  const pairSum = row.reconstructed_profile_pair_sum;
  const sourceAge = toNumber(row.source_age_seconds);
  const refreshTarget = toNumber(row.target_refresh_seconds);
  const breakdown = row.component_breakdown || {};
  node.innerHTML = `
    <div class="pressure-bar" aria-label="Up down profile distribution">
      <span style="width:${Math.max(0, Math.min(100, upPct * 100))}%"></span>
    </div>
    <div class="pressure-labels">
      <strong>Up ${pct(upPct)}</strong>
      <strong>Down ${pct(downPct)}</strong>
    </div>
    <div class="pressure-note">
      <strong>${escapeHtml(deltaSide)} ${delta === null ? "n/a" : pct(Math.abs(delta))}</strong>
      <span>Hedged-profile inventory delta, not direct outcome probability.</span>
    </div>
    <div class="price-strip" aria-label="Weighted profile entry prices">
      <div><span>Weighted Up cost</span><strong>${cents(upPrice)}</strong></div>
      <div><span>Weighted Down cost</span><strong>${cents(downPrice)}</strong></div>
      <div><span>Pair sum</span><strong>${pairSum === null || pairSum === undefined ? "n/a" : cents(pairSum)}</strong></div>
    </div>
    <div class="detail-list compact">
      <div><strong>Profiles</strong>${number(row.profile_count)} profiles / ${number(row.component_count)} components</div>
      <div><strong>Freshness</strong>${sourceAge === null ? "n/a" : `${number(sourceAge)}s age`} / ${refreshTarget === null ? "30s target" : `${number(refreshTarget)}s target`}</div>
      <div><strong>Method</strong>${escapeHtml(row.canonical_method || row.source_mode || "unknown")}</div>
      <div><strong>Price basis</strong>${escapeHtml(row.reconstructed_profile_price_method || "weighted cost / weighted shares")}</div>
      <div><strong>Source rows</strong>${number(row.source?.raw_activity_rows)} activity / ${number(row.source?.position_rows)} positions / ${number(row.source?.event_order_rows)} event orders</div>
      <div><strong>Event</strong>${escapeHtml(row.event_slug || "latest event unknown")}</div>
      <div><strong>Updated</strong>${shortDateTime(row.computed_at_utc)}</div>
    </div>
    ${profileBreakdownTable("By grade", breakdown.by_grade)}
    ${profileBreakdownTable("By style", breakdown.by_style)}
  `;
}

function profileBreakdownTable(title, rows) {
  if (!Array.isArray(rows) || !rows.length) return "";
  const visible = rows.slice(0, 6);
  return `
    <div class="mini-breakdown">
      <h4>${escapeHtml(title)}</h4>
      ${visible.map((row) => `
        <div class="mini-breakdown-row">
          <strong>${escapeHtml(row.label || "unknown")}</strong>
          <span>Up ${pct(row.up_pressure_ratio)} / Down ${pct(row.down_pressure_ratio)}</span>
          <span>${cents(row.up_reconstructed_profile_price)} / ${cents(row.down_reconstructed_profile_price)}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderStrategyRuntime() {
  const node = $("strategyRuntimePanel");
  if (!node) return;
  const rows = state.dashboard?.strategies || [];
  setText("strategyRuntimePill", `${rows.length} lanes`);
  if (!rows.length) {
    node.innerHTML = `<div class="empty-state">${escapeHtml(endpointEmptyState("dashboard", "No latest runtime lanes available."))}</div>`;
    return;
  }
  node.innerHTML = rows.map((row) => `
    <article class="runtime-row">
      <div>
        <strong>${escapeHtml(row.strategy_id)}</strong>
        <span>${escapeHtml(displayStatus(row.status))} - ${number(row.filled_position_count)} fills</span>
      </div>
      <b class="${toneClass(row.realized_pnl_usd)}">${money(row.realized_pnl_usd)}</b>
    </article>
  `).join("");
}

function renderEvents() {
  const rows = eventRows();
  const body = $("eventRows");
  if (!body) return;
  setText("eventsShown", `${rows.length} rows`);
  if (!rows.length) {
    body.innerHTML = emptyRow(9, endpointEmptyState("control", "No event intelligence rows available."));
    renderEventInspector(null);
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr data-event-key="${escapeAttr(row.event_path_stats_key)}" class="${row.event_path_stats_key === state.selectedEventKey ? "is-selected" : ""}">
      <td><div class="cell-main">${escapeHtml(row.event_slug || row.event_key)}</div><div class="cell-sub">${shortDateTime(row.event_start_time_utc)}</div></td>
      <td>${escapeHtml(row.symbol || "")}</td>
      <td>${number(row.snapshot_count)}</td>
      <td>${escapeHtml(row.path_direction || "unknown")}</td>
      <td>${pct(row.up_range)}</td>
      <td>${pct(row.up_abs_move_per_minute)}</td>
      <td>${pct(row.avg_swing_distance)} / ${pct(row.max_swing_distance)}</td>
      <td>${number(row.rebound_direction_flip_count)} flips / ${number(row.strong_rebound_touch_count)} strong</td>
      <td>${number(row.avg_source_latency_ms)} ms</td>
    </tr>
  `).join("");
  for (const rowNode of body.querySelectorAll("tr[data-event-key]")) {
    rowNode.addEventListener("click", () => {
      state.selectedEventKey = rowNode.dataset.eventKey;
      renderEvents();
    });
  }
  if (!state.selectedEventKey) state.selectedEventKey = rows[0].event_path_stats_key;
  renderEventInspector(selectedEvent());
}

function renderEventInspector(row) {
  const node = $("eventInspector");
  if (!node) return;
  if (!row) {
    setText("selectedEventPill", "none");
    node.innerHTML = `<div class="empty-state">${escapeHtml(endpointEmptyState("control", "Select an event row to inspect price path and replay context."))}</div>`;
    return;
  }
  setText("selectedEventPill", row.symbol || "event");
  node.innerHTML = `
    <div class="event-title">
      <strong>${escapeHtml(row.event_slug || row.event_key || "event")}</strong>
      <span>${shortDateTime(row.event_start_time_utc)} - ${shortDateTime(row.event_end_time_utc)}</span>
    </div>
    ${sparkline(row)}
    <div class="detail-list">
      <div><strong>Path</strong>${escapeHtml(row.path_direction || "unknown")} - efficiency ${pct(row.path_efficiency)}</div>
      <div><strong>Range</strong>${pct(row.up_range)} with ${number(row.level_crossing_count)} level crossings</div>
      <div><strong>Swings</strong>avg ${pct(row.avg_swing_distance)}, max ${pct(row.max_swing_distance)}</div>
      <div><strong>Rolling range</strong>30s avg ${pct(row.avg_rolling_30s_range)}, 60s avg ${pct(row.avg_rolling_60s_range)}</div>
      <div><strong>Pressure</strong>pair sum range ${pct(row.pair_sum_range)}, depth pressure ${number(row.avg_pair_depth_pressure)}</div>
      <div><strong>Latency</strong>avg ${number(row.avg_source_latency_ms)} ms, max ${number(row.max_source_latency_ms)} ms</div>
      <div><strong>Price buckets</strong>${escapeHtml(JSON.stringify(row.price_bucket_counts || {}))}</div>
    </div>
  `;
}

function renderPortfolio() {
  const portfolio = state.control?.portfolio || {};
  const ledgerRows = portfolio.ledger_rows || [];
  const visibleRows = filteredLedgerRows(ledgerRows);
  setText("cashStatusPill", displayStatus(portfolio.cash_balance_status));
  setText("ledgerCountPill", `${number(visibleRows.length)} shown`);
  const summary = $("portfolioSummary");
  if (summary) {
    const filledRows = ledgerRows.filter((row) => (toNumber(row.notional_filled_usd) || 0) > 0).length;
    const exceptionRows = ledgerRows.filter((row) => hasLifecycleException(row)).length;
    const zeroFillProbeRows = ledgerRows.filter((row) => !hasMeaningfulLedgerFlow(row)).length;
    summary.innerHTML = `
      <div class="stat-grid">
        ${statCell("Recorded cash", money(portfolio.recorded_cash_balance_usd))}
        ${statCell("Filled validation notional", money(portfolio.validation_notional_filled_usd))}
        ${statCell("Realized PnL", money(portfolio.validation_realized_pnl_usd), toneClass(portfolio.validation_realized_pnl_usd))}
        ${statCell("Open cost", money(portfolio.validation_open_cost_usd))}
        ${statCell("Positions", `${number(portfolio.filled_position_count)} filled`)}
        ${statCell("Unresolved", number(portfolio.unresolved_position_count))}
        ${statCell("Ledger exceptions", number(exceptionRows), exceptionRows ? "neg" : "")}
        ${statCell("Zero-fill probes", number(zeroFillProbeRows))}
        ${statCell("Filled budget rows", number(filledRows))}
      </div>
    `;
  }
  const body = $("ledgerRows");
  if (body) {
    body.innerHTML = visibleRows.length ? visibleRows.map((row) => `
      <tr>
        <td><div class="cell-main">${escapeHtml(row.validation_run_id)}</div><div class="cell-sub">${shortDateTime(row.started_at_utc)}</div></td>
        <td>${escapeHtml(row.strategy_or_component_id)}</td>
        <td>${money(row.budget_cap_usd)}</td>
        <td>${money(row.notional_filled_usd)}</td>
        <td class="${toneClass(row.realized_pnl_usd)}">${money(row.realized_pnl_usd)}</td>
        <td>${money(row.open_cost_usd)}</td>
        <td>${money(row.cash_balance_after_usd)}</td>
        <td><span class="state ${hasLifecycleException(row) ? "blocked" : slug(row.reconciliation_status || row.lifecycle_audit_status || row.cash_balance_status)}">${escapeHtml(displayStatus(row.reconciliation_status || row.lifecycle_audit_status || row.cash_balance_status))}</span><div class="cell-sub clamp">${escapeHtml(row.stop_reason || row.lifecycle_audit_status || "normal flow")}</div></td>
      </tr>
    `).join("") : emptyRow(8, endpointEmptyState("control", "No budget ledger rows match the selected flow filter."));
  }
}

function renderPositions() {
  const rows = state.control?.positions || [];
  const visibleRows = filteredPositionRows(rows);
  const body = $("positionRows");
  const summary = $("positionSummary");
  const groupedOpenRows = buildPositionExposureRows(rows);
  const openRows = rows.filter((row) => String(row.status || "").toLowerCase() === "open");
  const distinctEvents = new Set(openRows.map((row) => row.event_slug || row.event_token_key).filter(Boolean)).size;
  setText("positionsCount", `${number(visibleRows.length)} shown`);
  if (summary) {
    summary.innerHTML = `
      <div class="stat-grid tight">
        ${statCell("Open fills", number(openRows.length))}
        ${statCell("Grouped exposures", number(groupedOpenRows.length))}
        ${statCell("Distinct events", number(distinctEvents))}
        ${statCell("All rows", number(rows.length))}
      </div>
    `;
  }
  if (!body) return;
  body.innerHTML = visibleRows.length ? visibleRows.map((row) => `
    <tr>
      <td>${escapeHtml(row.strategy_id)}</td>
      <td><div class="cell-main">${escapeHtml(row.event_slug || row.event_token_key)}</div><div class="cell-sub">${escapeHtml(row.leg_count ? `${number(row.leg_count)} fills combined` : shortDateTime(row.event_start_time_utc))}</div></td>
      <td>${escapeHtml(row.outcome || "")}</td>
      <td>${number(row.shares)}</td>
      <td>${money(row.cost_basis_usd)}</td>
      <td><span class="state ${slug(row.status)}">${escapeHtml(displayStatus(row.status))}</span></td>
      <td>${shortDateTime(row.opened_at_utc)}</td>
      <td>${shortDateTime(row.updated_at_utc)}</td>
    </tr>
  `).join("") : emptyRow(8, endpointEmptyState("control", "No position rows match the selected flow filter."));
}

function renderOrders() {
  const rows = state.control?.orders || [];
  const body = $("orderRows");
  setText("ordersCount", number(rows.length));
  if (!body) return;
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.strategy_id || "")}</td>
      <td><div class="cell-main">${escapeHtml(row.event_slug || row.event_token_key || "")}</div><div class="cell-sub">${escapeHtml(row.token_id || "")}</div></td>
      <td>${escapeHtml(row.outcome || "")}</td>
      <td>${escapeHtml(row.intent_type || "")}</td>
      <td>${escapeHtml(row.order_type || "")}</td>
      <td>${escapeHtml(row.side || "")}</td>
      <td><span class="state ${slug(row.order_status)}">${escapeHtml(displayStatus(row.order_status))}</span><div class="cell-sub">${escapeHtml(displayStatus(row.intent_status))}</div></td>
      <td>${escapeHtml(row.exchange_order_id || "none")}</td>
      <td>${shortDateTime(row.updated_at_utc || row.submitted_at_utc)}</td>
    </tr>
  `).join("") : emptyRow(9, endpointEmptyState("control", "No recorded orders available."));
}

function renderHistory() {
  const rows = state.control?.history || [];
  const body = $("historyRows");
  setText("historyCount", number(rows.length));
  if (!body) return;
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${escapeHtml(displayStatus(row.activity_type))}</td>
      <td>${escapeHtml(row.strategy_id || "")}</td>
      <td>${escapeHtml(row.event_slug || row.event_key || "")}</td>
      <td>${escapeHtml(row.outcome || row.resolved_outcome || "")}</td>
      <td>${escapeHtml(row.side || "")}</td>
      <td>${number(row.filled_size)}</td>
      <td>${pct(row.filled_price)}</td>
      <td>${money(row.notional_usd)}</td>
      <td>${shortDateTime(row.filled_at_utc || row.settled_at_utc || row.inserted_at_utc)}</td>
    </tr>
  `).join("") : emptyRow(9, endpointEmptyState("control", "No fills or settlements recorded yet."));
}

function renderSignalFilters() {
  const typeSelect = $("filterType");
  if (typeSelect) {
    const current = typeSelect.value;
    const types = [...new Set(signalRows().map((row) => row.signal_type).filter(Boolean))].sort();
    typeSelect.innerHTML = `<option value="">All signal types</option>${types.map((type) => `<option value="${escapeAttr(type)}">${escapeHtml(type)}</option>`).join("")}`;
    if (types.includes(current)) typeSelect.value = current;
  }
}

function renderSignals() {
  renderSignalFilters();
  const rows = filteredSignals();
  const body = $("signalRows");
  if (!body) return;
  setText("signalShown", `${rows.length} shown`);
  body.innerHTML = rows.length ? rows.map((row) => {
    const hit = row.live_shadow_hit_rate ?? row.latest_phase_hit_rate ?? row.hit_rate;
    const samples = row.live_shadow_sample_count ?? row.latest_phase_sample_count ?? row.sample_count;
    const strict = row.strict_review_reasons || row.strict_review || [];
    const key = signalRowKey(row);
    const expanded = state.expandedSignalKey === key;
    return `
      <tr class="signal-row ${expanded ? "is-expanded" : ""}">
        <td><span class="tier ${slug(row.selection_tier)}">${escapeHtml(displayStatus(row.selection_tier))}</span></td>
        <td>${escapeHtml(row.signal_type || row.type || "")}</td>
        <td>${escapeHtml((row.sources || []).join(", "))}<div class="cell-sub">${escapeHtml((row.source_blocks || row.required_data_blocks || []).join("/"))}</div></td>
        <td>
          <div class="cell-main">${escapeHtml(row.variant || "")}</div>
          <div class="cell-sub">${escapeHtml(row.selection_reason || "")}</div>
          <button type="button" class="inspect-button" data-signal-key="${escapeAttr(key)}" aria-expanded="${expanded ? "true" : "false"}">
            ${expanded ? "Hide tests" : "Inspect tests"}
          </button>
        </td>
        <td>${escapeHtml(row.version || "v1")}</td>
        <td><span class="state ${slug(row.queue_status || row.status)}">${escapeHtml(displayStatus(row.queue_status || row.status))}</span><div class="cell-sub">${escapeHtml(displayStatus(row.promotion_state))}</div></td>
        <td>${pct(hit)}</td>
        <td>${number(samples)}</td>
        <td>${number(row.distinct_event_count || row.event_count)}</td>
        <td>${escapeHtml(row.impact_if_degraded || row.impact || "")}</td>
        <td><div class="cell-sub clamp">${escapeHtml(strict.join(", ") || "none")}</div></td>
        <td><div class="next-action-cell"><strong>${escapeHtml(displayStatus(row.action_state || "VALIDATION_REQUIRED"))}</strong><span>${escapeHtml(row.next_action || row.selection_next_action || "review evidence")}</span></div></td>
      </tr>
      <tr class="signal-disclosure-row ${expanded ? "is-open" : ""}">
        <td colspan="12">${expanded ? signalEvidencePanel(row) : ""}</td>
      </tr>
    `;
  }).join("") : emptyRow(12, endpointEmptyState("selection", "No signals match the current filters."));
  for (const button of body.querySelectorAll("[data-signal-key]")) {
    button.addEventListener("click", () => {
      const key = button.dataset.signalKey || "";
      state.expandedSignalKey = state.expandedSignalKey === key ? null : key;
      renderSignals();
    });
  }
}

function renderStrategyFilters() {
  const rows = strategyRows();
  const stateSelect = $("strategyStateFilter");
  const familySelect = $("strategyFamilyFilter");
  const trackSelect = $("strategyTrackFilter");

  if (stateSelect) {
    const current = state.strategyFilters.state;
    const options = [...new Set(rows.map((row) => row.promotion_state || row.state).filter(Boolean))].sort();
    stateSelect.innerHTML = `<option value="">All states</option>${options.map((value) => `<option value="${escapeAttr(value)}">${escapeHtml(displayStatus(value))}</option>`).join("")}`;
    stateSelect.value = options.includes(current) ? current : "";
  }

  if (familySelect) {
    const current = state.strategyFilters.family;
    const options = [...new Set(rows.map((row) => row.strategy_family).filter(Boolean))].sort();
    familySelect.innerHTML = `<option value="">All families</option>${options.map((value) => `<option value="${escapeAttr(value)}">${escapeHtml(value)}</option>`).join("")}`;
    familySelect.value = options.includes(current) ? current : "";
  }

  if (trackSelect) {
    const current = state.strategyFilters.track;
    const options = [...new Set(rows.map((row) => strategyTrack(row)))].filter(Boolean);
    trackSelect.innerHTML = `<option value="">All tracks</option>${options.map((value) => `<option value="${escapeAttr(value)}">${escapeHtml(displayTrack(value))}</option>`).join("")}`;
    trackSelect.value = options.includes(current) ? current : "";
  }
}

function renderStrategies() {
  renderStrategyFilters();
  renderStrategyPolicy();
  const rows = filteredStrategies();
  const body = $("strategyRows");
  if (!body) return;
  setText("strategyShown", `${rows.length} shown`);
  body.innerHTML = rows.length ? rows.map((row) => {
    const evidence = row.evidence || {};
    const blockers = row.blockers || [];
    const recentSamples = evidence.recent_shadow_live_economic_sample_count;
    const recentWinRate = evidence.recent_shadow_live_win_rate;
    const recentPnl = evidence.recent_shadow_live_simulated_pnl_usd;
    const track = strategyTrack(row);
    return `
      <tr>
        <td><div class="cell-main">${escapeHtml(row.strategy_id)}</div><div class="cell-sub">${escapeHtml(row.strategy_family || "")}</div></td>
        <td>${escapeHtml(row.strategy_version || "v1")}</td>
        <td><span class="state ${slug(row.promotion_state || row.state)}">${escapeHtml(displayStatus(row.promotion_state || row.state))}</span><div class="cell-sub">${escapeHtml(displayTrack(track))}</div></td>
        <td>${number(evidence.historical_replay_pass_count)}</td>
        <td>${number(evidence.live_replay_pass_count)}</td>
        <td><div class="cell-main">${pct(recentWinRate)}</div><div class="cell-sub">${number(recentSamples)} / 12 recent samples</div></td>
        <td><div class="${toneClass(recentPnl)}">${money(recentPnl)}</div><div class="cell-sub">1h shadow gate</div></td>
        <td>${number(evidence.supervised_live_pass_count)}</td>
        <td><div class="cell-sub clamp">${escapeHtml(blockers.join(", ") || "none")}</div></td>
        <td><div class="next-action-cell">${escapeHtml(row.next_action || "review promotion evidence")}</div></td>
      </tr>
    `;
  }).join("") : emptyRow(10, endpointEmptyState("strategies", "No strategy promotion rows available."));
}

function renderStrategyPolicy() {
  const node = $("strategyPolicyPanel");
  if (!node) return;
  const contract = promotionPolicyContract();
  const status = getEndpointStatus("strategies");
  if (!contract.schema_version) {
    node.innerHTML = `<div class="empty-state">${escapeHtml(endpointEmptyState("strategies", "Promotion policy contract is not available."))}</div>`;
    node.classList.toggle("is-loading", status.state === "loading");
    return;
  }
  const requirements = contract.strategies?.live_candidate_requirements || {};
  const signalPolicy = contract.signals || {};
  const liveSafety = contract.live_safety || {};
  const blockedLabels = asList(signalPolicy.not_promotable_labels).slice(0, 8);
  const minWinRate = requirements.recent_shadow_live_win_rate_gt;
  const sampleCount = requirements.recent_distinct_economic_samples;
  const pnlGate = requirements.recent_shadow_live_pnl_usd_gt;
  const strictBlockers = requirements.strict_signal_blockers;
  node.classList.remove("is-loading");
  node.innerHTML = `
    <div class="policy-summary">
      <div>
        <span>Policy Contract</span>
        <strong>${escapeHtml(contract.schema_version)}</strong>
      </div>
      <div>
        <span>Signal Gate</span>
        <strong>${escapeHtml(signalPolicy.promotable_state || "PROMOTION_READY")}</strong>
      </div>
      <div>
        <span>Live Candidate Gate</span>
        <strong>${escapeHtml(number(sampleCount))} samples, >${escapeHtml(pct(minWinRate))}, ${escapeHtml(money(pnlGate))}+ PnL</strong>
      </div>
      <div>
        <span>Strict Blockers</span>
        <strong>${escapeHtml(number(strictBlockers))} allowed</strong>
      </div>
      <div>
        <span>Live Authority</span>
        <strong>${liveSafety.chat_judgment_can_authorize_live || liveSafety.automation_can_authorize_live ? "unsafe review" : "supervised runtime only"}</strong>
      </div>
    </div>
    <div class="policy-tags" aria-label="Not promotable signal labels">
      ${blockedLabels.map((label) => `<span>${escapeHtml(displayStatus(label))}</span>`).join("")}
    </div>
  `;
}

function renderSystem() {
  const healthNode = $("systemHealth");
  if (healthNode) {
    const health = state.health || {};
    const blockers = health.integrity?.readiness_blockers || [];
    const external = health.external_services?.polymarket || {};
    healthNode.innerHTML = `
      <div class="detail-list">
        <div><strong>Status</strong>${escapeHtml(displayStatus(health.status || "unknown"))}</div>
        <div><strong>Polymarket</strong>${escapeHtml(displayStatus(external.status || "unknown"))}</div>
        <div><strong>Readiness blockers</strong>${escapeHtml(blockers.join(", ") || "none")}</div>
        <div><strong>DB</strong>${escapeHtml(state.control?.db_path || "unknown")}</div>
        <div><strong>Generated</strong>${shortDateTime(health.generated_at_utc || state.control?.generated_at_utc)}</div>
      </div>
    `;
  }
  const cryptoNode = $("cryptoIndicators");
  if (cryptoNode) {
    const prices = state.control?.crypto_indicators?.latest_prices || [];
    const techs = state.control?.crypto_indicators?.technicals || [];
    const rows = [
      ...prices.slice(0, 8).map((row) => ({
        title: `${row.symbol} ${row.source}`,
        meta: `${money(row.price)} - ${shortTime(row.observed_at_utc)}`,
        value: row.bid || row.ask ? `bid ${money(row.bid)} / ask ${money(row.ask)}` : "latest price",
      })),
      ...techs.slice(0, 10).map((row) => ({
        title: `${row.provider} ${row.symbol} ${row.interval}`,
        meta: `${row.summary_label || "summary unknown"} - ${shortTime(row.completed_at_utc)}`,
        value: `${row.buy_count} buy / ${row.sell_count} sell / ${row.neutral_count} neutral`,
      })),
    ];
    cryptoNode.innerHTML = rows.length ? rows.map((row) => `
      <article class="runtime-row">
        <div><strong>${escapeHtml(row.title)}</strong><span>${escapeHtml(row.meta)}</span></div>
        <b>${escapeHtml(row.value)}</b>
      </article>
    `).join("") : `<div class="empty-state">No crypto indicator rows available.</div>`;
  }
}

function renderAll() {
  renderStatus();
  renderMetrics();
  renderLoop();
  renderModules();
  renderOverviewEvent();
  renderProfilePressure();
  renderStrategyRuntime();
  renderEvents();
  renderPortfolio();
  renderPositions();
  renderOrders();
  renderHistory();
  renderSignals();
  renderStrategies();
  renderSystem();
}

async function loadAll() {
  const tasks = [
    ["control", "/dashboard/control-center-state", 30000],
    ["dashboard", "/dashboard/state", 12000],
    ["selection", "/signals/selection", 30000],
    ["strategies", "/strategies/promotion", 30000],
    ["strategyBacktests", "/strategies/replay/backtests", 30000],
    ["strategyLiveReplay", "/strategies/replay/live", 30000],
    ["signalBacktests", "/signals/replay/backtests", 30000],
    ["signalLiveReplay", "/signals/replay/live", 30000],
    ["health", "/health", 30000],
  ];
  for (const [key] of tasks) {
    setEndpointStatus(key, { state: "loading", detail: "" });
  }
  const unavailable = [];
  const failed = [];
  await Promise.allSettled(
    tasks.map(([key, path, timeout]) =>
      fetchJSON(path, timeout)
        .then((payload) => {
          state[key] = payload;
          setEndpointStatus(key, { state: "ready", detail: "" });
          renderAll();
        })
        .catch((error) => {
          const kind = error?.kind === "unavailable" ? "unavailable" : "error";
          const detail = String(error?.message || "request failed");
          setEndpointStatus(key, { state: kind, detail, path });
          if (kind === "unavailable") {
            unavailable.push(path);
          } else {
            failed.push(`${path} (${detail})`);
          }
          renderAll();
        })
    )
  );
  if (failed.length) {
    showToast(`${failed.length} control-center endpoint(s) failed to load.`, "bad");
  } else if (unavailable.length) {
    showToast(`${unavailable.length} optional endpoint(s) are not exposed on this backend yet.`, "warn");
  }
  renderAll();
}

function statCell(label, value, tone = "") {
  return `<div class="stat-cell ${escapeAttr(tone)}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function emptyRow(colspan, message) {
  return `<tr class="empty-row"><td colspan="${colspan}">${escapeHtml(message)}</td></tr>`;
}

function hasLifecycleException(row) {
  const lifecycle = String(row?.lifecycle_audit_status || "").toLowerCase();
  const reconciliation = String(row?.reconciliation_status || "").toLowerCase();
  return Boolean(
    row?.stop_reason ||
    Number(row?.hard_stop_triggered) > 0 ||
    lifecycle.includes("missing") ||
    lifecycle.includes("failed") ||
    lifecycle.includes("blocked") ||
    reconciliation.includes("failed") ||
    reconciliation.includes("mismatch")
  );
}

function hasMeaningfulLedgerFlow(row) {
  return hasLifecycleException(row) ||
    ((toNumber(row?.notional_filled_usd) || 0) > 0) ||
    ((toNumber(row?.open_cost_usd) || 0) > 0) ||
    ((toNumber(row?.realized_pnl_usd) || 0) !== 0);
}

function filteredLedgerRows(rows) {
  if (state.flowFilters.ledger === "exceptions") return rows.filter((row) => hasLifecycleException(row));
  if (state.flowFilters.ledger === "all") return rows;
  return rows.filter((row) => hasMeaningfulLedgerFlow(row));
}

function buildPositionExposureRows(rows) {
  const grouped = new Map();
  rows
    .filter((row) => String(row.status || "").toLowerCase() === "open")
    .forEach((row) => {
      const key = [
        row.strategy_id || "",
        row.event_slug || row.event_token_key || "",
        row.outcome || "",
        row.status || "",
      ].join("|");
      if (!grouped.has(key)) {
        grouped.set(key, {
          strategy_id: row.strategy_id,
          event_slug: row.event_slug,
          event_token_key: row.event_token_key,
          outcome: row.outcome,
          status: row.status,
          opened_at_utc: row.opened_at_utc,
          updated_at_utc: row.updated_at_utc,
          shares: 0,
          cost_basis_usd: 0,
          leg_count: 0,
        });
      }
      const entry = grouped.get(key);
      entry.shares += toNumber(row.shares) || 0;
      entry.cost_basis_usd += toNumber(row.cost_basis_usd) || 0;
      entry.leg_count += 1;
      if (row.opened_at_utc && (!entry.opened_at_utc || row.opened_at_utc < entry.opened_at_utc)) entry.opened_at_utc = row.opened_at_utc;
      if (row.updated_at_utc && (!entry.updated_at_utc || row.updated_at_utc > entry.updated_at_utc)) entry.updated_at_utc = row.updated_at_utc;
    });
  return [...grouped.values()];
}

function filteredPositionRows(rows) {
  if (state.flowFilters.positions === "all") return rows;
  if (state.flowFilters.positions === "open") return rows.filter((row) => String(row.status || "").toLowerCase() === "open");
  return buildPositionExposureRows(rows);
}

function sparkline(row) {
  const upSeries = extractPriceSeries(row.event_price_points, "up");
  const downSeries = extractPriceSeries(row.event_price_points, "down");
  const upPath = polylinePath(upSeries);
  const downPath = polylinePath(downSeries);
  if (!upPath && !downPath) {
    return `<div class="sparkline-empty">No price path points</div>`;
  }
  return `
    <svg class="sparkline" viewBox="0 0 240 72" role="img" aria-label="Up and down option price movement">
      <line x1="0" x2="240" y1="36" y2="36"></line>
      ${upPath ? `<polyline class="up" points="${upPath}"></polyline>` : ""}
      ${downPath ? `<polyline class="down" points="${downPath}"></polyline>` : ""}
    </svg>
  `;
}

function extractPriceSeries(points, side) {
  if (!points) return [];
  const keyOptions = side === "up"
    ? ["up", "up_price", "up_mid", "up_mid_price", "up_best_ask", "up_last_price"]
    : ["down", "down_price", "down_mid", "down_mid_price", "down_best_ask", "down_last_price"];
  if (Array.isArray(points)) {
    return points.map((item) => pickNumber(item, keyOptions)).filter((value) => value !== null);
  }
  if (typeof points === "object") {
    for (const key of keyOptions) {
      const value = points[key];
      if (Array.isArray(value)) return value.map((item) => (typeof item === "object" ? pickNumber(item, keyOptions) : toNumber(item))).filter((item) => item !== null);
    }
    const numeric = [];
    for (const value of Object.values(points)) {
      if (typeof value === "object") {
        const picked = pickNumber(value, keyOptions);
        if (picked !== null) numeric.push(picked);
      }
    }
    return numeric;
  }
  return [];
}

function pickNumber(item, keys) {
  if (!item || typeof item !== "object") return toNumber(item);
  for (const key of keys) {
    const parsed = toNumber(item[key]);
    if (parsed !== null) return parsed;
  }
  return null;
}

function polylinePath(values) {
  if (!values.length) return "";
  const width = 240;
  const height = 72;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = values.length === 1 ? width : (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 8) - 4;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function bindEvents() {
  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.view || "overview"));
  });
  $("refreshButton")?.addEventListener("click", () => loadAll());
  $("filterType")?.addEventListener("input", (event) => {
    state.signalFilters.type = event.target.value;
    renderSignals();
  });
  $("filterTier")?.addEventListener("input", (event) => {
    state.signalFilters.tier = event.target.value;
    renderSignals();
  });
  $("filterText")?.addEventListener("input", (event) => {
    state.signalFilters.text = event.target.value;
    renderSignals();
  });
  $("strategyStateFilter")?.addEventListener("input", (event) => {
    state.strategyFilters.state = event.target.value;
    renderStrategies();
  });
  $("strategyFamilyFilter")?.addEventListener("input", (event) => {
    state.strategyFilters.family = event.target.value;
    renderStrategies();
  });
  $("strategyTrackFilter")?.addEventListener("input", (event) => {
    state.strategyFilters.track = event.target.value;
    renderStrategies();
  });
  $("strategyTextFilter")?.addEventListener("input", (event) => {
    state.strategyFilters.text = event.target.value;
    renderStrategies();
  });
  $("ledgerScopeFilter")?.addEventListener("input", (event) => {
    state.flowFilters.ledger = event.target.value;
    renderPortfolio();
  });
  $("positionScopeFilter")?.addEventListener("input", (event) => {
    state.flowFilters.positions = event.target.value;
    renderPositions();
  });
}

bindEvents();
activateView(state.activeView);
loadAll();
window.setInterval(loadAll, 30000);
