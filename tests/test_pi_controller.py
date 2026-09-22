"""Tests for the PIController and HeatingMode API."""

import math
import re
import sys
from decimal import Decimal
from fractions import Fraction

import pytest

import heatingsystem as hs
from heatingsystem.pi_controller import pi_controller as pi_controller_module

# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------


def test_construction_defaults() -> None:
    ctrl = hs.PIController()
    # Verify the shipped defaults match the contract.
    assert ctrl.kp == pytest.approx(0.3)
    assert ctrl.ki == pytest.approx(0.015)
    assert ctrl.integral == pytest.approx(0.0)
    assert ctrl.duty_cycle == pytest.approx(0.0)
    assert ctrl.history == ()
    assert not ctrl.is_history_full


def test_construction_explicit_gains() -> None:
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=22.0)
    assert ctrl.kp == pytest.approx(0.5)
    assert ctrl.ki == pytest.approx(0.02)


def test_mode_accepts_enum_radiator() -> None:
    ctrl = hs.PIController(mode=hs.HeatingMode.RADIATOR)
    # Should not raise; just a smoke test.
    out = ctrl.update(20.0)
    assert 0.0 <= out <= 1.0


def test_mode_accepts_string_radiator() -> None:
    ctrl = hs.PIController(mode="radiator")
    out = ctrl.update(20.0)
    assert 0.0 <= out <= 1.0


def test_mode_accepts_enum_floor_heating() -> None:
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING)
    out = ctrl.update(20.0)
    assert out in {0.0, 1.0}


def test_mode_accepts_string_floor_heating() -> None:
    ctrl = hs.PIController(mode="floor_heating")
    out = ctrl.update(20.0)
    assert out in {0.0, 1.0}


def test_invalid_mode_string_raises() -> None:
    with pytest.raises(ValueError, match="mode"):
        hs.PIController(mode="steam_radiator")


def test_history_length_zero_raises() -> None:
    with pytest.raises(ValueError, match="history_length"):
        hs.PIController(history_length=0)


def test_history_length_negative_raises() -> None:
    with pytest.raises(ValueError, match="history_length"):
        hs.PIController(history_length=-5)


def test_non_finite_kp_raises() -> None:
    with pytest.raises(ValueError, match="kp"):
        hs.PIController(kp=math.nan)


def test_non_finite_ki_raises() -> None:
    with pytest.raises(ValueError, match="ki"):
        hs.PIController(ki=math.inf)


def test_non_finite_setpoint_raises() -> None:
    with pytest.raises(ValueError, match="setpoint"):
        hs.PIController(setpoint=math.nan)


# ---------------------------------------------------------------------------
# update() — input validation
# ---------------------------------------------------------------------------


def test_update_non_finite_measured_raises() -> None:
    ctrl = hs.PIController()
    with pytest.raises(ValueError, match="measured"):
        ctrl.update(math.nan)


def test_update_inf_measured_raises() -> None:
    ctrl = hs.PIController()
    with pytest.raises(ValueError, match="measured"):
        ctrl.update(math.inf)


def test_update_setpoint_override_non_finite_raises() -> None:
    ctrl = hs.PIController()
    with pytest.raises(ValueError, match="setpoint"):
        ctrl.update(20.0, setpoint=math.inf)


# ---------------------------------------------------------------------------
# Radiator mode — output behaviour
# ---------------------------------------------------------------------------


def test_radiator_positive_error_output_positive() -> None:
    # Room is cold (18 < 21): output must be > 0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(18.0)
    assert out > 0.0


def test_radiator_zero_error_output_zero() -> None:
    # Room exactly at setpoint: first call should give 0 (no integral buildup yet).
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(21.0)
    assert out == pytest.approx(0.0)


def test_radiator_very_large_error_clamped_to_one() -> None:
    # Room 0 C, setpoint 21: raw output >> 1; must clamp to 1.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(0.0)
    assert out == pytest.approx(1.0)


def test_radiator_negative_error_clamped_to_zero() -> None:
    # Room too hot (30 > 21): controller should return 0.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(30.0)
    assert out == pytest.approx(0.0)


def test_radiator_output_always_in_range() -> None:
    # Stress: varied temperatures must all yield outputs in [0, 1].
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    temps = [-50.0, 0.0, 10.0, 20.0, 21.0, 22.0, 30.0, 100.0]
    for t in temps:
        ctrl.reset()
        out = ctrl.update(t)
        assert 0.0 <= out <= 1.0, f"out-of-range for measured={t}: {out}"


def test_radiator_hand_computed_single_step() -> None:
    # kp=0.5, ki=0.02, setpoint=21.0, measured=19.0
    # The error is 2.0, integral becomes 2.0,
    # raw = 0.5*2 + 0.02*2 = 1.04, which clamps to 1.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(19.0)
    assert out == pytest.approx(1.0)


def test_radiator_hand_computed_small_error() -> None:
    # kp=0.3, ki=0.015, setpoint=21.0, measured=20.5
    # The error is 0.5, integral becomes 0.5,
    # raw = 0.3*0.5 + 0.015*0.5 = 0.15 + 0.0075 = 0.1575
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    out = ctrl.update(20.5)
    assert out == pytest.approx(0.1575)


def test_radiator_setpoint_override_per_call() -> None:
    # Passing setpoint kwarg to update() should override the stored setpoint.
    # Override with 22.0; measured=21.0, so error=1.0,
    # integral=1.0, raw=0.3*1+0.015*1=0.315.
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    out = ctrl.update(21.0, setpoint=22.0)
    assert out == pytest.approx(0.315)


# ---------------------------------------------------------------------------
# Anti-windup
# ---------------------------------------------------------------------------


def test_antiwindup_integral_does_not_grow_when_saturated() -> None:
    # Drive the controller into deep saturation (very cold room, setpoint high).
    # After output has been clamped to 1.0 for enough steps, the integral must
    # stop growing — verify it stays constant across several saturated steps.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=40.0)
    # Warm-up: run many steps to reach saturation plateau.
    for _ in range(50):
        ctrl.update(0.0)
    integral_before = ctrl.integral
    # Run more steps; integral must not keep increasing.
    for _ in range(10):
        ctrl.update(0.0)
    integral_after = ctrl.integral
    assert integral_after == pytest.approx(integral_before, abs=1e-6), (
        f"Integral kept growing under saturation: {integral_before} -> {integral_after}"
    )


def test_antiwindup_integral_does_not_grow_negative_saturation() -> None:
    # Drive controller to bottom saturation (room too hot).
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=5.0)
    for _ in range(50):
        ctrl.update(40.0)
    integral_before = ctrl.integral
    for _ in range(10):
        ctrl.update(40.0)
    integral_after = ctrl.integral
    assert integral_after == pytest.approx(integral_before, abs=1e-6), (
        f"Integral kept growing in negative saturation: {integral_before} -> {integral_after}"
    )


# ---------------------------------------------------------------------------
# Floor-heating mode
# ---------------------------------------------------------------------------


def test_floor_heating_output_binary() -> None:
    # Every returned value must be exactly 0.0 or 1.0.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    for measured in [15.0, 19.0, 20.5, 21.0, 22.0, 25.0]:
        ctrl.reset()
        out = ctrl.update(measured)
        assert out in {0.0, 1.0}, f"Non-binary output {out} for measured={measured}"


