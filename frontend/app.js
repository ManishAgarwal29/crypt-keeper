"use strict";
// Crypt Keeper UI. It only renders what /revive/stream reports; all analysis happens on the server.
// Everything that comes from a package (names, messages, diffs, logs) is inserted with textContent,
// never innerHTML, because it is untrusted.

const API = window.CRYPT_KEEPER_API ?? "";

const STAGES = [
  { id: "exhume", label: "Exhume", blurb: "Dig the package up from PyPI or GitHub" },
  { id: "autopsy", label: "Autopsy", blurb: "Find the cause of death" },
  { id: "revive", label: "Resurrect", blurb: "Rewrite what blocks modern Python" },
  { id: "proof_before", label: "Proof of life: the original", blurb: "Does the dead package run?" },
  { id: "proof_after", label: "Proof of life: the revived", blurb: "Does the revived package run?" },
];

const VERDICTS = {
  resurrected: { title: "It lives!", icon: "pulse" },
  improved: { title: "Stronger than before", icon: "pulse" },
  already_alive: { title: "Already alive", icon: "check" },
  still_dead: { title: "Still dead", icon: "skull" },
  regressed: { title: "Revival made it worse", icon: "down" },
  unverified: { title: "Unverified", icon: "warn" },
};

const KIND_TITLES = {
  "abandoned-package": "Abandoned",
  "python-support-gap": "Python support gap",
  "truncated-source": "Incomplete exhumation",
  "pinned-dependency": "Pinned dependencies",
  "upper-bounded-dependency": "Upper-bounded dependencies",
  "incompatible-dependency": "Incompatible dependencies",
  "yanked-dependency": "Yanked dependencies",
  "unmaintained-dependency": "Unmaintained dependencies",
  "unparseable-dependency": "Unreadable dependencies",
  "deprecated-api": "Removed APIs",
  "syntax-error": "Python 2 syntax",
};

const SEVERITY_RANK = { high: 0, medium: 1, low: 2 };
const FINDINGS_SHOWN = 30;
const DIFF_LINES_SHOWN = 400;

const $ = (id) => document.getElementById(id);
const form = $("exhume-form");
const targetInput = $("target");
const pythonSelect = $("python");
const raiseButton = $("raise");
const inputError = $("input-error");
const pythonHint = $("python-hint");
const ritual = $("ritual");
const stagesEl = $("stages");
const runError = $("run-error");
const results = $("results");

let activeRun = null;
let stageNodes = {};

// --- tiny DOM helpers ---------------------------------------------------------------------------------

// Children may be nodes, strings, numbers, nested arrays, or falsy placeholders (`cond && node`).
// Falsy values and nulls are dropped; everything else becomes a node.
function toNodes(children) {
  return children.flat(Infinity)
    .filter((child) => child != null && child !== false && child !== true)
    .map((child) => (child.nodeType ? child : document.createTextNode(String(child))));
}

function fill(node, ...children) {
  node.replaceChildren(...toNodes(children));
  return node;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...toNodes(children));
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  pulse: [["polyline", { points: "2 12 7 12 9.5 5 14 19 16.5 12 22 12" }]],
  check: [["polyline", { points: "4 12.5 9.5 18 20 6.5" }]],
  cross: [["line", { x1: 5, y1: 5, x2: 19, y2: 19 }], ["line", { x1: 19, y1: 5, x2: 5, y2: 19 }]],
  down: [["line", { x1: 12, y1: 4, x2: 12, y2: 19 }], ["polyline", { points: "6 13 12 19 18 13" }]],
  warn: [["path", { d: "M12 3 2 21h20L12 3z" }], ["line", { x1: 12, y1: 10, x2: 12, y2: 15 }], ["line", { x1: 12, y1: 18, x2: 12, y2: 18.1 }]],
  skull: [
    ["path", { d: "M12 3a7 7 0 0 0-7 7c0 2.4 1.2 4.4 3 5.6V19h2v-2h4v2h2v-3.4c1.8-1.2 3-3.2 3-5.6a7 7 0 0 0-7-7z" }],
    ["circle", { cx: 9, cy: 11, r: 1.4 }], ["circle", { cx: 15, cy: 11, r: 1.4 }],
  ],
};

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (const [tag, attrs] of ICONS[name]) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    svg.append(node);
  }
  return svg;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function yearsAgo(isoDate) {
  const then = Date.parse(isoDate);
  if (Number.isNaN(then)) return "";
  const years = (Date.now() - then) / (365.25 * 24 * 3600 * 1000);
  return years >= 1 ? ` (${years.toFixed(1)} years ago)` : "";
}

