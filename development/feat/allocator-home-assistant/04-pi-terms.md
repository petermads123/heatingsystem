# SectionAllocator ready for Home Assistant — P and I terms per room

<!-- claude-plan step=3 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/allocator-home-assistant` |
| Round | 4 |
| Branch | `feat/allocator-home-assistant` |
| Started | 2026-10-08 |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | pending |
| 4 | Verify | `/verify` | in `/build` | pending |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

Gates: accepted in advance by the user ("continue with round 2 on the same PR and the rest of
the rounds automatically from there", 2026-10-08); recorded and criticised as usual, and the
build still halts on anything that would amend section 1.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-coverage-and-holds.md` | Room coverage, per-room validation, normalisation; `hold`/`holds`; snapshot `coverage`/`history`/`hold`; 1.1.0 |
| 2 | `02-missing-temperature.md` | `None` as a room temperature |
| 3 | `03-change-layout.md` | `with_layout`: a new allocator with a new layout, state carried by name |

Not a step 8 recommendation: Change 5 of the spec, agreed in round 1's section 1 as round 4:

> Round 4 (Change 5): `error`, `p_term`, `i_term` of the last update on `Room` and on
> `PIController` (new read-only properties; no behaviour change).

Must not break: A1–A11, B1–B6, C1–C6 — in particular A9/B5 (a raising `update` changes
nothing), B1 (a `None` room is untouched), C1 (`with_layout` carries the last step's state), and
every `PIController` behaviour and its snapshot (`to_dict` keys unchanged).

---

## 1. Concept

### What this is

`PIController` and `Room` each expose three read-only properties describing the last PI step,
exactly as `update` computed them:

- `error` — `setpoint - measured`, with the setpoint in force for that step;
- `p_term` — `kp * error`;
- `i_term` — `ki * integral`, where *integral* is the **tentative** integral that step's raw
  output was computed from (the old integral plus this error), **whether or not anti-windup then
  committed it**. So `p_term + i_term` is exactly the raw PI sum before clamping, and the clamp of
  that sum is `pi_output`/`demand`.

All three are `None` before the first `update`, and again after `reset()` and `from_dict()`
(like `pi_output`). They are derived and are not added to `to_dict()`. A `Room` delegates to its
composed `PIController`. In the allocator, a `None` reading (round 2) leaves a room's three terms
at the last step's values; `with_layout` (round 3) carries them with the rest of the room's state;
a raising `update` restores them with the integral and demand.

### Why it is worth building

The consumer's dashboard shows each room's P term, I term and demand. Recomputing
`kp * (setpoint - measured)` and `ki * integral` outside is wrong: the setpoint or gains may have
changed since, and under anti-windup the committed integral is not the one the output used. Doing
the same on `PIController` keeps the two controllers alike, as the spec asks.

### Inputs and outputs

New read-only properties, each `-> float | None`: `PIController.error`, `PIController.p_term`,
`PIController.i_term`, `Room.error`, `Room.p_term`, `Room.i_term`. Nothing else changes signature.
In the non-finite guard case (extreme finite inputs, round 3 of `feat/fixed-output`) the terms are
reported as computed, which may be non-finite; `pi_output` is still 0.0. Terms are stored as
computed, `-0.0` included: they are diagnostics, not settings, so the package's `-0.0`
normalisation of settings does not apply (and normalising would break `p_term + i_term == raw`).

### How it connects to the rest of the repo

`pi_controller.py` (`update` writes the three alongside `_pi_output`; `reset`/`from_dict` clear
them) and `allocator.py` (`Room` delegates; `update`'s restore and `with_layout`'s carry include
them). `Modulator`, `_validation` and both snapshot formats are unchanged.

### Explicitly out of scope

- Any change to the control law, anti-windup or `PIController` behaviour beyond the new
  read-only properties.
- Adding the terms to `to_dict()` / `from_dict()`.
- A history of terms, or terms per section.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| D1 | After every `PIController.update`, `error == setpoint - measured` (setpoint in force for that step, including a per-call `setpoint`), `p_term == kp * error`, `i_term == ki * (previous integral + error)`, and when finite `clamp(p_term + i_term) == pi_output` — checked with `==` on hand-computed values across the linear region, both saturation directions (where the committed integral differs from the one `i_term` used), and a `fixed_output` hold. |
| D2 | The three read `None` before the first `update`, after `reset()` and after `from_dict()`; they are read-only (assignment raises `AttributeError`); `to_dict()` keys and values are unchanged. |
| D3 | A raising `PIController.update` (validation, or the modulator raising) leaves the three exactly as before. |
| D4 | `Room.error`/`p_term`/`i_term` equal its composed controller's after every allocator `update`, and equal those of a standalone `PIController` with the same gains, setpoint and readings (A3's twin, extended); a `None` reading leaves them unchanged; a raising allocator `update` restores them; `with_layout` carries them for matched rooms (`None` for new rooms). |
| D5 | Every existing behaviour is unchanged: the full round 1–3 and `PIController`/`Modulator` suites pass without edits to existing assertions. |
| D6 | Docstrings, `README.md` (dashboard use: read the terms rather than recompute them) and `STRUCTURE.md` describe the properties. |

### Open questions

None. Decided: `i_term` uses the tentative integral (so `p_term + i_term` is the raw sum);
non-finite terms are reported as computed; terms are not in the snapshot.

