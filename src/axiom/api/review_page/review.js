// Axiom memo review page. Plain JavaScript, no build step, no third-party code.
// Every value from the API is inserted as text (textContent / text nodes), never as HTML.
"use strict";

const KEY_STORE = "axiom.reviewKey";
const POLL_MS = 2500;

const STATUS = {
  queued: { label: "Queued", tone: "", text: "Waiting for the platform to start." },
  running: { label: "Drafting", tone: "", text: "The platform is drafting the memo. This page updates by itself." },
  awaiting_review: { label: "Needs review", tone: "accent", text: "The draft is ready and waiting for a credit officer's decision." },
  revising: { label: "Revising", tone: "", text: "The platform is redrafting with the credit officer's comments." },
  finalizing: { label: "Finalizing", tone: "", text: "Recording the credit officer's decision." },
  approved: { label: "Memo approved", tone: "good", text: "A credit officer approved this memo and the recommendation in it." },
  rejected: { label: "Memo rejected", tone: "bad", text: "A credit officer rejected this memo. It will not be used." },
  failed: { label: "Failed", tone: "bad", text: "The platform could not finish this memo. Nothing was decided." },
};
const ACTIVE = new Set(["queued", "running", "revising", "finalizing"]);
const TABS = [
  { id: "review", label: "Needs review", statuses: ["awaiting_review"] },
  { id: "progress", label: "In progress", statuses: ["queued", "running", "revising", "finalizing"] },
  { id: "done", label: "Done", statuses: ["approved", "rejected", "failed"] },
  { id: "all", label: "All", statuses: null },
];
const ROLE_LABEL = { submitter: "relationship manager", reviewer: "credit officer", auditor: "auditor" };
const RATIOS = [
  ["dscr", "Debt service coverage (DSCR)", "x"],
  ["pro_forma_dscr", "DSCR with the new loan", "x"],
  ["leverage", "Leverage (debt / EBITDA)", "x"],
  ["pro_forma_leverage", "Leverage with the new loan", "x"],
  ["interest_coverage", "Interest coverage", "x"],
  ["current_ratio", "Current ratio", "x"],
  ["debt_to_equity", "Debt to equity", "x"],
  ["ebitda_margin", "EBITDA margin", "%"],
  ["revenue_growth", "Revenue growth", "%"],
  ["proposed_annual_debt_service", "Annual debt service with the new loan", "$"],
];
const EVENT_LABEL = {
  job_submitted: "Memo requested",
  job_started: "Platform started drafting",
  model_call: "AI step",
  guardrail_checked: "Safety check",
  node_completed: "Step finished",
  node_failed: "Step failed",
  job_awaiting_review: "Paused for a credit officer",
  review_submitted: "Credit officer decided",
  job_resumed: "Platform resumed",
  job_approved: "Memo approved",
  job_rejected: "Memo rejected",
  job_failed: "Job failed",
};
const GUARDRAIL_LABEL = {
  pii_redaction: "Personal data in the documents",
  injection_screen: "Instructions aimed at the AI (local screen)",
  injection_shield: "Instructions aimed at the AI (cloud screen)",
  decision_language: "No credit decision stated as already made",
  investment_advice: "No investment advice",
  on_task: "Only memo content (no asides, links or echoed instructions)",
  number_provenance: "Every number traces to the documents or the ratio tools",
  citation_support: "Figures cite their source lines",
  pii_output: "No personal data in the draft",
  content_safety: "Harmful content in the draft",
};
const GUARDRAIL_OUTCOME = {
  passed: { label: "Passed", tone: "good" },
  triggered: { label: "Found a problem", tone: "bad" },
  not_evaluated: { label: "Not checked", tone: "warn" },
};
const ERROR_TEXT = {
  document_quarantined:
    "A borrower document contains text aimed at the AI, such as an instruction to ignore its rules, so no AI read the package. An auditor can see which document and line.",
  guardrail_failed:
    "The draft still failed a safety check after one automatic correction, so it was not sent for review. An auditor can see which check.",
  no_permitted_model: "Personal data could not be masked, and no model is allowed to see it unmasked here.",
  invalid_package: "The package could not be read, or loan terms are missing.",
};
const STEP_LABEL = {
  intake: "reading the documents",
  analyst: "financial analysis",
  drafter: "drafting",
  compliance: "compliance review",
  finalize: "finalizing",
};

const state = {
  key: null,
  me: null,
  packages: new Map(),
  jobs: [],
  tab: "all",
  selectedId: null,
  job: null,
  evidence: null,
  evidenceError: null,
  pending: null, // { decision, idem }
  decisionError: null,
  comments: "",
  busy: false,
  dashboard: null,
  dashboardError: null,
  listLoaded: false,
  lookups: new Map(), // "Explain this" answers for the selected memo
};

// Helpers ---------------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function storage(action, value) {
  try {
    if (action === "get") return window.sessionStorage.getItem(KEY_STORE);
    if (action === "set") window.sessionStorage.setItem(KEY_STORE, value);
    if (action === "clear") window.sessionStorage.removeItem(KEY_STORE);
  } catch (_) {
    /* storage can be unavailable; the key then lasts until reload */
  }
  return null;
}

