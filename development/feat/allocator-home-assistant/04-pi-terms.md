# SectionAllocator ready for Home Assistant — P and I terms per room

<!-- claude-plan step=8 status=active -->

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
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | done |
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

Implemented as planned, guide entries 1-6; no deviation, Public API table unchanged.

- `PIController`: private `_error`, `_p_term`, `_i_term` (`float | None`, `None` at construction); `update` computes `p_term = kp * error`, `i_term = ki * new_integral`, `raw = p_term + i_term` and writes the three after the modulator call, with `_integral`/`_pi_output`; `reset` clears them; `from_dict` builds a fresh controller, so they start `None` (confirmed, no change). Three read-only properties.
- `Room`: three delegating properties. `SectionAllocator.update` saves and restores the three private fields with integral and `_pi_output`; `with_layout` copies them for matched rooms.
- Showcases print the terms; README (PIController and allocator dashboard use) and STRUCTURE.md updated.
- Sanity: ruff, mypy and the existing suite (1612 tests) pass unedited.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | First run: `README.md` would be reformatted (the new dashboard `print` in the allocator usage block was one line too long for ruff's markdown code-block formatting). Fixed with `ruff format README.md` (the call is wrapped over four lines); re-run: 57 files already formatted. |
| `mypy` | Success: no issues found in 21 source files |
| `pytest` | 1612 passed, no existing assertion edited |
| Plan completeness | every signature in the Public API table exists as written: `PIController.error`/`p_term`/`i_term` and `Room.error`/`p_term`/`i_term`, read-only properties `-> float | None`; both `main()` showcases print the terms. No Missing, Deviation or Unplanned rows. |
| `STRUCTURE.md` | structure-auditor: in sync apart from two stale `main()` descriptions, both applied (allocator showcase: terms now described with the R3 no-reading block; PIController showcase: terms follow the setpoint override), plus the optional `from_dict` row now says `pi_output`, `error`, `p_term` and `i_term` are `None`. |
| `python -m heatingsystem.pi_controller.pi_controller` | Runs; prints `last step: error=0.7000  p_term=0.2100  i_term=0.1365` after the setpoint override (RuntimeWarning expected). |
| `python -m heatingsystem.allocator.allocator` | Runs; R3 no-reading block prints `demand = 0.5`, `error = 0.5, p_term = 0.5, i_term = 0.0` (RuntimeWarning expected). |

---

## 5. Test log

Readers: `input-space` designer ran (15 cases, 4 contradictions); the `contract` designer failed on an API rate
limit, so the contract pass was done by the test author from D1-D6, the docstrings and the Public API table.

| Intent | Test names | Result |
|---|---|---|
| T1 / D1 | `test_terms_linear_region_match_hand_computation_over_several_steps`, `..._saturated_high_use_the_tentative_integral_not_the_held_one`, `..._saturated_low_...`, `test_i_term_at_exact_upper_clamp_...`, `test_i_term_at_exact_lower_clamp_...`, `test_terms_follow_a_per_call_setpoint`, `test_terms_use_the_gains_in_force_for_each_step`, `test_terms_are_stored_not_recomputed_after_settings_change`, `test_terms_are_pi_values_not_the_actuator_command` (both modes), `test_terms_under_a_fixed_output_hold_match_an_unfixed_twin`, `test_terms_are_floats_for_int_and_fraction_inputs`, `test_terms_finite_while_the_raw_sum_overflows`, `test_terms_report_nan_and_inf_as_computed_when_the_error_overflows`, `test_zero_gain_with_negative_error_stores_a_negative_zero_p_term`, `test_signed_zero_terms_from_negative_gains_keep_their_sign` | pass |
| T2 / D2 | `test_terms_are_none_before_the_first_update`, `..._after_reset`, `..._after_from_dict_directly_and_through_json`, `test_terms_are_read_only`, `test_to_dict_does_not_gain_the_terms` | pass |
| T3 / D3 | `test_a_raising_update_leaves_the_terms_unchanged`, `test_a_raising_per_call_setpoint_...`, `test_a_raising_first_update_...`, `test_modulator_raise_leaves_the_terms_unchanged`, `test_modulator_raise_with_a_per_call_setpoint_stores_it_but_not_the_terms` | pass |
| T4 / D4 | `test_room_terms_equal_a_standalone_twin_over_many_steps`, `..._under_a_hold_and_saturation`, `test_room_terms_use_the_setpoint_and_gains_in_force_for_the_step`, `test_room_terms_are_read_only`, `test_room_terms_are_floats`, `test_a_none_reading_*` (3), `test_a_failing_first_update_restores_the_terms_to_none`, `test_a_failing_solver_restores_the_terms_to_the_previous_update` (Exception and KeyboardInterrupt), `test_a_later_room_step_raising_restores_earlier_rooms_terms`, `test_a_validation_failure_leaves_the_terms_unchanged`, `test_with_layout_*` (carry, no-arg, never updated, renamed/re-added, non-finite bit-for-bit, copy), `test_from_dict_leaves_the_terms_none_while_with_layout_carries_them`, `test_the_snapshot_does_not_contain_the_terms` | pass |
| T5 / D5 | whole suite, no existing assertion edited | 1673 passed (1612 before) |
| D6 | contradictions 1 and 2 below (docstring fixes) | applied |

Designer cases: 1-15 applied (3 and 4 together; 5-7 as restore tests; 8 stored-not-recomputed; 9 modulator
raise with setpoint; 10 signed zeros; 11-13 `with_layout`/`None`; 14 floor heating; 15 types).
Contradictions: (1) `PIController.error` docstring promised `-0.0`, unreachable: reworded (production docstring
fix, applied; `error` never `-0.0` is asserted). (2) `SectionAllocator.update` docstring omitted the restored terms:
applied. (3) restore window does not cover `modulator.command(duty)` after the `try`: unreachable today (duty in
[0, 1]); no test, noted here. (4) D1's `i_term` expression order: tests use `ki * (previous + error)` order.

No production bug found. Only change outside tests: the two docstrings.

Edge cases considered and deliberately skipped:

- Contradiction 3 (restore gap after the try): unreachable with duty in [0, 1]; testing needs a monkeypatched
  modulator and would pin an implementation detail.
- Empty, text and malformed input for the properties: they take no input; bad input to `update` is T3.
- Idempotency of reading a property: trivial.

---

## 6. Concept check

Audited against section 1 only, then the code as it stands (`ruff check` and `ruff format --check` clean, `mypy`
clean, `pytest` 1673 passed; both showcases exit 0). structure-auditor: in sync.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| D1 | After every `update`: `error == setpoint - measured`, `p_term == kp * error`, `i_term == ki * (previous integral + error)`, `clamp(p+i) == pi_output` when finite; linear, both saturations, hold | yes | `pi_controller.py` `update`: `p_term = self.kp * error`, `i_term = self.ki * new_integral`, `raw = p_term + i_term`, written with `_integral`/`_pi_output` after the modulator call. Tests: `test_terms_linear_region_match_hand_computation_over_several_steps`, `..._saturated_high_use_the_tentative_integral_not_the_held_one`, `..._saturated_low_...`, `test_i_term_at_exact_upper_clamp_...`, `test_terms_follow_a_per_call_setpoint`, `test_terms_under_a_fixed_output_hold_match_an_unfixed_twin`, `test_zero_gain_with_negative_error_stores_a_negative_zero_p_term`, non-finite cases |
| D2 | `None` before first update, after `reset()`, after `from_dict()`; read-only; `to_dict()` unchanged | yes | `__init__` sets the three `None`; `reset` clears them; `from_dict` builds a fresh object. `test_terms_are_none_before_the_first_update`, `..._after_reset`, `..._after_from_dict_directly_and_through_json`, `test_terms_are_read_only`, `test_to_dict_does_not_gain_the_terms`; `git diff 0d07729 HEAD` shows `to_dict` untouched |
| D3 | A raising `PIController.update` leaves the three as before | yes | The writes sit after `self._modulator.command(...)`. `test_a_raising_update_leaves_the_terms_unchanged`, `test_a_raising_per_call_setpoint_...`, `test_a_raising_first_update_...`, `test_modulator_raise_leaves_the_terms_unchanged` |
| D4 | `Room` terms equal the composed controller's and a standalone twin's; `None` reading leaves them; raising update restores them; `with_layout` carries them (`None` for new rooms) | yes | `Room.error/p_term/i_term` delegate to `self._pi`; `SectionAllocator.update` saves and restores the three in `saved`; `with_layout` copies them for matched rooms. `test_room_terms_equal_a_standalone_twin_over_many_steps`, `..._under_a_hold_and_saturation`, `test_a_none_reading_*`, `test_a_failing_solver_restores_the_terms_to_the_previous_update`, `test_a_later_room_step_raising_restores_earlier_rooms_terms`, `test_with_layout_*`, `test_from_dict_leaves_the_terms_none_while_with_layout_carries_them` |
| D5 | Every existing behaviour unchanged; suites pass without edits to existing assertions | yes | `git diff -U0 0d07729 HEAD -- tests` has zero removed lines (additions only: +279 in `test_allocator.py`, +293 in `test_pi_controller.py`); production diff changes `raw` only by splitting the same two products and one addition (bit-identical); full suite 1673 passed, 1612 before |
| D6 | Docstrings, `README.md` and `STRUCTURE.md` describe the properties | yes | Docstrings on all six properties and on `update`/`reset`/`SectionAllocator.update`; README PIController and allocator blocks tell the dashboard to read the terms; STRUCTURE.md rows for all six, `update`, `reset`, `from_dict`, `with_layout`, `main()`; showcases print `last step: error=0.7000  p_term=0.2100  i_term=0.1365` and `R3 error = 0.5, p_term = 0.5, i_term = 0.0` |

Drift found, and what was done about it: none. Out of scope held: no control-law change, nothing in
`to_dict`/`from_dict`, no term history, no per-section terms. The only surface added is the six properties.
Step 5's note that the restore window does not cover `modulator.command(duty)` after the `try` is unreachable
with duty in [0, 1] and unchanged by this round.

### Earlier rounds still hold

Re-read against the code as it stands, not only the green tests.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| 1 | A1 | Layout validation, room sum, coverage range | yes | Constructor and `_normalise` untouched by `git diff`; round 1 construction tests pass |
| 1 | A2 | Normalisation by controlled total | yes | Showcase E1/E3 unchanged; allocation code untouched |
| 1 | A3 | Dedicated room equals standalone floor-heating controller | yes | `raw = p_term + i_term` is the same expression value; `test_dedicated_room_produces_the_command_sequence_of_a_standalone_controller` passes |
| 1 | A4 | Cost, KKT, scaling, worked examples | yes | `_allocate` untouched; KKT and example tests pass |
| 1 | A5 | `hold` validation, `holds` | yes | `hold`/`holds` untouched |
| 1 | A6 | Held duty, compensation, release | yes | Showcase E2 and release unchanged; tests pass |
| 1 | A7 | All-held component calls no solver | yes | `if not free: continue` untouched; test passes |
| 1 | A8 | Room PI keeps stepping during a hold | yes | `update` step loop unchanged; twin test passes |
| 1 | A9 | Raising `update` changes nothing | yes | Restore now covers the three terms as well; round 1 test passes plus the new restore tests |
| 1 | A10 | Snapshot shape, restore, 1.0.0 refused | yes | `to_dict`/`from_dict` untouched; `test_the_snapshot_does_not_contain_the_terms` |
| 1 | A11 | Version 1.1.0 and docs | yes | `pyproject.toml` untouched; README/STRUCTURE only extended |
| 2 | B1 | `None` room: integral and demand untouched | yes | The `None` path skips `_step`, so the terms are untouched too; `test_a_none_reading_*` |
| 2 | B2 | Allocation uses last demand | yes | Untouched; showcase prints `R3 demand = 0.5` |
| 2 | B3 | Equals a standalone fed only real readings | yes | Tests pass; terms compared against the same twin |
| 2 | B4 | All-`None` accepted, refusals unchanged | yes | Validation untouched; tests pass |
| 2 | B5 | Raising `update` with `None` rooms changes nothing | yes | Tests pass; restore extended |
| 2 | B6 | Docs describe `None` readings | yes | README/STRUCTURE keep the `None` text, now with the terms |
| 3 | C1 | `with_layout()` copy with identical next 50 commands | yes | Three more fields copied; `to_dict` identical; tests pass |
| 3 | C2 | Priority/evenness/coverage change carries state | yes | Carry loop extended only; tests pass |
| 3 | C3 | `history_length` trimming | yes | Untouched; tests pass |
| 3 | C4 | Add/remove rooms and sections | yes | New rooms keep `None` terms; `test_with_layout_*` covers renamed/re-added rooms |
| 3 | C5 | Constructor refusals propagate, original unchanged | yes | Untouched; tests pass |
| 3 | C6 | Docs describe `with_layout` | yes | `with_layout` row now names the carried terms |

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | `3d1c9d7` Recommend round 3; concept and plan: round 4 — P and I terms per room; `3f9b69d` Plan accepted: round 4 — critique applied, start build; `a709972` Implement: round 4 — P and I terms per room; `49a6b44` Verify: P and I terms per room; `ea4fbf4` Test: PI terms (round 4); `37058cf` Concept check: P and I terms per room; plus the `Ship: SectionAllocator ready for Home Assistant — P and I terms per room` commit that records this section |
| Pushed to | `origin/feat/allocator-home-assistant` |
| Whole-tree gates | `ruff check .` clean; `ruff format --check .` 57 files formatted; `mypy` no issues in 21 source files; `pytest` 1673 passed |
| Diff review | No stray, scratch or sample files in the round's diff; working tree clean before this step. Every step 1 to 6 left a commit naming the round file. |

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

