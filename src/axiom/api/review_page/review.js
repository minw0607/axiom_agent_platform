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
  await refreshList();
}

function signOut() {
  storage("clear");
  Object.assign(state, {
    key: null, me: null, jobs: [], selectedId: null, job: null, evidence: null,
    evidenceError: null, pending: null, decisionError: null, comments: "",
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
    state.jobs = data.items;
  } catch (error) {
    if (error.status === 401) return signOut();
    return;
  }
  renderTabs();
  renderList();
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
  if (!state.selectedId) {
    const hint =
      state.me && state.me.role === "reviewer"
        ? "Pick a memo under “Needs review” to read the draft and decide."
        : state.me && state.me.role === "submitter"
          ? "Request a memo, or pick one from the list to follow its progress."
          : "Pick a memo to read it and its audit trail.";
    detail.replaceChildren(el("div", { class: "card placeholder" }, hint));
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
  return el(
    "li",
    { class: "finding" },
    severityPill(f),
    el(
      "div",
      {},
      el("div", {}, f.message),
      el("div", { class: "finding-ref" }, `${f.rule_id} · ${f.policy_ref}`),
    ),
  );
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
        flags.map((f) =>
          el(
            "li",
            { class: "finding" },
            el("span", { class: `pill ${f.severity === "high" ? "bad" : "warn"}` }, f.severity),
            el("div", {}, el("div", {}, f.message), el("div", { class: "finding-ref" }, [f.code, f.year, f.policy_ref].filter(Boolean).join(" · "))),
          ),
        ),
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
    el("p", { class: "muted" }, "Each statement shows where its figures come from in the borrower's documents."),
    (memo.sections || []).map((section) =>
      el(
        "div",
        { class: "memo-section" },
        el("h3", {}, section.heading),
        el(
          "ul",
          { class: "claims" },
          section.claims.map((claim) =>
            el(
              "li",
              {},
              claim.text,
              [...new Set(claim.citations.map((c) => `${c.doc_id}|${c.locator}`))].map((k) => {
                const [doc, locator] = k.split("|");
                return el("span", { class: "cite", title: doc }, locator);
              }),
            ),
          ),
        ),
      ),
    ),
  );
}

function renderRatios(memo) {
  const ratios = memo.ratios || [];
  const years = [...new Set(ratios.map((r) => r.year))].sort();
  const byKey = new Map(ratios.map((r) => [`${r.name}|${r.year}`, r]));
  const rows = RATIOS.filter(([name]) => ratios.some((r) => r.name === name)).map(([name, label, unit]) =>
    el(
      "tr",
      {},
      el("td", {}, label),
      years.map((y) => {
        const r = byKey.get(`${name}|${y}`);
        const text = r ? formatRatio(r.value, unit) : null;
        return text === null
          ? el("td", { class: "na", title: (r && r.note) || "" }, "n/a")
          : el("td", { title: r.formula }, text);
      }),
    ),
  );
  return el(
    "section",
    { class: "card" },
    el("h2", {}, "Key figures"),
    el("p", { class: "muted" }, "Calculated by code from the financial statements, not by the AI."),
    el(
      "div",
      { class: "table-wrap" },
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Ratio"), years.map((y) => el("th", {}, String(y))))), el("tbody", {}, rows)),
    ),
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
        return el(
          "li",
          { class: "finding" },
          el("span", { class: `pill ${o.tone}` }, o.label),
          el(
            "div",
            {},
            el("div", {}, GUARDRAIL_LABEL[g.control] || g.control),
            el("div", { class: "finding-ref" }, g.outcome === "passed" ? g.control : `${g.control} · ${g.reason}`),
          ),
        );
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

// Start -----------------------------------------------------------------------------------

async function poll() {
  if (state.key && state.me && !state.busy && document.visibilityState === "visible") {
    await refreshList();
    if (state.job && ACTIVE.has(state.job.status)) await loadSelected();
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
