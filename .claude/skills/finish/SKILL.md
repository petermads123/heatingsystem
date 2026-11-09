---
name: finish
description: Close a feature once every round has merged — run the final workflow (the feature's test selection, three concept checkers, two extras scouts), add a fix round if the check fails, hold for the user on a critical extra, otherwise write the version bump, STRUCTURE.md, README.md and DEVLOG.md, open the pull request to main, see its CI through to green (fixing it on the feature branch when red), and list the non-critical extras in chat. Use when /round reports the last round merged, or to resume a feature at status final.
model: sonnet
effort: high
---

# /finish — final check and pull request

Runs on the feature branch once `brief.json` is at status `final`. Opening the pull request
needs no confirmation when everything passes; a critical extra holds it for the user.

## 1. Final check

Call the Workflow tool with `scriptPath: ".claude/workflows/final.js"` and `args`
`{ "feature_dir": "development/<branch>", "feature_branch": "<branch>", "kind": "<brief.json kind>" }`. This skill
instructs you to run it: that is the opt-in it needs. It is read-only and returns the test
gate, three independent concept verdicts, the extras, and `missing`. It runs in the
background; when it completes, invoke `/finish` again and continue here.

- **Anything in `missing`** (no test result, fewer than three verdicts, a scout that did not
  return) — run the workflow once more. Still missing: tell the user and hold the pull
  request. A missing result is never read as a pass.
- **Tests red, or fewer than two of the three concept checks pass** — add a fix round (below)
  named "Final fixes", the failures and blocking findings as its summary and in `BRIEF.md`
  under Rounds. If the feature already had a "Final fixes" round, do not add another: tell the
  user what still fails and ask.
- **Green, with a majority passing** — go on. Blocking findings from a dissenting checker are
  shown to the user with the extras in step 6.

### Adding a round

Write the new entry in full: `n` = the last round's + 1, `name`, `slug` (kebab-case),
`branch` `<branch>--r<n>`, `status` `planned`, `summary`. Set `current_round` to n and
`status` to `rounds` in the same commit, push, and invoke `/round`.

## 2. Extras

Merge the scouts' extras: drop duplicates, keep at most five, best first.

- **Any extra marked critical** — keep the pull request closed. Show the critical ones with
  their reasons and ask with `AskUserQuestion` whether to add a round for them, ship anyway,
  or drop them. A new round is added as above.
- **None critical** — go on; the list is shown after the pull request opens.

## 3. Version and documents

- `git fetch origin main`. Read `main`'s version (`git show origin/main:pyproject.toml`) and
  raise it once by the brief's `bump`: `major` → X+1.0.0, `minor` → X.Y+1.0, `patch` →
  X.Y.Z+1. Counting from `main` now, not from when the feature started, keeps two branches
  from claiming the same number.
- If `main` moved since the feature branch was cut, `git merge origin/main` first and re-run
  the test gate; a conflict goes to the user.
- Start a `docs-writer` agent (`model: sonnet`, `effort: medium`) with the new version, the
  feature folder and today's date. It writes `pyproject.toml`, `STRUCTURE.md`, `README.md`
  and `DEVLOG.md` and runs `check_sync.py --base origin/main` until clean.
- Run the feature's gate yourself: the tests `python .claude/hooks/select_tests.py --base
  origin/main` selects (`pytest <files>`, `pytest` for `ALL`, none for an empty list),
  `ruff check .`, `ruff format --check .`, `mypy`,
  `python .claude/hooks/check_sync.py --base origin/main`. The full suite is CI's (step 5).

## 4. Pull request

- In `brief.json`: status `review`. That status is what keeps the merged brief from showing as
  live on `main`.
- Commit `release: <version> — <feature title>` with the documents and the state file, push.
- Open the pull request to `main` (`gh pr create`, or the GitHub tools where `gh` is not
  available), titled `<feature title> (v<version>)`. Body: the BRIEF's goal; the rounds, one
  line each; the feature's acceptance criteria with how each is met; the version and why that
  bump; the test gate's summary. For a fix (`kind` `fix`), first the Defect block's observed,
  root cause and chosen scope, so the reviewer checks the fix against the cause.
- Write the pull request URL into `brief.json` `pr`, commit `feature: record PR`, push.

## 5. CI

The rounds ran only the tests their changes can affect; the full suite runs in CI on the pull
request, so CI is part of finishing, not something that happens after it.

- Watch the pull request's checks until each one has concluded: `gh pr checks <number>
  --watch`, or where `gh` is not available the GitHub tools (the head commit's check runs)
  and the pull request activity subscription, which wakes the session when CI reports.
- **Green** — go on to the report.
- **Red** — this is work, not waiting on review. Read the failing job's log, reproduce the
  failure locally (the failing test files by name), fix it on the feature branch with a
  commit `fix: <what CI caught>`, run the gate of step 3 again, and push; the push runs CI
  again. Repeat until green. A failure that needs new behaviour, or that you cannot explain,
  goes to the user, with the log.
- **Pending** — keep waiting. Never call the feature done on a red or pending CI.

The merge rule in `CLAUDE.md` is unchanged: CI green is required before a merge, and green CI
does not make a merge due.

## 6. Report

In chat: the pull request link and the CI result with the link to the run, then the
non-critical extras — title, why, size — so the user can pick any up as a new `/feature`.
Nothing merges until the user merges it or tells you to in so many words; see `CLAUDE.md`.
