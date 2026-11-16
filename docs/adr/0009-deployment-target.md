# ADR 0009: Deployment target

**Status:** Accepted (week 9, Nov 15, 2026). Written and checked locally; not yet deployed.

## Context

Gate 3 asks for a public demo endpoint: HTTPS, a full job completing in the cloud, uploads refused, and a daily job cap (dev plan, week 9). The stack is five containers (API, worker, tool gateway, GKN, Regulus) and Postgres. It is a portfolio demo: traffic is a few visitors a week, often none for days, and the owner pays for it personally.

Two choices shape the cost more than the hosting product:

- **Model tokens.** A memo job uses about 32,000 tokens (the 15 live recordings of week 7 used 485,000). At $1 to $5 per million tokens, a job costs 3 to 16 cents; a cap of 20 jobs a day could still add $20 to $100 a month.
- **Always-on versus scale to zero.** Five small apps kept running cost about $55 a month at idle rates. Scaled to zero, they cost nothing while no one is using the demo.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| **Azure Container Apps, Consumption plan** | Billed per second only while a container runs; scales to zero; a free monthly grant (180,000 vCPU-seconds, 360,000 GiB-seconds); HTTPS and internal service names built in; KEDA scale rules, so the worker can start when a job is queued | Cold starts; fewer controls than Kubernetes; stateful services need care |
| AKS | Full Kubernetes; the phase 4 Helm chart would run unchanged | A node runs all month (about $30 or more for the smallest), plus cluster operations; far more than the demo needs |
| App Service | Simple for one web app | Billed per plan whether used or not; no natural home for the worker or the internal MCP services |

## Decision

**Container Apps on the Consumption plan, with the demo replaying recorded model responses.** Terraform for it is in [`infra/terraform/`](../../infra/terraform/) (ADR 0010).

- **No model keys in the cloud.** The demo runs in demo mode (`AXIOM_MODE=demo`): every model answer is a recording of a live run (week 7). No model key or alias map is deployed, so the cloud environment cannot spend tokens. Visitors see real memos made by real models; live runs stay on the owner's machine.
- **Catalog only, no uploads, a daily cap.** The API already accepts only a catalog package ID and refuses any other field. A new `AXIOM_DAILY_JOB_CAP` (25 by default in Terraform) refuses new jobs past the cap for the UTC day with `429 daily-cap-reached`; retries of an accepted job still return it. The cap counts jobs across all submitters, under one database lock, so it holds under concurrent submissions.
- **Scale to zero, except GKN.** The API, worker, gateway and Regulus scale to zero. KEDA starts the worker when the jobs table has work (`queued`, `revising` or `finalizing`), the same statuses the worker claims. GKN keeps each job's indexed documents in SQLite inside its container, so it runs as one replica all month (0.25 vCPU, 0.5 GiB, about $6): scaling it to zero would drop a job's documents between the draft and a reviewer's "revise".
- **Only the API is public.** The other four apps have internal ingress only. Postgres has no public address: it sits in a delegated subnet of the environment's virtual network. Using VNet integration rather than a private endpoint matters: a private endpoint adds a flat management fee to a Consumption environment, as does a planned maintenance window. This setup uses neither.
- **Secrets in Key Vault, no passwords for Azure resources.** Terraform generates the role keys, gateway tokens and service secrets, in the same shapes the local `demo-keys` service generates, and stores them in Key Vault. The apps read them, and pull images, through one managed identity.
- **The gateway's call log persists.** The hash-chained tool-call log (ADR 0014) goes to a 1 GB Azure Files share, so it survives restarts. The gateway runs at most one replica, so the log has one writer.
- **Telemetry.** Container logs go to Log Analytics, capped at 0.5 GB a day. The OpenTelemetry collector and Phoenix stay local; traces in the cloud are later work.
- **Access.** The role keys are in Key Vault; the owner shares them on request. A public sign-in for visitors is not built.

### Expected cost (US East, list prices, November 2026)

From the Azure retail prices API.

| Item | Price | Per month |
|---|---|---|
| Postgres Flexible Server, Burstable B1ms, 32 GB | $0.017 an hour, $0.115 per GB-month | about $16 (free for 12 months on a new Azure free account) |
| Container Registry, Basic | $0.167 a day | about $5 |
| GKN, one replica always on (0.25 vCPU, 0.5 GiB) | idle rates: $0.000003 per vCPU-second and per GiB-second | about $6 |
| Other apps, scaled to zero | $0.000024 per vCPU-second while active | about $0; a job costs about 1.4 cents, inside the free grant |
| Log Analytics | 5 GB a month free, then $2.30 per GB | about $0 |
| Key Vault, file share, private DNS zone | per operation, per GB, per zone | about $1 |
| **Total** | | **about $28**; about $12 in the first year on a free account |

A $30 monthly budget on the resource group emails at 50% and 80% of actual spend and at 100% of forecast spend. A budget alerts; it does not stop anything.

## Consequences

- **Cold starts.** The first visitor after a quiet spell waits while the API starts (roughly 10 to 30 seconds), and the first job waits for the worker, gateway and Regulus. `scripts/check_week9.py` allows for it.
- **The cloud demo is only as current as the recordings.** A prompt change without new recordings makes the affected packages unavailable in demo mode (`package-not-recorded`), as it does locally.
- **A GKN restart loses the indexed documents of jobs in progress.** A deploy or a platform restart of GKN means a job already awaiting review can be approved or rejected, but a "revise" can no longer retrieve its documents. A shared store for GKN would close this; it is not planned for the demo.
- **Not yet proven in Azure.** These were checked locally only. Three things are unverified until the first deployment: that the KEDA Postgres rule reaches the private database, that the gateway (running as a non-root user) can write to the file share, and that the MCP services accept the Host header that Container Apps sends.
- **Compared with the alternatives,** an always-on deployment would cost about $55 a month more, and live models about $20 to $100 more at the same cap.
