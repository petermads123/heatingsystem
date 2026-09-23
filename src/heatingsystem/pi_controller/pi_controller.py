"""PI controller for residential heating systems.

This module provides :class:`PIController`, a discrete-time proportional-integral
controller designed to regulate room temperature by modulating a heating actuator.
The controller is driven externally (e.g. by Home Assistant / AppDaemon on a fixed
5-minute polling interval) and contains no internal timing or scheduling logic.

Two heating modes are supported via :class:`HeatingMode`:

* ``RADIATOR`` — continuous output in [0, 1] (e.g. a thermostatic valve).
* ``FLOOR_HEATING`` — binary on/off signal derived from duty-cycle modulation over
  a rolling window of the last 24 samples (24 × 5 min = 2 h).

Module-level output clamp constants:

* ``OUTPUT_MIN = 0.0``
* ``OUTPUT_MAX = 1.0``

A controller's output can also be pinned to a fixed level via
:attr:`PIController.fixed_output`, overriding the PI result while the PI
calculation, including the integral, keeps running underneath it.
"""

import json
import math
import numbers
import sys
from collections import deque
from collections.abc import Mapping
from enum import StrEnum
from typing import Self

# ---------------------------------------------------------------------------
# Module-level output clamp constants — the actuator range is always [0, 1].
# ---------------------------------------------------------------------------
OUTPUT_MIN: float = 0.0
OUTPUT_MAX: float = 1.0

# The exact key set a snapshot from to_dict() must have for from_dict() to
# accept it; a missing or extra key is refused rather than partially applied.
_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {
        "kp",
        "ki",
        "setpoint",
        "mode",
        "history_length",
        "fixed_output",
        "integral",
        "history",
    }
)


def _finite(name: str, value: object) -> float:
    """Validate a value as a finite real number and return it as a float.

    The shared numeric contract behind every validating setting on
    :class:`PIController`: a real number (``bool`` excluded), finite, and
    representable as a ``float``.

    Args:
        name: The attribute or parameter name, used in the error messages.
        value: The value to validate.

    Returns:
        ``value`` converted to ``float``. A negative zero is returned as
        positive zero.

    Raises:
        TypeError: If ``value`` is a ``bool`` or not an
            :class:`numbers.Real` (an ``int``, ``float``, ``Fraction`` and
            most numpy scalar types pass — ``numpy.bool_`` does not, for the
            same reason a plain ``bool`` does not; a ``Decimal`` does not
            either).
        ValueError: If ``value`` is not finite (``nan`` or ``inf``).
        OverflowError: If ``value`` is too large to represent as a float
            (e.g. an ``int`` such as ``10**400``).
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(
            f"{name} must be a real number, got {value!r} ({type(value).__name__})."
        )
    try:
        number = float(value)
    except OverflowError as exc:
        raise OverflowError(f"{name} is too large to represent as a float.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return number + 0.0


def _window_length(value: object) -> int:
    """Validate a rolling-window length.

    The shared contract behind :attr:`PIController.history_length`: an
    ``int`` (``bool`` excluded) of at least 1, small enough for a
    :class:`collections.deque`'s ``maxlen``.

    Args:
        value: The value to validate.

    Returns:
        ``value``, unchanged.

    Raises:
        TypeError: If ``value`` is a ``bool`` or not an ``int``.
        ValueError: If ``value`` is less than 1.
        OverflowError: If ``value`` is too large for a ``deque``'s
            ``maxlen`` to hold.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"history_length must be an int, got {value!r} ({type(value).__name__})."
        )
    if value < 1:
        raise ValueError(f"history_length must be >= 1, got {value}.")
    if value > sys.maxsize:
        raise OverflowError(f"history_length is too large, got {value}.")
    return value