def test_floor_heating_full_demand_gives_one() -> None:
    # Very cold room: continuous output would be 1.0, so floor-heating must also be 1.0.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    out = 0.0
    for _ in range(5):
        out = ctrl.update(-10.0)
    assert out == pytest.approx(1.0)


def test_floor_heating_no_demand_gives_zero() -> None:
    # Very hot room: continuous output is 0.0 -> floor-heating must stay 0.0.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    out = 0.0
    for _ in range(5):
        out = ctrl.update(40.0)
    assert out == pytest.approx(0.0)


def test_floor_heating_duty_cycle_converges() -> None:
    # Over a long run at a moderate constant demand the window duty_cycle must
    # approximate the equivalent continuous (radiator) output within ~1/24.
    #
    # Both a radiator and a floor_heating controller run with the same gains
    # and the same constant measured temperature. After many steps the
    # floor_heating duty_cycle should agree with the radiator duty_cycle.
    kp, ki = 0.3, 0.015
    setpoint = 21.0
    measured = 20.0  # error = 1 -> initial raw ~0.3; rises slowly with integral

    n_steps = 200
    ctrl_r = hs.PIController(kp=kp, ki=ki, setpoint=setpoint)
    ctrl_f = hs.PIController(
        kp=kp, ki=ki, mode=hs.HeatingMode.FLOOR_HEATING, setpoint=setpoint
    )

    for _ in range(n_steps):
        ctrl_r.update(measured)
        ctrl_f.update(measured)

    rad_dc = ctrl_r.duty_cycle
    floor_dc = ctrl_f.duty_cycle

    # Both should be in [0,1] and within 1/24 of each other.
    tolerance = 1.0 / 24.0 + 0.01  # small slack for rounding on window boundary
    assert abs(floor_dc - rad_dc) <= tolerance, (
        f"Floor duty_cycle {floor_dc:.4f} diverges from radiator "
        f"duty_cycle {rad_dc:.4f}"
    )


# ---------------------------------------------------------------------------
# History / deque behaviour
# ---------------------------------------------------------------------------


def test_history_empty_on_fresh_controller() -> None:
    ctrl = hs.PIController()
    assert ctrl.history == ()


def test_history_length_after_n_updates() -> None:
    ctrl = hs.PIController(history_length=24)
    for _ in range(10):
        ctrl.update(20.0)
    assert len(ctrl.history) == 10


def test_history_caps_at_history_length() -> None:
    ctrl = hs.PIController(history_length=24)
    for _ in range(30):
        ctrl.update(20.0)
    # Should never exceed the window size.
    assert len(ctrl.history) == 24


def test_history_ordered_oldest_to_newest() -> None:
    # Use kp=1.0, ki=0 so output changes predictably with temperature.
    # Each call with a different measured value gives a distinct output.
    ctrl = hs.PIController(kp=1.0, ki=0.0, setpoint=21.0, history_length=5)
    outputs = []
    for measured in [18.0, 19.0, 20.0, 20.5, 21.0]:
        out = ctrl.update(measured)
        outputs.append(out)
    snap = ctrl.history
    # Snapshot must be a tuple ordered oldest (first update) to newest (last).
    assert snap == tuple(outputs)


def test_history_is_tuple() -> None:
    ctrl = hs.PIController()
    ctrl.update(20.0)
    assert isinstance(ctrl.history, tuple)


def test_history_snapshot_immutable() -> None:
    # Obtaining the tuple and mutating a copy must not affect the controller.
    ctrl = hs.PIController(history_length=5)
    for _ in range(5):
        ctrl.update(20.0)
    snap = ctrl.history
    # Build a mutable copy and verify the next history call is unchanged.
    snap_list = list(snap)
    snap_list[0] = -999.0
    assert ctrl.history == snap  # controller unaffected


def test_history_oldest_dropped_after_overflow() -> None:
    # When more than history_length items are added, the oldest must be dropped.
    ctrl = hs.PIController(kp=1.0, ki=0.0, setpoint=21.0, history_length=3)
    # Fill the window.
    out1 = ctrl.update(18.0)  # error=3 -> 3.0 -> clamp=1.0
    out2 = ctrl.update(20.0)  # error=1 -> 1.0
    out3 = ctrl.update(20.5)  # error=0.5 -> 0.5
    assert ctrl.history == (out1, out2, out3)
    # Push one more; out1 must be evicted.
    out4 = ctrl.update(20.8)  # error=0.2 -> 0.2
    assert ctrl.history == (out2, out3, out4)


# ---------------------------------------------------------------------------
# duty_cycle
# ---------------------------------------------------------------------------


def test_duty_cycle_zero_on_fresh_controller() -> None:
    ctrl = hs.PIController()
    assert ctrl.duty_cycle == pytest.approx(0.0)


def test_duty_cycle_correct_mean() -> None:
    # Radiator mode, kp=1.0, ki=0: output equals clamp(error, 0, 1).
    # setpoint=1.0: measured=0.0 -> output=1.0; measured=1.0 -> output=0.0;
    # measured=0.5 -> output=0.5.  Mean of [1.0, 0.0, 0.5] = 0.5.
    ctrl = hs.PIController(kp=1.0, ki=0.0, setpoint=1.0, history_length=10)
    ctrl.update(0.0)  # output = clamp(1.0, 0, 1) = 1.0
    ctrl.update(1.0)  # output = clamp(0.0, 0, 1) = 0.0
    ctrl.update(0.5)  # output = clamp(0.5, 0, 1) = 0.5
    assert ctrl.duty_cycle == pytest.approx((1.0 + 0.0 + 0.5) / 3)


# ---------------------------------------------------------------------------
# is_history_full
# ---------------------------------------------------------------------------


def test_is_history_full_false_before_window_fills() -> None:
    ctrl = hs.PIController(history_length=5)
    for step in range(4):
        ctrl.update(20.0)
        assert not ctrl.is_history_full, f"should not be full after {step + 1} updates"


def test_is_history_full_true_at_history_length() -> None:
    ctrl = hs.PIController(history_length=5)
    for _ in range(5):
        ctrl.update(20.0)
    assert ctrl.is_history_full


def test_is_history_full_remains_true_after_overflow() -> None:
    ctrl = hs.PIController(history_length=5)
    for _ in range(10):
        ctrl.update(20.0)
    assert ctrl.is_history_full


# ---------------------------------------------------------------------------
# reset() behaviour
# ---------------------------------------------------------------------------


def test_reset_zeroes_integral() -> None:
    # Use a small error (measured=20.5, setpoint=21.0 -> error=0.5) so the
    # first update stays in the linear region and the integral is committed.
    # raw = kp*0.5 + ki*0.5 = 0.5*0.5 + 0.02*0.5 = 0.26, which is < 1.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    ctrl.update(20.5)
    assert ctrl.integral != 0.0
    ctrl.reset()
    assert ctrl.integral == pytest.approx(0.0)


def test_reset_clears_history() -> None:
    ctrl = hs.PIController(history_length=5)
    for _ in range(5):
        ctrl.update(20.0)
    assert len(ctrl.history) == 5
    ctrl.reset()
    assert ctrl.history == ()


