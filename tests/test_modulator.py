"""Tests for the Modulator actuator mapping and the HeatingMode enum."""

import math
import sys
from decimal import Decimal
from fractions import Fraction

import pytest

import heatingsystem as hs
from heatingsystem.modulator import modulator as modulator_module
from heatingsystem.modulator.modulator import (
    HeatingMode,
    Modulator,
)
from heatingsystem.pi_controller import pi_controller as pi_controller_module

# ---------------------------------------------------------------------------
# Construction, defaults and validation — identical to what PIController's
# own constructor demanded before the split (D1, D2)
# ---------------------------------------------------------------------------


def test_construction_defaults() -> None:
    m = Modulator()
    assert m.mode is HeatingMode.RADIATOR
    assert m.history_length == 24
    assert m.fixed_output is None
    assert m.history == ()
    assert m.duty_cycle == 0.0
    assert not m.is_history_full


@pytest.mark.parametrize(
    "value", ["steam", "RADIATOR", " radiator", "", 1, None, True, []]
)
def test_mode_rejects_everything_except_valid_values(value: object) -> None:
    with pytest.raises(ValueError, match=r"\['radiator', 'floor_heating'\]"):
        Modulator(value)  # type: ignore[arg-type]  # deliberate misuse for the test


@pytest.mark.parametrize(
    "value", [0, -1, True, 24.0, "24", None, Fraction(24, 1), sys.maxsize + 1]
)
def test_history_length_rejects_everything_except_a_positive_int(value: object) -> None:
    with pytest.raises((TypeError, ValueError, OverflowError)):
        Modulator(history_length=value)  # type: ignore[arg-type]  # deliberate misuse


def test_history_length_sys_maxsize_is_accepted() -> None:
    assert Modulator(history_length=sys.maxsize).history_length == sys.maxsize


def test_history_length_is_read_only() -> None:
    m = Modulator()
    with pytest.raises(AttributeError):
        m.history_length = 5  # type: ignore[misc]  # deliberate misuse for the test


def test_constructor_is_keyword_only_after_mode() -> None:
    with pytest.raises(TypeError):
        Modulator("radiator", 4)  # type: ignore[call-arg]  # deliberate misuse for the test


@pytest.mark.parametrize("value", [float("nan"), 1.5, -0.1, True, "0.5", 10**400])
def test_fixed_output_rejects_bad_values_at_construction_and_setter(
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError, OverflowError)):
        Modulator(fixed_output=value)  # type: ignore[arg-type]  # deliberate misuse

    m = Modulator(fixed_output=0.3)
    with pytest.raises((TypeError, ValueError, OverflowError)):
        m.fixed_output = value  # type: ignore[assignment]  # deliberate misuse
    assert m.fixed_output == 0.3


def test_three_fault_construction_names_mode_first_two_fault_names_history_length() -> (
    None
):
    with pytest.raises(ValueError, match="mode"):
        Modulator("steam", history_length=0, fixed_output=5)
    with pytest.raises(ValueError, match="history_length"):
        Modulator(history_length=0, fixed_output=5)


def test_errors_are_identical_in_class_and_message_to_the_controllers() -> None:
    cases: list[dict[str, object]] = [
        {"mode": "steam"},
        {"mode": 1},
        {"history_length": 0},
        {"history_length": True},
        {"history_length": 2.0},
        {"history_length": sys.maxsize + 1},
        {"fixed_output": 1.5},
        {"fixed_output": Fraction(3, 2)},
        {"fixed_output": True},
        {"fixed_output": float("nan")},
        {"fixed_output": 10**400},
    ]
    for kwargs in cases:
        with pytest.raises(Exception) as modulator_exc:  # noqa: B017 - class checked below
            Modulator(**kwargs)  # type: ignore[arg-type]  # deliberate misuse
        with pytest.raises(Exception) as controller_exc:  # noqa: B017
            hs.PIController(**kwargs)  # type: ignore[arg-type]  # deliberate misuse
        assert type(modulator_exc.value) is type(controller_exc.value)
        assert str(modulator_exc.value) == str(controller_exc.value)


