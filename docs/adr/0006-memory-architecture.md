# ADR 0006: Memory architecture

**Status:** Accepted (week 4)

## Context

A memo job runs for minutes, then waits for a credit officer for hours or days. While it waits, the worker can restart, be redeployed, or move to another container. When the officer decides, the job must continue from exactly the state they reviewed: the same figures, findings and draft, with no model call repeated.

The plan also leaves room for memory that outlives a job, such as precedent memos retrieved for a new borrower. That kind of memory carries risks this platform has to control first:

1. **Contamination:** an injected instruction in one borrower's documents can be stored and replayed into later jobs.
2. **Mixing:** one borrower's data can surface in another borrower's memo.
3. **Unrepeatable evaluation:** an eval run's results depend on whatever the memory held that day.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Checkpointer only | Job-scoped state, keyed by job_id; pause and resume are built into LangGraph; nothing crosses jobs | No learning across jobs |
| Checkpointer plus a vector store of past memos | Precedent retrieval could improve drafts | All three risks above; needs isolation, a content screen on write, and pinned snapshots for evals before it is safe |
| Checkpointer plus a knowledge graph | Structured links between borrowers, industries and outcomes | The same risks, plus a schema to design and govern; no requirement needs it yet |

## Decision

**Checkpointer only.** Each job's graph state is kept by the LangGraph Postgres checkpointer (`AsyncPostgresSaver`), keyed by job_id as the thread. The job store owns it, so a paused job and its record live in the same database.

- The workflow pauses with a LangGraph interrupt after the compliance step. A review stores the decision, and the worker resumes the graph from the checkpoint with it.
- Retrieval through GKN is a tool, not memory: job documents are indexed per job, and the credit policy and lending references are read-only collections.
- Checkpoints load only an allowlist of the platform's own state types (`src/axiom/memory/checkpoint.py`), so a tampered checkpoint cannot make the worker build an arbitrary object.
- Tests and runs without Postgres use LangGraph's in-memory saver with the same allowlist.

Long-term memory stays deferred to phase 4. Before it ships it needs three controls: borrower isolation, a content screen on write, and a pinned memory snapshot for each evaluation run.

## Retention

All data in this project is synthetic. A deployment would set these by the bank's records schedule:

- **Checkpoints and job records** hold the full working state, borrower documents included. They are kept with the job and deleted together when its retention period ends.
- **The evidence log** holds hashes, codes, decisions and reviewer comments, not the documents. It is append-only and outlives the job's working data, so deleting a job's documents does not break its chain.
- **GKN's per-job index** should be deleted when the job closes. The `delete_job` tool exists; calling it at finalize or rejection is a phase 2 task.

## Consequences

- Week 4's acceptance test passes against the stack: a job paused at `awaiting_review` survives a restart of Postgres, the API and the worker, then resumes on approval (`scripts/check_week4.py`).
- No model call is repeated on resume: the drafter and compliance outputs come from the checkpoint.
- **A known gap:** if a worker crashes during a run, rather than while paused, the job stays `running`. The checkpoint holds the nodes that completed, but nothing reclaims the job yet, and that segment's evidence events are lost because they are written when the segment ends. A lease with a heartbeat, and per-node event writes, would close this; the plan schedules it with failure handling.
- In the cloud the checkpoint tables move with the database to Azure Database for PostgreSQL, encrypted at rest.
