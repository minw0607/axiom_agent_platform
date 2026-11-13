# Architecture decision records

Each ADR records one decision, the options considered, and the trade-offs. Files are named `NNNN-short-title.md`. Status moves from **Proposed** to **Accepted** when the ADR is written.

| # | Decision | Options considered | Planned | Status |
|---|---|---|---|---|
| [0001](0001-orchestration-framework.md) | Orchestration framework | LangGraph, AutoGen, Semantic Kernel, CrewAI | Week 1 | Accepted |
| [0002](0002-api-interaction-model.md) | API interaction model | Synchronous, async job + polling, async + webhook | Week 1 | Accepted |
| [0003](0003-tool-integration.md) | Tool integration | MCP servers, direct function calls, REST tools | Week 1 | Accepted |
| [0004](0004-guardrail-placement.md) | Guardrail placement | Gateway-only, per-node, layered | Week 2 | Accepted |
| [0005](0005-model-routing.md) | Model routing approach | Single provider, LiteLLM, in-house adapter | Week 2 | Accepted |
| [0006](0006-memory-architecture.md) | Memory architecture | Checkpointer only, plus vector store, plus knowledge graph | Week 4 | Accepted |
| [0007](0007-deterministic-computation.md) | Deterministic vs. LLM computation | LLM math, code tools, hybrid with verification | Week 3 | Accepted |
| [0008](0008-evaluation-thresholds.md) | Evaluation thresholds and gating policy | Fixed thresholds, relative to main, hybrid | Week 6 | Accepted |
| 0009 | Deployment target | Container Apps, AKS, App Service | Week 9 | Proposed |
| 0010 | Infrastructure as code | Terraform, Bicep | Week 9 | Proposed |
| [0011](0011-workflow-control.md) | Workflow control | Rule-based pipeline, LLM supervisor, hybrid | Week 3 | Accepted |
| [0012](0012-regulatory-position.md) | Regulatory position and compliance knowledge sources | Regulus for memo review, lending references plus rule engine, rule engine only | Week 1 | Accepted |
| 0013 | Question answering on the review page | Deterministic "explain this" only, a memo-scoped grounded assistant, a general chatbot | Phase 4 | Proposed |
