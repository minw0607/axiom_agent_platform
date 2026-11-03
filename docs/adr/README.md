# Architecture decision records

Each ADR records one decision, the options considered, and the trade-offs. Files are named `NNNN-short-title.md`. Status moves from **Proposed** to **Accepted** when the ADR is written.

| # | Decision | Options considered | Planned | Status |
|---|---|---|---|---|
| 0001 | Orchestration framework | LangGraph, AutoGen, Semantic Kernel, CrewAI | Week 1 | Proposed |
| 0002 | API interaction model | Synchronous, async job + polling, async + webhook | Week 1 | Proposed |
| 0003 | Tool integration | MCP servers, direct function calls, REST tools | Week 1 | Proposed |
| 0004 | Guardrail placement | Gateway-only, per-node, layered | Week 2 | Proposed |
| 0005 | Model routing approach | Single provider, LiteLLM, in-house adapter | Week 2 | Proposed |
| 0006 | Memory architecture | Checkpointer only, plus vector store, plus knowledge graph | Week 4 | Proposed |
| 0007 | Deterministic vs. LLM computation | LLM math, code tools, hybrid with verification | Week 3 | Proposed |
| 0008 | Evaluation thresholds and gating policy | Fixed thresholds, relative to main, hybrid | Week 6 | Proposed |
| 0009 | Deployment target | Container Apps, AKS, App Service | Week 9 | Proposed |
| 0010 | Infrastructure as code | Terraform, Bicep | Week 9 | Proposed |
| 0011 | Workflow control | Rule-based pipeline, LLM supervisor, hybrid | Week 3 | Proposed |
| 0012 | Regulatory position and compliance knowledge sources | Regulus for memo review, lending references plus rule engine, rule engine only | Week 1 | Proposed |
