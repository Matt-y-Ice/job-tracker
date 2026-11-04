// Job Tracker dashboard. Text from job postings is always inserted with textContent,
// never as HTML, and links must be http(s), because postings come from the internet.
"use strict";

const LISTS = { "Top Matches": "Top", "Low Matches": "Low", "Unsure Location": "Unsure" };
const state = {
  jobs: [], statuses: [], version: null,
  sort: { key: "score", dir: -1 }, expanded: new Set(),
  reloadWhenIdle: false, streaming: false,
  docs: {},  // job id -> generated documents status
};
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Job-Tracker": "1", ...(options.headers || {}) },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

// ------------------------------------------------------------------ loading and status

async function load() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs;
  state.statuses = data.statuses;
  state.version = data.version;
  fillSelect($("f-status"), state.statuses);
  fillSelect($("f-source"), [...new Set(state.jobs.map((j) => j.source).filter(Boolean))].sort());
  showStatus(data);
  render();
  loadDocs();
}

async function loadDocs() {
  try {
    state.docs = await api("/api/documents");
  } catch (err) {
    return;  // documents are optional; the table still works
  }
  if (editing()) state.reloadWhenIdle = true;
  else render();
}

const docsBusy = () => Object.values(state.docs).some((d) => d.state === "queued" || d.state === "running");

function fillSelect(select, options) {
  const keep = select.value;
  while (select.options.length > 1) select.remove(1);
  for (const option of options) select.add(new Option(option, option));
  select.value = options.includes(keep) ? keep : "";
}

function showStatus(data) {
  const banner = $("banner");
  let text = "";
  if (!data.workbook_exists) text = "No workbook yet. Click “Refresh jobs” to find and rank jobs.";
  else if (data.message) text = `${data.message} (${data.pending} change${data.pending === 1 ? "" : "s"} waiting)`;
  banner.textContent = text;
  banner.hidden = !text;
  if (data.refresh && data.refresh.running && !state.streaming) streamRefresh();
}

function editing() {
  const active = document.activeElement;
  return active && active.closest && active.closest("#jobs tbody") && /INPUT|TEXTAREA|SELECT/.test(active.tagName);
}

async function poll() {
  try {
    const data = await api("/api/status");
    showStatus(data);
    if (docsBusy()) await loadDocs();
    if (data.version !== state.version) {
      if (editing()) state.reloadWhenIdle = true;  // never yank a box you are typing in
      else await load();
    }
  } catch (err) {
    $("banner").textContent = "Lost contact with the dashboard server. Is the dashboard window still open?";
    $("banner").hidden = false;
  }
}

document.addEventListener("focusout", () => {
  setTimeout(() => {
    if (state.reloadWhenIdle && !editing()) {
      state.reloadWhenIdle = false;
      load();
    }
  }, 300);
});

// ------------------------------------------------------------------ filtering and sorting

