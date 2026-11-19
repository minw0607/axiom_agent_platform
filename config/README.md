# Configuration

Settings that change behavior without code changes:

- `routing.{demo,local,cloud}.yaml`: logical model aliases per task, sensitivity rules
- `guardrails.yaml`: an on/off switch per control, so attack success can be measured with and without it
- `eval_gates.yaml`: release-gate thresholds (ADR 0008), applied by `evals/gate.py` in CI, and the live runs (which packages, how many repeats, which baseline)
- `tool_gateway.yaml`: which workflow step may call which tool, with which arguments (ADR 0014)

Real model deployment names live in environment settings, never here.

**Built (week 3):** `routing.demo.yaml`, `routing.local.yaml`, `policy_thresholds.yaml` (now `apps/credit_memo/`, one folder per app's own settings, ADR 0016), `otel-collector.yaml`. **Built (week 5):** `guardrails.yaml`; it must list every control the platform has, and the platform refuses to start if a name is unknown or missing. **Built (week 7):** `tool_gateway.yaml`. **Built (week 8):** `eval_gates.yaml`. **Planned:** `routing.cloud.yaml` (week 9).
