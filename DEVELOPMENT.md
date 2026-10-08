# Development notes

Development-side open questions and things to fix later: what the code does not do yet, a
question nobody can answer yet, an idea that fell short of a recommendation. Not a design
document and not a changelog.

**Adding an entry:** a heading, the date and the branch/round it came from, the item itself,
and its next step or where it is configured. **Resolving an entry:** the change that
resolves it deletes it — this file only ever shows what is still open. Step 8 of every round
ends by cleaning it: entries the branch resolved are removed, duplicates are merged, and
only open items are left. Before an entry is deleted or renamed, anything that points at it
by heading is updated in the same change.

## `feat/fixed-output` round 2 — 2026-09-23

Deferred at step 8; the full rows are in `development/feat/fixed-output/02-attribute-surface.md` §8.

- **R4** — decide what a mid-run `ki` change does to the accumulated integral: rescale so
  the I-term stays continuous, or document that the caller should `reset()` after re-tuning.
- **R6** — record `pi_output` in the `test.py` simulation and draw it under the hold band;
  use `hs.OUTPUT_MIN`/`OUTPUT_MAX` there instead of literals. Lands together with round 3's R5.
- **R7** — move the room's first-order thermal model out of `test.py` into the package as
  its second model.

## `feat/fixed-output` round 3 — 2026-09-23

Deferred at step 8; the full rows are in `development/feat/fixed-output/03-state-snapshot.md` §8.

- **R4** — a `PIController.__repr__` built from `to_dict()`.
- **R5** — a restart in the closed-loop simulation: snapshot mid-hold, rebuild with
  `from_dict`, overlay a fresh controller's trajectory. Same two `test.py` signatures as
  round 2's R6, so the two land together.

## `feat/fixed-output` round 4 — 2026-09-24

Deferred at step 8; the full rows are in `development/feat/fixed-output/04-modulator.md` §8.

- **R4** — make `update()` transactional for the per-call setpoint too, so a modulator raise
  stores nothing. Take it with R5 if a second model lands.
- **R5** — give `_validation.window_length` a `name` parameter like the other helpers, and
  stop `_validation.py`'s module docstring naming its callers. The second caller has now
  arrived: `SectionAllocator` (`feat/section-allocator` round 1) validates `history_length`
  through it; the change itself is still to do.
- **R6** — regroup `tests/test_pi_controller.py` by subject, collapse the duplicated
  validation matrices, and prune `STRUCTURE.md`'s test narrative. The trigger for the
  per-subpackage `STRUCTURE.md` split has now arrived: `allocator/` is the third subpackage
  (`feat/section-allocator` round 1 left `STRUCTURE.md` flat); the split itself is still to do.
- **R7** — promote the modulator's `_to_dict`/`_from_dict` to public. The trigger has now
  arrived: `SectionAllocator` (`feat/section-allocator` round 1) is the first caller that
  drives standalone `Modulator`s and restores them through `Modulator._from_dict`, so it
  currently calls a private method from another module; the promotion itself is still to do.

## `feat/section-allocator` round 1 — 2026-10-07

Noted at step 8; nothing critical. Details in `development/feat/section-allocator/01-section-allocator.md` §3, §5.

- **Deployment** — scipy and numpy are now runtime dependencies; installing them inside the
  AppDaemon container (ready-built musllinux wheels exist for 3.13) is untested and belongs
  with the AppDaemon glue, which this round left out of scope.
- **A10 tolerance** — A10's absolute 1e-9 cost tolerance is vacuous when every priority is
  tiny; the tests normalise `J` by the largest priority. Reword A10 "relative to the largest
  priority" if the criterion is ever revisited.
- **`Room` internals** — a failed allocation restores each room's integral and demand but
  not its composed `PIController`'s one-slot radiator history (unobservable today); and
  `from_dict` accepts a non-binary floor-heating section history (the `Modulator` contract).
  Revisit both if `Room` ever exposes a history or the snapshot format is tightened.

## `feat/allocator-home-assistant` round 1 — 2026-10-08

Noted at step 8; nothing critical. Details in `development/feat/allocator-home-assistant/01-coverage-and-holds.md` §5, §6.

- **A6 wording** — a fractional hold is realised only to within about one slot of the level
  (the `Modulator` rule; window 2 at 0.5 gives 1/3), so A6's parenthetical "averaging the
  level over a window" is loose; the tests pin equality with a standalone `Modulator` twin.
  Reword it if the criterion is revisited.
- **Docstrings** — `from_dict` says holds go through "the same setters" (they are validated
  as `sections['HS1'].hold`, not via `hold`); `hold`'s `Raises` reads as if a non-`str`
  section names `holds[...]`; the constructor's "every error names the path" does not fit
  the uncovered-rooms message. A `/small-change`.
- **Late command raise** — `update` issues section commands outside the restore block, so a
  `Modulator.command` raising for a later section would leave earlier windows and the room
  integrals advanced; unreachable today (`_allocate` clamps, holds are validated).