def test_reset_duty_cycle_becomes_zero() -> None:
    ctrl = hs.PIController()
    ctrl.update(18.0)
    ctrl.reset()
    assert ctrl.duty_cycle == pytest.approx(0.0)


def test_reset_is_history_full_becomes_false() -> None:
    ctrl = hs.PIController(history_length=3)
    for _ in range(3):
        ctrl.update(20.0)
    assert ctrl.is_history_full
    ctrl.reset()
    assert not ctrl.is_history_full


# ---------------------------------------------------------------------------
# HeatingMode enum accessibility
# ---------------------------------------------------------------------------


def test_heating_mode_accessible_on_hs() -> None:
    assert hs.HeatingMode.RADIATOR == "radiator"
    assert hs.HeatingMode.FLOOR_HEATING == "floor_heating"


# ---------------------------------------------------------------------------
# Generalization: no AppDaemon / home-assistant import required
# ---------------------------------------------------------------------------


def test_no_appdaemon_dependency_required() -> None:
    # The class must work with plain Python floats — no HA/AppDaemon machinery.
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    for measured in [18.0, 19.5, 20.0, 21.0, 22.0]:
        out = ctrl.update(measured)
        assert isinstance(out, float)
        assert 0.0 <= out <= 1.0


def test_floor_heating_no_appdaemon_required() -> None:
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    out = ctrl.update(19.0)
    assert isinstance(out, float)
    assert out in {0.0, 1.0}


# ---------------------------------------------------------------------------
# Floor-heating — duty-cycle convergence and read-before-append (tester gaps)
# ---------------------------------------------------------------------------


def test_floor_duty_cycle_converges_to_fractional_demand() -> None:
    # With ki=0 the internal PI output u is constant for a constant measurement:
    # u = clamp(kp * error). kp=0.3, error=1.0 (setpoint 21, measured 20) -> u=0.3.
    # Over a full 24-sample window the realised ON-fraction must converge to that
    # target demand, within one sample's resolution (1/24).
    ctrl = hs.PIController(
        kp=0.3, ki=0.0, mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0
    )
    for _ in range(500):
        ctrl.update(20.0)
    # Quantised to multiples of 1/24; allow one slot of error inclusive.
    assert abs(ctrl.duty_cycle - 0.3) <= 1.0 / 24.0 + 1e-9


def test_floor_duty_cycle_converges_to_half_demand() -> None:
    # kp=0.5, error=1.0 -> u=0.5. The realised ON-fraction over a 24-sample
    # window is quantised to multiples of 1/24, so it settles at the nearest
    # achievable value (11/24) rather than exactly 0.5. Allow one slot of
    # quantisation error on either side (1/24, inclusive).
    ctrl = hs.PIController(
        kp=0.5, ki=0.0, mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0
    )
    for _ in range(500):
        ctrl.update(20.0)
    assert abs(ctrl.duty_cycle - 0.5) <= 1.0 / 24.0 + 1e-9


def test_floor_sustained_max_demand_is_all_on_no_saturation_bias() -> None:
    # A permanently freezing room must yield ON (1.0) on EVERY step, not just the
    # last one. This guards against a duty-cycle scheme that erroneously throttles
    # at full demand.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    outs = [ctrl.update(-50.0) for _ in range(60)]
    assert all(o == 1.0 for o in outs), f"expected all-ON, got {set(outs)}"
    assert ctrl.duty_cycle == pytest.approx(1.0)


def test_floor_sustained_too_hot_is_all_off() -> None:
    # A permanently overheated room must yield OFF (0.0) on EVERY step.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    outs = [ctrl.update(50.0) for _ in range(60)]
    assert all(o == 0.0 for o in outs), f"expected all-OFF, got {set(outs)}"
    assert ctrl.duty_cycle == pytest.approx(0.0)


def test_floor_first_command_is_on_due_to_read_before_append() -> None:
    # duty_cycle is read BEFORE the new command is appended. On a fresh controller
    # the window is empty so duty_cycle == 0.0. For any 0 < u < 1 the rule
    # "ON when duty_cycle < u" must therefore fire ON (1.0) on the very first step.
    # kp=0.5, ki=0.02, error=0.5 (setpoint 21, measured 20.5) ->
    # raw = 0.5*0.5 + 0.02*0.5 = 0.26, which is strictly inside (0, 1).
    ctrl = hs.PIController(
        kp=0.5, ki=0.02, mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0
    )
    assert ctrl.duty_cycle == pytest.approx(0.0)  # precondition: empty window
    out = ctrl.update(20.5)
    assert out == pytest.approx(1.0)


def test_floor_command_not_in_own_duty_cycle() -> None:
    # Read-before-append also means a single update never sees its own command:
    # after exactly one ON step the duty_cycle reflects that one stored sample
    # (1.0), but the decision for that step used the empty-window value (0.0).
    ctrl = hs.PIController(
        kp=0.5, ki=0.02, mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0
    )
    ctrl.update(20.5)  # ON because empty-window duty 0.0 < u
    assert ctrl.history == (1.0,)
    assert ctrl.duty_cycle == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Anti-windup — built-up integral is bounded and recovers (tester gaps)
# ---------------------------------------------------------------------------


def test_antiwindup_built_up_integral_is_bounded() -> None:
    # Pure-integral controller (kp=0) so the output is driven solely by the
    # accumulated integral: raw = ki * integral. With a small persistent positive
    # error the integral grows until raw reaches OUTPUT_MAX, after which
    # conditional integration holds it (error still > 0, saturated high).
    # The integral must therefore stay bounded near 1/ki, not run away.
    ki = 0.1
    ctrl = hs.PIController(kp=0.0, ki=ki, setpoint=21.0)
    for _ in range(1000):
        ctrl.update(20.5)  # constant error = 0.5
    # The committed integral cannot exceed the windup ceiling by more than one
    # step's worth of error (0.5): the last commit happened while raw was still
    # below OUTPUT_MAX.
    assert ctrl.integral <= (1.0 / ki) + 0.5 + 1e-9
    assert ctrl.integral > 0.0


def test_antiwindup_recovers_after_error_reverses() -> None:
    # Wind the integral up to its ceiling, then reverse the error sign (room
    # suddenly very hot). The output must drop out of saturation within a small,
    # bounded number of steps — proving the integral was not left wound up.
    ctrl = hs.PIController(kp=0.0, ki=0.1, setpoint=21.0)
    for _ in range(1000):
        ctrl.update(20.5)
    assert ctrl.update(20.5) == pytest.approx(1.0)  # confirm saturated high

    recovery_steps = 0
    out = 1.0
    for _ in range(50):
        out = ctrl.update(40.0)  # large negative error
        recovery_steps += 1
        if out < 1.0:
            break
    assert out < 1.0, "controller never recovered from saturation"
    assert recovery_steps <= 5, f"recovery took {recovery_steps} steps"


def test_antiwindup_negative_built_up_integral_bounded_and_recovers() -> None:
    # Mirror image: a persistent negative error drives the integral toward its
    # lower windup ceiling (-1/ki), and a reversed (positive) error must lift the
    # output off zero within a few steps.
    ki = 0.1
    ctrl = hs.PIController(kp=0.0, ki=ki, setpoint=21.0)
    for _ in range(1000):
        ctrl.update(21.5)  # constant error = -0.5
    assert ctrl.integral >= -(1.0 / ki) - 0.5 - 1e-9
    assert ctrl.update(21.5) == pytest.approx(0.0)  # saturated low

    out = 0.0
    steps = 0
    for _ in range(50):
        out = ctrl.update(0.0)  # large positive error
        steps += 1
        if out > 0.0:
            break
    assert out > 0.0, "controller never recovered from low saturation"
    assert steps <= 5, f"recovery took {steps} steps"


