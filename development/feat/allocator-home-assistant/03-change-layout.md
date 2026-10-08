# SectionAllocator ready for Home Assistant — change the layout keeping state

<!-- claude-plan step=3 status=active -->

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

