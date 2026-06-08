"""Tests for the PIController and HeatingMode API."""

import math

import pytest

import heatingsystem as hs

# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------


def test_construction_defaults():
    ctrl = hs.PIController()
    # Verify the shipped defaults match the contract.
    assert ctrl.kp == pytest.approx(0.3)
    assert ctrl.ki == pytest.approx(0.015)
    assert ctrl.integral == pytest.approx(0.0)
    assert ctrl.duty_cycle == pytest.approx(0.0)
    assert ctrl.history == ()
    assert not ctrl.is_history_full


def test_construction_explicit_gains():
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=22.0)
    assert ctrl.kp == pytest.approx(0.5)
    assert ctrl.ki == pytest.approx(0.02)


def test_mode_accepts_enum_radiator():
    ctrl = hs.PIController(mode=hs.HeatingMode.RADIATOR)
    # Should not raise; just a smoke test.
    out = ctrl.update(20.0)
    assert 0.0 <= out <= 1.0


def test_mode_accepts_string_radiator():
    ctrl = hs.PIController(mode="radiator")
    out = ctrl.update(20.0)
    assert 0.0 <= out <= 1.0


def test_mode_accepts_enum_floor_heating():
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING)
    out = ctrl.update(20.0)
    assert out in {0.0, 1.0}


def test_mode_accepts_string_floor_heating():
    ctrl = hs.PIController(mode="floor_heating")
    out = ctrl.update(20.0)
    assert out in {0.0, 1.0}


def test_invalid_mode_string_raises():
    with pytest.raises(ValueError, match="mode"):
        hs.PIController(mode="steam_radiator")


def test_history_length_zero_raises():
    with pytest.raises(ValueError):
        hs.PIController(history_length=0)


def test_history_length_negative_raises():
    with pytest.raises(ValueError):
        hs.PIController(history_length=-5)


def test_non_finite_kp_raises():
    with pytest.raises(ValueError):
        hs.PIController(kp=math.nan)


def test_non_finite_ki_raises():
    with pytest.raises(ValueError):
        hs.PIController(ki=math.inf)


def test_non_finite_setpoint_raises():
    with pytest.raises(ValueError):
        hs.PIController(setpoint=math.nan)


# ---------------------------------------------------------------------------
# update() — input validation
# ---------------------------------------------------------------------------


def test_update_non_finite_measured_raises():
    ctrl = hs.PIController()
    with pytest.raises(ValueError):
        ctrl.update(math.nan)


def test_update_inf_measured_raises():
    ctrl = hs.PIController()
    with pytest.raises(ValueError):
        ctrl.update(math.inf)


def test_update_setpoint_override_non_finite_raises():
    ctrl = hs.PIController()
    with pytest.raises(ValueError):
        ctrl.update(20.0, setpoint=math.inf)


# ---------------------------------------------------------------------------
# Radiator mode — output behaviour
# ---------------------------------------------------------------------------


def test_radiator_positive_error_output_positive():
    # Room is cold (18 < 21): output must be > 0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(18.0)
    assert out > 0.0


def test_radiator_zero_error_output_zero():
    # Room exactly at setpoint: first call should give 0 (no integral buildup yet).
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(21.0)
    assert out == pytest.approx(0.0)


def test_radiator_very_large_error_clamped_to_one():
    # Room 0 C, setpoint 21: raw output >> 1; must clamp to 1.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(0.0)
    assert out == pytest.approx(1.0)


def test_radiator_negative_error_clamped_to_zero():
    # Room too hot (30 > 21): controller should return 0.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(30.0)
    assert out == pytest.approx(0.0)


def test_radiator_output_always_in_range():
    # Stress: varied temperatures must all yield outputs in [0, 1].
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    temps = [-50.0, 0.0, 10.0, 20.0, 21.0, 22.0, 30.0, 100.0]
    for t in temps:
        ctrl.reset()
        out = ctrl.update(t)
        assert 0.0 <= out <= 1.0, f"out-of-range for measured={t}: {out}"


def test_radiator_hand_computed_single_step():
    # kp=0.5, ki=0.02, setpoint=21.0, measured=19.0
    # The error is 2.0, integral becomes 2.0,
    # raw = 0.5*2 + 0.02*2 = 1.04, which clamps to 1.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    out = ctrl.update(19.0)
    assert out == pytest.approx(1.0)


def test_radiator_hand_computed_small_error():
    # kp=0.3, ki=0.015, setpoint=21.0, measured=20.5
    # The error is 0.5, integral becomes 0.5,
    # raw = 0.3*0.5 + 0.015*0.5 = 0.15 + 0.0075 = 0.1575
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    out = ctrl.update(20.5)
    assert out == pytest.approx(0.1575)


