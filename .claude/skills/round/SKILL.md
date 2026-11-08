---
name: round
description: Coordinate one round of the feature in flight — create its branch and state files, run the round workflow in the background, answer or escalate its questions, record the merge in brief.json, then start the next round or /finish. Use after /feature writes the brief, when a round workflow completes, or to resume a round that stopped.
argument-hint: "[round number]"
model: sonnet
effort: high
---

# /round — coordinate one round

The main session coordinates; the round itself runs as the `round` workflow
(`.claude/workflows/round.js`), each step an agent on its own model. This skill instructs you
to run that workflow: that is the opt-in it needs.

Exactly one round runs at a time, and while it runs the working tree belongs to it: do not
edit files, switch branches or commit until the workflow returns. Talking with the user is
fine.

A skill's model holds for the turn that invokes it. The workflow's completion wakes the
session in a new turn, so **invoke `/round` again then**: it routes on the state below.

## 1. Find the round

`git fetch origin`, then read the live `brief.json` on the feature branch
(`python .claude/hooks/dev_state.py`). The round is the argument, else `current_round`.

- **`planned`** → start it (section 2).
- **`running`** → its true state is on the round branch, not the feature branch's copy of
  `round.json` (that one is frozen at "start"). If the round branch still exists,
  `git switch <round branch>`, `git pull --ff-only`, and read its `round.json`:
  - `done`, or the round branch is already an ancestor of the feature branch → the merge
    happened; `git switch <feature branch>`, `git pull --ff-only`, go to section 4 "merged".
  - `blocked` → the round is waiting on its `question`: section 4 "needs_input".
  - `failed` → read `note` and the end of `log.md`, tell the user, ask how to proceed.
  - `running` → section 3 with `start_step` = `step` and `attempt` = `attempt`.

  If the round branch is gone, check the feature branch's log: a merge commit for this round
  means section 4 "merged"; otherwise tell the user what is missing.
- **every round `merged`** → `/finish`.

## 2. Start it

On the feature branch, up to date with its remote:

- Create `<feature folder>/r<n>-<slug>/` with `log.md` (a title line, then the round's summary
  from the brief) and `round.json`: `schema` 1, `round`, `name`, `branch`, `status`
  `running`, `step` 1, `steps` (the nine names in `ROUND_STEPS`,
  `.claude/hooks/dev_state.py`), `attempt` 0, `question` "", `note` "starting".
- In `brief.json`: this round's status `running`, `current_round` = n.
- Commit `round <n>: start <name>`, push.
- `git switch -c <round branch>`, `git push -u origin <round branch>`.
  If that push is refused (a hosted session that may push only its own branch), switch back,
  set this round's `branch` in `brief.json` to the feature branch itself, commit, push, and
  run the round on the feature branch: the workflow then skips the merge.

From here `round.json` is written by the round's own steps, and `brief.json` only by you, on
the feature branch. The one exception is section 4, below, when the workflow returns a stop
the script itself could not record.

## 3. Run the workflow

Make sure the round branch is checked out and clean. Call the Workflow tool with
`scriptPath: ".claude/workflows/round.js"` and `args`:

```json
{ "feature_dir": "development/<branch>", "round_dir": "development/<branch>/r<n>-<slug>",
  "round": <n>, "feature_branch": "<branch>", "round_branch": "<round branch>",
  "start_step": <1, or the step to resume>, "attempt": <round.json attempt, 0 to start>,
  "kind": "<brief.json kind: feature or fix>",
  "adopt_repro": <for a fix: true while development/<branch>/diagnosis/test_reproduction.py exists
                  on the feature branch, else false>,
  "answers": [<every answer given in this round so far>] }
```

Keep the answers in the round's `log.md` under `## Answers` (step, question, answer) so a new
session can pass them again.

It runs in the background. Tell the user in one line that round n is running and what it
covers, then end the turn.

## 4. When it returns

Relay its trace: one line per step.

- **`merged`** — the closer merged the round into the feature branch and left it checked
  out. In `brief.json`: the round's status `merged`; `current_round` to the next round, or
  status `final` if this was the last. Commit `round <n>: merged`, push. Check the round's
  "Changes" against the rounds still planned: if this round changed what a later round
  assumes, update `BRIEF.md` in the same commit and say so. Then start the next round
  (section 2) or invoke `/finish`.
- **`needs_input`** — first make the state say so: if `round.json` on the round branch is not
  already `blocked`, set `status` `blocked`, `question` to the question, commit, push. Then
  answer it yourself when `BRIEF.md`, its Decisions and this conversation settle it, saying in
  one line what you answered and why; otherwise ask the user with `AskUserQuestion`, carrying
  the options the round saw. Record the answer under `## Answers` in `log.md`, set
  `round.json` back to `running` with `question` "", commit, push, and run section 3 with
  `start_step` = the step that asked. An answer that changes the feature itself also goes into
  `BRIEF.md`'s Decisions, on the feature branch, after the round.
- **`failed`** — if `round.json` is not already `failed`, set it with the summary as `note`,
  commit, push. Tell the user which step failed and why, and ask how to proceed.

A round that keeps failing its gates escalates as `needs_input` after two fix attempts,
counted across resumes; treat that as a real question, not a reason to rerun unchanged.
