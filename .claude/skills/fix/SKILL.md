---
name: fix
description: Start or resume a fix for a defect — wrong output, a crash, a check that lets something through. Diagnoses before anything is built: a short intake, then the diagnose workflow reproduces the symptom, hunts causes from three angles, proves the root cause and sizes its class, and has a critic try to break it. The user agrees the cause, the scope and the bump; then it runs through the same rounds and /finish as a feature. Use for any reported bug, whether the user names the skill or describes the symptom in prose.
argument-hint: <the symptom>
model: opus
effort: medium
---

# /fix — diagnose, then the feature pipeline

A fix is a feature whose first stage is a diagnosis. Nothing is built until the root cause is
proven and the user has agreed it. After that it is `/round` and `/finish`, exactly as for a
feature, with a few extra rules for fix rounds that `brief.json`'s `kind: "fix"` switches on.

| Stage | Who | Gate |
|---|---|---|
| A. Orient | you | — |
| B. Intake | you and the user | — |
| C. Diagnose | the `diagnose` workflow | only a question it escalates |
| D. Agree the fix | you and the user | the user confirms cause, scope and bump |
| E. Brief and rounds | you, then `/round` | — |

A skill's model holds for the turn that invokes it: invoke `/fix` again on each turn of the
intake and when the diagnose workflow completes.

## A. Orient

As `/feature` A: `git fetch origin`, and if a live brief exists, resume it rather than start a
second one (`rounds` with `/round`, `final` with `/finish`). A brief at status `diagnosis`:
`diagnosis/state.json` `done` means the diagnosis finished and waits at D, so go to D with the
Defect block from `diagnosis/log.md`; otherwise resume C at its `step`. Read `STRUCTURE.md`.

## B. Intake (short exchanges)

Get, in a few lines each: what happened, what should have happened, how to trigger it (the
call, the input, the command). An error message or traceback, if there is one, verbatim. Do
not diagnose in the chat; the workflow does that with evidence.

Then, without waiting:

- `git switch main`, `git pull --ff-only`, `git switch -c fix/<kebab-slug of the symptom>`.
  The hosted-session fallback is as in `/feature` C.
- `development/<branch>/brief.json` (schema in `development/README.md`): `schema` 1, `feature`
  (the slug), `title`, `branch` (the branch actually pushed), `summary` (the symptom in one
  line), `kind` `fix`, status `diagnosis`, `bump` `patch` (the diagnosis may raise it),
  `current_round` 0, `rounds` [], `pr` "". `BRIEF.md`: the symptom as reported.
- `development/<branch>/diagnosis/state.json`: `schema` 1, `name` "Diagnosis", `branch`,
  `status` `running`, `step` 1, `steps` (`DIAGNOSIS_STEPS` in `.claude/hooks/dev_state.py`),
  `attempt` 0, `question` "", `note` "starting"; and `diagnosis/log.md` with a title line.
- Commit `fix: start <branch>`, push with `-u`.

## C. Diagnose

Call the Workflow tool with `scriptPath: ".claude/workflows/diagnose.js"` and `args`
`{ "feature_dir": "development/<branch>", "branch": "<branch>", "symptom": "<the intake, in full>",
"start_step": <1, or state.json's step to resume>, "answers": [<answers so far>] }`. This skill
instructs you to run it: that is the opt-in it needs. It runs in the background; tell the user
in one line, end the turn, and invoke `/fix` again when it completes.

- **`needs_input`** — if `state.json` is not already `blocked`, set it with the question and
  push. Answer from the intake when it settles the question; otherwise ask the user. Record the
  answer under `## Answers` in `diagnosis/log.md`, set `state.json` back to `running`, and
  re-run with `start_step` = the step that asked. A question about a disputed diagnosis asks
  for the critic's findings to be recorded in `log.md` first: do that before asking. A reproduction that cannot be made to fail is
  always the user's question: never guess a fix for a bug nobody reproduced.
- **`failed`** — unless `state.json` already says `failed`, set it, with the step and the
  summary in `note`, commit and push; then tell the user why and ask how to proceed.
- **`diagnosed` with `is_bug: false`** — the code does what it promises. Tell the user what it
  promises and where; if they still want the other behaviour, that is a behaviour change: set
  this brief to `parked` (commit, push) and start `/feature` with what was learned.
- **`diagnosed`** — go to D.

## D. Agree the fix (waits for the user)

One short message, from the Defect block (`defect` in the result, else `## 3. Root cause` in
`diagnosis/log.md`):

- **Cause** — two lines, with `path:line`, and that it was proven and survived the critique.
- **Scope** — the two options with what each changes:
  - *this case*: fix exactly what was reported, and leave the class as a noted follow-up;
  - *the whole class*: fix the cause everywhere it applies (the class lists where).

  Recommend one. Prefer the whole class when it is the same change in a few places; prefer
  this case when the class reaches into code that needs its own design.
- **Bump** — `patch`, unless the fix changes what callers get back (then `minor` or `major` by
  `CLAUDE.md`'s table). The branch name stays `fix/…` either way.

End the turn on the question.

## E. Brief and rounds (no gate)

- `BRIEF.md`: a **Defect** section (observed, expected, reproduction, root cause, proof, class,
  the chosen scope), then Goal, Acceptance criteria — `F1` the reproduction passes, the last
  one no regression in the chosen scope — Rounds (usually one), Decisions.
- `brief.json`: status `rounds`, the agreed `bump`, `current_round` 1, the rounds as `/feature`
  E writes them.
- Commit `fix: brief for <branch>`, push, and invoke `/round`. From here it is the feature
  pipeline; the round and final workflows read `kind` and add the fix rules.
