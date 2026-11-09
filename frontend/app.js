// Crypt Keeper frontend. Talks to the backend /revive endpoint and renders the
// autopsy report, the resurrection diff, and the proof of life.
// Business logic lives in the backend; this file only renders.

const API_BASE = "http://127.0.0.1:8000";

const form = document.getElementById("exhume-form");
const statusEl = document.getElementById("status");
const results = document.getElementById("results");
const autopsyBody = document.getElementById("autopsy-body");
const revivalBody = document.getElementById("revival-body");
const proofBody = document.getElementById("proof-body");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const target = document.getElementById("target").value.trim();
  if (!target) return;

  setStatus("🕯️ Exhuming the grave…");
  results.hidden = true;

  // For the skeleton we send a placeholder snapshot. Task 11 wires real exhumation
  // so the backend fetches the snapshot itself from the package name.
  const snapshot = {
    ref: { source: target.includes("github.com") ? "github" : "pypi", identifier: target },
    last_release_date: null,
    declared_pythons: [],
    dependencies: [],
    source_map: {},
  };

  try {
    const res = await fetch(`${API_BASE}/revive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(snapshot),
    });
    if (!res.ok) throw new Error(`The crypt resisted (${res.status}).`);
    const data = await res.json();
    render(data);
    setStatus("");
  } catch (err) {
    setStatus(`💀 ${err.message}`);
  }
});

function setStatus(msg) {
  statusEl.textContent = msg;
}

function render(data) {
  renderAutopsy(data.autopsy);
  renderRevival(data.revival);
  renderProof(data.proof_of_life);
  results.hidden = false;
}

function renderAutopsy(report) {
  if (!report) return;
  const findings = (report.findings || [])
    .map(
      (f) =>
        `<div class="finding sev-${f.severity}"><strong>[${f.severity}]</strong> ${escapeHtml(
          f.message
        )}${f.location ? ` <em>(${escapeHtml(f.location)})</em>` : ""}</div>`
    )
    .join("");
  autopsyBody.innerHTML = `
    <p>Liveness: <strong>${escapeHtml(report.liveness)}</strong> ·
       target Python <strong>${escapeHtml(report.target_python)}</strong></p>
    ${findings || "<p>No findings recorded.</p>"}
  `;
}

function renderRevival(revival) {
  if (!revival) return;
  const applied = (revival.applied || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
  const manual = (revival.manual_items || [])
    .map((m) => `<li>${escapeHtml(m)}</li>`)
    .join("");
  const diffs = (revival.diffs || [])
    .map(
      (d) =>
        `<p>${escapeHtml(d.path)}</p><pre><span class="diff-del">- ${escapeHtml(
          d.before
        )}</span>\n<span class="diff-add">+ ${escapeHtml(d.after)}</span></pre>`
    )
    .join("");
  revivalBody.innerHTML = `
    ${applied ? `<p>Applied:</p><ul>${applied}</ul>` : ""}
    ${manual ? `<p>Manual revival needed:</p><ul>${manual}</ul>` : ""}
    ${diffs || "<p>No source changes yet.</p>"}
  `;
}

function renderProof(proof) {
  if (!proof) return;
  proofBody.innerHTML = proof.imported
    ? `<p class="sev-low">💓 Imported successfully. Tests: ${proof.tests_passed} passed, ${proof.tests_failed} failed.</p>`
    : `<p class="sev-high">💀 ${escapeHtml(proof.error || "Not yet alive.")}</p>`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}