# ---------------------------------------------------------------------------
# Deque maxlen with a non-default history_length (tester gap)
# ---------------------------------------------------------------------------


def test_history_maxlen_non_default_length() -> None:
    # A non-default window must cap at exactly history_length and not the
    # hard-coded default of 24.
    ctrl = hs.PIController(history_length=7)
    for _ in range(20):
        ctrl.update(20.0)
    assert len(ctrl.history) == 7
    assert ctrl.is_history_full


def test_history_maxlen_one() -> None:
    # The minimum legal window keeps only the single most recent command.
    ctrl = hs.PIController(kp=1.0, ki=0.0, setpoint=21.0, history_length=1)
    ctrl.update(18.0)  # error 3 -> clamp 1.0
    last = ctrl.update(20.5)  # error 0.5 -> 0.5
    assert ctrl.history == (last,)
    assert len(ctrl.history) == 1
    assert ctrl.is_history_full


# ---------------------------------------------------------------------------
# fixed_output — T1: unfixed behaviour is unchanged (A1)
# ---------------------------------------------------------------------------


def test_fixed_output_defaults_to_none_and_unfixed_sequence_unchanged() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    assert ctrl.fixed_output is None

    outs = [ctrl.update(20.5) for _ in range(4)]  # constant error 0.5

    # Hand-computed: raw = 0.3*0.5 + 0.015*integral, integral += 0.5 each step.
    assert outs == pytest.approx([0.1575, 0.165, 0.1725, 0.18])
    assert ctrl.integral == pytest.approx(2.0)
    assert ctrl.history == tuple(outs)
    assert ctrl.fixed_output is None


# ---------------------------------------------------------------------------
# fixed_output — T2: radiator mode returns exactly the level (A2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("measured", "setpoint"),
    [(-50.0, None), (50.0, None), (21.0, None), (21.0, 30.0), (21.0, 10.0)],
)
def test_fixed_output_radiator_returns_level_regardless_of_error(
    measured: float, setpoint: float | None
) -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    ctrl.update(20.5)
    ctrl.update(20.5)  # a couple of unfixed steps first

    ctrl.fixed_output = 0.2
    out = ctrl.update(measured, setpoint)

    assert out == 0.2
    assert ctrl.history[-1] == 0.2
    assert len(ctrl.history) == 3
    assert ctrl.fixed_output == 0.2
    if setpoint is not None:
        assert ctrl.setpoint == setpoint


@pytest.mark.parametrize("level", [0.0, 1.0])
def test_fixed_output_accepts_boundaries_at_construction_and_setter(
    level: float,
) -> None:
    ctrl = hs.PIController(setpoint=21.0, fixed_output=level)
    assert ctrl.fixed_output == level
    assert ctrl.update(20.0) == level

    ctrl2 = hs.PIController(setpoint=21.0)
    ctrl2.fixed_output = level
    assert ctrl2.fixed_output == level
    assert ctrl2.update(20.0) == level


def test_fixed_output_set_mid_run_leaves_state_untouched_then_pins_every_command() -> (
    None
):
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0, history_length=24)
    for _ in range(3):
        ctrl.update(15.0)  # large error, clamps to 1.0 and holds the integral
    before_history = ctrl.history
    before_integral = ctrl.integral

    ctrl.fixed_output = 0.2  # setting alone must not run a step

    assert ctrl.history == before_history
    assert ctrl.integral == before_integral

    outs = [ctrl.update(m) for m in (15.0, 21.0, 30.0)]
    assert outs == [0.2, 0.2, 0.2]
    last = ctrl.update(20.0, setpoint=25.0)
    assert last == 0.2
    assert ctrl.setpoint == 25.0
    assert ctrl.history == (1.0, 1.0, 1.0, 0.2, 0.2, 0.2, 0.2)


# ---------------------------------------------------------------------------
# fixed_output — T3: floor-heating duty-cycle modulation (A3)
# ---------------------------------------------------------------------------


def test_fixed_output_floor_quarter_level_converges_to_exact_fraction() -> None:
    # At measured=-50 the PI demand would clamp to 1.0; fixed_output=0.25
    # must modulate the *fixed* level instead, giving exactly 6 ON of 24.
    ctrl = hs.PIController(
        kp=0.3,
        ki=0.015,
        mode="floor_heating",
        setpoint=21.0,
        history_length=24,
        fixed_output=0.25,
    )
    outs = [ctrl.update(-50.0) for _ in range(24)]

    assert set(outs) <= {0.0, 1.0}
    on_steps = [i + 1 for i, out in enumerate(outs) if out == 1.0]
    assert on_steps == [1, 6, 10, 14, 18, 22]
    assert ctrl.duty_cycle == pytest.approx(0.25)
    assert ctrl.is_history_full

    for _ in range(476):  # 500 steps total
        ctrl.update(-50.0)
    assert abs(ctrl.duty_cycle - 0.25) <= 1 / 24 + 1e-9


@pytest.mark.parametrize(
    ("level", "measured", "expected"), [(0.0, -50.0, 0.0), (1.0, 50.0, 1.0)]
)
def test_fixed_output_floor_boundary_levels_are_constant(
    level: float, measured: float, expected: float
) -> None:
    ctrl = hs.PIController(mode="floor_heating", setpoint=21.0, fixed_output=level)
    outs = [ctrl.update(measured) for _ in range(60)]
    assert set(outs) == {expected}
    assert ctrl.duty_cycle == pytest.approx(expected)


def test_fixed_output_floor_first_command_reads_history_before_append() -> None:
    ctrl = hs.PIController(mode="floor_heating", setpoint=21.0, fixed_output=0.5)
    assert ctrl.duty_cycle == 0.0  # empty window, so the first slot fires

    out = ctrl.update(50.0)

    assert out == 1.0
    assert ctrl.history == (1.0,)


def test_fixed_output_floor_set_mid_run_reads_existing_history_first() -> None:
    ctrl = hs.PIController(
        kp=0.3, ki=0.015, mode="floor_heating", setpoint=21.0, history_length=24
    )
    for _ in range(24):
        ctrl.update(-50.0)  # fills the window with ON, duty_cycle == 1.0

    ctrl.fixed_output = 0.5
    first = ctrl.update(-50.0)  # duty_cycle (1.0) is not < 0.5, so OFF
    assert first == 0.0

    for _ in range(47):
        ctrl.update(-50.0)
    assert abs(ctrl.duty_cycle - 0.5) <= 1 / 24 + 1e-9


@pytest.mark.parametrize("level", [0.01, 0.3, 0.99])
def test_fixed_output_floor_history_length_one_alternates(level: float) -> None:
    # With a one-slot window any level strictly between 0 and 1 alternates:
    # ON when the single stored sample (last command) is below the level.
    ctrl = hs.PIController(
        mode="floor_heating", setpoint=21.0, history_length=1, fixed_output=level
    )
    outs = [ctrl.update(20.0) for _ in range(6)]
    assert outs == [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]