def _level(name: str, value: object) -> float:
    """Validate a value as a finite level in the actuator range.

    Builds on :func:`_finite`, adding the ``[OUTPUT_MIN, OUTPUT_MAX]`` range
    check shared by :attr:`PIController.fixed_output` and by each entry of
    a restored :attr:`PIController.history`.

    Args:
        name: The attribute or parameter name, used in the error message.
        value: The value to validate.

    Returns:
        ``value`` converted to ``float``.

    Raises:
        TypeError: If ``value`` is a ``bool`` or not a real number.
        ValueError: If ``value`` is not finite, or lies outside
            ``[OUTPUT_MIN, OUTPUT_MAX]``.
        OverflowError: If ``value`` is too large to represent as a float.
    """
    level = _finite(name, value)
    if level < OUTPUT_MIN or level > OUTPUT_MAX:
        raise ValueError(
            f"{name} must be in [{OUTPUT_MIN}, {OUTPUT_MAX}], got {value!r}."
        )
    return level


class HeatingMode(StrEnum):
    """Heating actuator mode.

    Selects how the demand level (the PI output, or the fixed output when
    one is set) is translated into an actuator command.

    Attributes:
        RADIATOR: Continuous modulation — the demand level is forwarded
            directly to the valve driver.
        FLOOR_HEATING: Binary on/off derived from duty-cycle modulation
            over a rolling 2-hour window (24 samples × 5 min).
    """

    RADIATOR = "radiator"
    FLOOR_HEATING = "floor_heating"