function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `k-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

class ApiError extends Error {
  constructor(status, body) {
    super((body && body.title) || `Request failed (${status})`);
    this.status = status;
    this.detail = body && body.detail;
    this.type = (body && body.type) || "";
  }
}

async function api(method, path, body, idem) {
  const headers = { "X-API-Key": state.key, Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idem) headers["Idempotency-Key"] = idem;
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (_) {
    throw new ApiError(0, { title: "The platform can't be reached. Is it running?" });
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = null;
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

function explain(error) {
  return error.detail ? `${error.message}. ${error.detail}` : error.message;
}

function money(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function formatRatio(value, unit) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (unit === "%") return `${(n * 100).toFixed(1)}%`;
  if (unit === "$") return money(n);
  return `${n.toFixed(2)}×`;
}

function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function clock(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function borrower(packageId) {
  const p = state.packages.get(packageId);
  return p ? p.borrower_name : packageId;
}

// "approve_with_conditions" -> "approve with conditions"
function recommendation(value) {
  return String(value || "").replaceAll("_", " ");
}

function pill(status) {
  const s = STATUS[status] || { label: status, tone: "" };
  return el("span", { class: `pill ${s.tone}` }, s.label);
}

function severityPill(finding) {
  if (finding.outcome === "not_evaluated") return el("span", { class: "pill warn" }, "Check yourself");
  if (finding.outcome === "passed") return el("span", { class: "pill good" }, "Passed");
  const tone = finding.severity === "high" ? "bad" : "warn";
  const label = finding.severity === "high" ? "High" : finding.severity === "warning" ? "Warning" : "Note";
  return el("span", { class: `pill ${tone}` }, label);
}

// Sign in ---------------------------------------------------------------------------------

async function signIn(key) {
  state.key = key;
  const me = await api("GET", "/v1/me");
  state.me = me;
  storage("set", key);
  const packages = await api("GET", "/v1/packages");
  state.packages = new Map(packages.map((p) => [p.package_id, p]));
  state.tab = me.role === "reviewer" ? "review" : "all";
  showApp();
  await Promise.all([refreshList(), loadDashboard()]);
}

function signOut() {
  storage("clear");
  Object.assign(state, {
    key: null, me: null, jobs: [], selectedId: null, job: null, evidence: null,
    evidenceError: null, pending: null, decisionError: null, comments: "",
    dashboard: null, dashboardError: null, listLoaded: false,
  });
  $("app").hidden = true;
  $("who").hidden = true;
  $("sign-in").hidden = false;
  $("key-input").value = "";
  $("key-input").focus();
}

function showApp() {
  $("sign-in").hidden = true;
  $("app").hidden = false;
  $("who").hidden = false;
  $("who-label").textContent = `${state.me.name} · ${ROLE_LABEL[state.me.role] || state.me.role}`;
  const request = $("request");
  request.hidden = state.me.role !== "submitter";
  if (!request.hidden) {
    const select = $("package-select");
    select.replaceChildren(
      ...[...state.packages.values()].map((p) =>
        el("option", { value: p.package_id }, `${p.borrower_name} (${p.package_id})`),
      ),
    );
  }
  renderTabs();
  renderDetail();
}

// The list --------------------------------------------------------------------------------

function renderTabs() {
  const tabs = $("tabs");
  tabs.replaceChildren(
    ...TABS.map((t) => {
      const count = t.statuses ? state.jobs.filter((j) => t.statuses.includes(j.status)).length : state.jobs.length;
      return el(
        "button",
        {
          class: "tab",
          type: "button",
          role: "tab",
          "aria-selected": t.id === state.tab ? "true" : "false",
          onclick: () => {
            state.tab = t.id;
            renderTabs();
            renderList();
          },
        },
        `${t.label} (${count})`,
      );
    }),
  );
}

function renderList() {
  const tab = TABS.find((t) => t.id === state.tab);
  const jobs = state.jobs.filter((j) => !tab.statuses || tab.statuses.includes(j.status));
  $("job-list").replaceChildren(
    ...jobs.map((j) =>
      el(
        "li",
        {},
        el(
          "button",
          {
            class: "job",
            type: "button",
            "aria-current": j.job_id === state.selectedId ? "true" : "false",
            onclick: () => select(j.job_id),
          },
          el("span", { class: "job-title" }, borrower(j.package_id)),
          el(
            "span",
            { class: "job-meta" },
            pill(j.status),
            j.draft_recommendation ? `Draft: ${recommendation(j.draft_recommendation)}` : null,
            el("span", {}, when(j.updated_at)),
          ),
        ),
      ),
    ),
  );
  const empty = $("job-list-empty");
  empty.hidden = jobs.length > 0;
  empty.textContent =
    state.me && state.me.role === "submitter" && state.jobs.length === 0
      ? "No memos yet. Request one above."
      : state.tab === "review"
        ? "Nothing is waiting for review."
        : "No memos here.";
}

async function refreshList() {
  try {
    const data = await api("GET", "/v1/memos?limit=200");
    const changed = !state.listLoaded || JSON.stringify(data.items) !== JSON.stringify(state.jobs);
    state.jobs = data.items;
    state.listLoaded = true;
    if (changed) {
      renderTabs();
      renderList();
    }
  } catch (error) {
    if (error.status === 401) return signOut();
    return;
  }
  const row = state.jobs.find((j) => j.job_id === state.selectedId);
  if (row && state.job && (row.status !== state.job.status || row.updated_at !== state.job.updated_at)) {
    await loadSelected();
  }
}

async function select(jobId) {
  state.selectedId = jobId;
  state.job = null;
  state.evidence = null;
  state.evidenceError = null;
  state.pending = null;
  state.decisionError = null;
  state.comments = "";
  state.lookups = new Map();
  renderList();
  renderDetail();
  if (window.matchMedia("(max-width: 860px)").matches) $("detail").scrollIntoView({ block: "start" });
  await loadSelected();
}

async function loadSelected() {
  if (!state.selectedId) return;
  try {
    const job = await api("GET", `/v1/memos/${encodeURIComponent(state.selectedId)}`);
    if (job.job_id !== state.selectedId) return;
    const changed = !state.job || state.job.status !== job.status;
    state.job = job;
    if (changed) state.evidence = null;
  } catch (error) {
    state.job = { error: explain(error) };
  }
  renderDetail();
}

async function requestMemo() {
  const button = $("request-button");
  const errorBox = $("request-error");
  errorBox.hidden = true;
  button.disabled = true;
  try {
    const packageId = $("package-select").value;
    const accepted = await api("POST", "/v1/memos", { package_id: packageId }, newKey());
    await refreshList();
    state.tab = "all";
    renderTabs();
    await select(accepted.job_id);
  } catch (error) {
    errorBox.textContent = explain(error);
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
  }
}

// The memo --------------------------------------------------------------------------------

function renderDetail() {
  const detail = $("detail");
  $("overview").setAttribute("aria-current", state.selectedId ? "false" : "true");
  if (!state.selectedId) {
    detail.replaceChildren(...renderDashboard());
    return;
  }
  const job = state.job;
  if (!job) {
    detail.replaceChildren(el("div", { class: "card placeholder" }, el("span", { class: "spinner" }), "Loading…"));
    return;
  }
  if (job.error && !job.job_id) {
    detail.replaceChildren(el("div", { class: "notice bad" }, job.error));
    return;
  }
  const status = STATUS[job.status] || { text: "" };
  const parts = [
    el(
      "div",
      { class: "card" },
      el("div", { class: "detail-head" }, el("h1", {}, borrower(job.package_id)), pill(job.status)),
      el("p", { class: "sub" }, `${job.package_id} · job ${job.job_id} · updated ${when(job.updated_at)}`),
      el(
        "p",
        { class: "status-text" },
        ACTIVE.has(job.status) ? el("span", { class: "spinner" }) : null,
        status.text,
        job.error ? ` ${ERROR_TEXT[job.error.code] || ""} Error code: ${job.error.code}.` : "",
      ),
    ),
  ];
  const memo = job.memo;
  if (memo) {
    parts.push(renderSummary(job, memo));
    if (state.me.role === "reviewer" && job.status === "awaiting_review") parts.push(renderDecision(job));
    if (state.me.role === "submitter" && job.status === "awaiting_review") {
      parts.push(el("div", { class: "notice" }, "A credit officer will review this draft. You can't decide on a memo you requested."));
    }
    parts.push(renderFindings(memo));
    parts.push(renderGuardrails(job, memo));
    parts.push(renderMemo(memo));
    parts.push(renderRatios(memo));
    parts.push(renderChecks(memo));
  }
  if (job.reviews && job.reviews.length) parts.push(renderReviews(job));
  if (state.me.role === "auditor") parts.push(renderAudit(job));
  detail.replaceChildren(...parts);
}

function renderSummary(job, memo) {
  const findings = memo.findings || [];
  const flags = memo.risk_flags || [];
  const highFlags = flags.filter((f) => f.severity === "high").length;
  // High-severity risk flags need the officer's attention as much as a triggered rule does.
  const attention = findings.filter((f) => f.outcome === "triggered").length + highFlags;
  const unchecked = findings.filter((f) => f.outcome === "not_evaluated").length;
  const passed = findings.filter((f) => f.outcome === "passed").length;
  const loan = memo.loan_request || {};
  const v = (k) => (loan[k] ? loan[k].value : null);
  const loanText = [
    v("requested_amount") && money(v("requested_amount")),
    v("interest_rate_pct") && `${v("interest_rate_pct")}% interest`,
    v("term_years") && `${v("term_years")}-year term`,
    v("amortization_years") && `${v("amortization_years")}-year amortization`,
  ]
    .filter(Boolean)
    .join(" · ");
  return el(
    "div",
    { class: "card summary" },
    el(
      "div",
      {},
      el("h2", {}, job.status === "approved" ? "Recommendation, approved by a credit officer" : "Draft recommendation"),
      el("p", { class: "recommendation" }, recommendation(memo.draft_recommendation)),
      loanText ? el("p", { class: "loan" }, `Request: ${loanText}`) : null,
      el("p", { class: "disclaimer" }, memo.disclaimer),
    ),
    el(
      "div",
      { class: "counts", "aria-label": "Findings summary" },
      el("div", { class: "count" }, el("b", {}, String(attention)), el("span", { class: `pill ${attention ? "bad" : "good"}` }, "need attention")),
      el("div", { class: "count" }, el("b", {}, String(unchecked)), el("span", { class: `pill ${unchecked ? "warn" : ""}` }, "to check yourself")),
      el("div", { class: "count" }, el("b", {}, String(passed)), el("span", { class: "pill good" }, "checks passed")),
      el("div", { class: "count" }, el("b", {}, String(flags.length)), el("span", { class: `pill ${highFlags ? "bad" : flags.length ? "warn" : ""}` }, flags.length === 1 ? "risk flag" : "risk flags")),
    ),
  );
}

function renderDecision(job) {
  const left = Math.max(0, 2 - job.revisions.human);
  const textarea = el("textarea", {
    id: "comments",
    maxlength: "2000",
    placeholder: "What should change, or a note for the record",
    oninput: (e) => {
      state.comments = e.target.value;
    },
  });
  textarea.value = state.comments;
  const choose = (decision) => () => {
    state.decisionError = null;
    state.comments = $("comments").value; // read the box itself, not only what input events saw
    if (decision === "revise" && !state.comments.trim()) {
      state.decisionError = "Say what should change before asking for changes.";
      renderDetail();
      $("comments").focus();
      return;
    }
    state.pending = { decision, idem: newKey() };
    renderDetail();
  };
  const rec = recommendation(job.memo.draft_recommendation);
  const verbs = {
    approve: `Approve this memo and its recommendation to ${rec}?`,
    revise: "Send it back for changes?",
    reject: "Reject this memo? It will not be used.",
  };
  const confirm = state.pending
    ? el(
        "div",
        { class: "confirm", role: "alertdialog", "aria-label": "Confirm decision" },
        el("p", {}, verbs[state.pending.decision]),
        el("button", { class: "primary", type: "button", disabled: state.busy, onclick: submitDecision }, "Confirm"),
        el("button", { class: "secondary", type: "button", disabled: state.busy, onclick: () => { state.pending = null; renderDetail(); } }, "Cancel"),
      )
    : null;
  return el(
    "section",
    { class: "card decide" },
    el("h2", {}, "Your decision"),
    el(
      "p",
      { class: "muted" },
      "Read the findings and the draft below, then decide. Approving accepts the memo and its recommendation; rejecting discards the memo. You are accountable for this decision. ",
      left > 0 ? `You can ask for changes ${left} more time${left === 1 ? "" : "s"}.` : "No more revisions are allowed: approve or reject.",
    ),
    el("label", { for: "comments" }, "Comments ", el("span", { class: "muted" }, "(required when asking for changes)")),
    textarea,
    el(
      "div",
      { class: "buttons" },
      el("button", { class: "primary", type: "button", disabled: !!state.pending, onclick: choose("approve") }, "Approve"),
      el("button", { class: "secondary", type: "button", disabled: !!state.pending || left === 0, onclick: choose("revise") }, "Ask for changes"),
      el("button", { class: "danger", type: "button", disabled: !!state.pending, onclick: choose("reject") }, "Reject"),
    ),
    confirm,
    state.decisionError ? el("p", { class: "error", role: "alert" }, state.decisionError) : null,
  );
}

async function submitDecision() {
  if (!state.pending || state.busy) return;
  state.busy = true;
  renderDetail();
  try {
    await api(
      "POST",
      `/v1/memos/${encodeURIComponent(state.selectedId)}/review`,
      { decision: state.pending.decision, comments: state.comments.trim() },
      state.pending.idem,
    );
    state.pending = null;
    state.comments = "";
    state.decisionError = null;
  } catch (error) {
    state.pending = null;
    state.decisionError = explain(error);
  } finally {
    state.busy = false;
  }
  await loadSelected();
  await refreshList();
}

function findingItem(f) {
  const li = el("li", { class: "finding" });
  li.append(
    severityPill(f),
    el(
      "div",
      {},
      el("div", {}, f.message),
      el(
        "div",
        { class: "finding-ref" },
        `${f.rule_id} · ${f.policy_ref} `,
        whyButton(li, `finding|${f.rule_id}`, () => findingWhy(li, f), { class: "why-link" }, "Explain this"),
      ),
    ),
  );
  return li;
}

// Explain this ----------------------------------------------------------------------------
// Click a citation, ratio, flag, finding or safety check to see where it comes from. Every
// answer is looked up, never generated: a source line, a formula with its inputs, or a
// quoted policy clause.

const GUARDRAIL_DETAIL = {
  pii_redaction: "Before any AI reads the package, personal identifiers such as names, account and phone numbers are masked. Financial figures and table rows are never masked.",
  injection_screen: "Each document is scanned for text aimed at the AI, such as “ignore previous instructions”. A document that contains it is quarantined and no AI reads the package.",
  injection_shield: "A cloud service's screen for text aimed at the AI. It runs only when that service is configured.",
  decision_language: "The draft must not state a credit decision as already made. Only a credit officer decides.",
  investment_advice: "The draft must not give investment advice.",
  on_task: "The draft may contain only memo content: no asides to the reader, no links, and no instructions repeated from the documents.",
  number_provenance: "Every number in the draft must match an extracted figure, a ratio the tools computed, a policy threshold or a number in the documents.",
  citation_support: "A claim that states an extracted figure must cite that figure's own source line.",
  pii_output: "The draft must not contain personal identifiers. The borrower's own figures are not identifiers.",
  content_safety: "A cloud service's check for harmful content. It runs only when that service is configured.",
};
const FINGERPRINT = {
  matches: { label: "Matches the fingerprint recorded during the job", tone: "good" },
  differs: { label: "Differs from the fingerprint recorded during the job", tone: "warn" },
  not_recorded: { label: "No fingerprint was recorded", tone: "warn" },
};
const CP_REF = /^CP-\d+(\.\d+)?$/;

// "pkg-04-income_statement" -> "income statement"
function docLabel(docId) {
  return String(docId).replace(/^pkg-\d+-/, "").replaceAll("_", " ");
}

function citeLabel(c) {
  return /^line \d+$/.test(c.locator) ? `${docLabel(c.doc_id)}, ${c.locator}` : `${docLabel(c.doc_id)}, retrieved passage`;
}

async function lookup(key, path) {
  if (!state.lookups.has(key)) {
    state.lookups.set(
      key,
      api("GET", path).catch((error) => ({ error: error.status === 404 ? null : explain(error) })),
    );
  }
  return state.lookups.get(key);
}

function sourceLookup(c) {
  const q = new URLSearchParams({ doc_id: c.doc_id, locator: c.locator });
  return lookup(`src|${c.doc_id}|${c.locator}`, `/v1/memos/${encodeURIComponent(state.selectedId)}/source?${q}`);
}

function policyLookup(ref) {
  return lookup(`cp|${ref}`, `/v1/policy/${encodeURIComponent(ref)}`);
}

// Opens or closes an explanation inside `host`. `build` returns nodes, or a promise of them.
function toggleWhy(host, key, build) {
  const open = host.querySelector(`:scope > .why[data-key="${CSS.escape(key)}"]`);
  host.querySelectorAll(":scope > .why").forEach((n) => n.remove());
  host.querySelectorAll(":scope .why-toggle[aria-expanded='true']").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (open) return false;
  const panel = el("div", { class: "why", "data-key": key }, el("span", { class: "spinner" }), "Looking it up…");
  host.append(panel);
  Promise.resolve(build()).then(
    (nodes) => panel.replaceChildren(...[nodes].flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false)),
    () => panel.replaceChildren("Could not look this up."),
  );
  return true;
}

function whyButton(host, key, build, attrs, ...children) {
  const button = el(
    "button",
    {
      ...attrs,
      class: `why-toggle ${(attrs && attrs.class) || ""}`.trim(),
      type: "button",
      "aria-expanded": "false",
      onclick: (event) => {
        event.stopPropagation();
        const target = typeof host === "function" ? host() : host;
        const opened = toggleWhy(target, key, build);
        button.setAttribute("aria-expanded", opened ? "true" : "false");
      },
    },
    ...children,
  );
  return button;
}

function sourceNodes(x, c) {
  if (!x || x.error === null) return [el("p", {}, `This citation (${citeLabel(c)}) does not resolve in this memo's package.`)];
  if (x.error) return [el("p", { class: "error" }, x.error)];
  const fp = FINGERPRINT[x.fingerprint] || { label: x.fingerprint, tone: "" };
  return [
    el("div", { class: "why-title" }, x.kind === "line" ? `${x.title}, line ${x.line}` : `${x.title}, passage the platform retrieved`),
    x.header ? el("pre", { class: "why-source header" }, x.header) : null,
    el("pre", { class: "why-source" }, x.text),
    el("div", { class: "why-meta" }, el("span", { class: `pill ${fp.tone}` }, fp.label), x.note ? el("span", {}, x.note) : null),
  ];
}