def test_radiator_setpoint_override_per_call():
    # Passing setpoint kwarg to update() should override the stored setpoint.
    # Override with 22.0; measured=21.0, so error=1.0,
    # integral=1.0, raw=0.3*1+0.015*1=0.315.
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    out = ctrl.update(21.0, setpoint=22.0)
    assert out == pytest.approx(0.315)


# ---------------------------------------------------------------------------
# Anti-windup
# ---------------------------------------------------------------------------


def test_antiwindup_integral_does_not_grow_when_saturated():
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


def test_antiwindup_integral_does_not_grow_negative_saturation():
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


def test_floor_heating_output_binary():
    # Every returned value must be exactly 0.0 or 1.0.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    for measured in [15.0, 19.0, 20.5, 21.0, 22.0, 25.0]:
        ctrl.reset()
        out = ctrl.update(measured)
        assert out in {0.0, 1.0}, f"Non-binary output {out} for measured={measured}"


def test_floor_heating_full_demand_gives_one():
    # Very cold room: continuous output would be 1.0, so floor-heating must also be 1.0.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    out = 0.0
    for _ in range(5):
        out = ctrl.update(-10.0)
    assert out == pytest.approx(1.0)


def test_floor_heating_no_demand_gives_zero():
    # Very hot room: continuous output is 0.0 -> floor-heating must stay 0.0.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    out = 0.0
    for _ in range(5):
        out = ctrl.update(40.0)
    assert out == pytest.approx(0.0)


def test_floor_heating_duty_cycle_converges():
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


def test_history_empty_on_fresh_controller():
    ctrl = hs.PIController()
    assert ctrl.history == ()


def test_history_length_after_n_updates():
    ctrl = hs.PIController(history_length=24)
    for _ in range(10):
        ctrl.update(20.0)
    assert len(ctrl.history) == 10


def test_history_caps_at_history_length():
    ctrl = hs.PIController(history_length=24)
    for _ in range(30):
        ctrl.update(20.0)
    # Should never exceed the window size.
    assert len(ctrl.history) == 24


def test_history_ordered_oldest_to_newest():
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


def test_history_is_tuple():
    ctrl = hs.PIController()
    ctrl.update(20.0)
    assert isinstance(ctrl.history, tuple)


def test_history_snapshot_immutable():
    # Obtaining the tuple and mutating a copy must not affect the controller.
    ctrl = hs.PIController(history_length=5)
    for _ in range(5):
        ctrl.update(20.0)
    snap = ctrl.history
    # Build a mutable copy and verify the next history call is unchanged.
    snap_list = list(snap)
    snap_list[0] = -999.0
    assert ctrl.history == snap  # controller unaffected


def test_history_oldest_dropped_after_overflow():
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


def test_duty_cycle_zero_on_fresh_controller():
    ctrl = hs.PIController()
    assert ctrl.duty_cycle == pytest.approx(0.0)


def test_duty_cycle_correct_mean():
    # Radiator mode, kp=1.0, ki=0: output equals clamp(error, 0, 1).
    # setpoint=1.0: measured=0.0 -> output=1.0; measured=1.0 -> output=0.0;
    # measured=0.5 -> output=0.5.  Mean of [1.0, 0.0, 0.5] = 0.5.
    ctrl = hs.PIController(kp=1.0, ki=0.0, setpoint=1.0, history_length=10)
    ctrl.update(0.0)   # output = clamp(1.0, 0, 1) = 1.0
    ctrl.update(1.0)   # output = clamp(0.0, 0, 1) = 0.0
    ctrl.update(0.5)   # output = clamp(0.5, 0, 1) = 0.5
    assert ctrl.duty_cycle == pytest.approx((1.0 + 0.0 + 0.5) / 3)


# ---------------------------------------------------------------------------
# is_history_full
# ---------------------------------------------------------------------------


def test_is_history_full_false_before_window_fills():
    ctrl = hs.PIController(history_length=5)
    for step in range(4):
        ctrl.update(20.0)
        assert not ctrl.is_history_full, f"should not be full after {step + 1} updates"


def test_is_history_full_true_at_history_length():
    ctrl = hs.PIController(history_length=5)
    for _ in range(5):
        ctrl.update(20.0)
    assert ctrl.is_history_full


def test_is_history_full_remains_true_after_overflow():
    ctrl = hs.PIController(history_length=5)
    for _ in range(10):
        ctrl.update(20.0)
    assert ctrl.is_history_full


