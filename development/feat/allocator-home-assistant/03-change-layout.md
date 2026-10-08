# SectionAllocator ready for Home Assistant — change the layout keeping state

<!-- claude-plan step=8 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/allocator-home-assistant` |
| Round | 3 |
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
| 1 | `01-coverage-and-holds.md` | Room-coverage semantics, per-room validation, normalisation; `hold`/`holds`; snapshot `coverage`/`history`/`hold`; 1.1.0 |
| 2 | `02-missing-temperature.md` | `None` as a room temperature: no PI step, allocation with the last demand (0.0 before the first) |

Not a step 8 recommendation: Change 4 of the spec, agreed in round 1's section 1 as round 3:

> Round 3 (Change 4): a method returning a new allocator with a new layout and/or
> `history_length`, carrying over each room's settings and integral, and each section's window
> (trimmed to its newest slots) and hold, where names match; `duty`/`demand` not reset to `None`.

Must not break: round 1's A1–A11 and round 2's B1–B6 — in particular A10 (snapshot) and B2
(a `None` room allocates its last demand, which this round now carries across a layout change).

---

## 1. Concept

### What this is

`SectionAllocator.with_layout(rooms=None, sections=None, *, history_length=None)` returns a
**new** allocator built from the given layout, any argument left `None` keeping the current
one. Everything that can carry over does, matched by name:

- **Rooms** present in both: `setpoint`, `kp`, `ki`, `integral` and the last `demand`. The
  new `priority`/`evenness` come from the new `rooms` mapping. A room new to the layout starts
  like a freshly constructed one (the constructor's defaults `kp=0.3`, `ki=0.015`,
  `setpoint=21.0`, integral 0, `demand` `None`); the caller sets it through `rooms[...]`.
- **Sections** present in both: the command window and the hold. A window longer than the new
  `history_length` keeps its newest slots; a shorter one keeps all of them. A new section starts
  with an empty window and no hold.
- **`duty`** carries over when the new layout has exactly the same section names (each section's
  last duty); otherwise it is `None` until the next `update` (a new section has no last
  allocation, and a removed one would be stale).

The original allocator is not changed. The new layout is validated exactly as the constructor
validates it (same classes, same messages and paths), and a refused layout produces nothing.

### Why it is worth building

Priorities, evenness, coverages and the window length are user settings in Home Assistant
and change at runtime. Today the consumer must edit a `to_dict()` snapshot by key names
(trimming windows itself) and call `from_dict()`, after which `duty` and every `demand` read
`None` until the next update — which also makes a `None`-reading room (round 2) allocate 0.0
instead of its last demand.

### Inputs and outputs

- In: `rooms: Mapping[str, Mapping[str, float]] | None`, `sections: Mapping[str, Mapping[str,
  float]] | None` (the constructor's shapes, coverage meaning from round 1), `history_length: int
  | None`.
- Out: a new `SectionAllocator` (`Self`, so a subclass gets its own type).
- Raises what the constructor raises for the same layout. A `sections` that names a room the
  (new or kept) `rooms` lacks, or leaves a room uncovered, is refused as in the constructor.

### How it connects to the rest of the repo

Only `allocator.py`. It builds through the constructor, then restores carried state through the
same private paths `from_dict` uses (room setters and integral; `Modulator._from_dict` for windows
and holds). `PIController`, `Modulator` and the snapshot format are unchanged.

### Explicitly out of scope

- `PIController.with_history_length` (the spec mentions it; `PIController` is out of scope).
- Renaming a room or section while keeping its state (names are the match key).
- Mutating the allocator in place.
- P/I terms (round 4).

### Acceptance criteria

| # | The finished feature... |
|---|---|
| C1 | `with_layout()` with no arguments returns a different object whose `to_dict()`, `duty`, `holds` and every room's `demand` equal the original's, and the two issue identical commands and duties over the next 50 updates (including `None` readings). The original is unchanged by the call. |
| C2 | Changing only priorities, evenness or coverages carries every room's `setpoint`, `kp`, `ki`, `integral` and `demand`, every section's window and hold, and `duty`; its next 50 commands, with a real reading for every room, equal those of `from_dict` on the same snapshot edited to the new layout; and on a `None` reading a carried room allocates its last demand (where that `from_dict` rebuild allocates 0.0). |
| C3 | `history_length=n`: every window keeps its newest `min(len, n)` slots and `history_length` reads `n`; the next commands, with real readings, equal those of a `from_dict` rebuild with each window trimmed to its newest `n` slots and `history_length` `n`. |
| C4 | Adding a room or section: matched names keep their state; a new room reads the constructor defaults with integral 0.0 and `demand` `None`; a new section has an empty window and no hold; `duty` is `None` when the section names changed. Removing a room or section drops its state. |
| C5 | A layout or `history_length` the constructor refuses raises the constructor's exception (class and message) and leaves the original unchanged. |
| C6 | Docstrings, `README.md` (in "SectionAllocator usage", the sentence that the layout cannot change after construction becomes the `with_layout` paragraph; the PIController window-trimming advice stays) and `STRUCTURE.md` describe `with_layout`, and nothing still says the allocator's layout is fixed; rounds 1 and 2 still hold. |

### Open questions

None. Decided above: new rooms take the constructor defaults (no extra keyword arguments);
`duty` carries only when the section names are unchanged.

---

## 2. Plan

### Approach

One public method, built from what exists. `with_layout` resolves each `None` argument to the
current value (`self.sections`, a rebuilt `{name: {"priority", "evenness"}}` mapping,
`self.history_length`), constructs `type(self)(rooms, sections, history_length=n)` — which does all
validation, so C5 is the constructor's own behaviour — then copies state into the new object by
name: room settings through the public setters and integral/demand through the private
`_pi._integral`/`_pi._pi_output` (as `update`'s restore does); each matched section's modulator
replaced via `Modulator._from_dict` with the newest `n` window slots and the hold as
`fixed_output` (as `from_dict` does); `duty` copied when `list(new sections) == list(old)`.
Rejected: a mutate-in-place `set_layout` (the components, matrices and modulators would all need
rebuilding anyway, and a failure halfway would leave a half-changed controller); building the new
allocator through `to_dict`/`from_dict` internally (loses `demand`/`duty`, which is the point).

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/allocator/allocator.py` | changed | `with_layout`; showcase case |
| `tests/test_allocator.py` | changed | C1–C5 tests |
| `README.md` | changed | layout-change paragraph |
| `STRUCTURE.md` | changed | new row, test summary |

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `SectionAllocator.with_layout(rooms: Mapping[str, Mapping[str, float]] \| None = None, sections: Mapping[str, Mapping[str, float]] \| None = None, *, history_length: int \| None = None) -> Self` | `allocator.py` | New allocator with the given layout, carrying state by name as in section 1; raises what the constructor raises. | C1–C5 |
| `main() -> None` | `allocator.py` | Showcase gains one case: raise R3's priority and shorten the window with `with_layout`, print that R3's integral and the duty carried. | C6 |

