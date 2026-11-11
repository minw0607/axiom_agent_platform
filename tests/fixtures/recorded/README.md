# Scripted recordings (test fixtures)

Written by `scripts/seed_recordings.py`. Intake answers come from the ground truth, and the analyst and drafter text is a template filled from the workflow's own inputs. Every file is marked `origin: scripted`; none is model output. Each passes the same validation a model's output must pass.

The tests replay these so their results are deterministic. Re-run the script after changing a prompt, the policy or the synthetic data. The demo replays live recordings from `data/recorded/` instead.