# ---------------------------------------------------------------------------
# reset() behaviour
# ---------------------------------------------------------------------------


def test_reset_zeroes_integral():
    # Use a small error (measured=20.5, setpoint=21.0 -> error=0.5) so the
    # first update stays in the linear region and the integral is committed.
    # raw = kp*0.5 + ki*0.5 = 0.5*0.5 + 0.02*0.5 = 0.26, which is < 1.0.
    ctrl = hs.PIController(kp=0.5, ki=0.02, setpoint=21.0)
    ctrl.update(20.5)
    assert ctrl.integral != 0.0
    ctrl.reset()
    assert ctrl.integral == pytest.approx(0.0)


def test_reset_clears_history():
    ctrl = hs.PIController(history_length=5)
    for _ in range(5):
        ctrl.update(20.0)
    assert len(ctrl.history) == 5
    ctrl.reset()
    assert ctrl.history == ()


def test_reset_duty_cycle_becomes_zero():
    ctrl = hs.PIController()
    ctrl.update(18.0)
    ctrl.reset()
    assert ctrl.duty_cycle == pytest.approx(0.0)


def test_reset_is_history_full_becomes_false():
    ctrl = hs.PIController(history_length=3)
    for _ in range(3):
        ctrl.update(20.0)
    assert ctrl.is_history_full
    ctrl.reset()
    assert not ctrl.is_history_full


# ---------------------------------------------------------------------------
# HeatingMode enum accessibility
# ---------------------------------------------------------------------------


def test_heating_mode_accessible_on_hs():
    assert hs.HeatingMode.RADIATOR == "radiator"
    assert hs.HeatingMode.FLOOR_HEATING == "floor_heating"


# ---------------------------------------------------------------------------
# Generalization: no AppDaemon / home-assistant import required
# ---------------------------------------------------------------------------


def test_no_appdaemon_dependency_required():
    # The class must work with plain Python floats — no HA/AppDaemon machinery.
    ctrl = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
    for measured in [18.0, 19.5, 20.0, 21.0, 22.0]:
        out = ctrl.update(measured)
        assert isinstance(out, float)
        assert 0.0 <= out <= 1.0


def test_floor_heating_no_appdaemon_required():
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    out = ctrl.update(19.0)
    assert isinstance(out, float)
    assert out in {0.0, 1.0}


# ---------------------------------------------------------------------------
# Floor-heating — duty-cycle convergence and read-before-append (tester gaps)
# ---------------------------------------------------------------------------


def test_floor_duty_cycle_converges_to_fractional_demand():
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


def test_floor_duty_cycle_converges_to_half_demand():
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


def test_floor_sustained_max_demand_is_all_on_no_saturation_bias():
    # A permanently freezing room must yield ON (1.0) on EVERY step, not just the
    # last one. This guards against a duty-cycle scheme that erroneously throttles
    # at full demand.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    outs = [ctrl.update(-50.0) for _ in range(60)]
    assert all(o == 1.0 for o in outs), f"expected all-ON, got {set(outs)}"
    assert ctrl.duty_cycle == pytest.approx(1.0)


def test_floor_sustained_too_hot_is_all_off():
    # A permanently overheated room must yield OFF (0.0) on EVERY step.
    ctrl = hs.PIController(mode=hs.HeatingMode.FLOOR_HEATING, setpoint=21.0)
    outs = [ctrl.update(50.0) for _ in range(60)]
    assert all(o == 0.0 for o in outs), f"expected all-OFF, got {set(outs)}"
    assert ctrl.duty_cycle == pytest.approx(0.0)


def test_floor_first_command_is_on_due_to_read_before_append():
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


def test_floor_command_not_in_own_duty_cycle():
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


def test_antiwindup_built_up_integral_is_bounded():
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


def test_antiwindup_recovers_after_error_reverses():
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


def test_antiwindup_negative_built_up_integral_bounded_and_recovers():
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


def test_history_maxlen_non_default_length():
    # A non-default window must cap at exactly history_length and not the
    # hard-coded default of 24.
    ctrl = hs.PIController(history_length=7)
    for _ in range(20):
        ctrl.update(20.0)
    assert len(ctrl.history) == 7
    assert ctrl.is_history_full


def test_history_maxlen_one():
    # The minimum legal window keeps only the single most recent command.
    ctrl = hs.PIController(kp=1.0, ki=0.0, setpoint=21.0, history_length=1)
    ctrl.update(18.0)  # error 3 -> clamp 1.0
    last = ctrl.update(20.5)  # error 0.5 -> 0.5
    assert ctrl.history == (last,)
    assert len(ctrl.history) == 1
    assert ctrl.is_history_full