// --- the ritual: live progress -------------------------------------------------------------------------------------

function buildStages() {
  stagesEl.replaceChildren();
  stageNodes = {};
  for (const stage of STAGES) {
    const state = el("span", { class: "stage-state", "aria-hidden": "true" });
    const meta = el("span", { class: "stage-meta", "aria-hidden": "true" });
    const status = el("span", { class: "sr-only", text: "waiting" });
    const li = el(
      "li", { class: "stage is-waiting", "data-stage": stage.id },
      state,
      el("span", {}, el("strong", { text: stage.label }), el("span", { class: "stage-blurb", text: stage.blurb })),
      meta, status,
    );
    stagesEl.append(li);
    stageNodes[stage.id] = { li, state, meta, status, startedAt: null };
  }
}

function setStage(id, status, event = {}) {
  const node = stageNodes[id];
  if (!node) return;
  node.li.className = `stage is-${status}`;
  fill(node.state, status === "done" && icon("check"), status === "failed" && icon("cross"));
  if (status === "running") {
    node.startedAt = performance.now();
    node.status.textContent = "running";
    node.meta.textContent = "0.0s";
  } else if (status === "done") {
    node.startedAt = null;
    const detail = event.detail ? `${event.detail} \u00b7 ` : "";
    node.meta.textContent = `${detail}${Number(event.seconds ?? 0).toFixed(1)}s`;
    node.status.textContent = `done: ${event.detail ?? ""}`;
  } else if (status === "failed") {
    node.startedAt = null;
    node.status.textContent = "failed";
  }
}

function tickTimers() {
  for (const node of Object.values(stageNodes)) {
    if (node.startedAt != null) node.meta.textContent = `${((performance.now() - node.startedAt) / 1000).toFixed(1)}s`;
  }
}

function showRunError(message) {
  for (const [id, node] of Object.entries(stageNodes)) if (node.startedAt != null) setStage(id, "failed");
  runError.textContent = message;
  runError.hidden = false;
}

// --- running a revival ----------------------------------------------------------------------------------------------

const guessSource = (target) => (/^https?:\/\/github\.com\//i.test(target) ? "github" : "pypi");

async function errorText(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) return `The crypt rejected the request: ${body.detail[0].msg}`;
  } catch { /* fall through */ }
  return `The crypt refused to open (HTTP ${response.status}).`;
}

async function readLines(stream, onLine) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onLine(JSON.parse(line));
    }
  }
  if (buffer.trim()) onLine(JSON.parse(buffer));
}

function setBusy(busy) {
  raiseButton.disabled = busy;
  raiseButton.textContent = busy ? "Raising\u2026" : "Raise it";
  for (const b of document.querySelectorAll(".headstone")) b.disabled = busy;
}

