# SectionAllocator ready for Home Assistant — a room without a temperature

<!-- claude-plan step=8 status=done -->

| Field | Value |
|---|---|
| Feature | `feat/allocator-home-assistant` |
| Round | 2 |
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
| 8 | Recommend | `/recommend` | with the user | done |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

Gates: the user asked (2026-10-08, after PR #8 opened) to "continue with round 2 on the same
PR and the rest of the rounds automatically from there". That is their acceptance of steps 1
and 2 for rounds 2–4 in advance; the concept and plan below are recorded and criticised as
usual, and the build still halts on anything that would amend section 1.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-coverage-and-holds.md` | Room-coverage semantics with per-room validation and normalisation; `hold`/`holds` with held sections as constants; snapshot `coverage`/`history`/`hold`; version 1.1.0 |

This round is not a step 8 recommendation: it is Change 3 of the spec, agreed in round 1's
section 1 ("Later rounds on this branch") as round 2:

> Round 2 (Change 3): `None` accepted as a room's temperature; that room's PI takes no step
> (integral unchanged, demand stays at its last value, 0.0 before the first) and the
> allocation runs for every room.

What is already on the branch that this round must not break: round 1's A1–A11 — in
particular A3 (one-section room ≡ standalone `PIController`), A8 (PIs unaffected by holds),
A9 (a raising `update` changes nothing) and A10 (snapshot shape, unchanged by this round).

---

## 1. Concept

### What this is

`SectionAllocator.update` accepts `None` as a room's temperature, meaning "no reading this
step". That room's PI takes no step: its integral and its last PI demand are untouched. The
allocation still runs for every room, using that room's last demand — or 0.0 if the room has
never had a reading (fresh, or restored by `from_dict`). Every other room steps normally, and
every section still issues a command. The caller decides when a dead sensor has gone on too
long and can then hold the room's sections with `hold`.

### Why it is worth building

One dead sensor today stops every room's step, because `update` needs a finite temperature
for every room. The consumer's workaround — feeding the setpoint as the measurement — is
close to "no step" but not the same: it still runs the PI (the integral is held only because
the error is zero, and the P term drops to zero, changing the demand).

### Inputs and outputs

- `update(measured: Mapping[str, float | None]) -> dict[str, float]` — the value type widens
  to admit `None`; the return is unchanged.
- A room's key must still be present: `None` is an explicit "no reading", a missing key is
  still the `ValueError` it is today. Every other refusal (non-mapping, unknown room,
  non-numeric, `bool`, non-finite, overflow) is unchanged.
- `Room.demand` keeps its meaning — the clamped demand of the room's last PI step — so it
  stays at its last value through `None` steps and stays `None` until the room's first real
  reading. The 0.0 used by the allocation before a first reading is internal, not reported.

### How it connects to the rest of the repo

Only `allocator.py` (`update`, `Room`'s private step). `PIController` is not changed: a
`None` room simply does not call it. Holds, snapshots and the allocation are untouched.

### Explicitly out of scope

- Counting missing readings, timeouts, or closing a room's sections automatically (the
  caller's job, with `hold`).
- `None` anywhere else (setpoints, gains, `PIController.update`).
- Persisting the last demand in the snapshot: after `from_dict` a `None` room allocates with
  0.0 until its first reading, as `demand` is `None` there by round 1's A10.
- Changing the layout (round 3) and P/I terms (round 4).

### Acceptance criteria

| # | The finished feature... |
|---|---|
| B1 | Given `None` for a room, that room's integral and `demand` are exactly what they were before the call, while every other room steps exactly as it would without the `None` (twin comparison). |
| B2 | The allocation uses a `None` room's last demand: on the round-1 E layout (HS1–HS5 over R1–R4, as in `main()`, `kp=1.0`, `ki=0.0`, other rooms at the setpoint), a step with R3 at demand 0.5 followed by a step with R3 `None` (other rooms unchanged) allocates E1 again (HS3 1.0, HS4 ≈ 0.215). Before the room's first reading it uses 0.0, and `demand` stays `None`. |
| B3 | A room's integral and demand sequence over a run with `None` steps interleaved equals a standalone `PIController` (same gains, setpoint) fed only that room's non-`None` readings. |
| B4 | `None` for every room is accepted (each room keeps its last demand, every section still gets a command, holds still apply); a missing key still raises `ValueError`; every other `update` refusal is unchanged in class and message. |
| B5 | A raising `update` with some rooms `None` (bad temperature elsewhere, or a failing solver) leaves every room, hold, window and `duty` unchanged. |
| B6 | Docstrings, `README.md` and `STRUCTURE.md` describe `None` readings, and round 1's A1–A11 still hold. |

### Open questions

None. Decided: `Room.demand` stays `None` before a first reading (it reports the PI, not the
allocation's input); the last demand is not added to the snapshot.

---

## 2. Plan

### Approach

Minimal and local. In `update`, after the existing missing/unknown checks, validate each
temperature only when it is not `None` (`_validation.finite` as today, same path). In the
`try`, a `None` room does not call `_step`; its allocation demand is `room.demand` or `0.0`
when that is `None`. Nothing else moves: the save/restore block already covers integrals and
PI outputs, and a `None` room's are untouched anyway. Rejected: feeding the setpoint as the
measurement internally (the workaround the concept exists to replace); a separate
`skip`/`missing` argument (a second way to say the same thing, and the consumer already builds
the mapping per room).

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/allocator/allocator.py` | changed | `update` accepts `None`; docstrings; showcase gets a `None` case |
| `tests/test_allocator.py` | changed | B1–B5 tests |
| `README.md` | changed | "SectionAllocator usage": a `None` reading |
| `STRUCTURE.md` | changed | `update` row and test summary |

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `SectionAllocator.update(measured: Mapping[str, float \| None]) -> dict[str, float]` | `allocator.py` | `None` = no reading: no PI step for that room, allocation uses its last demand (0.0 before its first). Missing/unknown checks first as today; then each non-`None` value through `_validation.finite(f"measured[{name!r}]", ...)`. Docstring `Args`/`Raises` updated. | B1–B5 |
| `Room.demand -> float \| None` | `allocator.py` | Unchanged signature; docstring says it is the last PI step's demand, unchanged by a `None` reading. | B2 |
| `main() -> None` | `allocator.py` | Showcase gains one case: R3 `None` after a reading, duty unchanged from the previous step. | B6 |

### Implementation guide

1. `update`: build `temperatures: dict[str, float | None]`, `None` passed through, others via
   `_validation.finite` (same order, same paths).
2. Inside the `try`: `demand[name] = room._step(t)` if `t is not None`, else
   `room.demand if room.demand is not None else 0.0`. In the same commit, remove only the
   `None` row from the refusal parametrize list in `tests/test_allocator.py` (around line 691,
   `{"R1": 20.0, "R2": 20.0, "R3": None}` → `TypeError`) and add a positive test in its place:
   same allocator, R3 `None`, no raise, R3's `integral`/`demand` unchanged. Record in section 3
   that B4 reversed this case (a replacement, not a deletion).
3. Docstrings: `update` Args (`None` = no reading) and the `TypeError` line (a `None` is no
   longer refused); `Room.demand`.
4. `main()`: one labelled case after E1 — the same allocator, R3 `None`, print duty.
5. README usage section: one paragraph and a line of the example; `STRUCTURE.md` `update` row.
6. Tests (step 5 extends): B1–B5 as below. The one reversed refusal case is already
   replaced at entry 2, so the tree stays green from step 3.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Twin = same allocator history; on the `None` step the twin gets the same readings for every other room and any finite reading for the `None` room. Every other room's `integral` and `demand` equal the twin's; the `None` room's `integral` and `demand` are identical (`==`, `is None` when `None`) to before the call. Duty is not compared (B2's job). | B1 |
