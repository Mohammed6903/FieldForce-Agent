/* ============================================================================
   FieldForce — AI Dispatch Console (vanilla JS, no build step)

   Talks to the backend at relative /api/* paths (served same-origin at "/").

   Endpoints used:
     GET  /api/technicians      -> [Technician, ...]            (for the map)
     GET  /api/jobs             -> [Job, ...]                   (job queue + map)
     GET  /api/sla              -> {at_risk_total, by_severity, jobs}
     GET  /api/actions          -> [ActionLog, ...]             (mission timeline)
     POST /api/dispatch/{id}    -> DispatchPlan                 (AI planning)
     POST /api/approve  (plan)  -> {ok, actions?, ...}          (execute plan)

   All endpoints are best-effort: any failure surfaces a toast but never breaks
   the rest of the console. The data shapes follow backend/models.py.
   ========================================================================== */

"use strict";

/* ─────────────────────────── Config / constants ─────────────────────────── */

// Fallback map center if no technicians are loaded (San Francisco).
const FALLBACK_CENTER = [37.7749, -122.4194];
const SLA_POLL_MS = 8000;       // SLA banner refresh cadence
const ACTIONS_POLL_MS = 6000;   // timeline refresh cadence

// Icons per action_type, used in the mission timeline.
const ACTION_ICONS = {
  assign_job: "🧰",
  reserve_parts: "📦",
  notify: "📣",
  reschedule_job: "🗓️",
};

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

/* ─────────────────────────── App state ─────────────────────────── */

const state = {
  jobs: [],
  technicians: [],
  selectedJobId: null,
  currentPlan: null,        // the exact DispatchPlan object returned by /api/dispatch
  map: null,
  techLayer: null,          // L.LayerGroup for technician markers
  jobLayer: null,           // L.LayerGroup for job markers
  routeLayer: null,         // L.LayerGroup for the dispatch route line
  jobMarkers: {},           // job_id -> marker (for fly-to on select)
  techMarkers: {},          // technician id -> marker (for locate-on-map)
};

/* ─────────────────────────── Tiny API helper ─────────────────────────── */

/**
 * fetch JSON with consistent error handling. Throws on non-2xx so callers can
 * surface a toast. Returns the parsed body on success.
 */
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body.detail || body.error || JSON.stringify(body);
    } catch (_) {
      detail = await res.text().catch(() => "");
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? " — " + detail : ""}`);
  }
  // Some endpoints may return empty bodies.
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

/* ─────────────────────────── DOM helpers ─────────────────────────── */

const $ = (sel) => document.querySelector(sel);

/** Escape user/agent text before injecting into innerHTML. */
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Transient toast message. type: "" | "error" | "success". */
function toast(message, type = "", ms = 4200) {
  const host = $("#toast-host");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 320);
  }, ms);
}

function setConn(live) {
  $("#conn-dot").className = "conn-dot " + (live ? "live" : "down");
  $("#conn-text").textContent = live ? "live" : "offline";
}

/* ─────────────────────────── Map ─────────────────────────── */

function initMap(center) {
  state.map = L.map("map", { zoomControl: true, attributionControl: false }).setView(center, 12);

  // Dark basemap (CARTO dark matter) — fits the ops-room theme.
  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { maxZoom: 19, subdomains: "abcd" }
  ).addTo(state.map);

  // Route line sits UNDER the markers; markers go on top.
  state.routeLayer = L.layerGroup().addTo(state.map);
  state.techLayer = L.layerGroup().addTo(state.map);
  state.jobLayer = L.layerGroup().addTo(state.map);
}

/** Build a divIcon for a technician marker. `recommended` gives it the
 *  highlighted, larger, pulsing look used for the crew's chosen technician. */
function techIcon(tech, recommended = false) {
  const busy = tech.status !== "available";
  const cls = recommended ? "tech-recommended" : busy ? "tech-busy" : "tech-available";
  const size = recommended ? 24 : 16;
  return L.divIcon({
    className: "",
    html: `<div class="ff-marker ${cls}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/** Build a divIcon (diamond pin) for a job marker, colored by severity. */