def test_fixed_output_floor_level_just_above_min_fires_once_per_window() -> None:
    level = math.nextafter(0.0, 1.0)
    ctrl = hs.PIController(
        mode="floor_heating", setpoint=21.0, history_length=24, fixed_output=level
    )
    outs = [ctrl.update(20.0) for _ in range(24)]
    assert outs[0] == 1.0
    assert all(out == 0.0 for out in outs[1:])
    assert ctrl.duty_cycle == pytest.approx(1 / 24)


def test_fixed_output_floor_level_just_below_max_rests_once_per_window() -> None:
    level = math.nextafter(1.0, 0.0)
    ctrl = hs.PIController(
        mode="floor_heating", setpoint=21.0, history_length=24, fixed_output=level
    )
    outs = [ctrl.update(-50.0) for _ in range(24)]
    assert outs.count(0.0) == 1
    assert outs[1] == 0.0
    assert ctrl.duty_cycle == pytest.approx(23 / 24)


# ---------------------------------------------------------------------------
# fixed_output — T4: the PI calculation still runs underneath (A4)
# ---------------------------------------------------------------------------


def test_fixed_output_integral_matches_unfixed_twin_when_error_reverses() -> None:
    # The risk called out in the plan: if anti-windup were keyed on the
    # *issued* command rather than the raw PI output, a fixed 0.0 with a
    # negative error would look saturated-low-and-worsening and wrongly hold.
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    twin = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    for _ in range(20):
        ctrl.update(20.5)
        twin.update(20.5)
    assert ctrl.integral == pytest.approx(10.0)

    ctrl.fixed_output = 0.0
    out = ctrl.update(21.2)  # error now -0.2: room overshot the setpoint
    twin.update(21.2)

    assert out == 0.0
    assert ctrl.integral == twin.integral


@pytest.mark.parametrize(
    "measured",
    [20.5, 0.0, 40.0],
    ids=["linear", "saturated_high_held", "saturated_low_held"],
)
@pytest.mark.parametrize("mode", ["radiator", "floor_heating"])
def test_fixed_output_integral_matches_unfixed_twin_across_regimes(
    mode: str, measured: float
) -> None:
    fixed = hs.PIController(
        kp=0.3, ki=0.015, mode=mode, setpoint=21.0, fixed_output=0.4
    )
    free = hs.PIController(kp=0.3, ki=0.015, mode=mode, setpoint=21.0)
    for _ in range(5):
        fixed.update(measured)
        free.update(measured)
        assert fixed.integral == free.integral


@pytest.mark.parametrize("mode", ["radiator", "floor_heating"])
def test_fixed_output_integral_matches_unfixed_twin_when_error_sequence_reverses(
    mode: str,
) -> None:
    sequence = [20.5] * 5 + [40.0] * 5 + [0.0] * 5
    fixed = hs.PIController(
        kp=0.3, ki=0.015, mode=mode, setpoint=21.0, fixed_output=0.4
    )
    free = hs.PIController(kp=0.3, ki=0.015, mode=mode, setpoint=21.0)
    for measured in sequence:
        fixed.update(measured)
        free.update(measured)
        assert fixed.integral == free.integral


def test_fixed_output_update_stores_setpoint_and_advances_integral() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0, fixed_output=0.0)
    out = ctrl.update(20.0, setpoint=21.5)
    assert out == 0.0
    assert ctrl.setpoint == 21.5
    assert ctrl.integral == pytest.approx(1.5)


@pytest.mark.parametrize(
    ("measured", "setpoint"),
    [
        (float("nan"), None),
        (float("inf"), None),
        (20.0, float("nan")),
        (20.0, float("-inf")),
    ],
)
def test_fixed_output_update_still_validates_and_appends_nothing(
    measured: float, setpoint: float | None
) -> None:
    ctrl = hs.PIController(setpoint=21.0, fixed_output=0.3)
    with pytest.raises(ValueError):
        ctrl.update(measured, setpoint)
    assert ctrl.history == ()
    assert ctrl.integral == 0.0
    assert ctrl.setpoint == 21.0
    assert ctrl.fixed_output == 0.3


# ---------------------------------------------------------------------------
# fixed_output — T5: invalid values raise and leave the setting alone (A5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        math.nextafter(0.0, -math.inf),
        math.nextafter(1.0, math.inf),
        -1e-9,
        1.0 + 1e-9,
        2,
        -1,
    ],
)
def test_fixed_output_rejects_non_finite_and_out_of_range(bad: float) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(bad))):
        hs.PIController(fixed_output=bad)

    ctrl = hs.PIController()
    with pytest.raises(ValueError, match=re.escape(repr(bad))):
        ctrl.fixed_output = bad


def test_fixed_output_failed_set_leaves_previous_value() -> None:
    ctrl = hs.PIController(fixed_output=0.4)
    with pytest.raises(ValueError):
        ctrl.fixed_output = 1.5
    assert ctrl.fixed_output == 0.4

    with pytest.raises(TypeError, match="fixed_output"):
        ctrl.fixed_output = "0.5"  # type: ignore[assignment]  # deliberate misuse
    assert ctrl.fixed_output == 0.4

    ctrl2 = hs.PIController()
    with pytest.raises(ValueError):
        ctrl2.fixed_output = float("nan")
    assert ctrl2.fixed_output is None


@pytest.mark.parametrize("bad", ["0.5", "", "unavailable", "None"])
def test_fixed_output_string_raises_type_error_not_value_error(bad: str) -> None:
    with pytest.raises(TypeError, match="fixed_output"):
        hs.PIController(fixed_output=bad)  # type: ignore[arg-type]  # deliberate misuse

    ctrl = hs.PIController(fixed_output=0.4)
    with pytest.raises(TypeError, match="fixed_output"):
        ctrl.fixed_output = bad  # type: ignore[assignment]  # deliberate misuse
    assert ctrl.fixed_output == 0.4


@pytest.mark.parametrize(("value", "expected"), [(1, 1.0), (0, 0.0)])
def test_fixed_output_int_stored_and_returned_as_float(
    value: int, expected: float
) -> None:
    ctrl = hs.PIController(setpoint=21.0, fixed_output=value)
    assert ctrl.fixed_output == expected
    assert type(ctrl.fixed_output) is float
    out = ctrl.update(20.0)
    assert out == expected
    assert type(out) is float

    ctrl2 = hs.PIController(setpoint=21.0)
    ctrl2.fixed_output = value
    assert ctrl2.fixed_output == expected
    assert type(ctrl2.fixed_output) is float


@pytest.mark.parametrize("bad", [True, False])
def test_fixed_output_bool_raises_type_error_naming_attribute_and_keeps_previous(
    bad: bool,
) -> None:
    # Round 2 flip: fixed_output=True/False used to be accepted and stored as
    # 1.0/0.0 (the opposite of "hold"); B2 rejects bool for every setting.
    with pytest.raises(TypeError, match="fixed_output"):
        hs.PIController(fixed_output=bad)

    ctrl = hs.PIController(fixed_output=0.4)
    with pytest.raises(TypeError, match="fixed_output"):
        ctrl.fixed_output = bad
    assert ctrl.fixed_output == 0.4
    assert ctrl.update(20.0) == 0.4


