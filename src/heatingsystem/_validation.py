"""Shared numeric validation contract for the heating-system models.

This is the single place the numeric contract behind every validating
setting on :class:`~heatingsystem.pi_controller.pi_controller.PIController`
and :class:`~heatingsystem.modulator.modulator.Modulator` is stated:

* A value must be a real number (:class:`numbers.Real`); ``bool`` is
  rejected even though it is technically an ``int`` subclass.
* It must be finite: ``nan`` and ``inf`` are refused.
* It must be representable as a ``float``; an ``int`` too large to convert
  (e.g. ``10**400``) raises :class:`OverflowError` rather than silently
  losing precision.
* A bad value raises :class:`TypeError`, :class:`ValueError` or
  :class:`OverflowError`, always naming the offending attribute or
  parameter, and always leaving whatever was there before unchanged.
* A negative zero is normalised to positive zero.
* A window length is additionally required to be an ``int`` (``bool``
  excluded) of at least 1 and no larger than :data:`sys.maxsize`.
* A level is additionally required to lie within the caller-supplied
  ``[lower, upper]`` actuator range.

Every setter's ``Raises:`` section refers back to this module rather than
repeating the contract in prose.
"""

import math
import numbers
import sys
from collections.abc import Mapping


def finite(name: str, value: object) -> float:
    """Validate a value as a finite real number and return it as a float.

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


def window_length(value: object) -> int:
    """Validate a rolling-window length.

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


def level(name: str, value: object, lower: float, upper: float) -> float:
    """Validate a value as a finite level within an actuator range.

    Builds on :func:`finite`, adding the ``[lower, upper]`` range check
    shared by a fixed-output override and by each entry of a restored
    history. Takes the bounds as parameters rather than importing them from
    the modulator module, so this module has no dependency on it. Inverted
    bounds (``lower > upper``) are not special-cased: they simply make the
    range check reject every value, since nothing can satisfy both.

    Args:
        name: The attribute or parameter name, used in the error message.
        value: The value to validate.
        lower: The lower bound of the accepted range, inclusive.
        upper: The upper bound of the accepted range, inclusive.

    Returns:
        ``value`` converted to ``float``.

    Raises:
        TypeError: If ``value`` is a ``bool`` or not a real number.
        ValueError: If ``value`` is not finite, or lies outside
            ``[lower, upper]``.
        OverflowError: If ``value`` is too large to represent as a float.
    """
    number = finite(name, value)
    if number < lower or number > upper:
        raise ValueError(f"{name} must be in [{lower}, {upper}], got {value!r}.")
    return number


def snapshot_mapping(data: object, keys: frozenset[str]) -> Mapping[str, object]:
    """Validate a snapshot as a mapping with exactly the given keys.

    Shared by every ``from_dict`` in the package: a snapshot must be a
    ``Mapping``, and its key set must match ``keys`` exactly — missing keys
    are reported before unknown ones, so a snapshot with both kinds of
    problem reports the missing ones first.

    Args:
        data: The candidate snapshot.
        keys: The exact set of keys ``data`` must have.

    Returns:
        ``data``, unchanged.

    Raises:
        TypeError: If ``data`` is not a ``Mapping``, naming its type.
        ValueError: If a key is missing or unknown, naming the keys
            (missing keys reported first).
    """
    if not isinstance(data, Mapping):
        raise TypeError(f"snapshot must be a mapping, got {type(data).__name__}.")

    missing = sorted(keys - data.keys())
    if missing:
        raise ValueError(f"snapshot is missing keys {missing}.")
    # key=repr: an unknown key set may mix types (e.g. a str and an int)
    # that Python cannot compare with <, so sorted() alone can raise a bare
    # TypeError instead of the documented ValueError.
    unknown = sorted(data.keys() - keys, key=repr)
    if unknown:
        raise ValueError(f"snapshot has unknown keys {unknown}.")

    return data


def main() -> None:
    """Showcase this module's functionality."""
    name = "kp"
    value = -0.0

    validated = finite(name, value)

    print(f"finite({name!r}, {value!r}) = {validated!r}")

    # A value outside the actuator range raises ValueError naming it.
    name = "fixed_output"
    value = 1.5
    lower, upper = 0.0, 1.0

    try:
        level(name, value, lower, upper)
    except ValueError as exc:
        print(f"level({name!r}, {value!r}, {lower}, {upper}) -> ValueError: {exc}")

    # A snapshot missing a required key raises ValueError naming it.
    data: dict[str, object] = {"kp": 0.3}
    keys = frozenset({"kp", "ki"})

    try:
        snapshot_mapping(data, keys)
    except ValueError as exc:
        print(f"snapshot_mapping({data!r}, {keys!r}) -> ValueError: {exc}")


if __name__ == "__main__":
    main()