async function run(target, python) {
  activeRun?.controller.abort();
  const controller = new AbortController();
  const state = { controller, finished: false, timer: setInterval(tickTimers, 200) };
  activeRun = state;

  results.hidden = true;
  runError.hidden = true;
  buildStages();
  ritual.hidden = false;
  setBusy(true);
  history.replaceState(null, "", `?package=${encodeURIComponent(target)}&python=${encodeURIComponent(python)}`);

  try {
    const response = await fetch(`${API}/revive/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: { source: guessSource(target), identifier: target }, target_python: python }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error(await errorText(response));

    await readLines(response.body, (event) => {
      if (event.event === "stage") {
        if (event.status === "start") setStage(event.stage, "running");
        else setStage(event.stage, "done", event);
      } else if (event.event === "result") {
        state.finished = true;
        renderResults(event.data);
      } else if (event.event === "error") {
        state.finished = true;
        showRunError(event.detail);
      }
    });
    if (!state.finished) throw new Error("The connection closed before the ritual finished.");
  } catch (error) {
    if (error.name !== "AbortError") showRunError(error.message || "Something stirred in the dark, and it was not the package.");
  } finally {
    clearInterval(state.timer);
    if (activeRun === state) {
      activeRun = null;
      setBusy(false);
    }
  }
}

// --- rendering results -------------------------------------------------------------------------------------------------

function renderResults(data) {
  renderVerdict(data);
  renderAutopsy(data);
  renderResurrection(data);
  renderProof(data);
  const t = data.timings || {};
  $("timings").textContent = ["exhume", "autopsy", "revive", "proof_of_life"]
    .filter((k) => k in t).map((k) => `${k.replace("_", " ")} ${t[k].toFixed(1)}s`).join(" \u00b7 ");
  results.hidden = false;
  const verdict = $("verdict");
  verdict.setAttribute("tabindex", "-1");
  verdict.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  verdict.focus({ preventScroll: true });
}

function renderVerdict(data) {
  const info = VERDICTS[data.verdict] ?? VERDICTS.unverified;
  const section = $("verdict");
  section.className = `panel verdict v-${data.verdict}`;
  const after = data.proof_after;
  const mismatch = after && after.python_used && !after.target_python_matched;
  fill(section,
    el("span", { class: "verdict-icon" }, icon(info.icon)),
    el("h2", { id: "verdict-heading", text: info.title }),
    el("p", { class: "reason", text: data.verdict_reason }),
    mismatch && el("p", { class: "callout", role: "note" },
      el("strong", { text: "Check this on the right Python. " }),
      `Python ${data.autopsy.target_python} is not installed on this machine, so the check ran on ${after.python_used}. ` +
      `A package that works there can still fail on ${data.autopsy.target_python}. Install it for a real verdict.`),
    el("p", { class: "subject" },
      el("strong", { text: data.package.identifier }),
      ` \u00b7 target Python ${data.autopsy.target_python}`,
      data.package.last_release_date && ` \u00b7 last release ${data.package.last_release_date}${yearsAgo(data.package.last_release_date)}`),
  );
}

function severityBadge(severity) {
  return el("span", { class: `badge sev-${severity}`, text: severity });
}

function renderAutopsy(data) {
  const report = data.autopsy;
  const section = $("autopsy");
  const counts = { high: 0, medium: 0, low: 0 };
  for (const f of report.findings) counts[f.severity] += 1;

  const groups = new Map();
  for (const f of report.findings) {
    if (!groups.has(f.kind)) groups.set(f.kind, []);
    groups.get(f.kind).push(f);
  }
  const ordered = [...groups.entries()].sort(([, a], [, b]) => {
    const worst = (list) => Math.min(...list.map((f) => SEVERITY_RANK[f.severity]));
    return worst(a) - worst(b) || b.length - a.length;
  });

  fill(section,
    el("h2", { id: "autopsy-heading", text: "Autopsy report" }),
    el("p", { class: "facts" },
      el("span", {}, "Liveness: ", el("span", { class: `badge live-${report.liveness}`, text: report.liveness })),
      el("span", {}, el("strong", { text: plural(data.package.file_count, "file") }), " examined"),
      el("span", {}, el("strong", { text: plural(data.package.dependencies.length, "dependency").replace("dependencys", "dependencies") }))),
    report.findings.length
      ? el("ul", { class: "severity-summary", "aria-label": "Findings by severity" },
          ["high", "medium", "low"].filter((s) => counts[s]).map((s) =>
            el("li", {}, el("span", { class: `badge sev-${s}`, text: `${counts[s]} ${s}` }))))
      : el("p", { class: "empty", text: "The coroner found nothing wrong." }),
    ordered.map(([kind, list]) => findingGroup(kind, list, list.length <= 3)),
  );
}

function findingGroup(kind, list, open) {
  const worst = list.reduce((w, f) => (SEVERITY_RANK[f.severity] < SEVERITY_RANK[w] ? f.severity : w), "low");
  const items = list.map((f) =>
    el("li", {}, f.message, f.location && el("code", { class: "where", text: f.location })));
  const shown = items.slice(0, FINDINGS_SHOWN);
  const rest = items.slice(FINDINGS_SHOWN);
  const ul = el("ul", { class: "finding-list" }, shown);
  const body = [ul];
  if (rest.length) {
    const more = el("button", {
      type: "button", class: "more", text: `Show ${plural(rest.length, "more finding")}`,
      onclick: () => { ul.append(...rest); more.remove(); },
    });
    body.push(more);
  }
  return el("details", { open },
    el("summary", {}, severityBadge(worst), el("strong", { text: KIND_TITLES[kind] ?? kind }), el("span", { class: "count", text: plural(list.length, "finding") })),
    body);
}

function renderResurrection(data) {
  const { revival, package: pkg } = data;
  const section = $("resurrection");
  const added = revival.diffs.reduce((n, d) => n + d.lines_added, 0);
  const removed = revival.diffs.reduce((n, d) => n + d.lines_removed, 0);
  const depChanges = revival.dependencies
    .map((after, i) => [pkg.dependencies[i], after])
    .filter(([before, after]) => before !== undefined && before !== after);

  fill(section,
    el("h2", { id: "resurrection-heading", text: "Resurrection" }),
    el("ul", { class: "stat-row" },
      stat(revival.diffs.length, "files rewritten"), stat(`+${added} \u2212${removed}`, "lines"),
      stat(depChanges.length, "dependencies changed"), stat(revival.manual_items.length, "need a human")),
    depChanges.length > 0 && [
      el("h3", { text: "Dependencies" }),
      el("ul", { class: "plain-list" }, depChanges.map(([before, after]) =>
        el("li", { class: "dep-change" }, el("code", { text: before }),
          el("span", { class: "arrow", "aria-hidden": "true", text: "\u2192" }), el("span", { class: "sr-only", text: "becomes" }),
          el("code", { text: after })))),
    ],
    revival.applied.length > 0 && el("details", {},
      el("summary", {}, el("strong", { text: "What the Necromancer did" }), el("span", { class: "count", text: plural(revival.applied.length, "change") })),
      el("ul", { class: "finding-list" }, revival.applied.slice(0, 100).map((a) => el("li", { text: a })))),
    revival.manual_items.length > 0 && el("details", { class: "manual" },
      el("summary", {}, el("span", { class: "badge sev-medium", text: "manual" }), el("strong", { text: "Needs a human" }),
        el("span", { class: "count", text: plural(revival.manual_items.length, "item") })),
      el("ul", { class: "finding-list" }, revival.manual_items.slice(0, 100).map((m) => el("li", { text: m })))),
    revival.diffs.length > 0 && [
      el("h3", { text: "Before and after" }),
      revival.diffs.map((d) => diffBlock(d)),
    ],
    !revival.diffs.length && !depChanges.length && el("p", { class: "empty", text: "Nothing could be rewritten automatically." }),
  );
}

function stat(value, label) {
  return el("li", { class: "stat" }, el("b", { text: String(value) }), el("span", { text: label }));
}

function diffBlock(diff) {
  const lines = diff.unified.split("\n");
  const shown = lines.slice(0, DIFF_LINES_SHOWN).map((line) => {
    const cls = line.startsWith("+++") || line.startsWith("---") ? "file"
      : line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
    return el("span", { class: `line ${cls}`, text: line || " " });
  });
  const note = lines.length > DIFF_LINES_SHOWN
    ? el("span", { class: "line file", text: `\u2026 ${lines.length - DIFF_LINES_SHOWN} more lines not shown` }) : null;
  return el("details", { open: true },
    el("summary", {}, el("code", { text: diff.path }), el("span", { class: "count", text: `+${diff.lines_added} \u2212${diff.lines_removed}` })),
    el("pre", { class: "diff", tabindex: "0", "aria-label": `Diff of ${diff.path}` }, shown, note));
}

function renderProof(data) {
  const section = $("proof");
  fill(section,
    el("h2", { id: "proof-heading", text: "Proof of life" }),
    data.proof_after || data.proof_before
      ? el("div", { class: "proof-grid" },
          proofCard("The original", data.proof_before, "Not checked."),
          proofCard("The revived", data.proof_after, "Not checked."))
      : el("p", { class: "empty", text: "Proof of life was not run." }),
  );
}

function proofCard(title, proof, missing) {
  if (!proof) return el("div", { class: "proof-card" }, el("h3", { text: title }), el("p", { class: "empty", text: missing }));
  if (proof.skipped) return el("div", { class: "proof-card" }, el("h3", { text: title }), el("p", { class: "empty", text: proof.error }));

  const total = proof.tests_passed + proof.tests_failed + proof.tests_errors;
  const what = proof.test_source === "shipped" ? "the package's own tests" : proof.test_source === "smoke" ? "a generated smoke check" : "no checks";
  const modules = Object.entries(proof.import_results || {});
  return el("div", { class: `proof-card ${proof.imported ? "alive" : "dead"}` },
    el("h3", { text: title }),
    el("p", { class: "proof-status" }, icon(proof.imported ? "pulse" : "skull"), proof.imported ? "Alive" : "Dead"),
    !proof.imported && proof.error && el("p", { class: "error-text", text: proof.error }),
    total > 0 && [
      el("p", { class: "test-line" },
        el("strong", { text: `${proof.tests_passed} passed` }), `, ${proof.tests_failed} failed, ${proof.tests_errors} errors`,
        ` (${what})`),
      el("div", { class: "bar", "aria-hidden": "true" },
        el("i", { class: "p", style: `width:${(100 * proof.tests_passed) / total}%` }),
        el("i", { class: "f", style: `width:${(100 * proof.tests_failed) / total}%` }),
        el("i", { class: "e", style: `width:${(100 * proof.tests_errors) / total}%` })),
    ],
    el("p", { class: "meta-line", text: `Ran on Python ${proof.python_used ?? "?"} in ${proof.duration_seconds}s`
      + (proof.python_used && !proof.target_python_matched ? " (not the target Python)" : "") }),
    modules.length > 1 && el("details", {},
      el("summary", {}, el("strong", { text: "Top-level modules" }), el("span", { class: "count", text: plural(modules.length, "module") })),
      el("ul", { class: "finding-list" }, modules.map(([name, err]) =>
        el("li", {}, el("code", { text: name }), err ? ` failed: ${err}` : " imported")))),
    proof.output && el("details", {},
      el("summary", {}, el("strong", { text: "Sandbox log" })),
      el("pre", { class: "log", tabindex: "0", text: proof.output })),
  );
}

// --- wiring --------------------------------------------------------------------------------------------------------------

function start(target) {
  const name = target.trim();
  if (!name) {
    inputError.textContent = "Name a package to exhume, for example attrdict.";
    inputError.hidden = false;
    targetInput.setAttribute("aria-invalid", "true");
    targetInput.focus();
    return;
  }
  inputError.hidden = true;
  targetInput.removeAttribute("aria-invalid");
  targetInput.value = name;
  run(name, pythonSelect.value);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  start(targetInput.value);
});

for (const button of document.querySelectorAll(".headstone")) {
  button.addEventListener("click", () => {
    targetInput.value = button.dataset.package;
    start(button.dataset.package);
  });
}

async function refreshPythonHint() {
  const target = pythonSelect.value;
  try {
    const response = await fetch(`${API}/proof-of-life/status?target_python=${encodeURIComponent(target)}`);
    if (!response.ok) return;
    const status = await response.json();
    if (pythonSelect.value !== target) return;
    pythonHint.textContent = !status.enabled
      ? "Proof of life is switched off on this server, so verdicts will read unverified."
      : status.target_python_matched
        ? `Proof of life will run on Python ${status.python_used}.`
        : `Python ${target} is not installed on this machine. Proof of life will run on ${status.python_used}, so the verdict will read unverified.`;
  } catch { /* the hint is a courtesy; ignore failures */ }
}

pythonSelect.addEventListener("change", refreshPythonHint);

const params = new URLSearchParams(location.search);
if ([...pythonSelect.options].some((o) => o.value === params.get("python"))) pythonSelect.value = params.get("python");
refreshPythonHint();
if (params.get("package")) {
  targetInput.value = params.get("package");
  start(params.get("package"));
}