### Implementation guide

1. Resolve arguments: `rooms` default `{name: {"priority": r.priority, "evenness": r.evenness}}`;
   `sections` default `self.sections`; `history_length` default `self.history_length`.
2. `new = type(self)(rooms, sections, history_length=history_length)` — no shared-gain keywords
   (new rooms take the defaults).
3. For each room name in both: assign `setpoint`, `kp`, `ki` through the `Room` setters, then
   `new._rooms[name]._pi._integral` and `._pi_output` directly (the values are already valid).
4. For each section name in both: `new._modulators[name] = Modulator._from_dict({"mode":
   "floor_heating", "history_length": n, "fixed_output": old hold, "history": list(old
   history)[-n:]})`.
5. `duty`: `new._duty = dict(self._duty)` when `self._duty is not None` and the section name
   lists are equal (order included); else stays `None`.
6. Docstring with Args/Returns/Raises, including "adding or removing a room usually needs both
   `rooms` and `sections`" and that errors are the constructor's, unchanged, even when the path
   names a defaulted argument. README: in "SectionAllocator usage", replace the sentence that the
   layout cannot change after construction (a new allocator being a full reset, around lines
   220–221) with the `with_layout` paragraph; leave the PIController window-trimming bullets
   (around lines 116–119) alone — `PIController.with_history_length` is out of scope. Also update
   the `SectionAllocator` class docstring ("Built once from a fixed layout; … read-only") and
   STRUCTURE.md's module description ("Built once from a fixed layout"). Showcase.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | No-arg call: new object, equal `to_dict`/`duty`/`holds`/demands, identical next 50 commands and duties (with `None` readings and a hold), original unchanged (snapshot before == after). | C1 |