# ---------------------------------------------------------------------------
# fixed_output — T6: clearing hands control back, reset leaves it set (A6)
# ---------------------------------------------------------------------------


def test_fixed_output_release_returns_twin_command_and_keeps_both_in_history() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0, fixed_output=0.9)
    twin = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    for _ in range(3):
        ctrl.update(20.5)
        twin.update(20.5)

    ctrl.fixed_output = None
    released = ctrl.update(20.5)
    twin_out = twin.update(20.5)

    assert released == twin_out
    assert ctrl.history == (0.9, 0.9, 0.9, released)
    assert ctrl.integral == twin.integral
    assert ctrl.fixed_output is None


def test_fixed_output_floor_release_duty_cycle_recovers_over_one_window() -> None:
    ctrl = hs.PIController(
        kp=0.5,
        ki=0.0,
        mode="floor_heating",
        setpoint=21.0,
        history_length=24,
        fixed_output=0.0,
    )
    for _ in range(24):
        ctrl.update(20.0)  # PI demand is constant 0.5 throughout
    assert ctrl.duty_cycle == 0.0

    ctrl.fixed_output = None
    first_released = ctrl.update(20.0)
    assert first_released == 1.0  # window is still all zeros, so duty < 0.5

    for _ in range(23):
        ctrl.update(20.0)
    assert ctrl.duty_cycle == pytest.approx(0.5)
    assert ctrl.is_history_full


@pytest.mark.parametrize(
    ("mode", "level", "first_after_reset"),
    [("radiator", 0.3, 0.3), ("floor_heating", 0.5, 1.0)],
)
def test_fixed_output_reset_leaves_override_set(
    mode: str, level: float, first_after_reset: float
) -> None:
    ctrl = hs.PIController(
        kp=0.3, ki=0.015, mode=mode, setpoint=21.0, fixed_output=level
    )
    for _ in range(3):
        ctrl.update(20.5)

    ctrl.reset()

    assert ctrl.integral == 0.0
    assert ctrl.history == ()
    assert ctrl.fixed_output == level

    out = ctrl.update(-50.0)
    assert out == first_after_reset


def test_fixed_output_set_and_cleared_while_saturated() -> None:
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    saturated = ctrl.update(19.0)  # error 2.0, clamps to 1.0, integral held at 0

    ctrl.fixed_output = 0.1
    fixed_command = ctrl.update(19.0)

    ctrl.fixed_output = None
    resumed = ctrl.update(19.0)

    assert saturated == 1.0
    assert fixed_command == 0.1
    assert resumed == 1.0
    assert ctrl.history == (1.0, 0.1, 1.0)
    assert ctrl.integral == 0.0


# ===========================================================================
# Round 2 — attribute surface: kp/ki/setpoint/mode as validating properties,
# pi_output, OUTPUT_MIN/OUTPUT_MAX re-exported (development/feat/fixed-output/
# 02-attribute-surface.md)
# ===========================================================================

# ---------------------------------------------------------------------------
# T1 (B1): kp, ki, setpoint reject non-finite values; the previous value is
# kept; the fixed_output range error names the value the caller passed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr", ["kp", "ki", "setpoint"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_numeric_setters_reject_non_finite_naming_attribute_and_keep_previous(
    attr: str, bad: float
) -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    previous = getattr(ctrl, attr)

    with pytest.raises(ValueError, match=re.escape(repr(bad))) as excinfo:
        setattr(ctrl, attr, bad)
    assert attr in str(excinfo.value)
    assert getattr(ctrl, attr) == previous

    with pytest.raises(ValueError, match=attr):
        hs.PIController(**{attr: bad})  # type: ignore[arg-type]  # attr is kp/ki/setpoint


def test_fixed_output_range_error_repeats_the_passed_value_not_the_float() -> None:
    ctrl = hs.PIController(fixed_output=0.4)

    with pytest.raises(ValueError, match=re.escape("Fraction(3, 2)")):
        ctrl.fixed_output = Fraction(3, 2)  # type: ignore[assignment]  # numbers.Real, not a float
    assert ctrl.fixed_output == 0.4

    ctrl.fixed_output = Fraction(1, 2)  # type: ignore[assignment]  # numbers.Real, not a float
    assert ctrl.fixed_output == 0.5
    assert type(ctrl.fixed_output) is float


# ---------------------------------------------------------------------------
# T2 (B2, B7): non-numeric and bool inputs raise TypeError naming the
# attribute; int and Fraction are accepted and read back as float; a huge
# int raises OverflowError rather than ValueError (decided at the plan gate,
# plan's Critique item 1 — not drift from section 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "0.3",
        "",
        None,
        [0.3],
        (0.3,),
        {},
        complex(0.3, 0),
        Decimal("0.3"),
        Decimal("nan"),
        b"0.3",
        True,
        False,
    ],
)
@pytest.mark.parametrize("attr", ["kp", "ki", "setpoint"])
def test_numeric_setters_reject_non_real_types_naming_attribute_and_type(
    attr: str, bad: object
) -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    previous = getattr(ctrl, attr)

    with pytest.raises(TypeError, match=attr) as excinfo:
        setattr(ctrl, attr, bad)
    assert type(bad).__name__ in str(excinfo.value)
    assert getattr(ctrl, attr) == previous

    with pytest.raises(TypeError, match=attr):
        hs.PIController(**{attr: bad})  # type: ignore[arg-type]  # deliberate misuse


@pytest.mark.parametrize(
    "bad",
    ["0.3", [0.3], True, False, Decimal("0.3")],
    ids=["str", "list", "true", "false", "decimal"],
)
def test_fixed_output_rejects_non_real_types_naming_attribute(bad: object) -> None:
    ctrl = hs.PIController(fixed_output=0.4)
    with pytest.raises(TypeError, match="fixed_output"):
        ctrl.fixed_output = bad  # type: ignore[assignment]  # deliberate misuse
    assert ctrl.fixed_output == 0.4


@pytest.mark.parametrize(
    "bad", ["20", [20.0], True, False], ids=["str", "list", "true", "false"]
)
def test_update_measured_rejects_non_numeric_and_bool_naming_measured(
    bad: object,
) -> None:
    ctrl = hs.PIController(setpoint=21.0)
    with pytest.raises(TypeError, match="measured"):
        ctrl.update(bad)  # type: ignore[arg-type]  # deliberate misuse


@pytest.mark.parametrize(
    "bad", ["22", [22.0], True, False], ids=["str", "list", "true", "false"]
)
def test_update_setpoint_kwarg_rejects_non_numeric_and_bool_naming_setpoint(
    bad: object,
) -> None:
    ctrl = hs.PIController(setpoint=21.0)
    with pytest.raises(TypeError, match="setpoint"):
        ctrl.update(20.0, setpoint=bad)  # type: ignore[arg-type]  # deliberate misuse
    assert ctrl.setpoint == 21.0


def test_update_setpoint_none_is_not_an_override_not_a_type_error() -> None:
    # None is a TypeError for the numeric setters, but update()'s setpoint
    # parameter uses None as "no override" by design (its own Optional
    # contract, not a validated value) — the two are different questions.
    ctrl = hs.PIController(setpoint=21.0)
    out = ctrl.update(20.5, setpoint=None)
    assert ctrl.setpoint == 21.0
    assert out == pytest.approx(0.1575)


