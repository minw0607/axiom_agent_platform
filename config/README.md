# Configuration

Settings that change behavior without code changes:

- `routing.{demo,local,cloud}.yaml`: logical model aliases per task, sensitivity rules
- `guardrails.yaml`: an on/off switch per control, so attack success can be measured with and without it
- `thresholds.yaml`: release-gate thresholds

Real model deployment names live in environment settings, never here.

**Built (week 3):** `routing.demo.yaml`, `routing.local.yaml`, `policy_thresholds.yaml`, `otel-collector.yaml`. **Built (week 5):** `guardrails.yaml`; it must list every control the platform has, and the platform refuses to start if a name is unknown or missing. **Planned:** `routing.cloud.yaml`, `thresholds.yaml` (weeks 8-9).