| T2 | Priority/evenness/coverage change: everything carried; (a) next 50 commands, real readings for every room, == `from_dict` of the edited snapshot; (b) separately, one `None` step for a carried room: `with_layout` allocates its last demand, the `from_dict` rebuild allocates 0.0. | C2 |
| T3 | `history_length` shorter (newest slots kept), longer (all kept, not full), equal; `history_length` reads the new value; next commands (real readings only) == trimmed `from_dict` rebuild; a fractional hold continues the same pattern as the trimmed rebuild. | C3 |
| T4 | Add/remove rooms and sections: matched state kept, new room defaults (`kp` 0.3, `ki` 0.015, `setpoint` 21.0, integral 0.0, demand `None`), new section empty and unheld, `duty` `None` when names change, and a reordered section list also gives `duty` `None`; removed state gone. | C4 |
| T5 | Every constructor refusal through `with_layout` (room sum > 1, unknown room in sections, uncovered room, bad `history_length` incl. `bool` and 0) raises the identical class and message; `rooms` without R3 plus the default `sections` raises the constructor's unknown-room error (its path names `sections[...]` though the caller did not pass it — unchanged, by C5); original `to_dict()` unchanged. | C5 |
| T6 | Subclass: `with_layout` returns the subclass; showcase runs; rounds 1–2 suites green. | C6 |

Coverage: C1–C6 each have a Public API row and test intents; every row cites a criterion.

### Risks

- If carrying `demand` through `_pi._pi_output` breaks a round-2 assumption (e.g. `Room.demand`
  documented as `None` after a rebuild), that docstring needs a clause, not a halt: `with_layout`
  is not `from_dict`.
- If a reordered-but-equal section set should carry `duty`, that is a concept question — the
  concept says the name lists must be equal including order; follow it.
### Critique

plan-critic (verdict: accept with changes):

1. T2/T3 compared against `from_dict` while also feeding `None` readings, which differ by
   design — **applied**: C2/C3 and T2/T3 compare with real readings only; C2 gains the separate
   `None` contrast (carried demand vs 0.0). C2's substance is unchanged; this pins it.
2. C6 did not name which README text changes — **applied**: the "SectionAllocator usage" fixed-
   layout sentence, the class docstring and STRUCTURE.md's description; the PIController
   trimming advice stays.
3. Defaulted-argument error paths — **applied**: documented as the constructor's, unchanged;
   docstring line on passing both `rooms` and `sections`; T5 case added.
4. T4's open musing — **applied**: a reordered section list gives `duty` `None`.

---

## 3. Implementation notes

Implemented as planned; no deviation from section 2 and no criterion affected.

- `SectionAllocator.with_layout` added to `allocator.py` exactly per the Public API row; guide
  entries 1-5 transcribed in order (defaults resolved, construction through
  `type(self)(rooms, sections, history_length=...)`, room settings via setters and `_pi._integral`/
  `_pi._pi_output`, matched sections rebuilt with `Modulator._from_dict` on the newest `n` slots
  and the hold, `duty` copied only when the section name lists are equal in order).