@pytest.mark.parametrize("attr", ["kp", "ki", "setpoint"])
def test_numeric_setters_accept_int_and_fraction_read_back_as_float(
    attr: str,
) -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)

    setattr(ctrl, attr, 1)
    assert getattr(ctrl, attr) == 1.0
    assert type(getattr(ctrl, attr)) is float

    setattr(ctrl, attr, Fraction(3, 10))
    assert getattr(ctrl, attr) == pytest.approx(0.3)
    assert type(getattr(ctrl, attr)) is float


def test_update_accepts_fraction_measured_and_int_setpoint() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    twin = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)

    out = ctrl.update(Fraction(41, 2))  # type: ignore[arg-type]  # numbers.Real, not a float; 20.5 exactly
    twin_out = twin.update(20.5)
    assert out == twin_out
    assert type(out) is float

    out2 = ctrl.update(20.0, setpoint=22)
    assert ctrl.setpoint == 22.0
    assert type(ctrl.setpoint) is float
    assert type(out2) is float


@pytest.mark.parametrize("attr", ["kp", "ki", "setpoint"])
def test_numeric_setters_overflow_raises_naming_attribute_and_keeps_previous(
    attr: str,
) -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    previous = getattr(ctrl, attr)

    with pytest.raises(OverflowError, match=attr):
        setattr(ctrl, attr, 10**400)
    assert getattr(ctrl, attr) == previous

    with pytest.raises(OverflowError, match=attr):
        hs.PIController(**{attr: 10**400})  # type: ignore[arg-type]  # attr is kp/ki/setpoint


def test_update_overflow_int_raises_naming_measured_or_setpoint() -> None:
    ctrl = hs.PIController(setpoint=21.0)

    with pytest.raises(OverflowError, match="measured"):
        ctrl.update(10**400)
    assert ctrl.history == ()

    with pytest.raises(OverflowError, match="setpoint"):
        ctrl.update(20.0, setpoint=10**400)
    assert ctrl.setpoint == 21.0


def test_fixed_output_overflow_int_raises_overflow_not_range_value_error() -> None:
    ctrl = hs.PIController(fixed_output=0.4)
    with pytest.raises(OverflowError, match="fixed_output"):
        ctrl.fixed_output = 10**400
    assert ctrl.fixed_output == 0.4


def test_numeric_setters_overflow_boundary_either_side() -> None:
    # The largest representable float, as an int, is accepted; one bit past
    # it overflows float() itself rather than merely failing the range/finite
    # check, on both sides of zero.
    ctrl = hs.PIController()

    ctrl.kp = int(sys.float_info.max)
    assert ctrl.kp == sys.float_info.max
    assert type(ctrl.kp) is float

    with pytest.raises(OverflowError, match="kp"):
        ctrl.kp = 2**1024
    with pytest.raises(OverflowError, match="kp"):
        ctrl.kp = -(2**1024)

    # fixed_output: one power of two short of the boundary is a plain
    # out-of-range ValueError (float() succeeds); past it, float() itself
    # overflows and OverflowError takes over instead.
    with pytest.raises(ValueError, match=re.escape(repr(2**1023))):
        ctrl.fixed_output = 2**1023
    with pytest.raises(OverflowError, match="fixed_output"):
        ctrl.fixed_output = 2**1024


# ---------------------------------------------------------------------------
# T3 (B3): mode coerces to the enum in both directions, an invalid value
# raises ValueError naming mode and listing the valid values
# ---------------------------------------------------------------------------


def test_mode_string_is_stored_as_enum_member_not_str() -> None:
    # `"radiator" == HeatingMode.RADIATOR` is True for a plain str comparison,
    # so this must assert identity, not equality, to actually prove coercion.
    ctrl = hs.PIController(mode="floor_heating")
    assert ctrl.mode is hs.HeatingMode.FLOOR_HEATING
    assert type(ctrl.mode) is hs.HeatingMode

    ctrl.mode = "radiator"
    assert ctrl.mode is hs.HeatingMode.RADIATOR


def test_mode_switch_radiator_to_floor_next_command_is_binary() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    ctrl.update(20.5)

    ctrl.mode = "floor_heating"
    out = ctrl.update(20.5)

    assert out in {0.0, 1.0}


def test_mode_switch_floor_to_radiator_next_command_is_continuous() -> None:
    # The R4 defect this round fixes: `ctrl.mode = "radiator"` as a plain
    # string used to silently keep routing to floor-heating modulation.
    ctrl = hs.PIController(mode="floor_heating", kp=0.3, ki=0.015, setpoint=21.0)
    ctrl.update(20.5)

    ctrl.mode = "radiator"
    out = ctrl.update(20.5)

    assert out == pytest.approx(0.165)


@pytest.mark.parametrize(
    "bad",
    ["steam", "RADIATOR", "Radiator", " radiator", "radiator\n", "", 1, None, True, []],
    ids=[
        "unknown",
        "upper-case",
        "title-case",
        "leading-space",
        "trailing-newline",
        "empty",
        "int",
        "none",
        "bool",
        "list",
    ],
)
def test_mode_rejects_everything_else_with_value_error_listing_valid_values(
    bad: object,
) -> None:
    ctrl = hs.PIController()

    with pytest.raises(ValueError, match=re.escape("['radiator', 'floor_heating']")):
        ctrl.mode = bad  # type: ignore[assignment]  # deliberate misuse
    assert ctrl.mode is hs.HeatingMode.RADIATOR

    with pytest.raises(ValueError, match="mode"):
        hs.PIController(mode=bad)  # type: ignore[arg-type]  # deliberate misuse


# ---------------------------------------------------------------------------
# T4 (B2, B4): a raising update() leaves every piece of state exactly as it
# was — the motivating R4 bug was update(nan, setpoint=22.0) raising but
# still storing the new setpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("measured", "setpoint"),
    [
        (math.nan, 22.0),
        ("x", 22.0),
        (20.5, math.nan),
        (True, None),
        (20.5, "22"),
        (20.5, True),
        (10**400, None),
        (20.5, 10**400),
    ],
    ids=[
        "nan-measured-good-setpoint",
        "str-measured-good-setpoint",
        "good-measured-nan-setpoint",
        "bool-measured",
        "good-measured-str-setpoint",
        "good-measured-bool-setpoint",
        "overflow-measured",
        "overflow-setpoint",
    ],
)
@pytest.mark.parametrize("fixed", [None, 0.3], ids=["unfixed", "fixed"])
def test_update_failed_call_leaves_every_piece_of_state_untouched(
    measured: object, setpoint: object, fixed: float | None
) -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0, fixed_output=fixed)
    ctrl.update(20.5)
    ctrl.update(20.5)
    before = (
        ctrl.setpoint,
        ctrl.integral,
        ctrl.history,
        ctrl.pi_output,
        ctrl.fixed_output,
    )

    with pytest.raises((ValueError, TypeError, OverflowError)):
        ctrl.update(measured, setpoint)  # type: ignore[arg-type]  # deliberate misuse

    after = (
        ctrl.setpoint,
        ctrl.integral,
        ctrl.history,
        ctrl.pi_output,
        ctrl.fixed_output,
    )
    assert after == before