def test_mode_and_fixed_output_setters_keep_the_previous_value_on_failure() -> None:
    m = Modulator(fixed_output=0.3)
    with pytest.raises(ValueError):
        m.fixed_output = 2
    assert m.fixed_output == 0.3

    m2 = Modulator()
    with pytest.raises(ValueError):
        m2.mode = "steam"
    assert m2.mode is HeatingMode.RADIATOR


def test_reset_clears_window_and_keeps_mode_length_and_hold() -> None:
    m = Modulator("floor_heating", history_length=4, fixed_output=0.25)
    for _ in range(4):
        m.command(1.0)

    m.reset()

    assert m.history == ()
    assert m.duty_cycle == 0.0
    assert not m.is_history_full
    assert m.mode is HeatingMode.FLOOR_HEATING
    assert m.history_length == 4
    assert m.fixed_output == 0.25
    assert m.command(1.0) == 1.0


def test_reset_is_a_no_op_when_called_twice() -> None:
    m = Modulator("floor_heating", history_length=4)
    m.command(0.5)
    m.reset()
    m.reset()
    assert m.history == ()


# ---------------------------------------------------------------------------
# command() — the mode mapping itself (D1)
# ---------------------------------------------------------------------------


def test_command_radiator_pass_through() -> None:
    m = Modulator("radiator", history_length=6)
    assert m.command(0.42) == 0.42
    assert m.history == (0.42,)


def test_command_floor_heating_binary_output() -> None:
    m = Modulator("floor_heating")
    assert set(m.command(v) for v in [0.0, 0.3, 0.7, 1.0]) <= {0.0, 1.0}


def test_command_reads_window_before_appending() -> None:
    m = Modulator("floor_heating", history_length=4)
    for _ in range(4):
        m.command(1.0)  # fills the window with ON, duty_cycle == 1.0

    m.fixed_output = 0.5
    first = m.command(1.0)  # duty_cycle (1.0) is not < 0.5, so OFF

    assert first == 0.0
    assert m.history == (1.0, 1.0, 1.0, 0.0)

    empty = Modulator("floor_heating")
    assert empty.command(math.nextafter(0.0, 1.0)) == 1.0  # empty window fires


def test_command_tie_at_exact_duty_rests_the_slot() -> None:
    m = Modulator("floor_heating", history_length=2)
    outs = [m.command(0.5) for _ in range(6)]
    assert outs == [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (0.0, 0.0),
        (1.0, 1.0),
        (math.nextafter(0.0, 1.0), math.nextafter(0.0, 1.0)),
        (math.nextafter(1.0, 0.0), math.nextafter(1.0, 0.0)),
    ],
)
def test_command_accepts_range_edges_exactly(level: float, expected: float) -> None:
    assert Modulator().command(level) == expected


@pytest.mark.parametrize(
    "level",
    [
        math.nextafter(1.0, 2.0),
        math.nextafter(0.0, -1.0),
        -0.1,
        1.5,
        2,
    ],
)
def test_command_rejects_one_step_outside_the_range(level: float) -> None:
    m = Modulator()
    with pytest.raises(
        ValueError,
        match=r"^level must be in \[0.0, 1.0\], got "
        + __import__("re").escape(repr(level)),
    ):
        m.command(level)
    assert m.history == ()


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (True, "level must be a real number, got True (bool)."),
        (False, "level must be a real number, got False (bool)."),
        ("0.5", "level must be a real number, got '0.5' (str)."),
        (None, "level must be a real number, got None (NoneType)."),
    ],
)
def test_command_rejects_non_real_types_naming_level(bad: object, message: str) -> None:
    m = Modulator()
    with pytest.raises(TypeError, match=r".*") as exc:
        m.command(bad)  # type: ignore[arg-type]  # deliberate misuse for the test
    assert str(exc.value) == message
    assert m.history == ()


def test_command_rejects_decimal_complex_list_and_bytes_naming_level() -> None:
    m = Modulator()
    for bad in [Decimal("0.5"), complex(0.5), [0.5], b"0.5"]:
        with pytest.raises(TypeError, match="level must be a real number"):
            m.command(bad)  # type: ignore[arg-type]  # deliberate misuse for the test
    assert m.history == ()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_command_rejects_non_finite_level(bad: float) -> None:
    m = Modulator()
    with pytest.raises(ValueError, match="finite"):
        m.command(bad)
    assert m.history == ()


