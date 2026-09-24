"""Tests for the shared numeric validation contract in heatingsystem._validation."""

import math
import re
import sys
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType

import pytest

from heatingsystem import _validation

# ---------------------------------------------------------------------------
# finite — the base numeric contract
# ---------------------------------------------------------------------------


def test_finite_accepts_a_plain_number_and_returns_float() -> None:
    assert _validation.finite("kp", 3) == 3.0
    assert type(_validation.finite("kp", 3)) is float


def test_finite_negative_zero_returns_positive_zero() -> None:
    result = _validation.finite("kp", -0.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0


def test_finite_accepts_int_and_fraction() -> None:
    assert _validation.finite("kp", 2) == 2.0
    assert _validation.finite("kp", Fraction(1, 4)) == 0.25


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_finite_rejects_non_finite_naming_the_attribute(bad: float) -> None:
    with pytest.raises(ValueError, match=f"kp must be a finite number, got {bad!r}"):
        _validation.finite("kp", bad)


@pytest.mark.parametrize(
    "bad",
    [True, False, "0.5", None, [0.5], (0.5,), {}, complex(0.5), Decimal("0.5"), b"0.5"],
)
def test_finite_rejects_non_real_types_naming_the_attribute_and_type(
    bad: object,
) -> None:
    expected = f"kp must be a real number, got {bad!r} ({type(bad).__name__})."
    with pytest.raises(TypeError, match=re.escape(expected)):
        _validation.finite("kp", bad)


def test_finite_rejects_a_huge_int_with_overflow_error() -> None:
    with pytest.raises(OverflowError, match="kp is too large to represent as a float"):
        _validation.finite("kp", 10**400)


# ---------------------------------------------------------------------------
# window_length — the rolling-window length check
# ---------------------------------------------------------------------------


def test_window_length_accepts_a_positive_int_unchanged() -> None:
    assert _validation.window_length(5) == 5
    assert _validation.window_length(sys.maxsize) == sys.maxsize


@pytest.mark.parametrize("bad", [True, 24.0, "24", None, Fraction(24, 1)])
def test_window_length_rejects_non_int_naming_it(bad: object) -> None:
    expected = f"history_length must be an int, got {bad!r} ({type(bad).__name__})."
    with pytest.raises(TypeError, match=re.escape(expected)):
        _validation.window_length(bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_window_length_rejects_less_than_one(bad: int) -> None:
    with pytest.raises(ValueError, match=f"history_length must be >= 1, got {bad}"):
        _validation.window_length(bad)


def test_window_length_rejects_one_past_sys_maxsize() -> None:
    with pytest.raises(OverflowError, match="history_length is too large"):
        _validation.window_length(sys.maxsize + 1)


# ---------------------------------------------------------------------------
# level — finite plus the actuator-range check
# ---------------------------------------------------------------------------


def test_level_accepts_values_inside_the_range() -> None:
    assert _validation.level("x", 0.5, 0.0, 1.0) == 0.5
    assert _validation.level("x", 0.0, 0.0, 1.0) == 0.0
    assert _validation.level("x", 1.0, 0.0, 1.0) == 1.0


def test_level_rejects_values_outside_the_range_naming_the_caller_value() -> None:
    with pytest.raises(ValueError, match=r"x must be in \[0.0, 1.0\], got 1.5"):
        _validation.level("x", 1.5, 0.0, 1.0)


def test_level_bounds_equal_accepts_only_that_exact_value() -> None:
    assert _validation.level("x", 0.5, 0.5, 0.5) == 0.5
    with pytest.raises(ValueError, match=r"x must be in \[0.5, 0.5\]"):
        _validation.level("x", math.nextafter(0.5, 1.0), 0.5, 0.5)


def test_level_inverted_bounds_reject_every_value() -> None:
    with pytest.raises(ValueError, match=r"x must be in \[1.0, 0.0\], got 0.5"):
        _validation.level("x", 0.5, 1.0, 0.0)


def test_level_checks_finiteness_before_the_range() -> None:
    with pytest.raises(ValueError, match="x must be a finite number, got nan"):
        _validation.level("x", float("nan"), 1.0, 0.0)


def test_level_negative_zero_returns_positive_zero() -> None:
    result = _validation.level("x", -0.0, 0.0, 1.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0


def test_level_fraction_range_error_repeats_the_original_value() -> None:
    with pytest.raises(ValueError, match=r"Fraction\(3, 2\)"):
        _validation.level("x", Fraction(3, 2), 0.0, 1.0)


# ---------------------------------------------------------------------------
# snapshot_mapping — the shared from_dict key check
# ---------------------------------------------------------------------------


def test_snapshot_mapping_identity_on_success() -> None:
    data: dict[str, object] = {}
    assert _validation.snapshot_mapping(data, frozenset()) is data


def test_snapshot_mapping_mapping_proxy_type_accepted_unchanged() -> None:
    data: MappingProxyType[str, object] = MappingProxyType({})
    assert _validation.snapshot_mapping(data, frozenset()) is data


def test_snapshot_mapping_unknown_key_reported() -> None:
    with pytest.raises(ValueError, match=r"snapshot has unknown keys \['a'\]"):
        _validation.snapshot_mapping({"a": 1}, frozenset())


def test_snapshot_mapping_missing_keys_reported_sorted() -> None:
    with pytest.raises(ValueError, match=r"snapshot is missing keys \['a', 'b'\]"):
        _validation.snapshot_mapping({}, frozenset({"b", "a"}))


def test_snapshot_mapping_missing_reported_before_unknown() -> None:
    with pytest.raises(ValueError, match="missing keys"):
        _validation.snapshot_mapping({"a": 1, "c": 1}, frozenset({"a", "b"}))


def test_snapshot_mapping_unknown_keys_of_mixed_types_sorted_by_repr() -> None:
    with pytest.raises(
        ValueError,
        match=r"snapshot has unknown keys \[\(1, 2\), 1, None\]",
    ):
        _validation.snapshot_mapping(
            {"a": 1, 1: 0, None: 0, (1, 2): 0}, frozenset({"a"})
        )


def test_snapshot_mapping_rejects_a_set() -> None:
    with pytest.raises(TypeError, match="snapshot must be a mapping, got set"):
        _validation.snapshot_mapping({1, 2}, frozenset({"a"}))


def test_snapshot_mapping_rejects_a_list_of_pairs() -> None:
    with pytest.raises(TypeError, match="snapshot must be a mapping, got list"):
        _validation.snapshot_mapping([("a", 1)], frozenset({"a"}))
