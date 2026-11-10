# ADR 0012: Regulatory position and compliance knowledge sources

**Status:** Accepted (week 2)

## Context

This ADR settles two related questions:

1. **Which regulatory framework does the platform claim to meet?** A credit-memo tool at a US bank would traditionally fall under model risk management guidance. That guidance changed in April 2026.
2. **Where does memo compliance review get its knowledge?** The compliance reviewer must check drafts against something authoritative, and Regulus, the sibling AI governance lookup service, is an obvious but possibly wrong candidate.

### What SR 26-2 says (checked against the primary text)

[SR 26-2](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.pdf) (April 17, 2026), issued jointly by the Federal Reserve, OCC and FDIC, attaches the revised *Guidance on Model Risk Management*. Points that matter here:

- **It supersedes SR 11-7** (2011) and SR 21-8 (2021, BSA/AML models).
- **Generative and agentic AI are out of scope.** Footnote 3 states that generative and agentic AI models are novel and rapidly evolving and are not within the guidance's scope. It adds that an organization's own risk management and governance practices should guide controls for systems the guidance does not cover, and that the principles apply to traditional quantitative models and non-generative, non-agentic AI.
- **Deterministic rule-based processes are not "models."** The definition excludes simple arithmetic and deterministic rule-based processes without underlying statistical, economic or financial theory.
- **Applicability.** It is expected to be most relevant to banking organizations with over $30 billion in total assets.
- **It is not enforceable.** It sets no prescriptive requirements, and non-compliance alone does not lead to supervisory criticism.

So **neither main part of this platform is a "model" under SR 26-2.** The LLM agents are excluded by footnote 3, and the ratio and policy-rule code is excluded by the definition.

## Options considered

### Regulatory position

| Option | Assessment |
|---|---|
| Claim SR 26-2 compliance | Inaccurate: the guidance excludes generative and agentic AI. Overclaiming would undermine the project's credibility. |
| Claim SR 11-7 compliance | Superseded. |
| NIST AI RMF as the primary reference, SR 26-2 principles applied voluntarily by analogy | Accurate. NIST AI RMF and its Generative AI Profile (NIST AI 600-1) explicitly cover generative AI. SR 26-2's principles still describe sound practice, and footnote 3 asks organizations to apply their own governance to out-of-scope systems. |

### Compliance knowledge sources for memo review

| Option | Assessment |
|---|---|
| Regulus reviews memo content | Wrong domain: Regulus indexes AI governance frameworks (NIST AI RMF, EU AI Act, OWASP and others), not lending regulation or credit policy. Its findings would look authoritative and be irrelevant. |
| Credit policy rule engine only | Exact and testable, but cannot surface regulatory context such as fair-lending considerations. |
| Credit policy rule engine plus cited lending references | Rule engine for hard thresholds; a small set of public lending references, retrieved through GKN and cited, for context. |

## Decision

1. **Regulatory position.** NIST AI RMF (with NIST AI 600-1) is the primary governance reference. SR 26-2 is **applied voluntarily by analogy**, and the repo never claims compliance with it. Every public description of the project states this position.
2. **Memo compliance knowledge.** Use the **credit policy rule engine plus cited lending references**:
   - a fictional, synthetic credit policy enforced by deterministic rules;
   - public lending references (candidates: Regulation B / ECOA and OCC commercial lending handbook material), each with source URL, retrieval date and status in a provenance manifest, retrieved through GKN's read-only `lending_refs` collection.
3. **Regulus keeps its AI governance role.** At finalize, Regulus attaches related AI governance provisions to the evidence bundle for the model risk reviewer. The output is labeled as related guidance and cannot block finalization.

### How SR 26-2 principles map by analogy

| SR 26-2 principle | Platform analog |
|---|---|
| Clear statement of purpose | Intended use and limits in the model card |
| Inherent risk, exposure and purpose determine materiality | Threat model ratings; P1 controls before Gate 2 |
| Testing during development | Unit tests, golden set, adversarial set |
| Effective challenge by objective experts | Independent judge models from a different family; red-team gate; human reviewer catch-rate test |
| Outcomes analysis | Golden-set comparison of extracted fields, ratios, flags and findings |
| Ongoing monitoring | Nightly eval runs, cost and latency dashboards, alignment and drift scenarios |
| Roles and responsibilities, conflicts of interest | Role-scoped keys; the submitter cannot approve |
| Model inventory | Routing config with logical aliases; model and prompt versions recorded per job |
| Documentation | ADRs, threat model, model card, control mapping |
| Vendor and third-party models | Hosted LLMs treated as vendor models: evaluated on the golden set, monitored, and behind a fallback |

## Consequences

- The README, model card and control mapping state the position: NIST AI RMF primary, SR 26-2 by analogy, no compliance claim. CLAUDE.md forbids treating SR 26-2 as a requirement.
- The control mapping (week 10) maps each NIST AI RMF function and each SR 26-2 principle to a control, its evidence source and its stated limit.
- Compliance findings are drafting aids, not legal conclusions. The memo says so.
- If the agencies issue guidance on generative or agentic AI, this ADR is revisited.
- The scope position was confirmed against the primary text on Nov 10, 2026, completing the week 1 plan task.