function sourceWhy(c) {
  return sourceLookup(c).then((x) => sourceNodes(x, c));
}

// A list of citation buttons; each opens its excerpt inside `host`.
function citeButtons(host, citations) {
  const seen = new Set();
  return citations
    .filter((c) => {
      const k = `${c.doc_id}|${c.locator}`;
      return seen.has(k) ? false : seen.add(k);
    })
    .map((c) =>
      whyButton(host, `src|${c.doc_id}|${c.locator}`, () => sourceWhy(c), { class: "cite", title: "Show the source text" }, citeLabel(c)),
    );
}

async function policyNodes(refText, intro) {
  // "CP-4.1, CP-6.3" is two clauses; "12 CFR 1002.4(a), 1002.6(b)" is one outside reference.
  const parts = String(refText || "").split(/,\s*/).filter(Boolean);
  const refs = parts.every((p) => CP_REF.test(p)) ? parts : [String(refText || "")].filter(Boolean);
  const nodes = intro ? [el("p", {}, intro)] : [];
  const outside = [];
  for (const ref of refs) {
    if (!CP_REF.test(ref)) {
      outside.push(ref);
      continue;
    }
    const clause = await policyLookup(ref);
    if (!clause || clause.error !== undefined) {
      outside.push(ref);
      continue;
    }
    nodes.push(
      el("div", { class: "why-title" }, `Credit policy ${clause.ref} · ${clause.section} (version ${clause.policy_version})`),
      el("blockquote", { class: "why-quote" }, clause.text),
    );
    if (clause.clauses && clause.clauses.length) {
      for (const sub of clause.clauses) {
        const c = await policyLookup(sub);
        if (c && c.text) nodes.push(el("blockquote", { class: "why-quote" }, `${c.ref} ${c.text}`));
      }
    }
  }
  if (outside.length) {
    nodes.push(el("p", { class: "muted" }, `${outside.join("; ")}: outside the bank's credit policy. The passages the finding cites are what it rests on.`));
  }
  return nodes;
}

