"""Tests for the PIController model."""

import heatingsystem as hs


def test_initial_gains():
    controller = hs.PIController(kp=0.5, ki=0.02)
    assert controller.kp == 0.5
    assert controller.ki == 0.02
    assert controller.integral == 0.0


def test_update_responds_to_error():
    # A positive error (setpoint above measurement) should drive output up.
    controller = hs.PIController()
    output = controller.update(setpoint=20.0, measurement=18.0, dt=1.0)
    assert output > 0.0


def test_update_zero_error_gives_zero_output():
    controller = hs.PIController()
    assert controller.update(setpoint=20.0, measurement=20.0, dt=1.0) == 0.0


def test_integral_accumulates_over_steps():
    # Edge case: repeated nonzero error should make the integral term grow.
    controller = hs.PIController()
    controller.update(setpoint=20.0, measurement=10.0, dt=1.0)
    first_integral = controller.integral
    controller.update(setpoint=20.0, measurement=10.0, dt=1.0)
    assert controller.integral > first_integral