---

## 2. Plan

### Approach

Three private fields on `PIController` (`_error`, `_p_term`, `_i_term`, initialised `None`),
written in `update` in the same place as `_integral`/`_pi_output` — after the modulator call, so a
raise leaves them untouched — from the locals `error`, `self.kp * error`, `self.ki * new_integral`
already computed there (the raw sum is then `p + i`; compute `raw` from those two locals so the
identity is exact rather than re-derived). `reset` clears them; `from_dict` produces a fresh
controller, so they start `None`. Three read-only properties. `Room` gains three delegating
properties. In `SectionAllocator.update` the save/restore tuple grows to include the three fields;
in `with_layout` the carry copies them for matched rooms. Rejected: computing the terms lazily from
the stored integral (wrong under anti-windup — the point of the change); a single `terms` tuple
property (the spec and the dashboard name three values).

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/pi_controller/pi_controller.py` | changed | fields, properties, `update`/`reset` writes; showcase prints the terms |
| `src/heatingsystem/allocator/allocator.py` | changed | `Room` properties; restore and carry; showcase prints a room's terms |
| `tests/test_pi_controller.py` | changed | D1–D3 |
| `tests/test_allocator.py` | changed | D4 |
| `README.md`, `STRUCTURE.md` | changed | D6 |

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `PIController.error -> float \| None` | `pi_controller.py` | Read-only; `setpoint - measured` of the last `update`. | D1–D3 |
| `PIController.p_term -> float \| None` | `pi_controller.py` | Read-only; `kp * error` of the last `update`. | D1–D3 |
| `PIController.i_term -> float \| None` | `pi_controller.py` | Read-only; `ki *` the tentative integral the last `update`'s raw output used. | D1–D3 |
| `Room.error -> float \| None` | `allocator.py` | Read-only; delegates. | D4 |
| `Room.p_term -> float \| None` | `allocator.py` | Read-only; delegates. | D4 |
| `Room.i_term -> float \| None` | `allocator.py` | Read-only; delegates. | D4 |
| `main() -> None` | both modules | Each showcase prints the terms once. | D6 |

### Implementation guide

1. `PIController.__init__`: `self._error = self._p_term = self._i_term = None` (typed
   `float | None`) next to `_pi_output`.
2. `update`: `p = self.kp * error`, `i = self.ki * new_integral`, `raw = p + i` (same value as
   today's expression — `kp*error + ki*new_integral` evaluates exactly so). After the modulator
   call, write `_error`, `_p_term`, `_i_term` with `_integral`/`_pi_output`.
3. `reset`: clear the three. `from_dict`: nothing (fresh object) — confirm.
4. Three properties with docstrings stating the definitions and the `None` cases.
5. `Room`: three delegating properties. `SectionAllocator.update`: save and restore the three
   private fields alongside `_integral`/`_pi_output`. `with_layout`: copy the three for matched
   rooms.
6. Showcases: one line each. README (PIController section and SectionAllocator usage: the
   dashboard should read these), STRUCTURE.md rows.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Hand-computed `error`/`p_term`/`i_term` after each of several steps: linear region, saturated high with positive error (integral held, `i_term` uses the tentative one), saturated low, a per-call setpoint, a gain change between steps, a `fixed_output` hold; `clamp(p+i) == pi_output` when finite; the non-finite guard case reports terms as computed with `pi_output` 0.0; `kp=0.0` with a negative error stores `p_term` as `-0.0` (`math.copysign(1, p_term) == -1.0`). | D1 |
| T2 | `None` before first update, after `reset`, after `from_dict` (and JSON round trip); assignment raises `AttributeError`; `to_dict()` identical in keys and values to before this round (literal). | D2 |
| T3 | Raising `update` (bad `measured`, bad `setpoint`, monkeypatched modulator) leaves the three unchanged. | D3 |
| T4 | Allocator: `Room` terms == composed controller's == a standalone `PIController` twin's (dedicated room, many steps); `None` reading leaves them; failing solver restores them; `with_layout` carries them, `None` for a new room; after `SectionAllocator.from_dict(a.to_dict())` (direct and through JSON) every room's three terms are `None` while `with_layout` on the same source carries them; the twin equality holds under a hold. | D4 |
| T5 | Full suite green with no existing assertion edited. | D5 |

Coverage: D1–D6 each have Public API rows and test intents; every row cites a criterion.

### Risks

- If `raw = p + i` is not bit-identical to the existing `kp*error + ki*new_integral` for some input,
  existing exact-value tests would move: it is the same two products and one addition in the same
  order, so it is identical; if a test moves anyway, **halt** (D5 says nothing changes).
- `test_pi_controller.py` has literal `to_dict` and attribute-surface tests; new properties must not
  appear in `to_dict` — D2 pins it.
### Critique

plan-critic (verdict: accept with changes; `raw = p + i` confirmed bit-identical, save points
confirmed complete, no attribute-surface test to break):

1. `-0.0` in the terms was undecided — **applied**: stored as computed, `-0.0` included; T1 case
   added.
2. Room terms after `SectionAllocator.from_dict` had no test intent — **applied**: T4 extended.

---

## 3. Implementation notes

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | |
| Pushed to | |

---

## 8. Recommendations

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