function flagWhy(f) {
  return policyNodes(f.policy_ref, null);
}

function findingWhy(host, f) {
  return policyNodes(f.policy_ref, null).then((nodes) => {
    if (f.citations && f.citations.length) {
      const list = el("div", { class: "why-cites" }, el("span", { class: "muted" }, "Cited: "));
      const box = el("div", {});
      list.append(...citeButtons(box, f.citations));
      nodes.push(list, box);
    }
    if (!nodes.length) nodes.push(el("p", {}, "No policy clause or passage is cited for this finding."));
    return nodes;
  });
}

function guardrailWhy(g) {
  return [
    el("p", {}, GUARDRAIL_DETAIL[g.control] || "No description is available for this check."),
    el("p", { class: "muted" }, `Ran on: ${g.stage === "input" ? "the borrower's documents" : "the draft"}. Result: ${g.reason || g.outcome}.`),
  ];
}

// The figure an input name refers to: an extracted figure for that year, the loan request,
// or another ratio the tools computed.
function inputSource(memo, name, year) {
  const fig = (memo.figures[String(year)] || {})[name] || (memo.loan_request || {})[name];
  if (fig) return { citation: { doc_id: fig.source.doc_id, locator: `line ${fig.source.line}` } };
  const ratio = (memo.ratios || []).find((r) => r.name === name && r.year === year);
  if (ratio) return { ratio };
  return {};
}