function filtered() {
  const q = $("f-search").value.trim().toLowerCase();
  const status = $("f-status").value;
  const source = $("f-source").value;
  const min = Number($("f-min").value || 0);
  const max = Number($("f-max").value || 100);
  const showLow = $("f-low").checked;
  const showUnsure = $("f-unsure").checked;
  return state.jobs.filter((j) => {
    if (j.sheet === "Low Matches" && !showLow) return false;
    if (j.sheet === "Unsure Location" && !showUnsure) return false;
    if (status && j.status !== status) return false;
    if (source && j.source !== source) return false;
    const score = Number(j.score) || 0;
    if (score < min || score > max) return false;
    if (q) {
      const hay = [j.title, j.company, j.location, j.notes, j.why].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function sorted(jobs) {
  const { key, dir } = state.sort;
  const value = (j) => {
    const v = j[key];
    if (key === "score") return Number(v) || 0;
    return v == null ? "" : String(v).toLowerCase();
  };
  return [...jobs].sort((a, b) => {
    const x = value(a), y = value(b);
    if (x === y) return (Number(b.score) || 0) - (Number(a.score) || 0);
    if (x === "") return 1;
    if (y === "") return -1;
    return x < y ? -dir : dir;
  });
}

for (const th of document.querySelectorAll("th[data-sort]")) {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : (key === "score" ? -1 : 1) };
    render();
  });
}
for (const id of ["f-search", "f-status", "f-min", "f-max", "f-source", "f-low", "f-unsure"]) {
  $(id).addEventListener("input", render);
}

// ------------------------------------------------------------------ rendering

function render() {
  const jobs = sorted(filtered());
  renderTable(jobs);
  renderStatusChart(jobs);
  renderScoreChart(jobs);
  const by = (sheet) => state.jobs.filter((j) => j.sheet === sheet).length;
  $("counts").textContent =
    `${by("Top Matches")} top matches · ${by("Low Matches")} low matches · ${by("Unsure Location")} unsure location`;
  $("showing").textContent = `Showing ${jobs.length} of ${state.jobs.length} jobs`;
  for (const th of document.querySelectorAll("th[data-sort]")) {
    th.classList.toggle("sorted-asc", th.dataset.sort === state.sort.key && state.sort.dir === 1);
    th.classList.toggle("sorted-desc", th.dataset.sort === state.sort.key && state.sort.dir === -1);
  }
}

function scoreClass(score) {
  return score >= 65 ? "good" : score >= 50 ? "mid" : "bad";
}

function safeLink(url) {
  return /^https?:\/\//i.test(url || "") ? url : null;
}

function renderTable(jobs) {
  const body = document.querySelector("#jobs tbody");
  body.replaceChildren();
  $("empty").hidden = jobs.length > 0;
  for (const job of jobs) {
    body.append(jobRow(job));
    if (state.expanded.has(job.id)) body.append(detailRow(job));
  }
}

function jobRow(job) {
  const open = state.expanded.has(job.id);
  const href = safeLink(job.link);
  const title = href
    ? el("a", { href, target: "_blank", rel: "noopener noreferrer", text: job.title })
    : el("span", { text: job.title });
  const note = el("span", { class: "save-note" + (job.pending ? " queued" : ""), text: job.pending ? "Waiting to save" : "" });
  const editable = Boolean(job.id);
  const row = el("tr", { class: "job" + (["Rejected", "Not Interested"].includes(job.status) ? " dimmed" : "") },
    el("td", {}, el("button", {
      class: "expand", type: "button", "aria-expanded": String(open), title: "Show why it matched",
      text: open ? "▼" : "▶",
      onclick: () => { open ? state.expanded.delete(job.id) : state.expanded.add(job.id); render(); },
    })),
    el("td", {}, el("span", { class: `pill ${scoreClass(Number(job.score) || 0)}`, text: String(job.score ?? "") })),
    el("td", { class: "title" }, title),
    el("td", { class: "company", text: job.company }),
    el("td", { class: "location", text: job.location }),
    el("td", { class: "salary", text: job.salary }),
    el("td", { text: job.source }),
    el("td", { class: "nowrap", text: job.date_posted }),
    el("td", {}, statusSelect(job, note, editable), note),
    el("td", {}, dateInput(job, note, editable)),
    el("td", {}, notesBox(job, note, editable)),
    docsCell(job),
    el("td", {}, el("span", { class: "tag", text: LISTS[job.sheet] || job.sheet })),
  );
  return row;
}

function statusSelect(job, note, editable) {
  const select = el("select", { "aria-label": `Status for ${job.title}` });
  for (const s of state.statuses) select.add(new Option(s, s));
  select.value = job.status || "New";
  select.disabled = !editable;
  select.addEventListener("change", () => {
    const changes = { status: select.value };
    if (select.value === "Applied" && !job.date_applied) {
      changes.date_applied = new Date().toLocaleDateString("en-CA");  // today, as YYYY-MM-DD
    }
    save(job, changes, note);
  });
  return select;
}

function dateInput(job, note, editable) {
  const input = el("input", { type: "date", "aria-label": `Date applied for ${job.title}` });
  input.value = /^\d{4}-\d{2}-\d{2}$/.test(job.date_applied || "") ? job.date_applied : "";
  input.disabled = !editable;
  input.addEventListener("change", () => save(job, { date_applied: input.value }, note));
  return input;
}

function notesBox(job, note, editable) {
  const box = el("textarea", { rows: "1", "aria-label": `Notes for ${job.title}` });
  box.value = job.notes || "";
  box.disabled = !editable;
  box.addEventListener("change", () => save(job, { notes: box.value }, note));  // saves when you leave the box
  return box;
}

function detailRow(job) {
  const section = (heading, ...content) => el("div", {}, el("h3", { text: heading }), ...content);
  const list = (text) => {
    const items = String(text || "").split(";").map((s) => s.trim()).filter(Boolean);
    if (!items.length) return el("p", { class: "muted", text: "None" });
    const ul = el("ul");
    for (const item of items) ul.append(el("li", { text: item }));
    return ul;
  };
  const chips = el("div", { class: "chips" });
  for (const skill of String(job.missing || "").split(",").map((s) => s.trim()).filter(Boolean)) {
    chips.append(el("span", { text: skill }));
  }
  if (!chips.childElementCount) chips.append(el("p", { class: "muted", text: "None listed" }));
  const parts = el("div", { class: "parts" });
  for (const [name, value] of job.subscores || []) {
    parts.append(el("span", { text: name }), el("strong", { text: value == null ? "–" : String(value) }));
  }
  const grid = el("div", { class: "detail-grid" },
    section("Why it matched", el("p", { text: job.why || "No review yet." })),
    section("Missing skills", chips),
    section("Red flags", list(job.red_flags)),
    section("LLM score parts", parts),
  );
  if (job.location_check) grid.append(section("Location check", el("p", { text: job.location_check })));
  const docs = state.docs[job.id];
  if (docs && docs.files && docs.files.length) {
    const facts = docs.notes.length ? el("ul") : el("p", { class: "muted", text: "Nothing needed correcting." });
    for (const n of docs.notes) facts.append(el("li", { text: n }));
    grid.append(section(`Tailored documents (${docs.generated_at.replace("T", " ")}, ${docs.model || ""})`,
      el("p", { class: "muted", text: "Fact check against your resume:" }), facts,
      docs.pdf_note ? el("p", { class: "muted", text: docs.pdf_note }) : null));
  }
  grid.append(section("Last seen in a scrape", el("p", { text: job.last_seen || "–" })));
  return el("tr", { class: "details" }, el("td", { colspan: "13" }, grid));
}

// ------------------------------------------------------------------ tailored documents

function docsCell(job) {
  const info = state.docs[job.id] || { state: "none", files: [], notes: [] };
  const busy = info.state === "queued" || info.state === "running";
  const has = info.files && info.files.length > 0;
  const cell = el("td", { class: "docs" });
  const button = el("button", {
    type: "button", class: "small",
    text: busy ? "Writing…" : has ? "Regenerate" : "Generate Documents",
    title: "Write a resume and cover letter tailored to this job (local AI, about 1-2 minutes)",
    onclick: () => generateDocs(job, info),
  });
  button.disabled = busy || !job.id;
  cell.append(button);
  if (busy) cell.append(el("span", { class: "doc-step", text: info.step || "Working…" }));
  if (info.state === "failed") cell.append(el("span", { class: "save-note error", text: info.error }));
  if (has && !busy) {
    for (const kind of ["Resume", "Cover letter"]) {
      const files = info.files.filter((f) => f.kind === kind);
      if (!files.length) continue;
      const line = el("div", { class: "doc-links" }, el("span", { text: `${kind}: ` }));
      files.forEach((f, i) => {
        if (i) line.append(" · ");
        line.append(el("a", { href: f.url, download: f.name, text: f.format }));
      });
      cell.append(line);
    }
    if (info.notes.length) {
      cell.append(el("button", {
        type: "button", class: "link doc-notes",
        text: `${info.notes.length} fact-check note${info.notes.length === 1 ? "" : "s"}`,
        onclick: () => { state.expanded.add(job.id); render(); },
      }));
    }
  }
  return cell;
}

async function generateDocs(job, info) {
  if (info.files && info.files.length &&
      !confirm(`This replaces the documents made on ${info.generated_at.replace("T", " ")}. ` +
               "If you edited those files, save copies first. Continue?")) return;
  try {
    state.docs[job.id] = await api(`/api/documents/${encodeURIComponent(job.id)}`, { method: "POST", body: "{}" });
  } catch (err) {
    state.docs[job.id] = { state: "failed", error: err.message, files: [], notes: [] };
  }
  render();
}

// ------------------------------------------------------------------ saving

async function save(job, changes, note) {
  note.className = "save-note";
  note.textContent = "Saving…";
  try {
    const data = await api(`/api/jobs/${encodeURIComponent(job.id)}`, {
      method: "POST", body: JSON.stringify(changes),
    });
    Object.assign(job, changes);
    note.textContent = data.saved ? "Saved ✓" : "Waiting to save";
    note.className = "save-note" + (data.saved ? "" : " queued");
    job.pending = !data.saved;
    if (data.saved) state.version = data.version;  // our own write: no need to reload
    showStatus(data);
    if (changes.date_applied !== undefined || changes.status !== undefined) {
      if (!editing()) render(); else state.reloadWhenIdle = true;
    }
  } catch (err) {
    note.className = "save-note error";
    note.textContent = err.message;
  }
}

// ------------------------------------------------------------------ charts (plain SVG)

const SVG = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

function renderStatusChart(jobs) {
  const svg = $("chart-status");
  svg.replaceChildren();
  const counts = state.statuses.map((s) => [s, jobs.filter((j) => (j.status || "New") === s).length]);
  const max = Math.max(1, ...counts.map(([, n]) => n));
  const width = svg.clientWidth || 400, rowH = 180 / counts.length, labelW = 100;
  counts.forEach(([status, n], i) => {
    const y = i * rowH;
    const w = Math.max(n ? 2 : 0, (width - labelW - 40) * (n / max));
    svg.append(svgEl("text", { x: 0, y: y + rowH * 0.65 }, status));
    svg.append(svgEl("rect", { x: labelW, y: y + rowH * 0.18, width: w, height: rowH * 0.64, rx: 3,
                               fill: "var(--accent)", opacity: status === "New" ? 0.45 : 0.9 }));
    svg.append(svgEl("text", { x: labelW + w + 6, y: y + rowH * 0.65, class: "value" }, String(n)));
  });
}

function renderScoreChart(jobs) {
  const svg = $("chart-scores");
  svg.replaceChildren();
  const bins = Array.from({ length: 10 }, (_, i) => ({ from: i * 10, n: 0 }));
  for (const j of jobs) bins[Math.min(9, Math.floor((Number(j.score) || 0) / 10))].n += 1;
  const max = Math.max(1, ...bins.map((b) => b.n));
  const width = svg.clientWidth || 400, plotH = 150, barW = width / bins.length;
  bins.forEach((b, i) => {
    const h = plotH * (b.n / max);
    const color = b.from >= 65 ? "var(--good-text)" : b.from >= 50 ? "var(--mid-text)" : "var(--bad-text)";
    svg.append(svgEl("rect", { x: i * barW + 3, y: plotH - h + 12, width: barW - 6, height: h, rx: 2,
                               fill: color, opacity: 0.75 }));
    if (b.n) svg.append(svgEl("text", { x: i * barW + barW / 2, y: plotH - h + 8, "text-anchor": "middle",
                                        class: "value" }, String(b.n)));
    svg.append(svgEl("text", { x: i * barW + barW / 2, y: 176, "text-anchor": "middle" }, `${b.from}s`));
  });
}

window.addEventListener("resize", () => render());

// ------------------------------------------------------------------ refresh

$("refresh-btn").addEventListener("click", async () => {
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
  } catch (err) {
    if (!/already running/.test(err.message)) { alert(err.message); return; }
  }
  streamRefresh();
});
$("refresh-close").addEventListener("click", () => { $("refresh-panel").hidden = true; });

function streamRefresh() {
  state.streaming = true;
  $("refresh-panel").hidden = false;
  $("refresh-title").textContent = "Refreshing jobs… (you can keep working)";
  $("refresh-log").textContent = "";
  $("refresh-progress").textContent = "";
  $("refresh-btn").disabled = true;
  const source = new EventSource("/api/refresh/stream?from=0");
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    if (event.kind === "progress") {
      $("refresh-progress").textContent = event.text;
    } else if (event.kind === "line") {
      $("refresh-log").textContent += event.text + "\n";
      $("refresh-log").scrollTop = $("refresh-log").scrollHeight;
    } else if (event.kind === "done") {
      source.close();
      state.streaming = false;
      $("refresh-btn").disabled = false;
      $("refresh-progress").textContent = "";
      $("refresh-title").textContent = event.text === "0" ? "Refresh finished." : "Refresh stopped. See the message above.";
      load();
    }
  };
  source.onerror = () => {
    source.close();
    state.streaming = false;
    $("refresh-btn").disabled = false;
  };
}

// ------------------------------------------------------------------ start

load().catch((err) => {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
});
setInterval(poll, 3000);
