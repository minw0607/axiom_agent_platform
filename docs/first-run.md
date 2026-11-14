# First run by someone new to the project

Gate 2 asks that someone who has never seen this project gets the demo running in under 10 minutes, with no API keys. This page is for that person, and for the owner watching.

## For the person trying it

You need:

- Docker Desktop (or Docker Engine with Compose), running
- git
- about 6 GB of free disk space for the images
- no API keys and no accounts

Please time yourself from step 1 to the end of step 3, and write down anything that confused you or did not work. Read only the project's README; ask the owner nothing until you finish or give up.

1. Start a timer. Open the [README](../README.md) and follow its **Quick start**.
2. When `docker compose logs demo-keys` prints three keys, stop and note the time.
3. Open http://localhost:8000/review:
   1. Sign in with the **relationship manager** key and request a memo for any package.
   2. Sign out, sign in with the **credit officer** key, open the memo and approve it, or ask for a change first.
   3. Sign out, sign in with the **auditor** key and open the memo's audit trail.

   Stop the timer.
4. Optional: in the audit trail, find a line that starts with "Tool used". Click a citation in the memo and check that it shows its source line.
5. When you are done, stop the demo with `docker compose down -v`. This also deletes its data.

## What to send back

| Question | Your answer |
|---|---|
| Your computer (operating system, chip) and connection (fast or slow) | |
| Minutes from step 1 to the keys appearing (step 2) | |
| Minutes from step 1 to the end of step 3 | |
| Did anything fail? Copy the error message | |
| Where did you get stuck or have to guess? | |
| Any words on the review page you did not understand? | |
| Did you need anything not in the README? | |

## For the owner

- Don't help while the timer runs. Each question the tester asks points at a gap in the README.
- Gate 2 passes if the tester reaches the end of step 3 in under 10 minutes without help. Record the result, with the date and the tester's machine, in the README's Gate 2 row. Don't record the tester's name.
- Turn each problem the tester reports into a README fix or an issue, then ask whether the fix would have helped.