- Entry 6: method docstring, class docstring (no longer says the layout is fixed), showcase case
  (R3 priority 0.5, window shortened to 2), README "Changing the layout" paragraph replacing the
  fixed-layout sentence (PIController trimming advice untouched), STRUCTURE.md row and module text.
- Existing suite (1557 tests), ruff and mypy are green; tests for `with_layout` are step 5's.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 56 files already formatted |
| `mypy` | Success: no issues found in 21 source files |
| `pytest` | 1557 passed |
| Plan completeness | `SectionAllocator.with_layout(rooms=None, sections=None, *, history_length=None) -> Self` exists as written; `main()` showcase gained the `with_layout` case. No missing, deviating or unplanned public surface. |
| `STRUCTURE.md` | in sync after one edit (below) |
| `python -m heatingsystem.allocator.allocator` | runs, exit 0; the `with_layout` case prints R3 integral kept 1.0, duty and holds carried, HS2 window `(0.0, 1.0)`; the invalid-layout case still prints the constructor's error |

structure-auditor findings and action:

- `duty` row lacked the `with_layout` clause: STRUCTURE.md row edited to "`None` before the first `update`, after `from_dict`, and after a `with_layout` whose section names or order differ."
- Module docstring (allocator.py lines 3-4) still said "built once from a fixed physical layout" (C6): reworded to "built from a physical layout (changeable through :meth:`SectionAllocator.with_layout`)".
- Also extended the `duty` property docstring with the same three `None` cases.
- grep of README.md and allocator.py for "fixed layout"/"read-only layout"/"cannot change": no remaining fixed-layout claim. README line 220-221 says an allocator's layout "is read-only, but `with_layout` returns a new allocator", which is accurate and is the C6 paragraph.

---

## 5. Test log

| Intent | Test names | Result |
|---|---|---|
| T1 (C1) | `test_with_layout_without_arguments_returns_an_equal_but_distinct_allocator`, `..._issues_identical_commands_including_none_readings`, `..._on_a_never_updated_allocator_has_no_duty_or_demand`, `..._chained_calls_equal_a_single_call`, `..._carries_a_zero_demand_as_zero_not_none`, isolation: `..._shares_no_state_with_the_original`, `..._original_updates_do_not_reach_the_new_allocator`, `..._does_not_alias_the_callers_mappings` | pass |
| T2 (C2) | `..._layout_change_carries_all_state`, `..._matches_the_from_dict_rebuild_for_real_readings`, `..._none_reading_allocates_the_carried_demand_unlike_from_dict`, `..._coverage_only_change_still_carries_duty_when_names_match`, `..._duty_carries_for_reordered_rooms_and_mapping_proxies`, `..._duty_carries_when_only_history_length_changes`, `..._none_reading_after_a_coverage_change_reuses_the_carried_demand` | pass |
| T3 (C3) | `..._keeps_the_newest_slots_at_every_window_boundary` (4), `..._a_longer_window_is_not_full_and_a_shrink_is_irreversible`, `..._history_length_matches_the_trimmed_from_dict_rebuild` (5), `..._a_fractional_hold_continues_the_pattern_over_a_grown_window`, `..._history_length_limits_match_the_constructor` | pass |
| T4 (C4) | `..._new_room_and_section_start_fresh_and_names_keep_state`, `..._new_room_takes_the_class_defaults_not_the_originals_gains`, `..._removing_a_room_and_its_section_drops_their_state`, `..._a_removed_then_readded_room_and_section_have_no_ghost_state`, `..._a_section_keeps_its_state_after_its_room_is_removed`, `..._reordered_sections_give_no_duty_but_keep_state_by_name`, `..._room_names_match_exactly` | pass |
| T5 (C5) | `..._refuses_what_the_constructor_refuses_and_changes_nothing` (13 cases), `..._rooms_without_a_room_the_default_sections_use_names_sections_path`, `..._an_empty_mapping_is_not_treated_as_keep`, `..._history_length_is_keyword_only`, `..._failed_call_leaves_duty_and_demands_and_next_commands_alone`, `..._carried_duty_survives_a_failed_first_update` | pass |
| T6 (C6) | `..._returns_the_subclass`, `..._does_not_mutate_the_callers_arguments`, `..._docs_no_longer_claim_a_fixed_layout`; showcase and rounds 1-2 suites unchanged and green | pass |

