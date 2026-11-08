# development/

The feature pipeline's state: one folder per feature branch, one subfolder per round. See
`CLAUDE.md` for the pipeline. Everything here is committed and pushed after every step.

```
development/feat/csv-export/
  BRIEF.md
  brief.json
  r1-writer-core/
    log.md
    round.json
  r2-cli/
    ...
```

Both JSON files are read by `.claude/hooks/dev_state.py` and by Claude Code mods that show
progress. Add fields freely; never rename or remove one, since a mod may read it.

## `brief.json` — written only by the main session

```json
{
  "schema": 1,
  "feature": "csv-export",
  "title": "CSV export",
  "branch": "feat/csv-export",
  "bump": "minor",
  "status": "rounds",
  "summary": "Export any table as CSV, from Python and the command line",
  "current_round": 1,
  "rounds": [
    { "n": 1, "name": "Writer core", "slug": "writer-core", "branch": "feat/csv-export--r1",
      "status": "running", "summary": "The CSV writer and its options" },
    { "n": 2, "name": "CLI", "slug": "cli", "branch": "feat/csv-export--r2",
      "status": "planned", "summary": "A command-line entry point" }
  ],
  "pr": ""
}
```

| Field | Values |
|---|---|
| `kind` | `feature`, or `fix` for one `/fix` started; absent means `feature` |
| `bump` | `major`, `minor`, `patch` |
| `status` | `diagnosis` (a fix being diagnosed), `concept` (talking it through), `rounds` (building), `final` (final check), `review` (pull request open), `parked` |
| `current_round` | the round running or next up; 0 before the first |
| `rounds[].status` | `planned`, `running`, `merged`, `failed` |
| `pr` | the pull request URL once opened |

Only `diagnosis`, `concept`, `rounds` and `final` are live: a mod or hook shows nothing for `review` or
`parked`, so a merged feature never shows as in progress on `main`.

## `round.json` — written only by the round's steps

```json
{
  "schema": 1,
  "round": 1,
  "name": "Writer core",
  "branch": "feat/csv-export--r1",
  "status": "running",
  "step": 4,
  "steps": ["Conceptualize", "Plan", "Implement", "Verify", "Test design",
            "Write tests", "Run tests", "Concept check", "Close"],
  "attempt": 0,
  "question": "",
  "note": "ruff and mypy clean; 3 review units out"
}
```

| Field | Values |
|---|---|
| `status` | `running`, `blocked` (waiting on `question`), `failed` (see `note`), `done` |
| `step` | 1 to 9, into `steps`: the step running now, or the last one when `done` |
| `attempt` | fix attempts spent on the current gate, at most 2 |
| `note` | one line on what the step is doing or just did |

`/round` creates the file at step 1 when it starts the round; from then on only the round's
steps write it. A mod shows the round as `round n/len(rounds)` from `brief.json` and the step
as `step/len(steps)` with `steps[step-1]` from `round.json`.

## `diagnosis/state.json` — a fix only, written by the diagnose workflow's steps

The same fields as `round.json` (without `round`), with the diagnosis's four steps:

```json
{
  "schema": 1,
  "name": "Diagnosis",
  "branch": "fix/csv-quoting",
  "status": "running",
  "step": 2,
  "steps": ["Reproduce", "Hunt", "Root cause", "Critique"],
  "attempt": 0,
  "question": "",
  "note": "reproduced: a comma inside a field splits it"
}
```

`/fix` creates it at step 1; while `brief.json`'s status is `diagnosis`, a mod shows this as
its own bar instead of the round bar. Beside it: `log.md` (reproduction, candidates, the
Defect block, the critique) and `test_reproduction.py`, which the fix round moves into `tests/`.