def test_command_rejects_huge_int_level_with_overflow_error() -> None:
    m = Modulator()
    with pytest.raises(OverflowError, match="level"):
        m.command(10**400)
    assert m.history == ()


def test_command_validates_level_even_while_the_hold_is_set() -> None:
    m = Modulator(fixed_output=0.5)
    for bad, exc in [
        (1.5, ValueError),
        (-0.1, ValueError),
        (float("nan"), ValueError),
        (True, TypeError),
        ("0.5", TypeError),
        (10**400, OverflowError),
    ]:
        with pytest.raises(exc, match="level"):
            m.command(bad)  # type: ignore[arg-type]  # deliberate misuse for the test
    assert m.history == ()
    assert m.fixed_output == 0.5


def test_command_negative_zero_returns_positive_zero_in_both_modes() -> None:
    radiator = Modulator()
    floor = Modulator("floor_heating")

    r = radiator.command(-0.0)
    f = floor.command(-0.0)

    assert r == 0.0 and math.copysign(1.0, r) == 1.0
    assert f == 0.0 and math.copysign(1.0, f) == 1.0
    assert math.copysign(1.0, radiator.history[0]) == 1.0


def test_command_accepts_int_and_fraction_and_returns_float() -> None:
    m = Modulator()
    assert m.command(1) == 1.0
    assert type(m.command(0)) is float
    assert m.command(Fraction(1, 4)) == 0.25  # type: ignore[arg-type]  # numbers.Real accepted at runtime, narrower than the float annotation


def test_command_fraction_range_error_repeats_the_original_value() -> None:
    m = Modulator()
    with pytest.raises(ValueError, match=r"Fraction\(3, 2\)"):
        m.command(Fraction(3, 2))  # type: ignore[arg-type]  # numbers.Real accepted at runtime, narrower than the float annotation


def test_mode_switch_mid_run_reads_fractional_radiator_history() -> None:
    m = Modulator()
    m.command(0.4)
    m.command(0.4)

    m.mode = "floor_heating"
    a = m.command(0.5)  # duty_cycle 0.4 < 0.5 -> ON
    assert a == 1.0
    assert m.history == (0.4, 0.4, 1.0)

    b = m.command(0.3)  # duty_cycle 0.6 not < 0.3 -> OFF
    assert b == 0.0

    m.mode = HeatingMode.RADIATOR
    c = m.command(0.7)
    assert c == 0.7
    assert m.mode is HeatingMode.RADIATOR


# ---------------------------------------------------------------------------
# The moved exact-schedule tests (R8): which slots fire is the modulator's
# own concern now, not PIController's (D7)
# ---------------------------------------------------------------------------


def test_schedule_quarter_level_is_identical_as_demand_and_as_a_hold() -> None:
    as_demand = Modulator("floor_heating", history_length=24)
    outs_a = [as_demand.command(0.25) for _ in range(24)]

    as_hold = Modulator("floor_heating", history_length=24, fixed_output=0.25)
    outs_b = [as_hold.command(1.0) for _ in range(24)]

    on_steps = [i + 1 for i, out in enumerate(outs_a) if out == 1.0]
    assert on_steps == [1, 6, 10, 14, 18, 22]
    assert outs_a == outs_b
    assert set(outs_a) <= {0.0, 1.0}
    assert as_demand.duty_cycle == pytest.approx(0.25)
    assert as_demand.is_history_full


def test_schedule_history_length_one_alternates_every_slot() -> None:
    m = Modulator("floor_heating", history_length=1, fixed_output=0.3)
    outs = [m.command(1.0) for _ in range(6)]
    assert outs == [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]


def test_schedule_level_just_above_min_fires_exactly_one_slot_per_window() -> None:
    level = math.nextafter(0.0, 1.0)
    m = Modulator("floor_heating", history_length=24)
    outs = [m.command(level) for _ in range(24)]
    assert outs[0] == 1.0
    assert all(out == 0.0 for out in outs[1:])
    assert m.duty_cycle == pytest.approx(1 / 24)


def test_schedule_level_just_below_max_rests_exactly_one_slot_per_window() -> None:
    level = math.nextafter(1.0, 0.0)
    m = Modulator("floor_heating", history_length=24)
    outs = [m.command(level) for _ in range(24)]
    assert outs.count(0.0) == 1
    assert outs[1] == 0.0
    assert m.duty_cycle == pytest.approx(23 / 24)


