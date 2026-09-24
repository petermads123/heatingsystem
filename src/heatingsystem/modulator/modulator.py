"""The actuator mapping, as its own reusable object.

This module provides :class:`Modulator`, which turns a demand level (a PI
output, or any other model's demand) into an actuator command for one of
two :class:`HeatingMode` values:

* ``RADIATOR`` — continuous output in [0, 1] (e.g. a thermostatic valve).
* ``FLOOR_HEATING`` — binary on/off signal derived from duty-cycle
  modulation over a rolling window of the last ``history_length`` samples
  (24 by default, 24 x 5 min = 2 h).

Nothing here is specific to a PI controller: the mode, the history window,
the duty cycle and the fixed-output hold are actuator concepts that any
model driving the same actuator range can reuse by composing a
:class:`Modulator` the way
:class:`~heatingsystem.pi_controller.pi_controller.PIController` does.

Module-level output clamp constants:

* ``OUTPUT_MIN = 0.0``
* ``OUTPUT_MAX = 1.0``
"""

from collections import deque
from collections.abc import Mapping
from enum import StrEnum
from typing import Self

from heatingsystem import _validation

# ---------------------------------------------------------------------------
# Module-level output clamp constants — the actuator range is always [0, 1].
# ---------------------------------------------------------------------------
OUTPUT_MIN: float = 0.0
OUTPUT_MAX: float = 1.0

# The exact key set the private snapshot from _to_dict() must have for
# _from_dict() to accept it; a missing or extra key is refused rather than
# partially applied.
_KEYS: frozenset[str] = frozenset({"mode", "history_length", "fixed_output", "history"})


class HeatingMode(StrEnum):
    """Heating actuator mode.

    Selects how the demand level (a PI controller's output, or any other
    model's demand, or the fixed output when one is set) is translated
    into an actuator command.

    Attributes:
        RADIATOR: Continuous modulation — the demand level is forwarded
            directly to the valve driver.
        FLOOR_HEATING: Binary on/off derived from duty-cycle modulation
            over a rolling window of ``history_length`` samples (the
            default 24 is 2 hours at 5-minute polling).
    """

    RADIATOR = "radiator"
    FLOOR_HEATING = "floor_heating"


def _heating_mode(value: object) -> HeatingMode:
    """Coerce a value to a :class:`HeatingMode`.

    Args:
        value: A :class:`HeatingMode` member or its string value.

    Returns:
        The coerced :class:`HeatingMode` member.

    Raises:
        ValueError: If ``value`` is not a valid :class:`HeatingMode` member
            or value.
    """
    try:
        return HeatingMode(value)  # type: ignore[arg-type]  # HeatingMode() itself validates any object at runtime
    except ValueError as exc:
        valid = [m.value for m in HeatingMode]
        raise ValueError(
            f"mode must be a HeatingMode or one of {valid}, got {value!r}."
        ) from exc


