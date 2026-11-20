# Scripted recordings (test fixtures)

Written by `scripts/seed_recordings.py`. Intake answers come from the ground truth, and the analyst and drafter text is a template filled from the workflow's own inputs. Revision drafts add one claim per reviewer comment, and compliance reviews apply keyword tests. Every file is marked `origin: scripted`; none is model output. Each passes the same validation a model's output must pass.

The tests replay these so their results are deterministic. Re-run the script after changing a prompt, the policy or the synthetic data. The demo replays live recordings from `data/recorded/` instead. Both folders hold one subfolder per app (`credit_memo/`, `model_review/`).

`model_review/` is written by `scripts/seed_model_review.py`: the assessor marks weak exactly the present sections the ground truth expects a finding on, giving that finding as its reason, and the drafter writes the four report sections from what the workflow passes it, with two revision rounds. A test reseeds into a temporary folder and fails if any file differs, so re-run the script after changing an app 2 prompt, the checklist or the documentation packages.