function ratioWhy(memo, r, label, unit) {
  const box = el("div", {});
  const rows = Object.entries(r.inputs || {}).map(([name, value]) => {
    const src = inputSource(memo, name, r.year);
    const cell = el("td", {});
    if (src.citation) cell.append(...citeButtons(box, [src.citation]));
    else if (src.ratio) {
      const known = RATIOS.find(([n]) => n === name);
      cell.append(`computed by the tools: ${known ? known[1].toLowerCase() : name.replaceAll("_", " ")}, formula `, el("code", {}, src.ratio.formula));
    }
    else cell.append(el("span", { class: "muted" }, "policy or loan terms"));
    return el("tr", {}, el("td", {}, name.replaceAll("_", " ")), el("td", {}, Number(value).toLocaleString("en-US")), cell);
  });
  return [
    el("div", { class: "why-title" }, `${label}, ${r.year}: ${formatRatio(r.value, unit) ?? "n/a"}`),
    el("p", {}, "Formula: ", el("code", {}, r.formula)),
    r.note ? el("p", { class: "muted" }, r.note) : null,
    rows.length
      ? el("div", { class: "table-wrap" }, el("table", { class: "why-inputs" }, el("thead", {}, el("tr", {}, el("th", {}, "Input"), el("th", {}, "Value"), el("th", {}, "Source"))), el("tbody", {}, rows)))
      : null,
    box,
    el("p", { class: "muted" }, "Computed by tested code, not by the AI. Rounded half-up to two decimal places."),
  ];
}

