const state = {
  apiKey: sessionStorage.getItem("buildlog_api_key") || "",
  refreshTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const apiKeyInput = $("#api-key");
apiKeyInput.value = state.apiKey;

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

$("#connect").addEventListener("click", () => {
  state.apiKey = apiKeyInput.value.trim();
  sessionStorage.setItem("buildlog_api_key", state.apiKey);
  refreshAll();
});
$("#refresh").addEventListener("click", refreshAll);
$("#run-status").addEventListener("change", loadRuns);
$("#close-dialog").addEventListener("click", () => $("#run-dialog").close());
$("#iteration-form").addEventListener("submit", submitIteration);

function setView(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `${name}-view`);
  });
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("Authorization", `Bearer ${state.apiKey}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const body = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    throw new Error(body.detail || body || `Request failed: ${response.status}`);
  }
  return body;
}

async function refreshAll() {
  try {
    await Promise.all([loadDashboard(), loadRuns(), loadJobs()]);
    setConnection("Connected", "ok");
  } catch (error) {
    setConnection("Blocked", "error");
    toast(error.message);
  }
}

async function loadDashboard() {
  const data = await api("/api/v1/dashboard");
  const metrics = [
    ["Completion rate", `${data.completion_rate}%`],
    ["Versioned artifacts", formatNumber(data.total_artifacts)],
    ["Evaluated runs", formatNumber(data.evaluated_runs)],
    ["Average quality", data.average_evaluation_score ?? "-"],
    ["Live publications", formatNumber(data.live_publications)],
    ["P95 pipeline", formatDuration(data.p95_pipeline_latency_ms)],
  ];
  $("#metric-grid").innerHTML = metrics
    .map(([label, value]) => `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

async function loadRuns() {
  const status = $("#run-status").value;
  const data = await api(`/api/v1/runs?limit=40${status ? `&status=${status}` : ""}`);
  const body = $("#runs-body");
  if (!data.length) {
    body.innerHTML = '<tr><td class="empty" colspan="8">No runs match this view.</td></tr>';
    return;
  }
  body.innerHTML = data.map((run) => `
    <tr>
      <td class="mono">${escapeHtml(shortId(run.id))}</td>
      <td>${escapeHtml(run.title)}</td>
      <td><span class="status ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></td>
      <td>${run.average_evaluation_score ?? "-"}</td>
      <td>${escapeHtml(formatDuration(run.duration_ms))}</td>
      <td>${run.artifact_count}</td>
      <td>${escapeHtml(formatDate(run.started_at))}</td>
      <td><button class="secondary small" data-run-id="${escapeHtml(run.id)}" type="button">View</button></td>
    </tr>`).join("");
  body.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", () => showRun(button.dataset.runId));
  });
}

async function loadJobs() {
  const data = await api("/api/v1/jobs?limit=30");
  const body = $("#jobs-body");
  if (!data.length) {
    body.innerHTML = '<tr><td class="empty" colspan="6">No API-submitted jobs yet.</td></tr>';
    return;
  }
  body.innerHTML = data.map((job) => `
    <tr>
      <td class="mono">${escapeHtml(shortId(job.id))}</td>
      <td><span class="status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></td>
      <td>${job.attempt_count}</td>
      <td class="mono">${escapeHtml(job.run_id ? shortId(job.run_id) : "-")}</td>
      <td>${escapeHtml(formatDate(job.updated_at))}</td>
      <td>${escapeHtml(job.safe_error_message || "-")}</td>
    </tr>`).join("");
}

async function showRun(runId) {
  try {
    const data = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
    $("#run-detail").textContent = JSON.stringify(data, null, 2);
    $("#run-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function submitIteration(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const title = form.get("title").trim();
  const payload = {
    id: `${slug(title)}-${Date.now()}`,
    title,
    goal: form.get("goal").trim(),
    context: form.get("context").trim(),
    problem: form.get("problem").trim(),
    actions: lines(form.get("actions")),
    decisions: [{
      decision: form.get("decision").trim(),
      reason: form.get("decision_reason").trim(),
      alternatives_considered: lines(form.get("alternatives")),
    }],
    trade_offs: lines(form.get("trade_offs")),
    result: form.get("result").trim(),
    lessons: lines(form.get("lessons")),
    evidence: lines(form.get("evidence")),
    audience: form.get("audience").trim(),
    metadata: { project_id: "buildlog-web", project_name: "BuildLog Web Intake" },
  };
  const status = $("#submit-status");
  status.textContent = "Submitting";
  try {
    const response = await api("/api/v1/jobs", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify(payload),
    });
    status.textContent = `Queued ${shortId(response.job.id)}`;
    event.currentTarget.reset();
    toast("Workflow queued");
    setView("overview");
    await refreshAll();
  } catch (error) {
    status.textContent = "Submission failed";
    toast(error.message);
  }
}

function lines(value) {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "iteration";
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function formatDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return "-";
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(1)} s`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function shortId(value) {
  return value.length > 20 ? `${value.slice(0, 17)}...` : value;
}

function setConnection(label, className) {
  const element = $("#connection-status");
  element.textContent = label;
  element.className = `connection-state ${className}`;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("visible");
  window.setTimeout(() => element.classList.remove("visible"), 3000);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

refreshAll();
state.refreshTimer = window.setInterval(() => {
  if ($("#overview-view").classList.contains("active")) refreshAll();
}, 10000);
