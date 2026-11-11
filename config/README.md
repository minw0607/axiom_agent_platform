# Configuration

Settings that change behavior without code changes:

- `routing.{demo,local,cloud}.yaml`: logical model aliases per task, sensitivity rules
- `guardrails.yaml`: an on/off switch per control, so attack success can be measured with and without it
- `thresholds.yaml`: release-gate thresholds

Real model deployment names live in environment settings, never here.

**Built (week 3):** `routing.demo.yaml`, `routing.local.yaml`, `policy_thresholds.yaml`, `otel-collector.yaml`. **Planned:** `routing.cloud.yaml`, `guardrails.yaml`, `thresholds.yaml` (weeks 5-9).