function renderFindings(memo) {
  const findings = memo.findings || [];
  const flags = memo.risk_flags || [];
  const triggered = findings.filter((f) => f.outcome === "triggered");
  const unchecked = findings.filter((f) => f.outcome === "not_evaluated");
  const passed = findings.filter((f) => f.outcome === "passed");
  const card = el("section", { class: "card" }, el("h2", {}, "Findings"));
  if (flags.length) {
    card.append(
      el("h3", { class: "group-title" }, "Risk flags"),
      el(
        "ul",
        { class: "findings" },
        flags.map((f) => {
          const li = el("li", { class: "finding" });
          li.append(
            el("span", { class: `pill ${f.severity === "high" ? "bad" : "warn"}` }, f.severity),
            el(
              "div",
              {},
              el("div", {}, f.message),
              el(
                "div",
                { class: "finding-ref" },
                `${[f.code, f.year, f.policy_ref].filter(Boolean).join(" · ")} `,
                f.policy_ref ? whyButton(li, `flag|${f.code}|${f.year}`, () => flagWhy(f), { class: "why-link" }, "Explain this") : null,
              ),
            ),
          );
          return li;
        }),
      ),
    );
  }
  if (triggered.length) {
    card.append(el("h3", { class: "group-title" }, "Needs attention"), el("ul", { class: "findings" }, triggered.map(findingItem)));
  }
  if (unchecked.length) {
    card.append(
      el("h3", { class: "group-title" }, "The platform could not check these. Please confirm them yourself"),
      el("ul", { class: "findings" }, unchecked.map(findingItem)),
    );
  }
  if (!flags.length && !triggered.length && !unchecked.length) {
    card.append(el("p", { class: "muted" }, "No risk flags and nothing flagged by the checks."));
  }
  if (passed.length) {
    card.append(
      el(
        "details",
        { class: "passed" },
        el("summary", {}, `Show the ${passed.length} checks that passed`),
        el("ul", { class: "findings" }, passed.map(findingItem)),
      ),
    );
  }
  return card;
}

function renderMemo(memo) {
  return el(
    "section",
    { class: "card" },
    el("h2", {}, "Draft memo"),
    el("p", { class: "muted" }, "Each statement shows where its figures come from. Click a source to read the exact line."),
    (memo.sections || []).map((section) =>
      el(
        "div",
        { class: "memo-section" },
        el("h3", {}, section.heading),
        el(
          "ul",
          { class: "claims" },
          section.claims.map((claim) => {
            const li = el("li", {}, claim.text);
            li.append(...citeButtons(li, claim.citations));
            return li;
          }),
        ),
      ),
    ),
  );
}

function renderRatios(memo) {
  const ratios = memo.ratios || [];
  const slot = el("div", { class: "why-slot" });
  const years = [...new Set(ratios.map((r) => r.year))].sort();
  const byKey = new Map(ratios.map((r) => [`${r.name}|${r.year}`, r]));
  const rows = RATIOS.filter(([name]) => ratios.some((r) => r.name === name)).map(([name, label, unit]) =>
    el(
      "tr",
      {},
      el("td", {}, label),
      years.map((y) => {
        const r = byKey.get(`${name}|${y}`);
        if (!r) return el("td", { class: "na" }, "n/a");
        const text = formatRatio(r.value, unit);
        return el(
          "td",
          { class: text === null ? "na" : "" },
          whyButton(slot, `ratio|${name}|${y}`, () => ratioWhy(memo, r, label, unit), { class: "cell-button", title: "Show the formula and inputs" }, text === null ? "n/a" : text),
        );
      }),
    ),
  );
  return el(
    "section",
    { class: "card" },
    el("h2", {}, "Key figures"),
    el("p", { class: "muted" }, "Calculated by code from the financial statements, not by the AI. Click a value to see its formula and inputs."),
    el(
      "div",
      { class: "table-wrap" },
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Ratio"), years.map((y) => el("th", {}, String(y))))), el("tbody", {}, rows)),
    ),
    slot,
  );
}