| T2 | E layout (not the test file's `ref()`), `kp=1.0`, `ki=0.0`, R1/R2/R4 at the setpoint, R3 20.5 then `None`: duty both steps HS3 1.0, HS4 ≈ 0.2154, HS5 0.0; fresh allocator with R3 `None` → R3 allocated as demand 0.0, `demand` is `None`; same after `from_dict`. | B2 |
| T3 | Interleaved `None` run equals a standalone `PIController` fed only the real readings (integral and `pi_output` per real step). | B3 |
| T4 | All rooms `None`; with holds; missing key still `ValueError`; every other refusal unchanged (class and message). | B4 |
| T5 | Raising `update` with `None` rooms present (NaN elsewhere; failing solver) leaves `to_dict()`, `duty`, `holds`, `history` unchanged. | B5 |
| T6 | Showcase runs; round-1 suite still green. | B6 |

Coverage: B1–B6 each have a Public API row and a test intent; every row cites a criterion.

### Risks

- The existing test passing `None` expecting `TypeError` reverses under this round — replaced
  at guide entry 2; not a halt.
- If anything in the round-1 suite depends on `None` being refused beyond those tests, it is
  the same reversal; not a halt.

### Critique

plan-critic (verdict: accept with changes; approach confirmed against the code):

1. B2/T2 underspecified the layout and gains (the test file's `ref()` gives HS4 0.5) —
   **applied**: B2 and T2 name the E layout, `kp=1.0`, `ki=0.0`, other rooms at the setpoint.
   B2's substance is unchanged; this pins the existing example.
2. T1 did not say what the twin is fed — **applied**: T1 rewritten.
3. The reversed refusal row goes red at step 3 — **applied**: moved to guide entry 2 with a
   positive replacement test.

---

## 3. Implementation notes

Guide entries 1-5 implemented as planned; no deviation from the Public API table.

- B4 reversed one refusal: `tests/test_allocator.py` parametrize row
  `{"R1": 20.0, "R2": 20.0, "R3": None}` -> `TypeError` was removed and replaced by
  `test_update_accepts_none_as_no_reading_and_leaves_that_room_untouched` (R3 `None`, no raise,
  R3's `integral`/`demand` unchanged). A replacement, not a deletion.
- `update` builds `temperatures: dict[str, float | None]`; inside the `try` a `None` room skips
  `_step` and allocates `room.demand` or 0.0. The save/restore block is unchanged.
- Docstrings (`update`, `Room.demand`), `main()` (a `None` R3 case after the release), README
  ("No reading" paragraph and a line in the example) and `STRUCTURE.md` (`update`, `Room.demand`,
  `main` rows) updated.
- Showcase output: R3 demand 0.5, duty HS3 1.0, HS4 0.2154 (E1 again).
- Local run: ruff and format clean, mypy clean after dropping a now-unused `type: ignore`,
  pytest 1517 passed.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 55 files already formatted |
| `mypy` | Success: no issues found in 21 source files |
| `pytest` | 1517 passed |
| Plan completeness | every signature in the Public API table exists as written: `SectionAllocator.update(measured: Mapping[str, float \| None]) -> dict[str, float]`, `Room.demand -> float \| None`, `main() -> None`; no missing, deviating or unplanned surface |
| `STRUCTURE.md` | in sync after one edit (below) |
| `python -m heatingsystem.allocator.allocator` | runs; expected RuntimeWarning; the "R3 has no reading" case prints R3 demand 0.5 and duty HS3 1.0, HS4 0.2154 (E1 again) |

structure-auditor: allocator.py entries matched the code; one finding, applied: the
`tests/test_allocator.py` summary now says a `None` temperature is accepted as no reading
(no raise, that room's `integral` and `demand` unchanged). Step 5 should extend that
sentence if it adds T1-T5.

---

## 5. Test log

| Intent | Test names | Result |
|---|---|---|
| T1 (B1) | `test_update_none_room_is_untouched_while_other_rooms_step_like_a_twin` (prior reading / fresh), `test_update_accepts_none_as_no_reading_and_leaves_that_room_untouched` (step 3) | pass |
| T2 (B2) | `test_update_none_reallocates_the_last_demand_on_the_e_layout`, `test_update_none_on_a_fresh_room_allocates_zero_and_reports_no_demand`, `test_update_none_after_from_dict_allocates_zero_not_the_pre_snapshot_demand`, `test_update_none_after_from_dict_keeps_the_integral_and_allocates_zero`, `test_update_none_reuses_a_saturated_last_demand_of_one`, `test_update_none_on_the_closed_form_path_uses_last_demand_or_zero` (2), `test_update_none_on_the_e3_closed_form_keeps_the_last_demand`, `test_update_none_defers_a_setting_change_to_the_next_real_reading` (2), `test_update_consecutive_none_steps_repeat_the_same_duty` | pass |
| T3 (B3) | `test_update_none_room_matches_a_standalone_pi_fed_only_real_readings` (5 sequences), `test_update_single_room_none_at_start_and_end_matches_a_standalone_pi` | pass |
| T4 (B4) | `test_update_none_for_every_room_on_a_fresh_allocator_issues_closed_commands`, `test_update_all_none_still_applies_holds_and_reuses_last_demands`, `test_update_refusals_with_none_rooms_present_are_unchanged_and_leave_no_trace` (12 rows incl. HA sentinel strings), `test_update_missing_key_is_not_no_reading_for_a_defaulting_mapping`, `test_update_refuses_an_unknown_room_even_when_its_value_is_none`, `test_update_error_names_the_bad_room_not_the_none_room` | pass |
| T5 (B5) | `test_update_none_before_a_first_reading_leaks_nothing_on_solver_failure`, `test_update_solver_failure_with_a_none_room_restores_every_room`, and the refusal table above (snapshot and history unchanged) | pass |
| T6 (B6) | `test_main_runs_and_reports_each_section`, whole round-1 suite, `test_to_dict_after_none_steps_round_trips_with_unchanged_integral` | pass |
| Designer extra | `test_update_reads_each_temperature_once` | pass (red before the fix) |

Run: ruff, ruff format, mypy clean; pytest 1557 passed (40 new).

Designer findings, applied or rebutted:

- Input-space 1-10, contract 1-12: applied as above (merged duplicates: both designers'
  all-`None`, from_dict, closed-form, setting-change and idempotency cases became one test
  each; contract 7's `"R2": True`/`"None"`/nan/overflow rows and input-space 11's sentinel
  strings joined one refusal table; contract 11 is the T1 twin).
- Contradiction (input-space): `update` read `values[name]` twice. **Real bug**, fixed in
  `allocator.py` (one read per room, then validate); test 12 was red first and is green.
- Contradiction (contract 1): setting changes on a `None` room take effect at the next real
  reading. Documented in the `Room.demand` docstring; pinned by the deferral test.
- Contradiction (contract 3 / input-space note): `Room.demand` is also `None` after
  `from_dict`. Added to the docstring; pinned by the two from_dict tests.
- Contradiction (contract 2): a modulator rejecting a duty runs outside the restore block.
  Pre-existing, unreachable through valid input, not this round's scope (B5 is about
  temperature and solver failures); rebutted as out of scope, left for `DEVELOPMENT.md`
  at step 8.
- Plan typo (stray table fragment in section 4): removed at step 7.

Edge cases considered and deliberately skipped:

- Numeric variants of non-`None` values (`Fraction`, `int`, `-0.0`): unchanged
  `_validation.finite` path, covered by round 1.
- Room-name text: unchanged this round.
- Purity of the caller's mapping: covered by the existing no-mutation test plus the
  `defaultdict` case.

---

## 6. Concept check

Audited against section 1 and the code as it stands (`allocator.py` `update` lines 597-688, `Room.demand`
lines 260-271, `main()`), not against section 2. Run: `pytest` 1557 passed; `python -m
heatingsystem.allocator.allocator` runs (expected `RuntimeWarning`).

| # | Criterion | Met | Evidence |
|---|---|---|---|
| B1 | A `None` room keeps its integral and `demand`; others step like a twin | yes | `update` skips `room._step` for a `None` reading (`allocator.py:657-664`); `test_update_none_room_is_untouched_while_other_rooms_step_like_a_twin` (prior reading and fresh), `test_update_accepts_none_as_no_reading_and_leaves_that_room_untouched`. |
| B2 | Allocation uses the last demand (E1 again); 0.0 before a first reading; `demand` stays `None` | yes | `allocator.py:661-664` (`room.demand`, else `0.0`); `test_update_none_reallocates_the_last_demand_on_the_e_layout`, `test_update_none_on_a_fresh_room_allocates_zero_and_reports_no_demand`, the two `from_dict` tests. Showcase output: R3 demand 0.5, duty HS3 1.0, HS4 0.2153846 (E1 again). |
| B3 | Interleaved run equals a standalone `PIController` fed the real readings | yes | `test_update_none_room_matches_a_standalone_pi_fed_only_real_readings` (5 sequences), `test_update_single_room_none_at_start_and_end_matches_a_standalone_pi`. |
| B4 | All `None` accepted; missing key still `ValueError`; other refusals unchanged | yes | `test_update_none_for_every_room_on_a_fresh_allocator_issues_closed_commands`, `test_update_all_none_still_applies_holds_and_reuses_last_demands`, `test_update_missing_key_is_not_no_reading_for_a_defaulting_mapping`, `test_update_refusals_with_none_rooms_present_are_unchanged_and_leave_no_trace`. Missing/unknown checks precede validation (`allocator.py:635-641`); the one 1.0.0 `None`-refusal row was replaced, as the concept's B4 requires. |
| B5 | A raising `update` with `None` rooms leaves everything unchanged | yes | `test_update_solver_failure_with_a_none_room_restores_every_room`, `test_update_none_before_a_first_reading_leaks_nothing_on_solver_failure`, the refusal table (snapshot and history unchanged). Save/restore block unchanged (`allocator.py:652-676`). |
| B6 | Docstrings, README, STRUCTURE.md describe `None`; A1-A11 hold | yes | `update` and `Room.demand` docstrings; README usage line 173-175 and the "No reading" paragraph (line 192); STRUCTURE.md `update`, `Room.demand`, `main` rows and the test summary (structure-auditor: in sync). Round-1 table below. |

Out of scope, checked: no counting or timeout of missing readings, no automatic closing; `None` accepted
only for a room temperature (`PIController`, `Room` setters and `main` setpoints untouched; `git diff main
--stat` shows no `pi_controller/` or `modulator/` change); the snapshot keys and shape are unchanged and
carry no last demand; no layout change and no P/I properties. Surface: the only public change is the
widened `update` value type; no new API. Connection: `None` flows only through `update`; `from_dict`
leaves `demand` `None` so a restored `None` room allocates 0.0, as the concept states. Showcase reads as a
worked example (named inputs, one call, a labelled result) and shows the `None` case.

Drift found, and what was done about it: none. One observation, not drift: the step 5 designers found
`update` read each temperature twice from a live mapping; fixed in step 5 and pinned by
`test_update_reads_each_temperature_once`. Step 5 also rebutted as out of scope a modulator that rejects a
duty after the restore block (pre-existing, unreachable through valid input); it belongs in
`DEVELOPMENT.md` at step 8, not here. The stray table fragment in section 4 was removed at step 7.

### Earlier rounds still hold

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| 1 | A1 | Reference layout constructs; room coverage sum above 1+1e-9 raises naming room and sum; several-room section may sum above 1; coverage outside `(0, 1]` names its path; other refusals unchanged | yes | Constructor untouched this round (diff only in `update`, `Room.demand` docstring, `main`); `test_construction_refuses_a_room_coverage_sum_above_one_naming_room_and_sum`, `test_coverage_sum_tolerance_boundary_is_one_nanounit`, `test_construction_refuses_a_bad_coverage_naming_its_path` green. |
| 1 | A2 | Normalised by each room's controlled total (E1, E3) | yes | Showcase prints E1 (HS3 1.0, HS4 0.2154) and E3 (HS1 0.4); `test_normalisation_e1_free_allocation_of_the_reference_layout`, `test_normalisation_e3_closed_form_is_relative_to_the_covered_part`. |
| 1 | A3 | One-section room `==` standalone floor-heating `PIController` | yes | `test_dedicated_room_produces_the_command_sequence_of_a_standalone_controller`, `test_equivalence_holds_for_a_dedicated_coverage_of_any_size`; real readings still go through `room._step` unchanged, `None` rooms are outside the comparison by design (B3). |
| 1 | A4 | KKT, scaling invariance, A5/A6, worked examples in coverage terms | yes | `_allocate` untouched; `test_allocation_satisfies_the_kkt_conditions_of_the_documented_cost`, `test_worked_examples_hold_within_a_thousandth` green. |
| 1 | A5 | `hold` validation, release, fresh `holds` | yes | `hold`/`holds` untouched; `test_hold_refuses_a_bad_level_naming_the_section_and_changes_nothing`, `test_hold_checks_the_section_before_the_level`, `test_holds_is_complete_fresh_and_in_constructor_order`. |
| 1 | A6 | Held duty is its level; free sections compensate; release returns to E1 | yes | Showcase E2 (HS4 0.5385) then release back to E1; `test_release_reallocates_exactly_like_a_fresh_allocator_e1`; `test_update_all_none_still_applies_holds_and_reuses_last_demands` shows holds apply with `None` rooms. |
| 1 | A7 | All-held component calls no solver | yes | `_allocate` untouched (`allocator.py:897-916`); no-solver test green. |
| 1 | A8 | Room PIs unaffected by holds | yes | Hold twin tests green; a hold never changes whether a room steps, and `None` skipping is independent of holds. |
| 1 | A9 | Raising `update` leaves holds, windows, rooms, `duty` unchanged | yes | `test_a_raising_update_leaves_holds_windows_rooms_and_duty_unchanged` green; the save/restore block is unchanged and a `None` room's saved state is its own. |
| 1 | A10 | Snapshot `coverage`/`history`/`hold`, holds restored, identical next 50 commands, 1.0.0 refused | yes | `to_dict`/`from_dict` untouched; `test_a_restored_allocator_with_holds_produces_the_same_next_fifty`, `test_from_dict_refuses_a_1_0_0_snapshot_naming_the_missing_keys`, `test_to_dict_after_none_steps_round_trips_with_unchanged_integral`. |
| 1 | A11 | Version 1.1.0; STRUCTURE.md and README describe coverage, normalisation, `hold`, burst | yes | `pyproject.toml` version 1.1.0, `test_the_package_version_is_1_1_0`; README coverage and hold paragraphs intact, `update` docstring still carries the burst note. |

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | `a9c3f64` Concept and plan: round 2 — a room without a temperature; `e5033c3` Plan accepted: round 2 — critique applied, start build; `a0489cf` Implement: round 2 — a room without a temperature; `dba2f5f` Verify: missing temperature; `e05ab78` Test: a room without a temperature (round 2); `9fc6c68` Concept check: a room without a temperature |
| Pushed to | `origin/feat/allocator-home-assistant` |
| Whole-tree gates | `ruff check .` clean, `ruff format --check .` 55 files formatted, `mypy` no issues in 21 source files, `pytest` 1557 passed |
| Diff review | round 2 touched only `allocator.py`, `tests/test_allocator.py`, `README.md`, `STRUCTURE.md` and this file; no stray or scratch files; every step 1-6 left a commit naming the round |

---

## 8. Recommendations

None. The one leftover (a section modulator raising outside `update`'s restore block) is
already a `DEVELOPMENT.md` note from round 1. Round 3 (Change 4) follows as agreed scope, not
as a recommendation.

---

## 9. Pull request

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