function jobIcon(job) {
  const assigned = ["assigned", "scheduled", "in_progress", "done"].includes(job.status);
  return L.divIcon({
    className: "",
    html: `<div class="ff-job ${esc(job.severity)} ${assigned ? "assigned" : ""}"></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

/** Pull {lat, lon} out of a record's `location` geo_point. */
function latlng(rec) {
  const loc = rec && rec.location;
  if (!loc || typeof loc.lat !== "number" || typeof loc.lon !== "number") return null;
  return [loc.lat, loc.lon];
}

/** The technician id the current plan recommends (or null). */
function recommendedTechId() {
  const rec = state.currentPlan && state.currentPlan.recommended_technician;
  return rec ? (rec.id || null) : null;
}

function renderTechMarkers() {
  if (!state.techLayer) return;
  state.techLayer.clearLayers();
  state.techMarkers = {};
  const recId = recommendedTechId();
  for (const t of state.technicians) {
    const ll = latlng(t);
    if (!ll) continue;
    const isRec = t.id != null && t.id === recId;
    const m = L.marker(ll, {
      icon: techIcon(t, isRec),
      zIndexOffset: isRec ? 1000 : 0,
    });
    m.bindPopup(
      `<div class="popup-title">${esc(t.name)}${isRec ? " ⭐" : ""}</div>` +
      `<div class="popup-sub">${esc(t.status)} · ★ ${esc(t.rating ?? "—")}</div>` +
      `<div class="popup-sub">${esc((t.skills || []).join(", ") || "no skills listed")}</div>`
    );
    state.techMarkers[t.id] = m;
    state.techLayer.addLayer(m);
  }
}

/** Fly to a technician's marker, open its popup, and pulse it. */
function focusTechnician(techId) {
  const m = state.techMarkers[techId];
  if (!m || !state.map) {
    toast("That technician isn't on the map.", "error");
    return;
  }
  state.map.flyTo(m.getLatLng(), 15, { duration: 0.6 });
  m.openPopup();
  // Restart the ping animation on the marker element.
  const el = m.getElement && m.getElement();
  const dot = el && el.querySelector(".ff-marker");
  if (dot) {
    dot.classList.remove("ping");
    void dot.offsetWidth; // force reflow so the animation re-triggers
    dot.classList.add("ping");
  }
}

/** Draw the dispatch route line from the job to the recommended technician,
 *  highlight that technician on the map, and frame both. */
function drawDispatchRoute(plan) {
  if (!state.routeLayer || !state.map) return;
  state.routeLayer.clearLayers();
  const rec = plan && plan.recommended_technician;
  if (!rec) return;
  const job =
    state.jobs.find((j) => j.id === plan.job_id) ||
    state.jobs.find((j) => j.id === state.selectedJobId);
  const techRec = state.technicians.find((t) => t.id === rec.id);
  const jobLL = job && latlng(job);
  const techLL = techRec && latlng(techRec);
  if (!jobLL || !techLL) return;

  const line = L.polyline([jobLL, techLL], {
    color: "#38bdf8",
    weight: 3,
    opacity: 0.9,
    dashArray: "1 10",
    lineCap: "round",
    className: "dispatch-route",
  });
  state.routeLayer.addLayer(line);

  // Re-render tech markers so the recommended one picks up the highlight icon.
  renderTechMarkers();

  // Frame the job and the technician together.
  state.map.fitBounds(L.latLngBounds([jobLL, techLL]).pad(0.45), { maxZoom: 15 });
}

/** Clear any drawn route and drop the recommended-technician highlight. */
function clearDispatchRoute() {
  if (state.routeLayer) state.routeLayer.clearLayers();
  renderTechMarkers();
}

function renderJobMarkers() {
  if (!state.jobLayer) return;
  state.jobLayer.clearLayers();
  state.jobMarkers = {};
  for (const j of state.jobs) {
    const ll = latlng(j);
    if (!ll) continue;
    const m = L.marker(ll, { icon: jobIcon(j) });
    m.bindPopup(
      `<div class="popup-title">${esc(j.customer_name)} — ${esc(j.service_type)}</div>` +
      `<div class="popup-sub">${esc(j.severity)} · ${esc(j.status)}</div>` +
      `<div class="popup-sub">${esc(j.address || "")}</div>`
    );
    // Clicking a map pin selects the job in the sidebar too.
    m.on("click", () => selectJob(j.id));
    state.jobMarkers[j.id] = m;
    state.jobLayer.addLayer(m);
  }
}

/* ─────────────────────────── Job Queue ─────────────────────────── */

/** Sort: most urgent first (severity, then nearest SLA deadline). Open jobs above done/cancelled. */
function sortedJobs() {
  const closed = (s) => s === "done" || s === "cancelled";
  return [...state.jobs].sort((a, b) => {
    if (closed(a.status) !== closed(b.status)) return closed(a.status) ? 1 : -1;
    const sev = (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9);
    if (sev !== 0) return sev;
    return new Date(a.sla_deadline || 0) - new Date(b.sla_deadline || 0);
  });
}

function renderJobQueue() {
  const list = $("#job-list");
  const jobs = sortedJobs();
  if (!jobs.length) {
    list.innerHTML = `<div class="empty-hint">No jobs in queue.</div>`;
    return;
  }
  list.innerHTML = "";
  for (const j of jobs) {
    const assigned = j.status !== "new" && j.status !== "diagnosing";
    const card = document.createElement("div");
    card.className =
      "job-card" +
      (j.id === state.selectedJobId ? " selected" : "") +
      (assigned ? " assigned" : "");
    card.dataset.jobId = j.id;
    card.innerHTML =
      `<div class="job-row">` +
        `<span class="chip ${esc(j.severity)}">${esc(j.severity)}</span>` +
        `<span class="job-title">${esc(j.customer_name)}</span>` +
        `<span class="status-tag ${esc(j.status)}">${esc(j.status)}</span>` +
      `</div>` +
      `<div class="job-meta">` +
        `<span class="svc">${esc(j.service_type)}</span>` +
        `<span>${esc(truncate(j.description, 60))}</span>` +
      `</div>`;
    card.addEventListener("click", () => selectJob(j.id));
    list.appendChild(card);
  }
}

function truncate(s, n) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

/* ─────────────────────────── Selection + Dispatch ─────────────────────────── */

function selectJob(jobId) {
  state.selectedJobId = jobId;
  renderJobQueue();
  // Fly to the job on the map and open its popup.
  const marker = state.jobMarkers[jobId];
  if (marker && state.map) {
    state.map.flyTo(marker.getLatLng(), 14, { duration: 0.6 });
    marker.openPopup();
  }
  showDispatchIntro(jobId);
}

/** Render the initial "Dispatch with AI" call-to-action for the selected job. */
function showDispatchIntro(jobId) {
  const job = state.jobs.find((j) => j.id === jobId);
  if (!job) return;
  const panel = $("#plan-panel");
  const body = $("#plan-body");
  panel.hidden = false;
  state.currentPlan = null;
  clearDispatchRoute(); // drop any previous route/highlight

  body.innerHTML =
    `<div class="plan-summary">` +
      `<strong>${esc(job.customer_name)}</strong> · ${esc(job.service_type)} ` +
      `<span class="chip ${esc(job.severity)}">${esc(job.severity)}</span><br/>` +
      esc(job.description || "") +
    `</div>` +
    `<button id="dispatch-btn" class="btn btn-primary">⚡ Dispatch with AI</button>`;

  $("#dispatch-btn").addEventListener("click", () => runDispatch(jobId));
}

/** Kick off AI planning: POST /api/dispatch/{id}, show loading, render the plan. */
async function runDispatch(jobId) {
  const body = $("#plan-body");
  body.innerHTML =
    `<div class="plan-loading">` +
      `<div class="spinner"></div>` +
      `<div>Coordinator is planning…</div>` +
      `<div class="loading-sub">Searching technicians, incidents & parts in Elasticsearch</div>` +
    `</div>`;
  try {
    const plan = await api(`/api/dispatch/${encodeURIComponent(jobId)}`, { method: "POST" });
    state.currentPlan = plan;
    renderPlan(plan);
  } catch (err) {
    body.innerHTML =
      `<div class="empty-hint">Dispatch failed.<br/>${esc(err.message)}</div>` +
      `<button id="retry-btn" class="btn btn-primary">↻ Retry</button>`;
    $("#retry-btn").addEventListener("click", () => runDispatch(jobId));
    toast("Dispatch failed: " + err.message, "error");
  }
}

/** Render a DispatchPlan as a readable card with an Approve & Execute button. */
function renderPlan(plan) {
  const body = $("#plan-body");
  const d = plan.diagnosis || {};
  const tech = plan.recommended_technician || null;
  const parts = plan.parts || [];
  const messages = plan.messages || [];
  const actions = plan.proposed_actions || [];

  const sections = [];

  // Summary
  if (plan.summary) {
    sections.push(`<div class="plan-summary">${esc(plan.summary)}</div>`);
  }

  // Diagnosis — flexible: render known fields, fall back to whatever keys exist.
  const diagRows = [];
  if (d.root_cause) diagRows.push(kv("Root cause", esc(d.root_cause)));
  const skills = d.skills || d.required_skills || d.skills_used;
  if (skills && skills.length) diagRows.push(kv("Skills", tagRow(skills)));
  const dparts = d.parts || d.required_parts || d.parts_used;
  if (dparts && dparts.length) diagRows.push(kv("Parts", tagRow(dparts)));
  const dur = d.est_duration_minutes ?? d.duration_minutes ?? d.estimated_duration;
  if (dur != null) diagRows.push(kv("Est. duration", `${esc(dur)} min`));
  if (d.summary && !plan.summary) diagRows.push(kv("Notes", esc(d.summary)));
  if (diagRows.length) {
    sections.push(section("Diagnosis", diagRows.join("")));
  }

  // Recommended technician — clickable: locates the technician on the map.
  if (tech) {
    const name = tech.name || tech.id || "Technician";
    const eta = tech.eta_minutes ?? tech.eta;
    const dist = tech.distance_km;
    const hasMarker = tech.id != null;
    sections.push(section("Recommended Technician",
      `<div class="tech-card${hasMarker ? " clickable" : ""}" id="rec-tech-card"` +
        ` data-tech-id="${esc(tech.id || "")}"` +
        (hasMarker ? ` title="Click to locate ${esc(name)} on the map"` : "") + `>` +
        `<div class="tech-avatar">${esc((name[0] || "T").toUpperCase())}</div>` +
        `<div class="tech-info">` +
          `<div class="tech-name">${esc(name)}</div>` +
          `<div class="tech-sub">★ ${esc(tech.rating ?? "—")}` +
            (dist != null ? ` · ${esc(dist)} km away` : "") + `</div>` +
        `</div>` +
        (eta != null
          ? `<div class="tech-eta">${esc(eta)}<small>min ETA</small></div>`
          : "") +
        (hasMarker ? `<div class="locate-hint">📍 Locate</div>` : "") +
      `</div>`
    ));
  }

  // Parts availability
  if (parts.length) {
    const items = parts.map((p) => {
      const inStock = p.in_stock ?? (p.quantity_on_hand > 0);
      return (
        `<div class="part-item">` +
          `<span class="part-name">${esc(p.name || p.sku || p.id)}</span>` +
          (p.nearest_stock_km != null
            ? `<span class="part-dist">${esc(p.nearest_stock_km)} km</span>`
            : "") +
          `<span class="part-status ${inStock ? "in" : "out"}">` +
            (inStock ? `in stock (${esc(p.quantity_on_hand ?? "?")})` : "out of stock") +
          `</span>` +
        `</div>`
      );
    });
    sections.push(section("Parts", items.join("")));
  }

  // Drafted messages (customer + technician)
  if (messages.length) {
    const cards = messages.map((m) => {
      const to = m.recipient || m.to || m.audience || "recipient";
      const ch = m.channel || "message";
      return (
        `<div class="msg-card">` +
          `<div class="msg-head"><span>${esc(to)}</span><span>${esc(ch)}</span></div>` +
          `<div class="msg-body">${esc(m.message || m.body || m.text || "")}</div>` +
        `</div>`
      );
    });
    sections.push(section("Drafted Messages", cards.join("")));
  }

  // Proposed actions preview
  if (actions.length) {
    const rows = actions.map((a) =>
      `<div class="action-preview">` +
        `<span class="ai">${ACTION_ICONS[a.action_type] || "•"}</span>` +
        `<span>${esc(a.description || a.action_type)}</span>` +
      `</div>`
    );
    sections.push(section(`Proposed Actions (${actions.length})`, rows.join("")));
  }

  // Approve button — disabled if the plan has nothing to execute.
  const canExecute = actions.length > 0;
  sections.push(
    `<button id="approve-btn" class="btn btn-approve" ${canExecute ? "" : "disabled"}>` +
      `✓ Approve &amp; Execute` +
    `</button>`
  );

  body.innerHTML = sections.join("");
  if (canExecute) {
    $("#approve-btn").addEventListener("click", () => approvePlan());
  }

  // Make the recommended-technician card locate that person on the map.
  const techCard = $("#rec-tech-card");
  if (techCard && techCard.dataset.techId) {
    techCard.addEventListener("click", () => focusTechnician(techCard.dataset.techId));
  }

  // Draw the dispatch route (job → technician) and highlight the technician.
  drawDispatchRoute(plan);
}

/* small markup builders -------------------------------------------------- */
function section(label, inner) {
  return `<div class="plan-section"><div class="sec-label">${esc(label)}</div>${inner}</div>`;
}
function kv(k, vHtml) {
  return `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${vHtml}</span></div>`;
}
function tagRow(items) {
  return `<span class="tag-row">${items.map((i) => `<span class="tag">${esc(i)}</span>`).join("")}</span>`;
}

/** Approve: POST /api/approve with the exact plan object, then refresh everything. */
async function approvePlan() {
  if (!state.currentPlan) return;
  const btn = $("#approve-btn");
  btn.disabled = true;
  btn.textContent = "Executing…";
  try {
    await api("/api/approve", {
      method: "POST",
      body: JSON.stringify(state.currentPlan),
    });
    toast("Plan executed — actions logged to the mission timeline.", "success");
    btn.className = "btn btn-success";
    btn.textContent = "✓ Executed";
    // Refresh the world so the UI reflects the new state.
    await Promise.all([loadJobs(), loadTechnicians(), loadActions()]);
    // Keep the executed plan visible but re-render the (now-assigned) job's queue row.
    renderJobQueue();
  } catch (err) {
    btn.disabled = false;
    btn.className = "btn btn-approve";
    btn.innerHTML = "✓ Approve &amp; Execute";
    toast("Approval failed: " + err.message, "error");
  }
}

/* ─────────────────────────── Mission Timeline ─────────────────────────── */

function renderTimeline(actions) {
  const el = $("#timeline");
  if (!actions || !actions.length) {
    el.innerHTML = `<div class="empty-hint">No actions yet.</div>`;
    return;
  }
  // newest first
  const sorted = [...actions].sort(
    (a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0)
  );
  el.innerHTML = sorted
    .map((a) => {
      const icon = ACTION_ICONS[a.action_type] || "•";
      return (
        `<div class="tl-item">` +
          `<div class="tl-icon">${icon}</div>` +
          `<div class="tl-body">` +
            `<div class="tl-summary">${esc(a.summary || a.action_type)}</div>` +
            `<div class="tl-meta">` +
              `<span class="tl-actor">${esc(a.actor || "system")}</span>` +
              `<span>${fmtTime(a.timestamp)}</span>` +
            `</div>` +
          `</div>` +
        `</div>`
      );
    })
    .join("");
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/* ─────────────────────────── SLA banner ─────────────────────────── */

function renderSla(sla) {
  const banner = $("#sla-banner");
  const text = $("#sla-text");
  const breakdown = $("#sla-breakdown");
  const total = sla && sla.at_risk_total ? sla.at_risk_total : 0;

  if (total > 0) {
    banner.className = "sla-banner sla-risk";
    text.textContent = `${total} job${total === 1 ? "" : "s"} at SLA risk`;
    const by = sla.by_severity || {};
    const parts = Object.keys(by)
      .sort((a, b) => (SEVERITY_ORDER[a] ?? 9) - (SEVERITY_ORDER[b] ?? 9))
      .map((k) => `${by[k]} ${k}`);
    breakdown.textContent = parts.length ? "· " + parts.join(", ") : "";
  } else {
    banner.className = "sla-banner sla-ok";
    text.textContent = "SLA: all clear";
    breakdown.textContent = "";
  }
}

/* ─────────────────────────── Loaders ─────────────────────────── */

async function loadTechnicians() {
  try {
    const data = await api("/api/technicians");
    state.technicians = Array.isArray(data) ? data : (data.technicians || []);
    renderTechMarkers();
    setConn(true);
  } catch (err) {
    setConn(false);
    toast("Could not load technicians: " + err.message, "error");
  }
}

async function loadJobs() {
  try {
    const data = await api("/api/jobs");
    state.jobs = Array.isArray(data) ? data : (data.jobs || []);
    renderJobQueue();
    renderJobMarkers();
    setConn(true);
  } catch (err) {
    setConn(false);
    $("#job-list").innerHTML = `<div class="empty-hint">Could not load jobs.</div>`;
    toast("Could not load jobs: " + err.message, "error");
  }
}

async function loadActions() {
  try {
    const data = await api("/api/actions");
    const actions = Array.isArray(data) ? data : (data.actions || []);
    renderTimeline(actions);
  } catch (err) {
    // Non-fatal: timeline just stays as-is.
    setConn(false);
  }
}

async function loadSla() {
  try {
    const sla = await api("/api/sla");
    renderSla(sla);
    setConn(true);
  } catch (err) {
    setConn(false);
  }
}

/* ─────────────────────────── Wiring + bootstrap ─────────────────────────── */

function wireControls() {
  $("#refresh-jobs").addEventListener("click", loadJobs);
  $("#refresh-actions").addEventListener("click", loadActions);
  $("#close-plan").addEventListener("click", () => {
    $("#plan-panel").hidden = true;
    state.selectedJobId = null;
    state.currentPlan = null;
    clearDispatchRoute();
    renderJobQueue();
  });
}

async function boot() {
  wireControls();

  // Load technicians first so we can center the map on them.
  await loadTechnicians();
  const first = state.technicians.map(latlng).find(Boolean);
  initMap(first || FALLBACK_CENTER);
  renderTechMarkers(); // re-render now that the layer exists

  // Load the rest in parallel.
  await Promise.all([loadJobs(), loadSla(), loadActions()]);

  // If technicians failed before the map existed, fit bounds to everything now.
  fitToData();

  // Live polling for the SLA banner and the timeline.
  setInterval(loadSla, SLA_POLL_MS);
  setInterval(loadActions, ACTIONS_POLL_MS);
}

/** Fit the map to all plotted points so the demo opens framed correctly. */
function fitToData() {
  if (!state.map) return;
  const pts = [
    ...state.technicians.map(latlng),
    ...state.jobs.map(latlng),
  ].filter(Boolean);
  if (pts.length > 1) {
    state.map.fitBounds(L.latLngBounds(pts).pad(0.18));
  }
}

// Go.
document.addEventListener("DOMContentLoaded", boot);