function renderChecks(memo) {
  const checks = memo.reconciliation || [];
  const failed = checks.filter((c) => c.outcome !== "passed");
  return el(
    "section",
    { class: "card" },
    el("h2", {}, "Do the statements add up?"),
    failed.length
      ? el(
          "ul",
          { class: "findings" },
          failed.map((c) =>
            el(
              "li",
              { class: "finding" },
              el("span", { class: `pill ${c.outcome === "triggered" ? "bad" : "warn"}` }, c.outcome === "triggered" ? "Failed" : "Not checked"),
              el("div", {}, `${c.detail}${c.year ? ` (${c.year})` : ""}`),
            ),
          ),
        )
      : el("p", {}, `Yes. All ${checks.length} reconciliation checks passed.`),
  );
}

function renderReviews(job) {
  const word = { approve: "Approved", reject: "Rejected", revise: "Asked for changes" };
  return el(
    "section",
    { class: "card" },
    el("h2", {}, "Decisions so far"),
    el(
      "ul",
      { class: "findings" },
      job.reviews.map((r) =>
        el(
          "li",
          { class: "finding" },
          el("span", { class: "pill" }, `Round ${r.round}`),
          el("div", {}, el("div", {}, word[r.decision] || r.decision), r.comments ? el("div", { class: "finding-ref" }, `“${r.comments}”`) : null),
        ),
      ),
    ),
  );
}

// Audit trail (auditor only) ----------------------------------------------------------------

function renderGuardrails(job, memo) {
  const checks = memo.guardrails || [];
  const card = el("section", { class: "card" }, el("h2", {}, "Safety checks"));
  if (!checks.length) {
    card.append(el("p", { class: "muted" }, "No safety checks were recorded for this draft."));
    return card;
  }
  const skipped = checks.filter((g) => g.outcome === "not_evaluated").length;
  card.append(
    el(
      "p",
      { class: "muted" },
      "Automatic checks on the borrower's documents and on the draft. ",
      skipped
        ? `${skipped} could not run here, so they are marked “Not checked”, never “Passed”. `
        : "",
      job.revisions && job.revisions.automated
        ? `The platform corrected the draft automatically ${job.revisions.automated === 1 ? "once" : `${job.revisions.automated} times`} after a check failed; the results below are for the corrected draft.`
        : "",
    ),
    el(
      "ul",
      { class: "findings" },
      checks.map((g) => {
        // Masking personal data is the control doing its job, not a problem with the memo.
        const o = g.action === "redacted" ? { label: "Masked", tone: "good" } : GUARDRAIL_OUTCOME[g.outcome] || { label: g.outcome, tone: "" };
        const li = el("li", { class: "finding" });
        li.append(
          el("span", { class: `pill ${o.tone}` }, o.label),
          el(
            "div",
            {},
            el("div", {}, GUARDRAIL_LABEL[g.control] || g.control),
            el(
              "div",
              { class: "finding-ref" },
              `${g.outcome === "passed" ? g.control : `${g.control} · ${g.reason}`} `,
              whyButton(li, `guard|${g.control}|${g.stage}`, () => guardrailWhy(g), { class: "why-link" }, "What it checks"),
            ),
          ),
        );
        return li;
      }),
    ),
  );
  return card;
}

function renderAudit(job) {
  const card = el("section", { class: "card" }, el("h2", {}, "Audit trail"));
  if (state.evidenceError) card.append(el("p", { class: "error" }, state.evidenceError));
  if (!state.evidence) {
    card.append(
      el("p", { class: "muted" }, "Every step, who took it, and a check that nobody has edited the record since."),
      el("button", { class: "secondary", type: "button", onclick: loadEvidence }, "Show the audit trail"),
    );
    return card;
  }
  const e = state.evidence;
  const verified = e.chain && e.chain.verified;
  card.append(
    el(
      "p",
      {},
      el("span", { class: `pill ${verified ? "good" : "bad"}` }, verified ? "Record intact" : "Record broken"),
      " ",
      verified
        ? `All ${e.chain.events} entries link up; none were changed or removed.`
        : `The chain breaks at entry ${e.chain.first_broken_seq}.`,
    ),
    el("p", { class: "muted" }, `Requested by ${e.submitter}.`),
    el(
      "ol",
      { class: "timeline" },
      e.events.map((ev) => {
        const p = ev.payload || {};
        let label = EVENT_LABEL[ev.type] || ev.type;
        if (p.node) label += `: ${STEP_LABEL[p.node] || p.node}`;
        if (ev.type === "review_submitted") label += `: ${p.decision}${p.comments ? ` (“${p.comments}”)` : ""}`;
        if (ev.type === "model_call" && p.attempt > 1) label += ` (attempt ${p.attempt})`;
        if (ev.type === "guardrail_checked") {
          const o = GUARDRAIL_OUTCOME[p.outcome] || { label: p.outcome };
          const did = { redacted: "masked", quarantined: "quarantined, job stopped" }[p.action];
          label = `${EVENT_LABEL[ev.type]}: ${GUARDRAIL_LABEL[p.control] || p.control}, ${did || o.label.toLowerCase()}`;
        }
        return el("li", {}, el("time", { datetime: ev.at }, clock(ev.at)), el("span", {}, label), el("span", { class: "actor" }, ev.actor));
      }),
    ),
  );
  const g = e.related_guidance;
  if (g) {
    card.append(el("h3", { class: "group-title" }, "Related governance guidance (for information only)"));
    if (g.status !== "attached" || !g.provisions || !g.provisions.length) {
      card.append(el("p", { class: "muted" }, g.reason || "No related guidance was found."));
    } else {
      card.append(
        el(
          "ul",
          { class: "guidance" },
          g.provisions.map((p) => el("li", {}, el("div", {}, p.citation), el("div", { class: "url" }, p.source_url))),
        ),
      );
    }
  }
  return card;
}