class PIController:
    """Discrete-time PI controller for residential heating.

    Every setting — ``kp``, ``ki``, ``mode``, ``setpoint``, ``fixed_output``
    and ``integral`` — is a validating property: assigning it, at
    construction or afterwards, raises on a bad value and leaves the
    previous value unchanged. ``history_length`` is validated at
    construction and exposed as a read-only property; the window itself
    cannot be resized afterwards.

    The controller is stateful: it accumulates an integral term across
    successive :meth:`update` calls and maintains a rolling window of
    past actuator commands for duty-cycle estimation (floor heating).

    Anti-windup is implemented via *conditional integration*: the integral
    is only advanced when the raw PI output is inside the valid output
    range, or when integration would pull a saturated output back toward
    the valid range.

    The controller's complete state — every setting plus the running
    state — can be captured with :meth:`to_dict` and rebuilt with
    :meth:`from_dict`, for a caller (Home Assistant / AppDaemon) that
    needs to persist a controller across a restart or a code reload.

    Args:
        kp: Proportional gain.  Default ``0.3``.  Must be a finite real
            number; ``bool`` is rejected.
        ki: Integral gain.  Default ``0.015``.  Same numeric contract as
            ``kp``.
        mode: Heating mode — ``"radiator"`` or ``"floor_heating"``.
            Accepts a :class:`HeatingMode` instance or a plain string;
            strings are coerced via ``HeatingMode(mode)``.
        setpoint: Initial temperature setpoint in °C.  Default ``21.0``.
            Same numeric contract as ``kp``.
        history_length: Length of the rolling command window used for
            duty-cycle calculation in floor-heating mode.  Must be an
            ``int`` of at least 1; ``bool`` is rejected.  At 5-minute
            polling intervals, ``24`` equals 2 hours.  Default ``24``.
        fixed_output: Optional fixed actuator level in
            ``[OUTPUT_MIN, OUTPUT_MAX]``.  While set, :meth:`update`
            returns this level (mode-mapped) instead of the PI result.
            ``None`` (the default) leaves the PI loop in control.  Same
            numeric contract as ``kp``, plus the range check.  Assigned
            through the :attr:`fixed_output` setter, so an invalid value
            raises here too.

    Raises:
        ValueError: If ``mode`` is not a valid :class:`HeatingMode` value.
        ValueError: If ``history_length`` is less than 1.
        TypeError: If ``history_length`` is a ``bool`` or not an ``int``.
        OverflowError: If ``history_length`` is too large for a rolling
            window to hold.
        ValueError: If ``kp``, ``ki``, ``setpoint`` or ``fixed_output``
            are not finite numbers (e.g. ``nan``, ``inf``), or
            ``fixed_output`` is not ``None`` and lies outside
            ``[OUTPUT_MIN, OUTPUT_MAX]``.
        TypeError: If ``kp``, ``ki``, ``setpoint`` or ``fixed_output`` are
            not a real number, ``bool`` included; the message names the
            attribute.
        OverflowError: If ``kp``, ``ki``, ``setpoint`` or ``fixed_output``
            are too large to represent as a ``float`` (e.g. an ``int`` such
            as ``10**400``); the message names the attribute.

    Example:
        >>> ctrl = PIController(kp=0.5, ki=0.02, setpoint=22.0)
        >>> ctrl.update(20.0)
        1.0
    """

    _kp: float
    _ki: float
    _setpoint: float
    _mode: HeatingMode
    _history_length: int
    _integral: float

    def __init__(
        self,
        kp: float = 0.3,
        ki: float = 0.015,
        mode: HeatingMode | str = HeatingMode.RADIATOR,
        setpoint: float = 21.0,
        *,
        history_length: int = 24,
        fixed_output: float | None = None,
    ) -> None:
        """Initialise the PI controller.

        The arguments and the errors they raise are documented once, on the
        class docstring, so they cannot drift between two copies. Every
        setting is assigned through its validating property, so
        construction raises exactly what later assignment would.
        """
        self.mode = mode

        self._history_length = _window_length(history_length)

        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint

        # Integral accumulator, reset to zero on construction and via reset().
        self.integral = 0.0

        # Rolling window of past actuator commands.
        # maxlen=24 at 5-min intervals == 2 h of history for duty-cycle tracking.
        self._history: deque[float] = deque(maxlen=self._history_length)

        # Clamped PI result of the last update(); None before the first step.
        self._pi_output: float | None = None

        # Fixed output override — None means the PI loop is in control.
        self._fixed_output: float | None = None
        self.fixed_output = fixed_output

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self, measured: float, setpoint: float | None = None) -> float:
        """Advance the controller by one time step and return the actuator command.

        This is the method called by Home Assistant / AppDaemon every
        5 minutes.  The fixed step size is implicit in the gain values
        ``ki`` (= Ki × Δt); there is no ``dt`` argument.

        Args:
            measured: Current room temperature in °C.
            setpoint: Optional new setpoint in °C.  If provided the stored
                setpoint is replaced before the control calculation.

        Returns:
            The actuator command for this step.  For ``RADIATOR`` mode this
            is a continuous value in [0.0, 1.0].  For ``FLOOR_HEATING`` mode
            it is binary: 0.0 (off) or 1.0 (on).  While :attr:`fixed_output`
            is set, the command is derived from it instead of the PI
            result — the PI calculation still runs in full underneath
            (inputs are validated, the integral still advances with the
            usual anti-windup), and the fixed command is still recorded in
            :attr:`history`.

        Raises:
            TypeError: If ``measured`` or the new ``setpoint`` is not a
                real number, ``bool`` included; the message names which.
            ValueError: If ``measured`` or the new ``setpoint`` is not
                finite.
            OverflowError: If ``measured`` or the new ``setpoint`` is too
                large to represent as a float.

        Note:
            Reachable only with extreme finite inputs: if the raw PI sum
            or the tentative integral is not finite, the PI demand is
            treated as ``OUTPUT_MIN`` (:attr:`pi_output` reads ``0.0``)
            and the integral is held rather than advanced. While
            :attr:`fixed_output` is set the command is unaffected, since
            the fixed level replaces the demand regardless.
        """
        measured = _finite("measured", measured)

        # --- Optional setpoint update ---
        if setpoint is not None:
            self.setpoint = setpoint

        # --- Error: positive means too cold, controller ramps output up ---
        error: float = self.setpoint - measured

        # --- PI computation with conditional-integration anti-windup ---
        #
        # We tentatively integrate first, compute the raw output, then decide
        # whether to commit the new integral value.  This prevents the integral
        # from winding up when the actuator is saturated.
        new_integral: float = self.integral + error
        raw: float = self.kp * error + self.ki * new_integral

        # Reachable only with extreme finite inputs: a non-finite raw sum or
        # tentative integral (kp * -inf, an overflowing integral, ...) closes
        # the valve and holds the integral rather than propagating nan/inf.
        finite: bool = math.isfinite(raw) and math.isfinite(new_integral)

        # Clamp raw output to the actuator's physical range.
        u: float = max(OUTPUT_MIN, min(OUTPUT_MAX, raw)) if finite else OUTPUT_MIN
        self._pi_output = u

        # Anti-windup — only commit the new integral when it is useful:
        #   * Not saturated at all  → always safe to integrate.
        #   * Saturated HIGH (raw >= OUTPUT_MAX) → only integrate when
        #     error < 0, i.e. measurement is rising and integration will
        #     drag the output back down toward the valid range.
        #   * Saturated LOW  (raw <= OUTPUT_MIN) → only integrate when
        #     error > 0, i.e. measurement is falling and integration will
        #     push the output back up toward the valid range.
        #   Otherwise: hold the old integral to avoid making windup worse.
        if finite:
            if OUTPUT_MIN < raw < OUTPUT_MAX:
                # Inside the linear region — unrestricted integration.
                self.integral = new_integral
            elif raw >= OUTPUT_MAX and error < 0:
                # Saturated high but cooling trend: allow integration to wind down.
                self.integral = new_integral
            elif raw <= OUTPUT_MIN and error > 0:
                # Saturated low but warming trend: allow integration to wind up.
                self.integral = new_integral
            # else: output is saturated and error would deepen windup — hold.
        # When raw/new_integral is not finite, the integral is held above.

        # --- Map demand level to actuator command ---
        # While fixed_output is set, it replaces the PI result u as the
        # demand level handed to _to_command; the PI computation and
        # anti-windup above are unaffected either way.
        level: float = u if self._fixed_output is None else self._fixed_output
        # _to_command reads self._history (the trailing window) BEFORE we
        # append the new command, so duty_cycle reflects only past samples.
        command: float = self._to_command(level)

        # Append AFTER _to_command so this step's command is not included
        # in its own duty-cycle calculation.
        self._history.append(command)

        return command

    def reset(self) -> None:
        """Reset the controller state to initial values.

        Clears the integral accumulator, the history window, and
        :attr:`pi_output`.  The gains, mode, setpoint, and
        :attr:`fixed_output` are left unchanged.
        """
        self.integral = 0.0
        self._history.clear()
        self._pi_output = None

    def to_dict(self) -> dict[str, object]:
        """Capture the controller's complete state as a snapshot.

        Every setting and every piece of running state, as built-in types
        ``json.dumps`` accepts: the gains, the setpoint, the mode as its
        string value, the window length, the fixed-output hold, the
        integral accumulator, and the command history oldest first. The PI
        demand (:attr:`pi_output`) is derived, not stored, and is not part
        of the snapshot.

        Returns:
            A fresh ``dict`` with exactly the keys ``kp``, ``ki``,
            ``setpoint``, ``mode``, ``history_length``, ``fixed_output``,
            ``integral`` and ``history``. The dict and its ``history`` list
            are copies: changing either afterwards, or updating the
            controller, leaves the other unchanged.
        """
        return {
            "kp": self.kp,
            "ki": self.ki,
            "setpoint": self.setpoint,
            "mode": self.mode.value,
            "history_length": self.history_length,
            "fixed_output": self.fixed_output,
            "integral": self.integral,
            "history": list(self._history),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Rebuild a controller from a snapshot produced by :meth:`to_dict`.

        Every value is applied through the same validating setter or check
        a direct assignment would go through, so a bad snapshot raises
        exactly what a bad assignment would, naming the offending key, and
        no controller is produced.

        Args:
            data: A mapping with exactly the keys ``to_dict`` produces:
                ``kp``, ``ki``, ``setpoint``, ``mode``, ``history_length``,
                ``fixed_output``, ``integral`` and ``history``.

        Returns:
            A new controller equal to the one ``to_dict`` was called on, in
            every setting and in :attr:`integral`, :attr:`history`,
            :attr:`duty_cycle`, :attr:`is_history_full` and
            :attr:`fixed_output`. :attr:`pi_output` is ``None``, as it is
            on any freshly constructed controller.

        Raises:
            TypeError: If ``data`` is not a ``Mapping``, naming it; if
                ``history`` is not a list or tuple, naming it; or if a
                value is not the type its setter or check requires, naming
                the key (``history[i]`` for an entry).
            ValueError: If a key is missing or unknown, naming the keys;
                if ``mode`` is not a valid :class:`HeatingMode` value; if
                ``history`` has more entries than ``history_length``; or if
                a numeric value is out of range, naming the key.
            OverflowError: If a numeric value is too large to represent as
                a float, naming the key.
        """
        if not isinstance(data, Mapping):
            raise TypeError(f"snapshot must be a mapping, got {type(data).__name__}.")

        missing = sorted(_SNAPSHOT_KEYS - data.keys())
        if missing:
            raise ValueError(f"snapshot is missing keys {missing}.")
        unknown = sorted(data.keys() - _SNAPSHOT_KEYS)
        if unknown:
            raise ValueError(f"snapshot has unknown keys {unknown}.")

        mode = data["mode"]
        if not isinstance(mode, (HeatingMode, str)):
            valid = [m.value for m in HeatingMode]
            raise ValueError(
                f"mode must be a HeatingMode or one of {valid}, got {mode!r}."
            )

        fixed = data["fixed_output"]
        fixed_output = None if fixed is None else _finite("fixed_output", fixed)

        history = data["history"]
        if not isinstance(history, (list, tuple)):
            raise TypeError(
                f"history must be a list, got {history!r} ({type(history).__name__})."
            )

        controller = cls(
            kp=_finite("kp", data["kp"]),
            ki=_finite("ki", data["ki"]),
            mode=mode,
            setpoint=_finite("setpoint", data["setpoint"]),
            history_length=_window_length(data["history_length"]),
            fixed_output=fixed_output,
        )
        controller.integral = _finite("integral", data["integral"])

        if len(history) > controller.history_length:
            raise ValueError(
                f"history has {len(history)} entries but history_length is "
                f"{controller.history_length}."
            )
        levels: list[float] = []
        for i, entry in enumerate(history):
            levels.append(_level(f"history[{i}]", entry))
        controller._history.extend(levels)

        return controller

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
        try:
            self._mode = HeatingMode(value)
        except ValueError as exc:
            valid = [m.value for m in HeatingMode]
            raise ValueError(
                f"mode must be a HeatingMode or one of {valid}, got {value!r}."
            ) from exc

    @property
    def kp(self) -> float:
        """The proportional gain."""
        return self._kp

    @kp.setter
    def kp(self, value: float) -> None:
        """Set the proportional gain.

        Args:
            value: A finite real number. ``bool`` is rejected.

        Raises:
            TypeError: If ``value`` is not a real number, ``bool``
                included. The previous gain is left unchanged.
            ValueError: If ``value`` is not finite. The previous gain is
                left unchanged.
            OverflowError: If ``value`` is too large to represent as a
                float. The previous gain is left unchanged.
        """
        self._kp = _finite("kp", value)

    @property
    def ki(self) -> float:
        """The integral gain."""
        return self._ki

    @ki.setter
    def ki(self, value: float) -> None:
        """Set the integral gain.

        Args:
            value: A finite real number. ``bool`` is rejected.

        Raises:
            TypeError: If ``value`` is not a real number, ``bool``
                included. The previous gain is left unchanged.
            ValueError: If ``value`` is not finite. The previous gain is
                left unchanged.
            OverflowError: If ``value`` is too large to represent as a
                float. The previous gain is left unchanged.
        """
        self._ki = _finite("ki", value)

    @property
    def setpoint(self) -> float:
        """The target temperature in °C."""
        return self._setpoint

    @setpoint.setter
    def setpoint(self, value: float) -> None:
        """Set the target temperature.

        Args:
            value: A finite real number. ``bool`` is rejected.

        Raises:
            TypeError: If ``value`` is not a real number, ``bool``
                included. The previous setpoint is left unchanged.
            ValueError: If ``value`` is not finite. The previous setpoint
                is left unchanged.
            OverflowError: If ``value`` is too large to represent as a
                float. The previous setpoint is left unchanged.
        """
        self._setpoint = _finite("setpoint", value)

    @property
    def history_length(self) -> int:
        """The rolling command-window length given at construction.

        Read-only: the window is sized once, at construction, and a
        restore through :meth:`from_dict` builds a new controller rather
        than resizing this one.
        """
        return self._history_length

    @property
    def integral(self) -> float:
        """The integral accumulator."""
        return self._integral

    @integral.setter
    def integral(self, value: float) -> None:
        """Set the integral accumulator.

        Args:
            value: A finite real number. ``bool`` is rejected.

        Raises:
            TypeError: If ``value`` is not a real number, ``bool``
                included. The previous value is left unchanged.
            ValueError: If ``value`` is not finite. The previous value is
                left unchanged.
            OverflowError: If ``value`` is too large to represent as a
                float. The previous value is left unchanged.
        """
        self._integral = _finite("integral", value)

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
        long-run ON fraction converges to the target demand level.

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

    @property
    def pi_output(self) -> float | None:
        """The clamped PI result of the last :meth:`update` call.

        Reports the PI loop's demand even while :attr:`fixed_output` holds
        the actuator at a different level, so a caller can see demand and
        actual command side by side.

        Returns:
            The clamped PI output in [``OUTPUT_MIN``, ``OUTPUT_MAX``], or
            ``None`` before the first :meth:`update` call and again after
            :meth:`reset`.
        """
        return self._pi_output

    @property
    def fixed_output(self) -> float | None:
        """The current fixed-output override, or ``None`` if unset.

        Returns:
            The fixed actuator level in [``OUTPUT_MIN``, ``OUTPUT_MAX``], or
            ``None`` when the PI loop is in control.
        """
        return self._fixed_output

    @fixed_output.setter
    def fixed_output(self, value: float | None) -> None:
        """Set or clear the fixed-output override.

        Args:
            value: A finite number in [``OUTPUT_MIN``, ``OUTPUT_MAX``] to
                fix the output, or ``None`` to release it back to the PI
                loop. ``bool`` is rejected. Stored as ``float(value)``, so
                an ``int`` such as ``0`` or ``1`` is accepted and read back
                as a float.

        Raises:
            TypeError: If ``value`` is not ``None`` and is not a real
                number, ``bool`` included. The previous setting is left
                unchanged.
            ValueError: If ``value`` is not ``None`` and is not finite or
                lies outside [``OUTPUT_MIN``, ``OUTPUT_MAX``]. The previous
                setting is left unchanged.
            OverflowError: If ``value`` is too large to represent as a
                float. The previous setting is left unchanged.
        """
        if value is None:
            self._fixed_output = None
            return
        self._fixed_output = _level("fixed_output", value)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_command(self, level: float) -> float:
        """Map a demand level to a mode-specific actuator command.

        ``level`` is the demand level (the PI output, or the fixed output
        when one is set).  For ``RADIATOR`` mode it is forwarded unchanged
        (already in [0, 1]).  For ``FLOOR_HEATING`` mode a binary signal is
        derived using duty-cycle modulation:

        * If ``level`` is at or below ``OUTPUT_MIN`` the slot is always OFF.
        * If ``level`` is at or above ``OUTPUT_MAX`` the slot is always ON.
        * Otherwise the slot is ON when the realised duty cycle so far
          (mean of the trailing window) is below the target ``level``.  Over
          many cycles this causes the ON-fraction to converge to ``level``,
          which is why a long window (24 samples = 2 h at 5-min intervals)
          is needed for good modulation fidelity.

        This method is called BEFORE the new command is appended to the
        history, so :attr:`duty_cycle` reflects only past samples.

        Args:
            level: Demand level in [``OUTPUT_MIN``, ``OUTPUT_MAX``] — the PI
                output, or the fixed output when one is set.

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


def main() -> None:
    """Demonstrate every public element of the pi_controller module.

    This function constructs :class:`PIController` instances in both
    :attr:`HeatingMode.RADIATOR` and :attr:`HeatingMode.FLOOR_HEATING`
    modes, performs several :meth:`~PIController.update` calls that
    include a mid-run setpoint change and a :attr:`~PIController.fixed_output`
    override, and prints the :attr:`~PIController.history`,
    :attr:`~PIController.duty_cycle`, :attr:`~PIController.is_history_full`
    and :attr:`~PIController.pi_output` properties, a :attr:`~PIController.mode`
    reassignment, and the effect of :meth:`~PIController.reset`.  It also
    demonstrates the ``HeatingMode`` enum directly, a state snapshot round
    trip through :meth:`~PIController.to_dict` and
    :meth:`~PIController.from_dict` (including a JSON round trip), and shows
    that invalid or non-numeric constructor and attribute values, and an
    invalid snapshot, raise :class:`ValueError` or :class:`TypeError`.
    """
    print("=== HeatingMode enum ===")
    for hm in HeatingMode:
        print(f"  HeatingMode.{hm.name} = {hm.value!r}")

    print("\n=== RADIATOR mode (5-step warm-up) ===")
    # Construct with explicit gains and a 6-sample window for this demo.
    ctrl_rad = PIController(
        kp=0.3,
        ki=0.015,
        mode=HeatingMode.RADIATOR,
        setpoint=21.0,
        history_length=6,
    )
    print(f"  Initial integral : {ctrl_rad.integral}")
    print(f"  Initial duty_cycle: {ctrl_rad.duty_cycle}")
    print(f"  is_history_full  : {ctrl_rad.is_history_full}")

    # Simulate a cold room gradually warming up.
    temps_rad = [18.0, 18.5, 19.2, 20.0, 20.6, 21.3]
    for i, t in enumerate(temps_rad):
        cmd = ctrl_rad.update(measured=t)
        print(
            f"  step {i + 1}: measured={t:.1f} C  command={cmd:.4f}"
            f"  integral={ctrl_rad.integral:.4f}"
        )

    print(f"  history         : {ctrl_rad.history}")
    print(f"  duty_cycle      : {ctrl_rad.duty_cycle:.4f}")
    print(f"  is_history_full : {ctrl_rad.is_history_full}")

    # Demonstrate mid-run setpoint change via the update() keyword argument.
    print("\n  -- setpoint raised to 22 deg C mid-run --")
    cmd = ctrl_rad.update(measured=21.3, setpoint=22.0)
    print(
        f"  command after setpoint change: {cmd:.4f}  (setpoint now {ctrl_rad.setpoint})"
    )

    # Demonstrate the fixed_output override.
    print("\n  -- fixed_output override --")
    fixed_level = 0.2
    fixed_steps = 3
    cold_temp = 18.0

    ctrl_rad.fixed_output = fixed_level

    for i in range(fixed_steps):
        cmd = ctrl_rad.update(measured=cold_temp)
        print(
            f"  fixed step {i + 1}: command={cmd:.4f}  integral={ctrl_rad.integral:.4f}"
            f"  pi_output={ctrl_rad.pi_output:.4f}"
        )
    print(f"  fixed_output    : {ctrl_rad.fixed_output}")

    ctrl_rad.fixed_output = None
    cmd = ctrl_rad.update(measured=cold_temp)
    print(
        f"  fixed_output    : {ctrl_rad.fixed_output}  (released -> PI result: {cmd:.4f})"
    )

    # Demonstrate reset().
    ctrl_rad.reset()
    print(
        f"\n  After reset(): integral={ctrl_rad.integral}, history={ctrl_rad.history}"
    )

    print("\n=== FLOOR_HEATING mode (24-sample window) ===")
    # Use the default 24-sample window (24 × 5 min = 2 h at HA polling rate).
    ctrl_floor = PIController(
        kp=0.3,
        ki=0.015,
        mode="floor_heating",  # plain string coercion
        setpoint=21.0,
        history_length=24,
    )
    print(f"  mode coerced to : {ctrl_floor.mode!r}")

    # Run 10 steps at a constant cold temperature to observe binary switching.
    print("  10 steps at 19.0 deg C (demand > 0 -> ON/OFF switching):")
    for i in range(10):
        cmd = ctrl_floor.update(measured=19.0)
        print(
            f"    step {i + 1:2d}: duty_cycle={ctrl_floor.duty_cycle:.3f}"
            f"  command={cmd:.0f}"
        )

    print(f"  is_history_full : {ctrl_floor.is_history_full}")

    # Run to full window.
    print(f"  Running {24 - len(ctrl_floor.history)} more steps to fill window...")
    while not ctrl_floor.is_history_full:
        ctrl_floor.update(measured=19.0)
    print(f"  is_history_full : {ctrl_floor.is_history_full}")
    print(f"  duty_cycle      : {ctrl_floor.duty_cycle:.3f}")

    print("\n=== State snapshot and restore ===")
    # A fresh controller, unrelated to ctrl_rad and ctrl_floor above, holding
    # a fixed_output override -- the case a restart must not lose.
    hold_level = 0.2  # None, or a level in [0, 1]
    ctrl_saved = PIController(kp=0.3, ki=0.015, setpoint=21.0, fixed_output=hold_level)
    cold_measurement = 18.0

    ctrl_saved.update(measured=cold_measurement)
    ctrl_saved.update(measured=cold_measurement)

    snapshot = ctrl_saved.to_dict()
    text = json.dumps(snapshot)
    restored = PIController.from_dict(json.loads(text))

    next_saved = ctrl_saved.update(measured=cold_measurement)
    next_restored = restored.update(measured=cold_measurement)

    print(f"  snapshot JSON          : {text}")
    print(f"  restored fixed_output  : {restored.fixed_output}")
    print(f"  next command (saved)   : {next_saved:.4f}")
    print(f"  next command (restored): {next_restored:.4f}")

    # A snapshot missing a required key raises ValueError naming it.
    bad_snapshot = {"kp": 0.3}
    try:
        PIController.from_dict(bad_snapshot)
    except ValueError as exc:
        print(f"  Bad snapshot            -> ValueError: {exc}")

    # Demonstrate reassigning mode on an existing (just-reset) controller.
    print("\n  -- mode reassigned on the radiator controller --")
    new_mode = HeatingMode.FLOOR_HEATING  # RADIATOR, FLOOR_HEATING

    ctrl_rad.mode = new_mode
    cmd = ctrl_rad.update(measured=cold_temp)

    print(f"  command after mode change: {cmd:.0f}  (mode now {ctrl_rad.mode!r})")

    print("\n=== ValueError and TypeError demonstrations ===")

    # Invalid mode string.
    try:
        PIController(mode="steam")
    except ValueError as exc:
        print(f"  Bad mode       -> ValueError: {exc}")

    # Non-finite kp.
    try:
        PIController(kp=float("inf"))
    except ValueError as exc:
        print(f"  Infinite kp    -> ValueError: {exc}")

    # history_length < 1.
    try:
        PIController(history_length=0)
    except ValueError as exc:
        print(f"  history_length=0 -> ValueError: {exc}")

    # fixed_output out of range.
    bad_level = 1.5
    try:
        PIController(fixed_output=bad_level)
    except ValueError as exc:
        print(f"  fixed_output={bad_level} -> ValueError: {exc}")

    # Non-finite measurement.
    try:
        ctrl_rad.update(measured=float("nan"))
    except ValueError as exc:
        print(f"  NaN measured   -> ValueError: {exc}")

    # Non-finite setpoint passed to update().
    try:
        ctrl_rad.update(measured=20.0, setpoint=float("nan"))
    except ValueError as exc:
        print(f"  NaN setpoint   -> ValueError: {exc}")

    # Non-numeric setpoint (TypeError, not ValueError).
    bad_setpoint = "22"
    try:
        ctrl_rad.setpoint = bad_setpoint  # type: ignore[assignment]  # deliberate misuse for the demo
    except TypeError as exc:
        print(f"  Bad setpoint   -> TypeError: {exc}")

    print("\nAll demonstrations completed successfully.")


if __name__ == "__main__":
    main()
