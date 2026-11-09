---
name: feature
description: Start or resume a feature. Talks the concept through with the user in short exchanges, agrees the branch name and the version bump, creates the branch, sends repo scouts out in the background while the conversation narrows the feature down, then writes BRIEF.md and brief.json with the rounds and starts round 1. Use for any new feature, behaviour change or bug fix, whether the user names the skill or describes the work in prose; in a session with a feature in flight, it reports where it stands and resumes it.
argument-hint: <what to build>
model: opus
effort: medium
---

# /feature — Main Conceptualize

The first stage of a feature, in the main session. It ends with a written brief and round 1
running. Two gates belong to the user: the concept with its branch name and bump, and the
rounds.

| Stage | Who | Gate |
|---|---|---|
| A. Orient | you | — |
| B. Concept, branch name, bump | you and the user | the user confirms |
| C. Branch | you | — |
| D. Narrow down, scouts in the background | you, the user, `repo-scout` × 4 | the user agrees the rounds |
| E. Write the brief, start round 1 | you, then `/round` | — |

## One question at a time

This governs every turn of B and D. The scouts report while the user is reading and typing,
so the chat must never fill up with anything but the one open question.

- **A turn that waits for the user ends on exactly one question**: one sentence, one decision.
  No numbered or bulleted list of questions, no "also, …", no second question tucked into the
  explanation. With two things open, ask the one that decides more and keep the other for the
  next turn. The concept summary in B and the rounds proposal in D are each one question
  ("Go?", "Agree?") under a short summary.
- **A scout's report is not a turn for the user.** When one arrives, read it, add its findings
  to `BRIEF.md` under `## Scout findings` (left uncommitted; E commits it), and end the turn
  with no message at all. Never relay, summarise or quote a report in the chat, and never ask
  a new question because one arrived: the question already on screen stays the only one.
- **Use the findings on the user's next answer.** They shape the next single question, and a
  finding that changes something already said (a building block to reuse, a caller that would
  break, a bump that should be higher) gets one clause in that reply. If a report showed the
  pending question was moot or wrongly framed, say so in one line then and ask the corrected
  question as the turn's one question.
- **Propose the rounds only once all four reports are in**, so no report can land after them.
  If the conversation is done first, say in one line that you are waiting for the scouts and
  end the turn; propose when the last report arrives.

## A. Orient

- `git fetch origin`, `git status --short --branch`.
- A live feature already exists if any `development/**/brief.json` has status `diagnosis`,
  `concept`, `rounds` or `final` (`python .claude/hooks/dev_state.py` prints it). Then do not start a
  second one: report the feature, its rounds and the current step, and resume — `concept`
  here at the stage the brief says, `diagnosis` with `/fix`, `rounds` with `/round`, `final`
  with `/finish`.
- A request that reports something existing behaving wrongly is a defect: hand it to `/fix`,
  which diagnoses before anything is agreed. A new
  request that belongs to that feature goes into it; one that does not waits, or the user
  parks the live one (status `parked`).
- Read `STRUCTURE.md` before saying anything about the code.

## B. Concept (waits for the user)

A skill's model holds for the turn that invokes it, and this stage spans several turns:
invoke `/feature` again at the start of each turn of the conversation until round 1 starts.

Keep every reply short — three to six lines — so the conversation moves, and end it on one
question (see *One question at a time* below). Settle:

- **What** the feature does, as a caller would see it, and what it does not do.
- **Branch name** — `type/kebab-case` per `CLAUDE.md`: `feat/` for new behaviour, `fix/` for
  a defect, `refactor/` for the same behaviour restructured.
- **Bump** — `major`, `minor` or `patch` per the table in `CLAUDE.md`. Say why in one line.
  If the user's framing implies another level ("it's just an addition", "this will break
  callers"), name the mismatch.

Then put the three to the user in one short summary and end the turn on the question. "Go",
"yes", or a correction followed by "go" is the confirmation.

## C. Branch

- `git switch main`, `git pull --ff-only`, `git switch -c <branch>`.
- Create `development/<branch>/` with `brief.json` at status `concept`, the agreed `bump`,
  `current_round` 0 and an empty `rounds` list (schema in `development/README.md`), and a
  `BRIEF.md` holding the concept so far.
- Commit `feature: start <branch>` and `git push -u origin <branch>`.

A hosted session that starts on a `claude/<words>` branch commits nothing there: branch the
agreed name off `main`. Only if pushing that name is refused, fall back to the session's
branch and record the real name in `brief.json`'s `branch`; the folder keeps the agreed
name. Rounds then run on that branch directly (`/round` section 2 says how).

## D. Narrow it down (waits for the user)

Right after the branch exists, start four `repo-scout` agents **in the background**, in one
message, with `model: sonnet` and `effort: medium`, one angle each: `building-blocks`, `fit`,
`conventions`, `risks`. Brief each with the agreed concept and its angle.

Do not wait for them. Keep the conversation going, one question per turn: the exact
behaviour, the inputs and edge cases that matter, what is out of scope. Each scout's report is
folded in silently as it arrives, by the rules in *One question at a time*.

Then decide the **rounds**. A round is one coherent piece that can be built, tested and
merged on its own, small enough for one pass of the round workflow (roughly: a module or two
and their tests). Most features are one round; split only where the pieces really stand
alone, and order them so each builds on the last. Rounds never run in parallel.

Propose the rounds — a short name, a one-line summary each — and the feature's acceptance
criteria, and end the turn on the question.

## E. Write the brief and start (no gate)

- `BRIEF.md`: Goal; Out of scope; Acceptance criteria (numbered `F1`, `F2`, … observable,
  feature-level); Rounds (per round: name, scope, depends on); Building blocks and risks from
  the scouts, with `path:line` (the `## Scout findings` notes, sorted into these); Decisions (each question settled in the conversation, one
  line each). This is what every round reads, so write down everything the conversation
  decided — anything left only in the chat is lost to the rounds.
- `brief.json`: status `rounds`, `current_round` 1, every round at status `planned` with
  `n`, `name`, `slug`, `branch` (`<branch>--r<n>`), `summary`.
- Commit `feature: brief for <branch>`, push.
- Invoke `/round` for round 1. From here the rounds run without asking, except for questions
  a round escalates.