# ---------------------------------------------------------------------------
# The private snapshot pair — not the public wire format (T2/D3)
# ---------------------------------------------------------------------------


def test_private_snapshot_has_four_keys_in_order_and_is_a_copy() -> None:
    m = Modulator("floor_heating", history_length=3, fixed_output=0.25)
    m.command(1.0)

    d = m._to_dict()  # noqa: SLF001 - the private snapshot is exactly what this test proves

    assert list(d) == ["mode", "history_length", "fixed_output", "history"]
    assert d == {
        "mode": "floor_heating",
        "history_length": 3,
        "fixed_output": 0.25,
        "history": [1.0],
    }

    mutated = dict(d)
    mutated["history"] = [*d["history"], 99.0]  # type: ignore[misc]  # deliberate mutation of the copy
    assert m.history == (1.0,)

    assert Modulator._from_dict(d)._to_dict() == d  # noqa: SLF001


def test_private_snapshot_refuses_the_controllers_eight_key_snapshot() -> None:
    with pytest.raises(
        ValueError, match=r"unknown keys \['integral', 'ki', 'kp', 'setpoint'\]"
    ):
        Modulator._from_dict(hs.PIController().to_dict())  # noqa: SLF001


def test_private_snapshot_reports_missing_keys_before_unknown_ones() -> None:
    with pytest.raises(
        ValueError,
        match=r"missing keys \['fixed_output', 'history', 'history_length'\]",
    ):
        Modulator._from_dict({"mode": "radiator", "bogus": 1})  # noqa: SLF001


def test_private_snapshot_refuses_a_non_mapping() -> None:
    with pytest.raises(TypeError, match="snapshot must be a mapping, got str"):
        Modulator._from_dict("text")  # type: ignore[arg-type]  # noqa: SLF001


def test_load_history_is_atomic_and_checks_length_before_entries() -> None:
    m = Modulator(history_length=3)
    m.command(0.5)

    with pytest.raises(TypeError, match=r"history\[1\] must be a real number"):
        m._load_history([0.1, "x"])  # noqa: SLF001
    assert m.history == (0.5,)

    with pytest.raises(
        ValueError, match="history has 4 entries but history_length is 3"
    ):
        m._load_history([0.1, 0.2, 0.3, "x"])  # noqa: SLF001
    assert m.history == (0.5,)

    m._load_history([])  # noqa: SLF001
    assert m.history == ()

    m._load_history((0.2, 1.0, 1.0))  # noqa: SLF001
    assert m.history == (0.2, 1.0, 1.0)
    assert m.is_history_full


def test_subclass_from_dict_returns_an_instance_of_the_subclass() -> None:
    class Sub(Modulator):
        pass

    original = Sub(mode="floor_heating", history_length=3)
    restored = Sub._from_dict(original._to_dict())  # noqa: SLF001

    assert type(restored) is Sub
    assert restored.mode is HeatingMode.FLOOR_HEATING
    assert restored.history_length == 3


# ---------------------------------------------------------------------------
# D5: the numeric contract, the mode coercion and the history loader each
# exist exactly once, shared by the controller and the modulator
# ---------------------------------------------------------------------------


def test_helpers_and_mode_coercion_exist_once_and_are_shared() -> None:
    for name in ["_finite", "_level", "_window_length", "_to_command", "_heating_mode"]:
        assert not hasattr(pi_controller_module, name)
    assert not hasattr(hs.PIController, "_to_command")
    assert not hasattr(modulator_module, "_finite")

    from heatingsystem import _validation

    assert callable(_validation.finite)
    assert callable(_validation.window_length)
    assert callable(_validation.level)
    assert callable(_validation.snapshot_mapping)

    original = modulator_module._heating_mode

    def _raise(_value: object) -> HeatingMode:
        raise KeyError("nope")

    modulator_module._heating_mode = _raise  # type: ignore[assignment]  # deliberate monkeypatch
    try:
        with pytest.raises(KeyError):
            hs.PIController().mode = "radiator"
        with pytest.raises(KeyError):
            Modulator().mode = "radiator"
        with pytest.raises(KeyError):
            hs.PIController.from_dict(hs.PIController().to_dict())
    finally:
        modulator_module._heating_mode = original