class Modulator:
    """Maps a demand level to an actuator command.

    Every setting — ``mode`` and ``fixed_output`` — is a validating
    property: assigning it, at construction or afterwards, raises on a bad
    value and leaves the previous value unchanged. ``history_length`` is
    validated at construction and exposed as a read-only property; the
    window itself cannot be resized afterwards.

    The modulator is stateful: it maintains a rolling window of past
    actuator commands for duty-cycle estimation (floor heating).

    Args:
        mode: Heating mode — ``"radiator"`` or ``"floor_heating"``.
            Accepts a :class:`HeatingMode` instance or a plain string;
            strings are coerced via ``HeatingMode(mode)``.
        history_length: Length of the rolling command window used for
            duty-cycle calculation in floor-heating mode.  Must be an
            ``int`` of at least 1; ``bool`` is rejected.  At 5-minute
            polling intervals, ``24`` equals 2 hours.  Default ``24``.
        fixed_output: Optional fixed actuator level in
            ``[OUTPUT_MIN, OUTPUT_MAX]``.  While set, :meth:`command`
            returns this level (mode-mapped) instead of the demand it is
            given.  ``None`` (the default) leaves the demand in control.
            See :mod:`heatingsystem._validation` for the numeric contract,
            plus the range check.  Assigned through the
            :attr:`fixed_output` setter, so an invalid value raises here
            too.

    Raises:
        ValueError: If ``mode`` is not a valid :class:`HeatingMode` value.
        ValueError: If ``history_length`` is less than 1.
        TypeError: If ``history_length`` is a ``bool`` or not an ``int``.
        OverflowError: If ``history_length`` is too large for a rolling
            window to hold.
        TypeError, ValueError, OverflowError: See
            :mod:`heatingsystem._validation` for the numeric contract
            behind ``fixed_output`` (plus its range check); raised naming
            the attribute.
    """

    _mode: HeatingMode
    _history_length: int
    _fixed_output: float | None
    _history: deque[float]

    def __init__(
        self,
        mode: HeatingMode | str = HeatingMode.RADIATOR,
        *,
        history_length: int = 24,
        fixed_output: float | None = None,
    ) -> None:
        """Initialise the modulator.

        The arguments and the errors they raise are documented once, on
        the class docstring, so they cannot drift between two copies.
        Every setting is assigned through its validating property, so
        construction raises exactly what later assignment would.
        """
        self.mode = mode

        self._history_length = _validation.window_length(history_length)

        # Rolling window of past actuator commands.
        self._history: deque[float] = deque(maxlen=self._history_length)

        # Fixed output override — None means the demand level is in control.
        self._fixed_output = None
        self.fixed_output = fixed_output

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def command(self, level: float) -> float:
        """Map a demand level to this step's actuator command.

        ``level`` is validated first, even while :attr:`fixed_output` is
        set — an invalid demand still raises, though the target the
        validated demand feeds into is then replaced by the hold. The
        window is read (for floor-heating's duty cycle) BEFORE this
        step's command is appended, so the command does not factor into
        its own calculation.

        Args:
            level: Demand level in ``[OUTPUT_MIN, OUTPUT_MAX]`` — the PI
                output, or any other model's demand.

        Returns:
            The actuator command: a float in [0.0, 1.0] for ``RADIATOR``
            mode, or exactly 0.0 or 1.0 for ``FLOOR_HEATING`` mode.

        Raises:
            TypeError: If ``level`` is a ``bool`` or not a real number.
            ValueError: If ``level`` is not finite, or lies outside
                ``[OUTPUT_MIN, OUTPUT_MAX]``.
            OverflowError: If ``level`` is too large to represent as a
                float.
        """
        demand = _validation.level("level", level, OUTPUT_MIN, OUTPUT_MAX)
        target = demand if self._fixed_output is None else self._fixed_output
        result = self._to_command(target)

        # Append AFTER _to_command so this step's command is not included
        # in its own duty-cycle calculation.
        self._history.append(result)

        return result

    def reset(self) -> None:
        """Clear the history window.

        The mode, ``history_length`` and :attr:`fixed_output` are left
        unchanged.
        """
        self._history.clear()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> HeatingMode:
        """The heating actuator mode."""
        return self._mode

    @mode.setter
    def mode(self, value: HeatingMode | str) -> None:
        """Set the heating actuator mode.

        Args:
            value: A :class:`HeatingMode` member or its string value.

        Raises:
            ValueError: If ``value`` is not a valid :class:`HeatingMode`
                member or value. The previous mode is left unchanged.
        """
        self._mode = _heating_mode(value)

    @property
    def history_length(self) -> int:
        """The rolling command-window length given at construction.

        Read-only: the window is sized once, at construction, and a
        restore through :meth:`_from_dict` builds a new modulator rather
        than resizing this one.
        """
        return self._history_length

    @property
    def fixed_output(self) -> float | None:
        """The current fixed-output override, or ``None`` if unset.

        Returns:
            The fixed actuator level in [``OUTPUT_MIN``, ``OUTPUT_MAX``], or
            ``None`` when the demand level is in control.
        """
        return self._fixed_output

    @fixed_output.setter
    def fixed_output(self, value: float | None) -> None:
        """Set or clear the fixed-output override.

        Args:
            value: A finite number in [``OUTPUT_MIN``, ``OUTPUT_MAX``] to
                fix the output, or ``None`` to release it back to the
                demand level. ``bool`` is rejected. Stored as
                ``float(value)``, so an ``int`` such as ``0`` or ``1`` is
                accepted and read back as a float.

        Raises:
            TypeError, ValueError, OverflowError: See
                :mod:`heatingsystem._validation` for the numeric contract:
                raised naming ``fixed_output`` for a value that is not
                ``None`` and fails it (including a value outside
                [``OUTPUT_MIN``, ``OUTPUT_MAX``]); the previous setting is
                left unchanged.
        """
        if value is None:
            self._fixed_output = None
            return
        self._fixed_output = _validation.level(
            "fixed_output", value, OUTPUT_MIN, OUTPUT_MAX
        )

    @property
    def history(self) -> tuple[float, ...]:
        """Immutable snapshot of the command history, oldest to newest.

        Returns:
            A tuple of past actuator commands in chronological order.
        """
        return tuple(self._history)

    @property
    def duty_cycle(self) -> float:
        """Mean of the current history window (fraction of ON-time).

        Used by :meth:`_to_command` in floor-heating mode to decide
        whether the current slot should be ON or OFF so that the
        long-run ON fraction converges to within about one slot
        (``1 / history_length``) of the target demand level.

        Returns:
            The mean of the history window, or ``0.0`` when the window
            is empty.
        """
        # Guard against ZeroDivisionError on an empty deque.
        if not self._history:
            return 0.0
        return sum(self._history) / len(self._history)

    @property
    def is_history_full(self) -> bool:
        """Whether the rolling history window has been completely filled.

        Returns:
            ``True`` once :attr:`history` contains ``history_length``
            samples; ``False`` during the initial warm-up period.
        """
        return len(self._history) == self._history_length

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_command(self, level: float) -> float:
        """Map a target level to a mode-specific actuator command.

        ``level`` is the target level (the demand, or the fixed output
        when one is set).  For ``RADIATOR`` mode it is forwarded unchanged
        (already in [0, 1]).  For ``FLOOR_HEATING`` mode a binary signal is
        derived using duty-cycle modulation:

        * If ``level`` is at or below ``OUTPUT_MIN`` the slot is always OFF.
        * If ``level`` is at or above ``OUTPUT_MAX`` the slot is always ON.
        * Otherwise the slot is ON when the realised duty cycle so far
          (mean of the trailing window) is below the target ``level``; a tie
          rests the slot.  Over many cycles the ON-fraction converges to
          within about one slot (``1 / history_length``) of ``level`` — a
          two-slot window at 0.5 settles at 1/3 — which is why a long
          window (the default 24 samples is 2 h at 5-min intervals) is
          needed for good modulation fidelity.

        This method is called BEFORE the new command is appended to the
        history, so :attr:`duty_cycle` reflects only past samples.

        Args:
            level: Target level in [``OUTPUT_MIN``, ``OUTPUT_MAX``] — the
                demand, or the fixed output when one is set.

        Returns:
            The actuator command: a float in [0.0, 1.0] for ``RADIATOR``
            mode, or exactly 0.0 or 1.0 for ``FLOOR_HEATING`` mode.
        """
        if self.mode is HeatingMode.RADIATOR:
            # Continuous modulation — pass through directly.
            return level

        # --- FLOOR_HEATING: duty-cycle modulation ---
        if level <= OUTPUT_MIN:
            # No demand — keep the floor off.
            return 0.0
        if level >= OUTPUT_MAX:
            # Full demand — keep the floor on.
            return 1.0

        # Fire this slot when the realised duty so far is below target level.
        # Over a full window the ON-fraction will converge to level, spreading
        # heat pulses evenly rather than bunching them at the start of the window.
        return 1.0 if self.duty_cycle < level else 0.0

    def _load_history(self, entries: object) -> None:
        """Load a restored history, replacing the current window.

        An entry is a level in the actuator range, not necessarily a
        command the current mode would emit — a restored floor-heating
        history may hold fractional entries, exactly as a mid-run mode
        switch (radiator to floor heating) already produces, since a
        fractional radiator command is a valid actuator-range level even
        though floor heating itself only ever appends 0.0 or 1.0.

        Args:
            entries: The candidate history — must be a ``list`` or
                ``tuple`` of no more than :attr:`history_length` levels.

        Raises:
            TypeError: If ``entries`` is not a ``list`` or ``tuple``, or
                an entry is not a real number, naming ``history[i]``.
            ValueError: If ``entries`` has more entries than
                :attr:`history_length`, or an entry is not finite or lies
                outside ``[OUTPUT_MIN, OUTPUT_MAX]``, naming ``history[i]``.
            OverflowError: If an entry is too large to represent as a
                float, naming ``history[i]``.
        """
        if not isinstance(entries, (list, tuple)):
            raise TypeError(
                f"history must be a list or tuple, got {entries!r} "
                f"({type(entries).__name__})."
            )
        if len(entries) > self._history_length:
            raise ValueError(
                f"history has {len(entries)} entries but history_length is "
                f"{self._history_length}."
            )
        levels: list[float] = []
        for i, entry in enumerate(entries):
            levels.append(
                _validation.level(f"history[{i}]", entry, OUTPUT_MIN, OUTPUT_MAX)
            )

        self._history.clear()
        self._history.extend(levels)

    def _to_dict(self) -> dict[str, object]:
        """Capture the modulator's settings and history as a private snapshot.

        Not a public wire format: package-internal, for any model in this
        package that composes a modulator and folds these four keys into
        its own snapshot, as
        :class:`~heatingsystem.pi_controller.pi_controller.PIController`
        does (see the module docstring for the composition recipe).

        Returns:
            A fresh ``dict`` with exactly the keys ``mode``,
            ``history_length``, ``fixed_output`` and ``history``, in that
            order. The dict and its ``history`` list are copies.
        """
        return {
            "mode": self.mode.value,
            "history_length": self.history_length,
            "fixed_output": self.fixed_output,
            "history": list(self._history),
        }

    @classmethod
    def _from_dict(cls, data: Mapping[str, object]) -> Self:
        """Rebuild a modulator from a snapshot produced by :meth:`_to_dict`.

        Args:
            data: A mapping with exactly the keys ``_to_dict`` produces:
                ``mode``, ``history_length``, ``fixed_output`` and
                ``history``.

        Returns:
            A new modulator equal to the one ``_to_dict`` was called on.

        Raises:
            TypeError: If ``data`` is not a ``Mapping``, naming its type;
                if ``history`` is not a list or tuple, naming it; or if a
                value is not the type its setter or check requires, naming
                the key (``history[i]`` for an entry).
            ValueError: If a key is missing or unknown, naming the keys;
                if ``mode`` is not a valid :class:`HeatingMode` value; if
                ``history`` has more entries than ``history_length``; or if
                a numeric value is out of range, naming the key.
            OverflowError: If a numeric value is too large to represent as
                a float, naming the key.
        """
        data = _validation.snapshot_mapping(data, _KEYS)

        fixed = data["fixed_output"]
        # _validation.level, not _validation.finite: raises with the
        # caller's own value (e.g. a Fraction) in the message, matching
        # what the fixed_output setter itself would raise — finite alone
        # would convert to float first and report the converted value.
        fixed_output = (
            None
            if fixed is None
            else _validation.level("fixed_output", fixed, OUTPUT_MIN, OUTPUT_MAX)
        )

        modulator = cls(
            mode=data["mode"],  # type: ignore[arg-type]  # _heating_mode validates any object
            history_length=_validation.window_length(data["history_length"]),
            fixed_output=fixed_output,
        )
        modulator._load_history(data["history"])

        return modulator