async function loadEvidence() {
  state.evidenceError = null;
  try {
    state.evidence = await api("GET", `/v1/memos/${encodeURIComponent(state.selectedId)}/evidence`);
  } catch (error) {
    state.evidenceError = explain(error);
  }
  renderDetail();
}

// The dashboard ---------------------------------------------------------------------------

const DASHBOARD_TITLE = {
  submitter: "Your requests",
  reviewer: "Your review queue",
  auditor: "Audit overview",
};
const DASHBOARD_HINT = {
  submitter: "Request a memo, or pick one to follow its progress.",
  reviewer: "Memos with high-severity risk come first. Pick one to read the draft and decide.",
  auditor: "Pick a memo to read it and its audit trail.",
};

// What the overview shows, ignoring the clock, so polling re-renders only on a change.
// Re-rendering replaces the buttons, which would swallow a click in progress.
function boardSignature(board) {
  if (!board) return "";
  return JSON.stringify({
    tiles: board.tiles.map((t) => [t.key, t.unit === "seconds" ? duration(t.value) : t.value, t.tone]),
    sections: board.sections,
  });
}

async function loadDashboard() {
  const before = state.dashboardError || boardSignature(state.dashboard);
  try {
    state.dashboard = await api("GET", "/v1/dashboard");
    state.dashboardError = null;
  } catch (error) {
    if (error.status === 401) return signOut();
    state.dashboardError = explain(error);
  }
  const after = state.dashboardError || boardSignature(state.dashboard);
  if (!state.selectedId && after !== before) renderDetail();
}

async function showOverview() {
  state.selectedId = null;
  state.job = null;
  renderList();
  renderDetail();
  await loadDashboard();
}

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return "under a minute";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} days`;
}

function itemReason(item) {
  const error = item.status === "failed" && item.code ? ERROR_TEXT[item.code] : null;
  return error ? `${item.reason}. ${error}` : item.reason;
}

function renderDashboard() {
  const role = state.me ? state.me.role : null;
  const board = state.dashboard;
  const head = el(
    "div",
    { class: "card" },
    el("h1", {}, DASHBOARD_TITLE[role] || "Overview"),
    el("p", { class: "sub" }, DASHBOARD_HINT[role] || ""),
  );
  if (state.dashboardError) return [head, el("div", { class: "notice bad" }, state.dashboardError)];
  if (!board) return [head, el("div", { class: "card placeholder" }, el("span", { class: "spinner" }), "Loading…")];
  const tiles = el(
    "div",
    { class: "tiles", "aria-label": "Summary" },
    ...board.tiles.map((t) =>
      el(
        "div",
        { class: `tile ${t.tone}`, title: t.detail || null },
        el("b", {}, t.unit === "seconds" ? duration(t.value) : String(t.value ?? "–")),
        el("span", {}, t.label),
      ),
    ),
  );
  head.append(tiles);
  const scanned =
    board.jobs_considered >= board.scan_limit
      ? el("p", { class: "muted small" }, `Based on the ${board.scan_limit} most recently updated memos.`)
      : null;
  if (scanned) head.append(scanned);
  const sections = board.sections.map((s) =>
    el(
      "div",
      { class: "card" },
      el("h2", {}, s.title),
      s.items.length
        ? el(
            "ul",
            { class: "board-list" },
            ...s.items.map((item) =>
              el(
                "li",
                {},
                el(
                  "button",
                  { class: "board-item", type: "button", onclick: () => select(item.job_id) },
                  el("span", { class: "job-title" }, borrower(item.package_id)),
                  el(
                    "span",
                    { class: "job-meta" },
                    pill(item.status),
                    el("span", { class: item.tone ? `tone-${item.tone}` : "" }, itemReason(item)),
                    el("span", {}, when(item.updated_at)),
                  ),
                ),
              ),
            ),
          )
        : el("p", { class: "muted" }, s.empty),
    ),
  );
  return [head, ...sections];
}

// Start -----------------------------------------------------------------------------------

async function poll() {
  if (state.key && state.me && !state.busy && document.visibilityState === "visible") {
    await refreshList();
    if (state.job && ACTIVE.has(state.job.status)) await loadSelected();
    if (!state.selectedId) await loadDashboard();
  }
  window.setTimeout(poll, POLL_MS);
}

document.addEventListener("DOMContentLoaded", async () => {
  $("sign-in-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const errorBox = $("sign-in-error");
    errorBox.hidden = true;
    const key = $("key-input").value.trim();
    try {
      await signIn(key);
    } catch (error) {
      state.key = null;
      errorBox.textContent =
        error.status === 401
          ? "That key wasn't recognised. Use the part after the colon on one of the *_API_KEYS lines in .env, not your OpenAI or Azure key."
          : explain(error);
      errorBox.hidden = false;
    }
  });
  $("sign-out").addEventListener("click", signOut);
  $("request-button").addEventListener("click", requestMemo);
  $("overview").addEventListener("click", showOverview);

  const saved = storage("get");
  if (saved) {
    try {
      await signIn(saved);
    } catch (_) {
      signOut();
    }
  } else {
    $("sign-in").hidden = false;
  }
  window.setTimeout(poll, POLL_MS);
});