def test_update_failed_call_does_not_consume_a_step() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    twin = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    ctrl.update(20.5)
    twin.update(20.5)

    with pytest.raises(ValueError):
        ctrl.update(math.nan, setpoint=22.0)

    ctrl.update(20.5)
    twin.update(20.5)

    assert ctrl.integral == twin.integral
    assert ctrl.pi_output == twin.pi_output
    assert ctrl.history == twin.history


# ---------------------------------------------------------------------------
# T5 (B5): pi_output — the clamped PI result of the last update()
# ---------------------------------------------------------------------------


def test_pi_output_none_then_value_across_fix_release_and_reset() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    assert ctrl.pi_output is None

    ctrl.update(20.5)
    assert ctrl.pi_output == pytest.approx(0.1575)

    ctrl.fixed_output = 0.9
    assert ctrl.pi_output == pytest.approx(0.1575)  # assignment alone runs no step

    cmd = ctrl.update(20.5)
    assert cmd == 0.9
    assert ctrl.pi_output == pytest.approx(0.165)  # the PI result, not the fixed level

    ctrl.fixed_output = None
    assert ctrl.pi_output == pytest.approx(0.165)

    cmd2 = ctrl.update(20.5)
    assert cmd2 == pytest.approx(0.1725)
    assert ctrl.pi_output == pytest.approx(0.1725)

    ctrl.fixed_output = 0.9
    ctrl.reset()
    assert ctrl.pi_output is None
    assert ctrl.fixed_output == 0.9  # reset leaves fixed_output alone

    ctrl.update(20.5)
    assert ctrl.pi_output == pytest.approx(0.1575)  # integral was cleared too


def test_pi_output_is_exactly_clamp_bound_in_saturation() -> None:
    high = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    high.update(19.0)
    assert high.pi_output == 1.0

    low = hs.PIController()
    low.update(30.0)
    assert low.pi_output == 0.0


def test_pi_output_at_exact_clamp_edges_holds_the_integral() -> None:
    # raw lands exactly on a clamp bound rather than deep in saturation.
    at_zero = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    at_zero.update(21.0)  # error 0.0 -> raw exactly 0.0
    assert at_zero.pi_output == 0.0
    assert at_zero.integral == 0.0

    at_one = hs.PIController(kp=1.0, ki=0.0, setpoint=21.0)
    at_one.update(20.0)  # error 1.0 -> raw exactly 1.0
    assert at_one.pi_output == 1.0
    assert at_one.integral == 0.0


def test_pi_output_reports_pi_demand_not_fixed_level_while_fixed() -> None:
    fixed = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0, fixed_output=0.9)
    free = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)

    cmd = fixed.update(20.5)
    free.update(20.5)

    assert cmd == 0.9
    assert fixed.pi_output == free.pi_output == pytest.approx(0.1575)


def test_pi_output_reports_fractional_demand_not_binary_command_in_floor_mode() -> None:
    ctrl = hs.PIController(
        kp=0.3, ki=0.015, mode="floor_heating", setpoint=21.0, fixed_output=0.25
    )
    cmd = ctrl.update(20.5)
    assert cmd in {0.0, 1.0}
    assert ctrl.pi_output == pytest.approx(0.1575)


def test_pi_output_is_read_only() -> None:
    ctrl = hs.PIController()
    with pytest.raises(AttributeError):
        ctrl.pi_output = 0.5  # type: ignore[misc]  # deliberate misuse
    assert ctrl.pi_output is None


# ---------------------------------------------------------------------------
# T6 (B6): OUTPUT_MIN and OUTPUT_MAX re-exported from the package root
# ---------------------------------------------------------------------------


def test_output_constants_at_package_root_are_the_module_objects() -> None:
    assert hs.OUTPUT_MIN is hs.pi_controller.OUTPUT_MIN
    assert hs.OUTPUT_MIN is pi_controller_module.OUTPUT_MIN
    assert hs.OUTPUT_MAX is hs.pi_controller.OUTPUT_MAX
    assert hs.OUTPUT_MAX is pi_controller_module.OUTPUT_MAX
    assert hs.OUTPUT_MIN == 0.0
    assert hs.OUTPUT_MAX == 1.0
    assert "OUTPUT_MIN" in hs.__all__
    assert "OUTPUT_MAX" in hs.__all__
    assert "OUTPUT_MIN" in hs.pi_controller.__all__
    assert "OUTPUT_MAX" in hs.pi_controller.__all__


# ---------------------------------------------------------------------------
# T7 (B7): construction and later assignment raise the identical error for
# the same bad value; state isolation between settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attr", "value", "exc_type"),
    [
        ("kp", math.nan, ValueError),
        ("ki", math.inf, ValueError),
        ("setpoint", math.nan, ValueError),
        ("mode", "steam", ValueError),
        ("mode", None, ValueError),
        ("fixed_output", 1.5, ValueError),
        ("kp", "0.3", TypeError),
        ("setpoint", None, TypeError),
        ("fixed_output", True, TypeError),
        ("fixed_output", Decimal("0.5"), TypeError),
        ("ki", 10**400, OverflowError),
    ],
)
def test_constructor_and_setter_raise_identical_error_for_same_value(
    attr: str, value: object, exc_type: type[Exception]
) -> None:
    ctrl = hs.PIController()

    with pytest.raises(exc_type) as ctor_info:
        hs.PIController(**{attr: value})  # type: ignore[arg-type]  # deliberate misuse
    with pytest.raises(exc_type) as setter_info:
        setattr(ctrl, attr, value)

    assert type(ctor_info.value) is exc_type
    assert type(setter_info.value) is exc_type
    assert str(ctor_info.value) == str(setter_info.value)
    assert attr in str(ctor_info.value)


def test_setters_on_success_change_only_their_own_attribute() -> None:
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0, fixed_output=0.4)
    for _ in range(4):
        ctrl.update(20.5)
    integral, history, pi_output = ctrl.integral, ctrl.history, ctrl.pi_output

    ctrl.kp = 0.5
    ctrl.ki = 0.02
    ctrl.setpoint = 22.0
    ctrl.mode = "floor_heating"
    ctrl.mode = hs.HeatingMode.FLOOR_HEATING  # idempotent re-assignment

    assert ctrl.kp == 0.5
    assert ctrl.ki == 0.02
    assert ctrl.setpoint == 22.0
    assert ctrl.mode is hs.HeatingMode.FLOOR_HEATING
    assert ctrl.integral == integral
    assert ctrl.history == history
    assert ctrl.pi_output == pi_output
    assert ctrl.fixed_output == 0.4


def test_every_pre_round_1_constructor_case_still_raises_value_error_with_attribute() -> (
    None
):
    # T7/B7 regression: every constructor case the pre-round-1 suite already
    # covered raises the same class as before, now naming the attribute too.
    cases: list[tuple[dict[str, object], str]] = [
        ({"mode": "steam_radiator"}, "mode"),
        ({"history_length": 0}, "history_length"),
        ({"history_length": -5}, "history_length"),
        ({"kp": math.nan}, "kp"),
        ({"ki": math.inf}, "ki"),
        ({"setpoint": math.nan}, "setpoint"),
    ]
    for kwargs, attr in cases:
        with pytest.raises(ValueError, match=attr):
            hs.PIController(**kwargs)  # type: ignore[arg-type]  # attr varies per case