def main() -> None:
    """Showcase this module's functionality."""
    print("=== HeatingMode enum ===")
    for hm in HeatingMode:
        print(f"  HeatingMode.{hm.name} = {hm.value!r}")

    print("\n=== RADIATOR mode: pass-through ===")
    mode = HeatingMode.RADIATOR  # RADIATOR, FLOOR_HEATING
    history_length = 6
    radiator = Modulator(mode, history_length=history_length)

    demand = 0.42
    command = radiator.command(demand)

    print(f"  command({demand}) = {command}  history={radiator.history}")

    print("\n=== FLOOR_HEATING mode: duty-cycle convergence ===")
    mode = HeatingMode.FLOOR_HEATING  # RADIATOR, FLOOR_HEATING
    history_length = 4
    floor = Modulator(mode, history_length=history_length)

    demand = 0.25
    for _ in range(history_length * 3):
        floor.command(demand)

    print(
        f"  demand={demand}  duty_cycle={floor.duty_cycle:.3f}  history={floor.history}"
    )

    print("\n=== fixed_output override ===")
    hold_level = 1.0
    floor.fixed_output = hold_level

    command = floor.command(demand)

    print(f"  fixed_output={floor.fixed_output}  command({demand}) = {command}")

    floor.fixed_output = None
    floor.reset()
    print(
        f"  released and reset: fixed_output={floor.fixed_output}  history={floor.history}"
    )


if __name__ == "__main__":
    main()
