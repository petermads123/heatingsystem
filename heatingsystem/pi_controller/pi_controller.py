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
"""

import math
from collections import deque
from enum import StrEnum

# ---------------------------------------------------------------------------
# Module-level output clamp constants — the actuator range is always [0, 1].
# ---------------------------------------------------------------------------
OUTPUT_MIN: float = 0.0
OUTPUT_MAX: float = 1.0


class HeatingMode(StrEnum):
    """Heating actuator mode.

    Selects how the normalised PI output (0–1) is translated into an
    actuator command.

    Attributes:
        RADIATOR: Continuous modulation — the PI output is forwarded
            directly to the valve driver.
        FLOOR_HEATING: Binary on/off derived from duty-cycle modulation
            over a rolling 2-hour window (24 samples × 5 min).
    """

    RADIATOR = "radiator"
    FLOOR_HEATING = "floor_heating"


class PIController:
    """Discrete-time PI controller for residential heating.

    The controller is stateful: it accumulates an integral term across
    successive :meth:`update` calls and maintains a rolling window of
    past actuator commands for duty-cycle estimation (floor heating).

    Anti-windup is implemented via *conditional integration*: the integral
    is only advanced when the raw PI output is inside the valid output
    range, or when integration would pull a saturated output back toward
    the valid range.

    Args:
        kp: Proportional gain.  Default ``0.3``.
        ki: Integral gain.  Default ``0.015``.
        mode: Heating mode — ``"radiator"`` or ``"floor_heating"``.
            Accepts a :class:`HeatingMode` instance or a plain string;
            strings are coerced via ``HeatingMode(mode)``.
        setpoint: Initial temperature setpoint in °C.  Default ``21.0``.
        history_length: Length of the rolling command window used for
            duty-cycle calculation in floor-heating mode.  Must be ≥ 1.
            At 5-minute polling intervals, ``24`` equals 2 hours.
            Default ``24``.

    Raises:
        ValueError: If ``mode`` is not a valid :class:`HeatingMode` value.
        ValueError: If ``history_length`` is less than 1.
        ValueError: If ``kp``, ``ki``, or ``setpoint`` are not finite
            numbers (e.g. ``nan``, ``inf``).

    Example:
        >>> ctrl = PIController(kp=0.5, ki=0.02, setpoint=22.0)
        >>> ctrl.update(20.0)
        1.0
    """

    def __init__(
        self,
        kp: float = 0.3,
        ki: float = 0.015,
        mode: HeatingMode | str = HeatingMode.RADIATOR,
        setpoint: float = 21.0,
        *,
        history_length: int = 24,
    ) -> None:
        """Initialise the PI controller.

        Args:
            kp: Proportional gain.
            ki: Integral gain.
            mode: Heating actuator mode.
            setpoint: Initial temperature setpoint in °C.
            history_length: Rolling window length for duty-cycle
                computation (must be ≥ 1).

        Raises:
            ValueError: If ``mode`` is not a recognised :class:`HeatingMode`.
            ValueError: If ``history_length`` < 1.
            ValueError: If ``kp``, ``ki``, or ``setpoint`` are non-finite.
        """
        # --- Coerce and validate mode ---
        try:
            self.mode: HeatingMode = HeatingMode(mode)
        except ValueError as exc:
            valid = [m.value for m in HeatingMode]
            raise ValueError(
                f"Invalid heating mode {mode!r}. Must be one of {valid}."
            ) from exc

        # --- Validate numeric constructor arguments ---
        if history_length < 1:
            raise ValueError(
                f"history_length must be >= 1, got {history_length}."
            )
        if not math.isfinite(kp):
            raise ValueError(f"kp must be a finite number, got {kp!r}.")
        if not math.isfinite(ki):
            raise ValueError(f"ki must be a finite number, got {ki!r}.")
        if not math.isfinite(setpoint):
            raise ValueError(f"setpoint must be a finite number, got {setpoint!r}.")

        self.kp: float = kp
        self.ki: float = ki
        self.setpoint: float = setpoint

        # Integral accumulator, reset to zero on construction and via reset().
        self.integral: float = 0.0

        # Rolling window of past actuator commands.
        # maxlen=24 at 5-min intervals == 2 h of history for duty-cycle tracking.
        self._history: deque[float] = deque(maxlen=history_length)

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
            it is binary: 0.0 (off) or 1.0 (on).

        Raises:
            ValueError: If ``measured`` or the new ``setpoint`` are
                non-finite.
        """
        # --- Optional setpoint update ---
        if setpoint is not None:
            if not math.isfinite(setpoint):
                raise ValueError(
                    f"setpoint must be a finite number, got {setpoint!r}."
                )
            self.setpoint = setpoint

        # --- Validate measurement ---
        if not math.isfinite(measured):
            raise ValueError(
                f"measured must be a finite number, got {measured!r}."
            )

        # --- Error: positive means too cold, controller ramps output up ---
        error: float = self.setpoint - measured

        # --- PI computation with conditional-integration anti-windup ---
        #
        # We tentatively integrate first, compute the raw output, then decide
        # whether to commit the new integral value.  This prevents the integral
        # from winding up when the actuator is saturated.
        new_integral: float = self.integral + error
        raw: float = self.kp * error + self.ki * new_integral

        # Clamp raw output to the actuator's physical range.
        u: float = max(OUTPUT_MIN, min(OUTPUT_MAX, raw))

        # Anti-windup — only commit the new integral when it is useful:
        #   * Not saturated at all  → always safe to integrate.
        #   * Saturated HIGH (raw >= OUTPUT_MAX) → only integrate when
        #     error < 0, i.e. measurement is rising and integration will
        #     drag the output back down toward the valid range.
        #   * Saturated LOW  (raw <= OUTPUT_MIN) → only integrate when
        #     error > 0, i.e. measurement is falling and integration will
        #     push the output back up toward the valid range.
        #   Otherwise: hold the old integral to avoid making windup worse.
        if OUTPUT_MIN < raw < OUTPUT_MAX:
            # Inside the linear region — unrestricted integration.
            self.integral = new_integral
        elif raw >= OUTPUT_MAX and error < 0:
            # Saturated high but cooling trend: allow integration to wind down.
            self.integral = new_integral
        elif raw <= OUTPUT_MIN and error > 0:
            # Saturated low but warming trend: allow integration to wind up.
            self.integral = new_integral
        # else: output is saturated and error would deepen the windup — hold.

        # --- Map normalised output to actuator command ---
        # _to_command reads self._history (the trailing window) BEFORE we
        # append the new command, so duty_cycle reflects only past samples.
        command: float = self._to_command(u)

        # Append AFTER _to_command so this step's command is not included
        # in its own duty-cycle calculation.
        self._history.append(command)

        return command

    def reset(self) -> None:
        """Reset the controller state to initial values.

        Clears the integral accumulator and the history window.  The gains,
        mode, and setpoint are left unchanged.
        """
        self.integral = 0.0
        self._history.clear()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

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
        long-run ON fraction converges to the target PI output.

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
        return len(self._history) == self._history.maxlen

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_command(self, u: float) -> float:
        """Map a normalised PI output to a mode-specific actuator command.

        For ``RADIATOR`` mode the output is forwarded unchanged (already in
        [0, 1]).  For ``FLOOR_HEATING`` mode a binary signal is derived
        using duty-cycle modulation:

        * If ``u`` is at or below ``OUTPUT_MIN`` the slot is always OFF.
        * If ``u`` is at or above ``OUTPUT_MAX`` the slot is always ON.
        * Otherwise the slot is ON when the realised duty cycle so far
          (mean of the trailing window) is below the target ``u``.  Over
          many cycles this causes the ON-fraction to converge to ``u``,
          which is why a long window (24 samples = 2 h at 5-min intervals)
          is needed for good modulation fidelity.

        This method is called BEFORE the new command is appended to the
        history, so :attr:`duty_cycle` reflects only past samples.

        Args:
            u: Normalised PI output in [``OUTPUT_MIN``, ``OUTPUT_MAX``].

        Returns:
            The actuator command: a float in [0.0, 1.0] for ``RADIATOR``
            mode, or exactly 0.0 or 1.0 for ``FLOOR_HEATING`` mode.
        """
        if self.mode is HeatingMode.RADIATOR:
            # Continuous modulation — pass through directly.
            return u

        # --- FLOOR_HEATING: duty-cycle modulation ---
        if u <= OUTPUT_MIN:
            # No demand — keep the floor off.
            return 0.0
        if u >= OUTPUT_MAX:
            # Full demand — keep the floor on.
            return 1.0

        # Fire this slot when the realised duty so far is below target u.
        # Over a full window the ON-fraction will converge to u, spreading
        # heat pulses evenly rather than bunching them at the start of the window.
        return 1.0 if self.duty_cycle < u else 0.0