Result: 55 new tests (all `with_layout`), whole suite 1612 passed; ruff, format, mypy clean.

Merge of the two designer reports (input-space 12 cases, contract 14): all duplicates folded
(no-aliasing both ways, new-room defaults, removed-then-readded, reordered-section duty, `duty` with
`history_length` only, falsy/empty arguments, chained calls, keyword-only, `False`/limits, failed-call
twin comparison) and applied. Applied additions beyond the plan: `run50`-based twins instead of
spot checks.

Contradictions, on the record:

1. README "nothing is reset to `None`" (contract 1) — confirmed against the code and its own paragraph.
   Fixed the README: it now says a carried room's `demand` is kept, and the `duty` sentence gains
   "and in the same order (otherwise it is `None` until the next `update`)" (contract 3). A test
   (`..._docs_no_longer_claim_a_fixed_layout`) pins both.
2. Stale `duty` when names match but coverage/rooms change (input-space 1) — rebutted. Section 1 decided
   it ("`duty` carries when the section names are unchanged"), the docstring says it, and the test
   `..._coverage_only_change_still_carries_duty_when_names_match` pins the decision. It is not a halt:
   it is the agreed behaviour, not an undecided case. The next `update` replaces `duty`.
3. New room takes class defaults, not the original's gains (contract 2) — rebutted as a defect: section 1
   decided the constructor defaults and the docstring states the numbers; pinned by
   `..._new_room_takes_the_class_defaults_not_the_originals_gains`.
4. Subclass with an extra required constructor argument (input-space 2, contract 4) — rebutted: `from_dict`
   has the same coupling and a subclass with a different signature is out of scope of the concept; tested
   with a signature-preserving subclass only.
5. Error order (history_length checked last) — no contradiction; pinned by the `bad_layout_wins_over_bad_history`
   refusal case.

Bugs found in production code: none. No production code changed this step; README wording fixed (C6).

Edge cases considered and deliberately skipped, with reasons:

- A subclass whose `__init__` needs extra arguments: not supported by `from_dict` either, out of scope.
- Numeric validation of `kp`/`ki`/`setpoint` values on carry: the values are already validated in the old allocator.
- Rename of a room or section keeping state: out of scope (section 1); only the exact-name match is pinned.
- `PIController.with_history_length`: out of scope (section 1).

---

## 6. Concept check

