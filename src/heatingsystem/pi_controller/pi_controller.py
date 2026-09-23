"""PI controller for residential heating systems.

This module provides :class:`PIController`, a discrete-time proportional-integral
controller designed to regulate room temperature by modulating a heating actuator.
The controller is driven externally (e.g. by Home Assistant / AppDaemon on a fixed
5-minute polling interval) and contains no internal timing or scheduling logic.

The actuator side — the mode, the rolling history window, the duty cycle and the
fixed-output hold — is owned by a :class:`~heatingsystem.modulator.modulator.Modulator`,
which the controller composes and delegates to; see that module for the mode
mapping itself. This module re-exports :class:`~heatingsystem.modulator.modulator.HeatingMode`,
``OUTPUT_MIN`` and ``OUTPUT_MAX`` from there so existing imports keep working.

A controller's output can also be pinned to a fixed level via
:attr:`PIController.fixed_output`, overriding the PI result while the PI
calculation, including the integral, keeps running underneath it.
"""

import json
import math
from collections.abc import Mapping
from typing import Self

from heatingsystem import _validation
from heatingsystem.modulator.modulator import (
    OUTPUT_MAX,
    OUTPUT_MIN,
    HeatingMode,
    Modulator,
)

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


class PIController:
    """Discrete-time PI controller for residential heating.

    Every setting — ``kp``, ``ki``, ``mode``, ``setpoint``, ``fixed_output``
    and ``integral`` — is a validating property: assigning it, at
    construction or afterwards, raises on a bad value and leaves the
    previous value unchanged. See :mod:`heatingsystem._validation` for the
    numeric contract behind them. ``history_length`` is validated at
    construction and exposed as a read-only property; the window itself
    cannot be resized afterwards.

    The actuator mapping — the mode, the rolling history window, the duty
    cycle and the fixed-output hold — is owned by a composed
    :class:`~heatingsystem.modulator.modulator.Modulator`, reachable only
    through this controller's own properties and methods; the modulator
    instance itself is not exposed.

    The controller is stateful: it accumulates an integral term across
    successive :meth:`update` calls, and its modulator maintains a rolling
    window of past actuator commands for duty-cycle estimation (floor
    heating).

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
        TypeError, ValueError, OverflowError: See
            :mod:`heatingsystem._validation` for the numeric contract
            behind ``kp``, ``ki``, ``setpoint`` and ``fixed_output``
            (plus its range check for ``fixed_output``); each raises
            naming the offending attribute.

    Example:
        >>> ctrl = PIController(kp=0.5, ki=0.02, setpoint=22.0)
        >>> ctrl.update(20.0)
        1.0
    """

    _kp: float
    _ki: float
    _setpoint: float
    _integral: float
    _pi_output: float | None
    _modulator: Modulator

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
        self._modulator = Modulator(mode, history_length=history_length)

        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint

        # Integral accumulator, reset to zero on construction and via reset().
        self._integral = 0.0

        # Clamped PI result of the last update(); None before the first step.
        self._pi_output = None

        # Fixed output override — delegated to the modulator, assigned last
        # to keep the original constructor's validation order.
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
            usual anti-windup, except on the non-finite raw sum described
            under Note), and the fixed command is still recorded in
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
        measured = _validation.finite("measured", measured)

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
        new_integral: float = self._integral + error
        raw: float = self.kp * error + self.ki * new_integral

        # Reachable only with extreme finite inputs: a non-finite raw sum or
        # tentative integral (kp * -inf, an overflowing integral, ...) closes
        # the valve and holds the integral rather than propagating nan/inf.
        is_finite: bool = math.isfinite(raw) and math.isfinite(new_integral)

        # Clamp raw output to the actuator's physical range. `+ 0.0`
        # normalises a -0.0 clamp result explicitly, rather than relying on
        # max(OUTPUT_MIN, ...) returning its first (positive) argument when
        # tied with -0.0 -- the same argument-order accident R2 asked this
        # round to remove.
        u: float = (
            (max(OUTPUT_MIN, min(OUTPUT_MAX, raw)) + 0.0) if is_finite else OUTPUT_MIN
        )

        # Anti-windup — only commit the new integral when it is useful:
        #   * Not saturated at all  → always safe to integrate.
        #   * Saturated HIGH (raw >= OUTPUT_MAX) → only integrate when
        #     error < 0, i.e. measurement is rising and integration will
        #     drag the output back down toward the valid range.
        #   * Saturated LOW  (raw <= OUTPUT_MIN) → only integrate when
        #     error > 0, i.e. measurement is falling and integration will
        #     push the output back up toward the valid range.
        #   Otherwise: hold the old integral to avoid making windup worse.
        next_integral = self._integral
        if is_finite:
            if OUTPUT_MIN < raw < OUTPUT_MAX:
                # Inside the linear region — unrestricted integration.
                next_integral = new_integral
            elif raw >= OUTPUT_MAX and error < 0:
                # Saturated high but cooling trend: allow integration to wind down.
                next_integral = new_integral
            elif raw <= OUTPUT_MIN and error > 0:
                # Saturated low but warming trend: allow integration to wind up.
                next_integral = new_integral
            # else: output is saturated and error would deepen windup — hold.
        # When raw/new_integral is not finite, the integral is held above.

        # --- Map demand level to actuator command ---
        # The modulator reads its own history window (for floor heating's
        # duty cycle) before appending this step's command, and applies
        # fixed_output itself when one is set; the PI computation and
        # anti-windup above are unaffected either way. Computed into a
        # local before any state is written, so a raise here leaves the
        # controller's own state untouched.
        command = self._modulator.command(u)

        self._integral = next_integral
        self._pi_output = u

        return command

    def reset(self) -> None:
        """Reset the controller state to initial values.

        Clears the integral accumulator, the modulator's history window,
        and :attr:`pi_output`.  The gains, mode, setpoint, and
        :attr:`fixed_output` are left unchanged.
        """
        self._integral = 0.0
        self._modulator.reset()
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
        modulator_state = self._modulator._to_dict()
        return {
            "kp": self.kp,
            "ki": self.ki,
            "setpoint": self.setpoint,
            "mode": modulator_state["mode"],
            "history_length": modulator_state["history_length"],
            "fixed_output": modulator_state["fixed_output"],
            "integral": self.integral,
            "history": modulator_state["history"],
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
        data = _validation.snapshot_mapping(data, _SNAPSHOT_KEYS)

        modulator = Modulator._from_dict(
            {k: data[k] for k in ("mode", "history_length", "fixed_output", "history")}
        )

        controller = cls(
            kp=_validation.finite("kp", data["kp"]),
            ki=_validation.finite("ki", data["ki"]),
            mode=modulator.mode,
            setpoint=_validation.finite("setpoint", data["setpoint"]),
            history_length=modulator.history_length,
            fixed_output=modulator.fixed_output,
        )
        controller.integral = _validation.finite("integral", data["integral"])
        controller._modulator = modulator

        return controller

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> HeatingMode:
        """The heating actuator mode."""
        return self._modulator.mode

    @mode.setter
    def mode(self, value: HeatingMode | str) -> None:
        """Set the heating actuator mode.

        Args:
            value: A :class:`HeatingMode` member or its string value.

        Raises:
            ValueError: If ``value`` is not a valid :class:`HeatingMode`
                member or value. The previous mode is left unchanged.
        """
        self._modulator.mode = value

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
            TypeError, ValueError, OverflowError: See
                :mod:`heatingsystem._validation` for the numeric contract:
                raised naming ``kp``, with the previous gain left
                unchanged.
        """
        self._kp = _validation.finite("kp", value)

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
            TypeError, ValueError, OverflowError: See
                :mod:`heatingsystem._validation` for the numeric contract:
                raised naming ``ki``, with the previous gain left
                unchanged.
        """
        self._ki = _validation.finite("ki", value)

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
            TypeError, ValueError, OverflowError: See
                :mod:`heatingsystem._validation` for the numeric contract:
                raised naming ``setpoint``, with the previous setpoint
                left unchanged.
        """
        self._setpoint = _validation.finite("setpoint", value)

    @property
    def history_length(self) -> int:
        """The rolling command-window length given at construction.

        Read-only: the window is sized once, at construction, and a
        restore through :meth:`from_dict` builds a new controller rather
        than resizing this one.
        """
        return self._modulator.history_length

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
            TypeError, ValueError, OverflowError: See
                :mod:`heatingsystem._validation` for the numeric contract:
                raised naming ``integral``, with the previous value left
                unchanged.
        """
        self._integral = _validation.finite("integral", value)

    @property
    def history(self) -> tuple[float, ...]:
        """Immutable snapshot of the command history, oldest to newest.

        Returns:
            A tuple of past actuator commands in chronological order.
        """
        return self._modulator.history

    @property
    def duty_cycle(self) -> float:
        """Mean of the current history window (fraction of ON-time).

        Returns:
            The mean of the history window, or ``0.0`` when the window
            is empty.
        """
        return self._modulator.duty_cycle

    @property
    def is_history_full(self) -> bool:
        """Whether the rolling history window has been completely filled.

        Returns:
            ``True`` once :attr:`history` contains ``history_length``
            samples; ``False`` during the initial warm-up period.
        """
        return self._modulator.is_history_full

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
        return self._modulator.fixed_output

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
            TypeError, ValueError, OverflowError: See
                :mod:`heatingsystem._validation` for the numeric contract:
                raised naming ``fixed_output`` for a value that is not
                ``None`` and fails it (including a value outside
                [``OUTPUT_MIN``, ``OUTPUT_MAX``]); the previous setting is
                left unchanged.
        """
        self._modulator.fixed_output = value


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