def main() -> None:
    """Demonstrate every public element of the pi_controller module.

    This function constructs :class:`PIController` instances in both
    :attr:`HeatingMode.RADIATOR` and :attr:`HeatingMode.FLOOR_HEATING`
    modes, performs several :meth:`~PIController.update` calls that
    include a mid-run setpoint change, and prints the :attr:`~PIController.history`,
    :attr:`~PIController.duty_cycle`, :attr:`~PIController.is_history_full`
    properties and the effect of :meth:`~PIController.reset`.  It also
    demonstrates the ``HeatingMode`` enum directly and shows that invalid
    constructor arguments raise :class:`ValueError`.
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
    print(f"  command after setpoint change: {cmd:.4f}  (setpoint now {ctrl_rad.setpoint})")

    # Demonstrate reset().
    ctrl_rad.reset()
    print(f"\n  After reset(): integral={ctrl_rad.integral}, history={ctrl_rad.history}")

    print("\n=== FLOOR_HEATING mode (24-sample window) ===")
    # Use the default 24-sample window (24 × 5 min = 2 h at HA polling rate).
    ctrl_floor = PIController(
        kp=0.3,
        ki=0.015,
        mode="floor_heating",   # plain string coercion
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

    print("\n=== ValueError demonstrations ===")

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

    print("\nAll demonstrations completed successfully.")


if __name__ == "__main__":
    main()