Audited against section 1 only, with the code as it stands (`allocator.py:875-960`), the 1612-test
suite green, and the showcase run.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| C1 | No-arg call: distinct object, equal state, identical next 50 commands, original unchanged | yes | `with_layout` resolves every `None` to the current value (`allocator.py:921-929`) and carries rooms, windows, holds, `duty` and `demand`. `test_with_layout_without_arguments_returns_an_equal_but_distinct_allocator`, `..._issues_identical_commands_including_none_readings`, `..._shares_no_state_with_the_original`, `..._original_updates_do_not_reach_the_new_allocator`. |
| C2 | Priority/evenness/coverage change carries everything; equals `from_dict` for real readings; `None` reading allocates the carried demand | yes | Carry loop `allocator.py:931-951` (`setpoint`/`kp`/`ki` via setters, `_integral`/`_pi_output` directly, `duty` copied at 952). `test_with_layout_layout_change_carries_all_state`, `..._matches_the_from_dict_rebuild_for_real_readings`, `..._none_reading_allocates_the_carried_demand_unlike_from_dict`, `..._coverage_only_change_still_carries_duty_when_names_match`. |
| C3 | `history_length=n`: newest `min(len, n)` slots kept, property reads `n`, equals trimmed `from_dict` rebuild | yes | `"history": list(kept.history)[-new._history_length:]` into `Modulator._from_dict` (`allocator.py:945-951`). `test_with_layout_keeps_the_newest_slots_at_every_window_boundary`, `..._history_length_matches_the_trimmed_from_dict_rebuild`, `..._a_fractional_hold_continues_the_pattern_over_a_grown_window`. Showcase: HS2 window `(0.0, 1.0)` after shrinking to 2. |
| C4 | Add/remove rooms and sections: matched keep state, new room defaults, new section empty and unheld, `duty` `None` when names change, removed state dropped | yes | New room skipped in the carry loop so it keeps the constructor defaults; new section skipped so it keeps its fresh modulator; `duty` carried only when `list(new._modulators) == list(self._modulators)`. `test_with_layout_new_room_and_section_start_fresh_and_names_keep_state`, `..._new_room_takes_the_class_defaults_not_the_originals_gains`, `..._removing_a_room_and_its_section_drops_their_state`, `..._reordered_sections_give_no_duty_but_keep_state_by_name`. |
| C5 | Constructor refusals identical in class and message; original unchanged | yes | The layout is validated by `type(self)(...)` before any state is touched (`allocator.py:931`), so a refusal produces nothing. `test_with_layout_refuses_what_the_constructor_refuses_and_changes_nothing` (13 cases), `..._rooms_without_a_room_the_default_sections_use_names_sections_path`, `..._failed_call_leaves_duty_and_demands_and_next_commands_alone`. |
| C6 | Docstrings, README, STRUCTURE.md describe `with_layout`; nothing says the layout is fixed; rounds 1-2 hold | yes | Method, class, module and `duty` docstrings updated; README "Changing the layout" paragraph (lines 220-228) replaces the fixed-layout sentence; STRUCTURE.md row, module text, showcase row and test summary. `grep` for "fixed layout" finds nothing; `test_with_layout_docs_no_longer_claim_a_fixed_layout`. Structure auditor: no changes needed. Showcase `python -m heatingsystem.allocator.allocator` runs and prints the `with_layout` case (R3 integral 1.0 kept, duty and hold carried, HS2 window `(0.0, 1.0)`). Earlier rounds: table below. |

Drift found, and what was done about it:

- None. The diff of `src/` since round 2's ship is confined to `with_layout`, the module, class and
  `duty` docstrings (the removed lines are the "built once from a fixed layout" wording and the
  one-line `duty` docstring) and the showcase case; the constructor, `update`, `hold`, `to_dict`,
  `from_dict`, `Room`, `PIController` and `Modulator` are untouched.
- Out of scope, checked: no `PIController.with_history_length`, no renaming, no in-place mutation,
  no P/I terms; `pyproject.toml` version still 1.1.0 and the snapshot format is unchanged.
- Surface: the only new public name is `with_layout`, exactly the planned signature. Decided
  behaviours (new rooms take the constructor defaults, `duty` carries only for identical ordered
  section names) are as section 1 states them and pinned by tests.
- Accepted limitation, not drift: a coverage-only change with unchanged section names carries a
  `duty` computed under the old coverages until the next `update` (section 1 decided this).

### Earlier rounds still hold

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| 1 | A1 | Coverage-sum refusal naming room and sum; several-room section may exceed 1; coverage range and other refusals | yes | Constructor and `_validate_layout` unchanged this round; `with_layout` calls the constructor, so the same refusals apply (C5's 13 cases include a room sum above 1). Round 1 refusal tests green. |
| 1 | A2 | Normalisation by each room's controlled total (E1, E3) | yes | Matrix assembly untouched; E-layout tests green; `with_layout` rebuilds through the constructor so a new coverage is normalised afresh. |
| 1 | A3 | Single-section room equals a standalone floor-heating `PIController` | yes | `update` and `Room` untouched; the A3 bit-exact twin test is green. |
| 1 | A4 | KKT, independent solve, priority scaling, A5/A6 scope, worked examples | yes | `_allocate` and the components are untouched; those suites are green in the 1612. |
| 1 | A5 | `hold`/`holds` validation, release, raising call changes nothing | yes | `hold` and `holds` unchanged; and `with_layout` carries a hold into the new modulator (`fixed_output`), `test_with_layout_..._holds`/the showcase show `holds carried` `HS2: 1.0`. |
| 1 | A6 | Held section's duty is its level; free sections compensate; release | yes | `_allocate` untouched; a carried hold behaves the same on the new allocator (twin commands in the C1 and C2 tests include a hold). |
| 1 | A7 | Fully held component calls no solver | yes | Untouched; test with a raising `lsq_linear` green. |
| 1 | A8 | Room PI keeps running during a hold | yes | Untouched; the un-held twin test is green. |
| 1 | A9 | Raising `update` leaves holds, windows, rooms, `duty` unchanged | yes | The save/restore block in `update` is untouched (`allocator.py:652-676`); tests green. |
| 1 | A10 | Snapshot `coverage`/`history`/`hold`; identical next 50 commands; 1.0.0 snapshot refused | yes | `to_dict`/`from_dict` untouched; snapshot suite green; `with_layout` is also compared against `from_dict` for real readings (C2, C3). |
| 1 | A11 | Version 1.1.0; STRUCTURE.md and README describe coverage, normalisation, `hold` | yes | `pyproject.toml` version = "1.1.0"; the README and STRUCTURE.md sections are intact, and only the layout sentence changed. |
| 2 | B1 | A `None` room keeps its integral and `demand`; others step like a twin | yes | `update` untouched; tests green; C1's 50-step twin includes `None` readings. |
| 2 | B2 | Allocation uses the last demand (E1 again); 0.0 before a first reading | yes | Untouched. Round 3 strengthens it: `demand` now survives a layout change (`..._none_reading_after_a_coverage_change_reuses_the_carried_demand`). |
| 2 | B3 | Interleaved run equals a standalone `PIController` fed real readings | yes | Untouched; test green. |
| 2 | B4 | All `None` accepted; missing key and other refusals unchanged | yes | Untouched; tests green. |
| 2 | B5 | A raising `update` with `None` rooms leaves everything unchanged | yes | Untouched; tests green, and `..._carried_duty_survives_a_failed_first_update` shows it on a carried allocator. |
| 2 | B6 | Docstrings, README, STRUCTURE.md describe `None`; A1-A11 hold | yes | `None`-reading text in `update`, `Room.demand`, README and STRUCTURE.md is intact; the `Room.demand` row ("`None` before the first real reading and after `from_dict`") is still accurate, since `with_layout` carries rather than resets. |

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | `912c394` Recommend round 2; concept and plan: round 3 — change the layout keeping state; `73176ea` Plan accepted: round 3 — critique applied, start build; `0207b48` Implement: round 3 — change the layout keeping state; `3e035af` Verify: SectionAllocator.with_layout; `8733de6` Test: with_layout keeping state (round 3); `a18dac8` Concept check: change the layout keeping state; plus this step's `Ship: SectionAllocator ready for Home Assistant — change the layout keeping state` |
| Pushed to | `origin/feat/allocator-home-assistant` |
| Whole-tree gates | `ruff check .` passed; `ruff format --check .` 56 files formatted; `mypy` no issues in 21 source files; `pytest` 1612 passed |
| Stray files | none; diff confined to `allocator.py`, `tests/test_allocator.py`, `README.md`, `STRUCTURE.md` and plan files |
| Missing step commits | none; steps 1-2 share the `912c394`/`73176ea` commits |

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

