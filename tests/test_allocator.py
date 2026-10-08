"""Tests for SectionAllocator and Room (heatingsystem.allocator)."""

import copy
import json
import math
import re
import sys
import tomllib
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest

import heatingsystem as hs
from heatingsystem import allocator as allocator_package
from heatingsystem.allocator import allocator as allocator_module
from heatingsystem.allocator.allocator import Room, SectionAllocator
from heatingsystem.pi_controller.pi_controller import PIController

# ---------------------------------------------------------------------------
# Helpers and the reference layout, in coverage terms (the fraction of a room's
# floor heating a section provides): R1 <- HS1 (0.7) + HS2 (0.3);
# R2 <- HS2 (0.3) + HS3 (0.7); R3 <- HS4 (1.0). Every room's coverages sum to
# 1, so a coverage is also its normalised coefficient. kp=1, ki=0 makes a
# room's demand exactly clamp(setpoint - measured).
# ---------------------------------------------------------------------------

GAINS = {"kp": 1.0, "ki": 0.0}
SETPOINT = 21.0


def ref_rooms(
    e1: float = 0.0,
    e2: float = 0.0,
    e3: float = 0.0,
    p1: float = 1.0,
    p2: float = 1.0,
    p3: float = 1.0,
) -> dict[str, dict[str, float]]:
    return {
        "R1": {"priority": p1, "evenness": e1},
        "R2": {"priority": p2, "evenness": e2},
        "R3": {"priority": p3, "evenness": e3},
    }


def ref_sections() -> dict[str, dict[str, float]]:
    return {
        "HS1": {"R1": 0.7},
        "HS2": {"R1": 0.3, "R2": 0.3},
        "HS3": {"R2": 0.7},
        "HS4": {"R3": 1.0},
    }


def ref(
    e1: float = 0.0, e2: float = 0.0, history_length: int = 24, **kwargs: float
) -> SectionAllocator:
    return SectionAllocator(
        ref_rooms(e1, e2, **kwargs),
        ref_sections(),
        history_length=history_length,
        kp=1.0,
        ki=0.0,
    )


def meas(*demands: float) -> dict[str, float]:
    """Temperatures that give R1, R2, R3 ... the stated demands."""
    return {f"R{i + 1}": SETPOINT - d for i, d in enumerate(demands)}


def coef(a: SectionAllocator, section: str, room: str) -> float:
    """Normalised coefficient: the coverage over the room's coverage total.

    Written from the public ``sections`` only, so it is independent of the
    allocator's own normalisation.
    """
    sections = a.sections
    total = math.fsum(c[room] for c in sections.values() if room in c)
    return sections[section].get(room, 0.0) / total


def delivered(a: SectionAllocator, room: str) -> float:
    duty = a.duty
    assert duty is not None
    return sum(coef(a, name, room) * duty[name] for name in a.sections)


def spread(a: SectionAllocator, room: str) -> float:
    """The penalised quantity: sum of (u_s - mean)^2 over the room's sections."""
    duty = a.duty
    assert duty is not None
    values = [duty[n] for n, shares in a.sections.items() if room in shares]
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values)


def allocate(
    a: SectionAllocator, **demands: float
) -> dict[str, float]:  # private call, as test_modulator does for helpers
    return a._allocate(demands)


def mismatch(a: SectionAllocator, room: str, demand: float) -> float:
    return abs(demand - delivered(a, room))


def snapshot(a: SectionAllocator) -> tuple[object, object, object]:
    return (
        a.to_dict(),
        a.duty,
        {name: room.demand for name, room in a.rooms.items()},
    )


def one_room(
    priority: float = 1.0, evenness: float = 0.0
) -> dict[str, dict[str, float]]:
    return {"R1": {"priority": priority, "evenness": evenness}}


# ---------------------------------------------------------------------------
# T1 / A1: layout refusals
# ---------------------------------------------------------------------------


def build(
    rooms: object = None, sections: object = None, **kwargs: object
) -> SectionAllocator:
    return SectionAllocator(
        ref_rooms() if rooms is None else rooms,  # type: ignore[arg-type]  # deliberate misuse
        ref_sections() if sections is None else sections,  # type: ignore[arg-type]  # deliberate misuse
        **kwargs,  # type: ignore[arg-type]  # deliberate misuse
    )


def with_coverage(
    value: object, section: str = "HS1", room: str = "R1"
) -> dict[str, dict[str, float]]:
    sections = ref_sections()
    sections[section][room] = value  # type: ignore[assignment]  # deliberate misuse
    return sections


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (0.0, ValueError),
        (-0.0, ValueError),
        (-0.1, ValueError),
        (math.nextafter(1.0, 2.0), ValueError),
        (1.5, ValueError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        ("0.5", TypeError),
        (None, TypeError),
        (True, TypeError),
        (10**400, OverflowError),
    ],
)
def test_construction_refuses_a_bad_coverage_naming_its_path(
    value: object, error: type[Exception]
) -> None:
    with pytest.raises(error, match=re.escape("sections['HS1']['R1']")):
        build(sections=with_coverage(value))


def test_construction_refuses_a_room_coverage_sum_above_one_naming_room_and_sum() -> (
    None
):
    sections = ref_sections()
    sections["HS2"] = {"R1": 0.6, "R2": 0.3}  # R1: 0.7 + 0.6
    total = math.fsum([0.7, 0.6])
    with pytest.raises(ValueError) as caught:
        build(sections=sections)
    assert str(caught.value) == f"rooms['R1'] coverages sum to {total}, more than 1."


def test_construction_names_the_first_room_in_room_order_when_several_are_over() -> (
    None
):
    sections = {"HS1": {"R1": 0.7, "R2": 0.7}, "HS2": {"R1": 0.6, "R2": 0.6}}
    rooms = {k: ref_rooms()[k] for k in ("R2", "R1")}  # R2 comes first
    with pytest.raises(ValueError, match=re.escape("rooms['R2']")):
        build(rooms=rooms, sections=sections)


def test_construction_accepts_a_section_covering_several_rooms_with_a_sum_above_one() -> (
    None
):
    """Only a room's own coverages are limited; a section may cover many rooms."""
    sections = {
        "HS1": {"R1": 0.7, "R2": 0.7, "R3": 0.7},
        "HS2": {"R1": 0.3, "R2": 0.3, "R3": 0.3},
    }
    a = build(sections=sections)
    assert a.sections["HS1"] == {"R1": 0.7, "R2": 0.7, "R3": 0.7}


def test_construction_accepts_coverage_sums_that_are_one_up_to_float_error() -> None:
    sections = ref_sections()
    sections["HS1"] = {"R1": 0.7}
    sections["HS2"] = {"R1": 0.1 * 3, "R2": 0.3}  # 0.7 + 0.30000000000000004
    a = build(sections=sections)
    assert a.sections["HS2"] == {"R1": 0.1 * 3, "R2": 0.3}
    ten = {f"HS{i}": {"R0": 0.1} for i in range(10)}
    build(rooms={"R0": {"priority": 1, "evenness": 0}}, sections=ten)
    thirds = {f"HS{i}": {"R1": 1 / 3} for i in range(3)}
    build(rooms=one_room(), sections=thirds)


@pytest.mark.parametrize(("extra", "accepted"), [(1e-9, True), (2e-9, False)])
def test_coverage_sum_tolerance_boundary_is_one_nanounit(
    extra: float, accepted: bool
) -> None:
    sections = {"HS1": {"R1": 1.0, "R2": extra}, "HS2": {"R2": 1.0}}
    rooms = {k: ref_rooms()[k] for k in ("R1", "R2")}
    if accepted:
        build(rooms=rooms, sections=sections)
    else:
        with pytest.raises(ValueError, match=re.escape("rooms['R2'] coverages sum")):
            build(rooms=rooms, sections=sections)


def test_construction_refuses_a_section_naming_an_unknown_room() -> None:
    sections = ref_sections()
    sections["HS1"] = {"RX": 1.0}
    with pytest.raises(ValueError, match="'RX'"):
        build(sections=sections)


def test_construction_refuses_a_non_string_room_in_a_coverage_mapping() -> None:
    with pytest.raises(ValueError, match="unknown room 1"):
        build(sections={"HS1": {1: 1.0}})


def test_construction_refuses_an_uncovered_room_naming_it_sorted() -> None:
    rooms = ref_rooms()
    rooms["R0"] = {"priority": 1.0, "evenness": 0.0}
    rooms["R9"] = {"priority": 1.0, "evenness": 0.0}
    with pytest.raises(ValueError, match=re.escape("['R0', 'R9']")):
        build(rooms=rooms)


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (0.0, ValueError),
        (-0.0, ValueError),
        (-1.0, ValueError),
        (float("nan"), ValueError),
        (float("-inf"), ValueError),
        ("x", TypeError),
        (None, TypeError),
        (True, TypeError),
        (10**400, OverflowError),
    ],
)
def test_construction_refuses_a_bad_priority_naming_its_path(
    value: object, error: type[Exception]
) -> None:
    rooms = ref_rooms()
    rooms["R1"]["priority"] = value  # type: ignore[assignment]  # deliberate misuse
    with pytest.raises(error, match=re.escape("rooms['R1'].priority")):
        build(rooms=rooms)


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (-1.0, ValueError),
        (-5e-324, ValueError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        ("x", TypeError),
        (True, TypeError),
        (10**400, OverflowError),
    ],
)
def test_construction_refuses_a_bad_evenness_naming_its_path(
    value: object, error: type[Exception]
) -> None:
    rooms = ref_rooms()
    rooms["R2"]["evenness"] = value  # type: ignore[assignment]  # deliberate misuse
    with pytest.raises(error, match=re.escape("rooms['R2'].evenness")):
        build(rooms=rooms)


def test_construction_accepts_the_edges_of_every_range() -> None:
    sections = {"HS1": {"R1": 5e-324}, "HS2": {"R1": 1.0}}  # sum 1.0 after rounding
    a = build(rooms=one_room(priority=5e-324, evenness=-0.0), sections=sections)
    assert a.rooms["R1"].priority == 5e-324
    assert math.copysign(1.0, a.rooms["R1"].evenness) == 1.0
    assert a.sections["HS1"]["R1"] == 5e-324
    build(rooms=one_room(evenness=1.0), sections={"HS1": {"R1": 1}})


def test_construction_stores_int_inputs_as_float() -> None:
    a = build(rooms=one_room(priority=1, evenness=1), sections={"HS1": {"R1": 1}})
    assert type(a.sections["HS1"]["R1"]) is float
    assert type(a.rooms["R1"].priority) is float
    assert type(a.rooms["R1"].evenness) is float


@pytest.mark.parametrize(
    ("rooms", "error", "match"),
    [
        ([], TypeError, "rooms must be a mapping"),
        ([("R1", {})], TypeError, "rooms must be a mapping"),
        ({}, ValueError, "rooms must not be empty"),
        ({"": {"priority": 1, "evenness": 0}}, ValueError, "non-empty"),
        ({1: {"priority": 1, "evenness": 0}}, TypeError, "must be str"),
        ({"R1": []}, TypeError, re.escape("rooms['R1'] must be a mapping")),
        ({"R1": {"priority": 1}}, ValueError, re.escape("rooms['R1']: ")),
        ({"R1": {"priority": 1, "evenness": 0, "x": 1}}, ValueError, "unknown keys"),
        ({"R1": {"Priority": 1, "evenness": 0}}, ValueError, "missing keys"),
    ],
)
def test_construction_refuses_a_malformed_rooms_argument(
    rooms: object, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        SectionAllocator(rooms, {"HS1": {"R1": 1.0}})  # type: ignore[arg-type]  # deliberate misuse


@pytest.mark.parametrize(
    ("sections", "error", "match"),
    [
        ([], TypeError, "sections must be a mapping"),
        ({}, ValueError, "sections must not be empty"),
        ({"": {"R1": 1.0}}, ValueError, "non-empty"),
        ({1: {"R1": 1.0}}, TypeError, "must be str"),
        ({"HS1": []}, TypeError, re.escape("sections['HS1'] must be a mapping")),
        ({"HS1": {}}, ValueError, re.escape("sections['HS1'] must cover")),
    ],
)
def test_construction_refuses_a_malformed_sections_argument(
    sections: object, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        SectionAllocator(one_room(), sections)  # type: ignore[arg-type]  # deliberate misuse


def test_room_and_section_may_share_a_name_and_whitespace_names_are_accepted() -> None:
    rooms = {
        "R1": {"priority": 1, "evenness": 0},
        " ": {"priority": 1, "evenness": 0},
        "Stue/Køkken ☀": {"priority": 1, "evenness": 0},
    }
    sections = {
        "R1": {"R1": 0.5, " ": 0.5},
        " ": {" ": 0.5, "Stue/Køkken ☀": 0.5},
        "Stue/Køkken ☀": {"Stue/Køkken ☀": 0.5},
        "S": {"R1": 0.5},
    }
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0, history_length=4)
    measured = {"R1": 20.5, " ": 20.2, "Stue/Køkken ☀": 20.0}
    twin = SectionAllocator.from_dict(json.loads(json.dumps(a.to_dict())))
    for _ in range(6):
        assert a.update(measured) == twin.update(measured)
    assert list(a.rooms) == ["R1", " ", "Stue/Køkken ☀"]


def test_construction_refuses_a_bad_history_length_like_pi_controller() -> None:
    for value in (0, -1, True, 1.0, "24", None, sys.maxsize + 1):
        with pytest.raises(Exception) as direct:  # noqa: B017 - class compared below
            PIController(history_length=value)  # type: ignore[arg-type]  # deliberate misuse
        with pytest.raises(type(direct.value)) as allocator:
            build(history_length=value)
        assert str(allocator.value) == str(direct.value)


def test_history_length_edges_one_and_maxsize_are_accepted() -> None:
    assert build(history_length=1).history_length == 1
    assert build(history_length=sys.maxsize).history_length == sys.maxsize
    a = build(
        rooms=one_room(),
        sections={"HS1": {"R1": 1.0}},
        history_length=1,
        kp=1.0,
        ki=0.0,
    )
    commands = [a.update({"R1": 20.5})["HS1"] for _ in range(4)]
    assert commands == [1.0, 0.0, 1.0, 0.0]


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"kp": float("nan")}, "kp"),
        ({"kp": "x"}, "kp"),
        ({"ki": float("inf")}, "ki"),
        ({"ki": True}, "ki"),
        ({"setpoint": 10**400}, "setpoint"),
        ({"setpoint": None}, "setpoint"),
    ],
)
def test_bad_shared_gain_matches_pi_controller_and_names_the_argument(
    kwargs: dict[str, object], name: str
) -> None:
    with pytest.raises(Exception) as direct:  # noqa: B017 - class compared below
        PIController(**kwargs)  # type: ignore[arg-type]  # deliberate misuse
    with pytest.raises(type(direct.value)) as allocator:
        build(**kwargs)
    assert str(allocator.value) == str(direct.value)
    assert str(allocator.value).startswith(name)
    assert "rooms[" not in str(allocator.value)


def test_error_order_priority_before_coverage_and_coverage_before_history_length() -> (
    None
):
    rooms = ref_rooms()
    rooms["R1"]["priority"] = 0.0
    with pytest.raises(ValueError, match=re.escape("rooms['R1'].priority")):
        build(rooms=rooms, sections=with_coverage(0.0))
    with pytest.raises(ValueError, match=re.escape("sections['HS1']['R1']")):
        build(sections=with_coverage(0.0), history_length=0)
    with pytest.raises(ValueError, match="kp"):
        build(rooms=rooms, kp=float("nan"))


def test_construction_does_not_alias_the_callers_mappings() -> None:
    rooms = ref_rooms()
    sections = ref_sections()
    a = build(rooms=rooms, sections=sections, kp=1.0, ki=0.0)
    expected = a.update(meas(0.1, 0.6, 0.0))
    sections["HS1"]["R1"] = 0.1
    sections["HS2"]["R1"] = 0.9
    rooms["R1"]["priority"] = 9.0
    rooms["R1"]["evenness"] = 9.0
    assert a.sections["HS1"]["R1"] == 0.7
    assert a.sections["HS2"]["R1"] == 0.3
    assert a.rooms["R1"].priority == 1.0
    assert a.rooms["R1"].evenness == 0.0
    fresh = build(rooms=ref_rooms(), sections=ref_sections(), kp=1.0, ki=0.0)
    assert fresh.update(meas(0.1, 0.6, 0.0)) == expected
    assert a.update(meas(0.1, 0.6, 0.0)) == fresh.update(meas(0.1, 0.6, 0.0))


def test_construction_accepts_mapping_proxies_and_ordered_dicts() -> None:
    rooms = MappingProxyType({k: MappingProxyType(v) for k, v in ref_rooms().items()})
    sections = OrderedDict((k, MappingProxyType(v)) for k, v in ref_sections().items())
    a = build(rooms=rooms, sections=sections)
    assert list(a.sections) == ["HS1", "HS2", "HS3", "HS4"]


# ---------------------------------------------------------------------------
# T2 / A1: the layout is read-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "assign",
    [
        lambda a: setattr(a, "rooms", {}),
        lambda a: setattr(a, "sections", {}),
        lambda a: setattr(a, "history_length", 3),
        lambda a: setattr(a, "duty", {}),
        lambda a: setattr(a, "history", {}),
        lambda a: setattr(a.rooms["R1"], "name", "x"),
        lambda a: setattr(a.rooms["R1"], "priority", 2.0),
        lambda a: setattr(a.rooms["R1"], "evenness", 1.0),
        lambda a: setattr(a.rooms["R1"], "integral", 1.0),
        lambda a: setattr(a.rooms["R1"], "demand", 0.5),
    ],
)
def test_layout_and_state_attributes_cannot_be_assigned(
    assign: Callable[[SectionAllocator], None],
) -> None:
    a = ref()
    with pytest.raises(AttributeError):
        assign(a)


def test_rooms_view_rejects_item_assignment_and_deletion() -> None:
    a = ref()
    with pytest.raises(TypeError):
        a.rooms["R9"] = a.rooms["R1"]  # type: ignore[index]  # read-only view
    with pytest.raises(TypeError):
        del a.rooms["R1"]  # type: ignore[attr-defined]  # read-only view


def test_returned_copies_do_not_reach_the_controller() -> None:
    a = ref()
    a.update(meas(0.1, 0.6, 0.0))
    before = snapshot(a)
    a.sections["HS2"]["R1"] = 0.0
    a.sections["NEW"] = {}
    duty = a.duty
    assert duty is not None
    duty["HS1"] = 1.0
    a.history["HS1"] = ()
    assert a.sections is not a.sections
    assert a.duty is not a.duty
    assert snapshot(a) == before
    assert a.sections["HS2"]["R1"] == 0.3


def test_room_does_not_expose_the_pi_controller_surface() -> None:
    room = ref().rooms["R1"]
    for name in ("mode", "fixed_output", "update", "reset", "history", "pi_output"):
        assert not hasattr(room, name)


def test_to_dict_containers_are_fresh_and_do_not_reach_back() -> None:
    a = ref(history_length=4)
    a.update(meas(0.1, 0.6, 0.0))
    d = a.to_dict()
    d["sections"]["HS1"]["history"].append(1.0)  # type: ignore[index]  # object-typed snapshot
    d["sections"]["HS1"]["coverage"]["R1"] = 0.1  # type: ignore[index]  # object-typed snapshot
    d["rooms"]["R1"]["kp"] = 99.0  # type: ignore[index]  # object-typed snapshot
    assert a.to_dict() != d
    assert a.to_dict() == ref_after_one_update()


def ref_after_one_update() -> dict[str, object]:
    a = ref(history_length=4)
    a.update(meas(0.1, 0.6, 0.0))
    return a.to_dict()


# ---------------------------------------------------------------------------
# T3 / A2: per-room setpoint, kp and ki
# ---------------------------------------------------------------------------

BAD_NUMBERS: list[object] = [
    float("nan"),
    float("inf"),
    float("-inf"),
    "x",
    None,
    True,
    Decimal("1"),
    complex(1),
    [1.0],
    10**400,
    10**308 * 10,
]


@pytest.mark.parametrize("attribute", ["setpoint", "kp", "ki"])
@pytest.mark.parametrize("value", BAD_NUMBERS, ids=repr)
def test_room_setters_match_pi_controller_and_keep_the_old_value(
    attribute: str, value: object
) -> None:
    standalone = PIController()
    room = ref().rooms["R2"]
    old = getattr(room, attribute)
    with pytest.raises(Exception) as direct:  # noqa: B017 - class compared below
        setattr(standalone, attribute, value)
    with pytest.raises(type(direct.value)) as raised:
        setattr(room, attribute, value)
    assert str(raised.value) == str(direct.value)
    assert getattr(room, attribute) == old


@pytest.mark.parametrize("attribute", ["setpoint", "kp", "ki"])
def test_room_setters_accept_int_and_fraction_and_store_float(attribute: str) -> None:
    room = ref().rooms["R1"]
    setattr(room, attribute, 2)
    assert getattr(room, attribute) == 2.0
    assert type(getattr(room, attribute)) is float
    setattr(room, attribute, Fraction(1, 4))
    assert getattr(room, attribute) == 0.25


def test_room_setting_change_takes_effect_on_the_next_update_like_a_twin() -> None:
    a = SectionAllocator(ref_rooms(), ref_sections(), kp=0.4, ki=0.02, history_length=6)
    twin = PIController(0.4, 0.02, "radiator", SETPOINT, history_length=1)
    for step in range(12):
        m3 = 20.0 + 0.1 * step
        if step == 5:
            a.rooms["R3"].kp = 0.9
            a.rooms["R3"].ki = 0.05
            a.rooms["R3"].setpoint = 22.0
            twin.kp = 0.9
            twin.ki = 0.05
            twin.setpoint = 22.0
        r1_before = a.rooms["R1"].integral
        a.update({"R1": 20.5, "R2": 20.5, "R3": m3})
        assert a.rooms["R3"].demand == twin.update(m3)
        assert a.rooms["R3"].integral == twin.integral
        assert a.rooms["R1"].setpoint == SETPOINT
        assert a.rooms["R1"].kp == 0.4
        assert a.rooms["R1"].integral >= r1_before


def test_a_setting_change_leaves_other_rooms_and_state_untouched() -> None:
    a = ref()
    a.update(meas(0.1, 0.6, 0.3))
    before = a.to_dict()
    duty = a.duty
    a.rooms["R3"].kp = 0.5
    expected = copy.deepcopy(before)
    expected["rooms"]["R3"]["kp"] = 0.5  # type: ignore[index]  # object-typed snapshot
    assert a.to_dict() == expected
    assert a.duty == duty


def test_room_numeric_setters_normalise_negative_zero() -> None:
    room = ref().rooms["R1"]
    room.setpoint = -0.0
    room.kp = -0.0
    room.ki = -0.0
    for attribute in ("setpoint", "kp", "ki"):
        assert math.copysign(1.0, getattr(room, attribute)) == 1.0


# ---------------------------------------------------------------------------
# T4 / A3: update
# ---------------------------------------------------------------------------


def test_update_returns_exactly_the_section_names_with_binary_values() -> None:
    a = ref()
    commands = a.update(meas(0.1, 0.6, 0.3))
    assert list(commands) == ["HS1", "HS2", "HS3", "HS4"]
    assert set(commands.values()) <= {0.0, 1.0}
    assert all(type(v) is float for v in commands.values())


def test_update_returns_a_fresh_dict_that_cannot_reach_the_history() -> None:
    a = ref()
    first = a.update(meas(0.1, 0.6, 0.3))
    first["HS1"] = 99.0
    second = a.update(meas(0.1, 0.6, 0.3))
    assert second is not first
    assert 99.0 not in a.history["HS1"]


def test_duty_and_demand_are_none_before_the_first_update_and_set_after() -> None:
    a = ref()
    assert a.duty is None
    assert all(room.demand is None for room in a.rooms.values())
    a.update(meas(0.1, 0.6, 0.3))
    assert a.duty is not None
    assert list(a.duty) == ["HS1", "HS2", "HS3", "HS4"]
    assert a.rooms["R1"].demand == pytest.approx(0.1)
    assert a.rooms["R2"].demand == pytest.approx(0.6)
    assert a.rooms["R3"].demand == pytest.approx(0.3)


@pytest.mark.parametrize(
    ("measured", "error", "match"),
    [
        ([("R1", 20.0)], TypeError, "measured must be a mapping"),
        ("R1", TypeError, "measured must be a mapping"),
        (None, TypeError, "measured must be a mapping"),
        ({}, ValueError, re.escape("['R1', 'R2', 'R3']")),
        ({"R1": 20.0, "R2": 20.0}, ValueError, re.escape("missing rooms ['R3']")),
        (
            {"R1": 20.0, "R2": 20.0, "R3": 20.0, "R9": 1.0},
            ValueError,
            re.escape("unknown rooms ['R9']"),
        ),
        (
            {"R1": 20.0, "R2": 20.0, "R3": 20.0, "x": 1.0, 1: 2.0},
            ValueError,
            "unknown rooms",
        ),
        ({"R1": 20.0, "R2": 20.0, "R9": 1.0}, ValueError, "missing rooms"),
        (
            {"R1": float("nan"), "R2": 20.0, "R3": 20.0},
            ValueError,
            re.escape("measured['R1']"),
        ),
        (
            {"R1": 20.0, "R2": float("inf"), "R3": 20.0},
            ValueError,
            re.escape("measured['R2']"),
        ),
        (
            {"R1": 20.0, "R2": 20.0, "R3": float("-inf")},
            ValueError,
            re.escape("measured['R3']"),
        ),
        (
            {"R1": np.float64("inf"), "R2": 20.0, "R3": 20.0},
            ValueError,
            re.escape("measured['R1']"),
        ),
        ({"R1": "20", "R2": 20.0, "R3": 20.0}, TypeError, re.escape("measured['R1']")),
        ({"R1": 20.0, "R2": True, "R3": 20.0}, TypeError, re.escape("measured['R2']")),
        (
            {"R1": 10**400, "R2": 20.0, "R3": 20.0},
            OverflowError,
            re.escape("measured['R1']"),
        ),
    ],
    ids=repr,
)
def test_update_refuses_bad_measurements_and_changes_nothing(
    measured: object, error: type[Exception], match: str
) -> None:
    a = ref(e1=0.1, history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    a.update(meas(0.2, 0.5, 0.0))
    before = snapshot(a)
    history = a.history
    with pytest.raises(error, match=match):
        a.update(measured)  # type: ignore[arg-type]  # deliberate misuse
    assert snapshot(a) == before
    assert a.history == history


def test_update_accepts_none_as_no_reading_and_leaves_that_room_untouched() -> None:
    a = ref(e1=0.1, history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    room = a.rooms["R3"]
    integral, demand = room.integral, room.demand
    a.update({"R1": 20.0, "R2": 20.0, "R3": None})
    assert room.integral == integral
    assert room.demand == demand


def test_update_reports_a_missing_room_before_a_bad_value() -> None:
    a = ref()
    with pytest.raises(ValueError, match="missing rooms"):
        a.update({"R1": float("nan"), "R2": 20.0})


def test_update_does_not_mutate_the_callers_measurements() -> None:
    a = ref()
    measured = meas(0.1, 0.6, 0.3)
    expected = dict(measured)
    a.update(measured)
    assert measured == expected
    proxy = MappingProxyType(dict(reversed(list(expected.items()))))
    a.update(proxy)
    assert dict(proxy) == expected


def test_update_accepts_numeric_variants_and_any_key_order() -> None:
    plain = ref(history_length=4)
    variant = ref(history_length=4)
    for _ in range(5):
        expected = plain.update({"R1": 20.9, "R2": 20.4, "R3": 21.0})
        got = variant.update(
            MappingProxyType(
                {
                    "R3": 21,
                    "R2": np.float64(20.4),
                    "R1": Fraction(209, 10),  # type: ignore[dict-item]  # Real, not float
                }
            )
        )
        assert got == expected
        assert variant.duty == plain.duty
        assert {n: r.demand for n, r in variant.rooms.items()} == {
            n: r.demand for n, r in plain.rooms.items()
        }


def test_update_normalises_negative_zero_measurements() -> None:
    a = SectionAllocator(one_room(), {"HS1": {"R1": 1.0}}, kp=1.0, ki=0.0, setpoint=0.0)
    a.update({"R1": -0.0})
    assert a.rooms["R1"].demand is not None
    assert math.copysign(1.0, a.rooms["R1"].demand) == 1.0


def test_update_all_rooms_warm_gives_exact_positive_zero_duty() -> None:
    a = ref(e1=1.0)
    commands = a.update(meas(0.0, 0.0, 0.0))
    assert a.duty is not None
    for value in a.duty.values():
        assert value == 0.0
        assert math.copysign(1.0, value) == 1.0
    assert set(commands.values()) == {0.0}


def test_update_with_demand_far_above_range_clamps_to_full_duty() -> None:
    a = ref(e1=0.5, e2=0.5)
    a.update({"R1": -50.0, "R2": -50.0, "R3": -50.0})
    assert a.duty is not None
    assert all(0.0 <= v <= 1.0 for v in a.duty.values())
    assert a.duty["HS4"] == 1.0


# ---------------------------------------------------------------------------
# T4b: failures inside the allocation leave no trace
# ---------------------------------------------------------------------------


def _patch_solver(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    def fake(*_args: object, **_kwargs: object) -> object:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(allocator_module, "lsq_linear", fake)


@pytest.mark.parametrize(
    ("result", "error", "match"),
    [
        (
            SimpleNamespace(x=np.array([np.nan, 0.0, 0.0]), status=1),
            ArithmeticError,
            re.escape("['HS1', 'HS2', 'HS3']"),
        ),
        (
            SimpleNamespace(x=np.array([np.inf, 0.0, 0.0]), status=1),
            ArithmeticError,
            "not converge to a finite",
        ),
        (
            SimpleNamespace(x=np.array([0.1, 0.1, 0.1]), status=0),
            ArithmeticError,
            "solver status 0",
        ),
        (
            SimpleNamespace(x=np.array([0.1, 0.1, 0.1]), status=-1),
            ArithmeticError,
            "solver status -1",
        ),
        (ValueError("boom"), ValueError, "boom"),
        (np.linalg.LinAlgError("singular"), np.linalg.LinAlgError, "singular"),
    ],
    ids=["nan", "inf", "status-0", "status-minus-1", "valueerror", "linalgerror"],
)
def test_update_restores_every_room_and_keeps_the_previous_duty_on_solver_failure(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    error: type[Exception],
    match: str,
) -> None:
    a = ref(e1=0.1, history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    before = snapshot(a)
    history = a.history
    _patch_solver(monkeypatch, result)
    with pytest.raises(error, match=match):
        a.update(meas(0.9, 0.9, 0.9))
    assert snapshot(a) == before
    assert a.duty is not None
    assert a.history == history
    monkeypatch.undo()
    # The controller still works afterwards and matches an untouched twin.
    twin = ref(e1=0.1, history_length=4)
    twin.update(meas(0.1, 0.6, 0.3))
    assert a.update(meas(0.2, 0.5, 0.0)) == twin.update(meas(0.2, 0.5, 0.0))


def test_update_failure_before_the_first_update_leaves_duty_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = ref()
    _patch_solver(monkeypatch, ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        a.update(meas(0.1, 0.6, 0.3))
    assert a.duty is None
    assert all(room.demand is None for room in a.rooms.values())
    assert a.to_dict() == ref().to_dict()


def test_update_restores_state_when_a_modulator_rejects_the_duty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allocator bug leaking an out-of-range duty cannot reach the actuator."""
    a = ref(history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    before = snapshot(a)
    monkeypatch.setattr(
        SectionAllocator,
        "_allocate",
        lambda _self, _demand: dict.fromkeys(("HS1", "HS2", "HS3", "HS4"), 1.5),
    )
    with pytest.raises(ValueError, match="level"):
        a.update(meas(0.9, 0.9, 0.9))
    assert a.duty == before[1]


@pytest.mark.parametrize("weight", [0.5, 1e-6, 1e-300, 1e-308, 1e-320, 5e-324])
@pytest.mark.parametrize(("e1", "e2"), [(0.0, 0.0), (0.4, 0.2), (1.0, 0.1)])
def test_uniformly_scaled_priorities_allocate_like_unit_priorities(
    weight: float, e1: float, e2: float
) -> None:
    """A11 for equal priorities, down to the smallest subnormal (ratio taken first)."""
    demand = {"R1": 0.1, "R2": 0.6, "R3": 0.3}
    scaled = SectionAllocator(
        ref_rooms(e1, e2, 0.0, weight, weight, weight),
        ref_sections(),
        kp=1.0,
        ki=0.0,
    )
    unit = SectionAllocator(ref_rooms(e1, e2), ref_sections(), kp=1.0, ki=0.0)
    got = scaled._allocate(demand)
    want = unit._allocate(demand)
    assert all(math.isfinite(v) for v in got.values())
    assert got == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("factor", [0.3, 0.7, 1e-3, 1e-9])
@pytest.mark.parametrize(
    ("p1", "p2", "e1", "e2"),
    [
        (1.0, 1.0, 1.0, 1.0),  # worked example C
        (0.3, 1.0, 1.0, 1.0),  # worked example E
        (1.0, 1.0, 1.0, 0.1),  # worked example D
        (1.0, 0.5, 0.0, 0.0),  # many exact fits: the tie-break must not move
        (0.8, 1.0, 0.3, 0.7),
    ],
)
def test_scaling_every_priority_by_one_factor_leaves_the_duties_unchanged(
    factor: float, p1: float, p2: float, e1: float, e2: float
) -> None:
    demand = {"R1": 0.1, "R2": 0.6, "R3": 0.3}
    base = SectionAllocator(
        ref_rooms(e1, e2, p1=p1, p2=p2, p3=1.0), ref_sections(), kp=1.0, ki=0.0
    )
    scaled = SectionAllocator(
        ref_rooms(e1, e2, p1=p1 * factor, p2=p2 * factor, p3=factor),
        ref_sections(),
        kp=1.0,
        ki=0.0,
    )
    assert scaled._allocate(demand) == pytest.approx(base._allocate(demand), abs=1e-9)


@pytest.mark.parametrize(
    ("priority", "evenness"),
    [(5e-324, 0.4), (5e-324, 0.2), (1e-323, 0.2), (1e-300, 0.4), (1.0, 0.4)],
)
def test_evenness_survives_a_subnormal_priority(
    priority: float, evenness: float
) -> None:
    """Regression: priority * evenness underflowed to 0 and dropped the spread rows."""
    # Two equal coverages: the room's heat is the mean duty, so demand 0.6 is
    # met exactly (and with zero spread) only by 0.6 on both.
    sections = {"HS1": {"R1": 0.5}, "HS2": {"R1": 0.5}}
    a = SectionAllocator(one_room(priority, evenness), sections, kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.6})
    assert got["HS1"] == pytest.approx(0.6, abs=1e-9)
    assert got["HS2"] == pytest.approx(0.6, abs=1e-9)


@pytest.mark.parametrize("priority", [1.0, 0.5, 1e-300, 5e-324])
def test_evenness_zero_ignores_the_priority_scale_exactly(priority: float) -> None:
    demand = {"R1": 0.1, "R2": 0.6, "R3": 0.0}
    base = SectionAllocator(ref_rooms(), ref_sections(), kp=1.0, ki=0.0)
    scaled = SectionAllocator(
        ref_rooms(p1=priority, p2=priority, p3=priority),
        ref_sections(),
        kp=1.0,
        ki=0.0,
    )
    assert scaled._allocate(demand) == base._allocate(demand)  # bit-identical


@pytest.mark.parametrize("evenness", [5e-324, 1e-13, 1e-12])
def test_tiny_positive_evenness_is_floored_not_dropped(evenness: float) -> None:
    a = SectionAllocator(ref_rooms(e1=evenness), ref_sections(), kp=1.0, ki=0.0)
    component = next(c for c in a._components if c.matrix is not None)
    assert component.matrix is not None
    assert component.matrix.shape[0] == 2 + 2  # two fit rows, two spread rows
    # R1: 0.7 u1 + 0.3 u2 = 0.1 and R2: 0.3 u2 + 0.7 u3 = 0.3 leave the exact
    # fits u1 = (0.1 - 0.3 t) / 0.7, u2 = t; the spread of R1 is least at
    # u1 = u2 = 0.1 (t = 0.1), and then u3 = (0.3 - 0.03) / 0.7.
    got = a._allocate({"R1": 0.1, "R2": 0.3, "R3": 0.0})
    assert got["HS1"] == pytest.approx(got["HS2"], abs=1e-6)
    assert got["HS1"] == pytest.approx(0.1, abs=1e-6)
    assert got["HS3"] == pytest.approx(0.27 / 0.7, abs=1e-6)


# ---------------------------------------------------------------------------
# T5 / A4: evenness 0 meets feasible demands exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "demands",
    [
        (0.1, 0.6, 0.0),
        (0.3, 0.8, 0.5),
        (0.0, 0.0, 0.0),
        (0.9, 0.2, 1.0),  # 0.7 u1 + 0.3 u2 = 0.9 with u2 = 2/3, u3 = 0
        (1.0, 1.0, 1.0),
    ],
)
def test_feasible_demands_are_delivered_exactly_with_evenness_zero(
    demands: tuple[float, float, float],
) -> None:
    a = ref()
    a.update(meas(*demands))
    assert a.duty is not None
    assert all(0.0 <= v <= 1.0 for v in a.duty.values())
    for room, demand in zip(("R1", "R2", "R3"), demands, strict=True):
        assert delivered(a, room) == pytest.approx(demand, abs=1e-9)


def test_the_documented_example_meets_both_demands_exactly() -> None:
    a = ref()
    a.update(meas(0.1, 0.6, 0.0))
    assert delivered(a, "R1") == pytest.approx(0.1, abs=1e-9)
    assert delivered(a, "R2") == pytest.approx(0.6, abs=1e-9)


def test_a_chain_of_three_rooms_meets_feasible_demands() -> None:
    rooms = {f"R{i}": {"priority": 1.0, "evenness": 0.0} for i in (1, 2, 3)}
    sections = {  # every room's coverages sum to exactly 1
        "S1": {"R1": 0.5, "R2": 0.5},
        "S2": {"R2": 0.5, "R3": 0.5},
        "S3": {"R1": 0.5},
        "S4": {"R3": 0.5},
    }
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    u = {"S1": 0.3, "S2": 0.7, "S3": 0.4, "S4": 0.2}
    demand = {r: sum(sections[s].get(r, 0.0) * u[s] for s in sections) for r in rooms}
    got = a._allocate(demand)
    for room in rooms:
        d = sum(sections[s].get(room, 0.0) * got[s] for s in sections)
        assert d == pytest.approx(demand[room], abs=1e-9)
    assert all(0.0 <= v <= 1.0 for v in got.values())


def test_a_long_feasible_chain_is_solved_within_the_iteration_budget() -> None:
    n = 60
    rooms = {f"R{i}": {"priority": 1.0, "evenness": 0.0} for i in range(n)}
    sections: dict[str, dict[str, float]] = {}
    for i in range(n - 1):
        sections[f"S{i}"] = {f"R{i}": 0.5, f"R{i + 1}": 0.5}
    sections[f"S{n - 1}"] = {f"R{n - 1}": 0.5}
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    u = {name: 0.1 + 0.4 * ((i * 7) % 11) / 10 for i, name in enumerate(sections)}
    demand = {
        r: sum(coef(a, s, r) * u[s] for s in sections if r in sections[s])
        for r in rooms
    }
    got = a._allocate(demand)
    for room in rooms:
        d = sum(coef(a, s, room) * got[s] for s in sections if room in sections[s])
        assert d == pytest.approx(demand[room], abs=1e-9)


@pytest.mark.parametrize(
    ("demand", "duty"), [(0.0, 0.0), (0.3, 0.3), (0.6, 0.6), (1.0, 1.0)]
)
def test_closed_form_component_gives_min_of_one_and_the_demand(
    demand: float, duty: float
) -> None:
    """A lone section's normalised coefficient is 1, whatever its coverage."""
    a = SectionAllocator(one_room(), {"S": {"R1": 0.5}}, kp=1.0, ki=0.0)
    a.update({"R1": SETPOINT - demand})
    assert a.duty is not None
    assert a.duty["S"] == pytest.approx(duty, abs=1e-12)
    assert allocate(a, R1=demand)["S"] == min(1.0, demand)


def test_closed_form_gives_exact_values_for_exact_demands() -> None:
    a = SectionAllocator(one_room(), {"S": {"R1": 0.5}}, kp=1.0, ki=0.0)
    assert allocate(a, R1=0.0) == {"S": 0.0}
    assert allocate(a, R1=0.25) == {"S": 0.25}
    assert allocate(a, R1=1.0) == {"S": 1.0}


def test_shared_components_use_least_squares_not_the_closed_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = allocator_module.lsq_linear

    def spy(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real(*args, **kwargs)  # type: ignore[operator]  # scipy untyped

    monkeypatch.setattr(allocator_module, "lsq_linear", spy)
    # A section covering two rooms.
    two = SectionAllocator(
        {k: ref_rooms()[k] for k in ("R1", "R2")},
        {"S": {"R1": 0.5, "R2": 0.5}},
        kp=1.0,
        ki=0.0,
    )
    two._allocate({"R1": 0.1, "R2": 0.1})
    assert len(calls) == 1
    # A room with one section that also covers another room.
    allocate(ref(), R1=0.1, R2=0.2, R3=0.3)
    assert len(calls) == 2  # R3's component is closed form: no third call
    # A room with two sections and nobody else.
    one = SectionAllocator(
        one_room(), {"A": {"R1": 0.5}, "B": {"R1": 0.5}}, kp=1.0, ki=0.0
    )
    one._allocate({"R1": 0.3})
    assert len(calls) == 3


def test_components_are_independent_of_each_others_demands() -> None:
    a = ref()
    first = allocate(a, R1=0.1, R2=0.6, R3=0.0)
    second = allocate(a, R1=0.1, R2=0.6, R3=1.0)
    assert {k: first[k] for k in ("HS1", "HS2", "HS3")} == {
        k: second[k] for k in ("HS1", "HS2", "HS3")
    }


def test_single_shared_section_weights_fit_rows_by_square_root_priority() -> None:
    rooms = {
        "R1": {"priority": 0.25, "evenness": 0.0},
        "R2": {"priority": 0.25, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": 0.0},
    }
    third = 1 / 3  # each room's only section: its normalised coefficient is 1
    sections = {"HS": {"R1": third, "R2": third, "R3": third}}
    weighted = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    got = weighted._allocate({"R1": 0.1, "R2": 0.2, "R3": 0.3})
    # u = (0.25 * 0.1 + 0.25 * 0.2 + 1.0 * 0.3) / (0.25 + 0.25 + 1.0) = 0.25
    assert got["HS"] == pytest.approx(0.25, abs=1e-9)
    for room in rooms.values():
        room["priority"] = 1.0
    equal = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    assert equal._allocate({"R1": 0.1, "R2": 0.2, "R3": 0.3})["HS"] == pytest.approx(
        0.2,
        abs=1e-9,  # the mean demand
    )


def test_duplicate_sections_make_a_rank_deficient_matrix_that_still_solves() -> None:
    rooms = {k: ref_rooms()[k] for k in ("R1", "R2")}
    sections = {"HS1": {"R1": 0.5, "R2": 0.5}, "HS2": {"R1": 0.5, "R2": 0.5}}
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    feasible = a._allocate({"R1": 0.3, "R2": 0.3})
    assert feasible["HS1"] + feasible["HS2"] == pytest.approx(0.6, abs=1e-9)
    compromise = a._allocate({"R1": 0.2, "R2": 0.6})
    assert 0.5 * (compromise["HS1"] + compromise["HS2"]) == pytest.approx(0.4, abs=1e-9)
    assert all(0.0 <= v <= 1.0 for v in compromise.values())


def test_tiny_coverages_stay_finite_and_in_range() -> None:
    rooms = {k: ref_rooms()[k] for k in ("R1", "R2")}
    sections = {"HS1": {"R1": 1e-300, "R2": 1e-300}, "HS2": {"R1": 1.0}}
    # R2's only section is HS1: normalised, its coefficient is 1, so R2's demand
    # is reachable however small the coverage; R1 gets 1e-300 from HS1.
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.5, "R2": 0.5})
    assert all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in got.values())
    assert got["HS1"] == pytest.approx(0.5, abs=1e-9)
    assert got["HS2"] == pytest.approx(0.5, abs=1e-9)
    # With a second R2 section the tiny coverage really is negligible.
    more = {**sections, "HS3": {"R2": 1.0}}
    b = SectionAllocator(rooms, more, kp=1.0, ki=0.0)
    got = b._allocate({"R1": 0.5, "R2": 0.5})
    assert all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in got.values())
    assert got["HS3"] == pytest.approx(0.5, abs=1e-9)
    assert got["HS2"] == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# The weight floor: very different priorities must not drop a room's demand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p1", "p2"),
    [
        (5e-324, 1.0),
        (1.0, 5e-324),
        (1e-300, 1.0),
        (1.0, 1e-300),
        (1e-30, 1.0),
        (1e-150, 1.0),
        (1e-12, 1.0),
        (1.0, 1e-12),
        (math.nextafter(1e-12, 0.0), 1.0),
    ],
)
def test_feasible_demands_survive_an_extreme_priority_ratio(
    p1: float, p2: float
) -> None:
    a = SectionAllocator(ref_rooms(p1=p1, p2=p2), ref_sections(), kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.1, "R2": 0.6, "R3": 0.0})
    a._duty = got
    assert all(0.0 <= v <= 1.0 for v in got.values())
    assert delivered(a, "R1") == pytest.approx(0.1, abs=1e-9)
    assert delivered(a, "R2") == pytest.approx(0.6, abs=1e-9)


@pytest.mark.parametrize(
    ("priority", "evenness"),
    [
        (1.0, 1.0),
        (1.0, 1e-6),
        (1e-300, 1.0),
        (5e-324, 1.0),
        (1e-300, 1e-12),
    ],
)
def test_one_room_two_sections_with_evenness_gives_equal_duties(
    priority: float, evenness: float
) -> None:
    sections = {"HS1": {"R1": 0.5}, "HS2": {"R1": 0.5}}
    a = SectionAllocator(one_room(priority, evenness), sections, kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.6})
    assert got["HS1"] == pytest.approx(0.6, abs=1e-9)
    assert got["HS2"] == pytest.approx(0.6, abs=1e-9)


@pytest.mark.parametrize("evenness", [0.0, 1.0, math.nextafter(1.0, 0.0), 5e-324])
def test_evenness_of_a_single_section_room_is_inert(evenness: float) -> None:
    rooms = ref_rooms(e2=evenness)
    sections = {"HS1": {"R1": 0.7}, "HS2": {"R1": 0.3, "R2": 0.3}}
    a = SectionAllocator(rooms, {**sections, "HS4": {"R3": 1.0}}, kp=1.0, ki=0.0)
    base = SectionAllocator(
        ref_rooms(), {**sections, "HS4": {"R3": 1.0}}, kp=1.0, ki=0.0
    )
    got = a._allocate({"R1": 0.2, "R2": 0.3, "R3": 0.0})
    want = base._allocate({"R1": 0.2, "R2": 0.3, "R3": 0.0})
    assert got == pytest.approx(want, abs=1e-12)


# ---------------------------------------------------------------------------
# T6 / A5: priority
# ---------------------------------------------------------------------------


def shared_only(p1: float, p2: float) -> SectionAllocator:
    rooms = {
        "R1": {"priority": p1, "evenness": 0.0},
        "R2": {"priority": p2, "evenness": 0.0},
    }
    return SectionAllocator(rooms, {"HS1": {"R1": 0.5, "R2": 0.5}}, kp=1.0, ki=0.0)


def test_raising_priority_never_increases_that_rooms_mismatch_shared_section() -> None:
    """A5, evenness-0 clause: absolute mismatch, shared section, evenness 0."""
    last = math.inf
    for p in (1e-8, 1e-7, 5e-7, 1e-6, 2e-6, 1e-5, 1e-3, 1.0):
        a = shared_only(p, 1e-6)
        a.update({"R1": SETPOINT - 0.2, "R2": SETPOINT - 0.6})
        current = mismatch(a, "R1", 0.2)
        assert current <= last + 1e-9
        last = current


def test_the_higher_priority_room_ends_with_the_smaller_mismatch() -> None:
    a = shared_only(1.0, 0.5)
    a.update({"R1": SETPOINT - 0.2, "R2": SETPOINT - 0.6})
    assert a.duty is not None
    # A lone shared section has coefficient 1 for both rooms:
    # u = (1.0 * 0.2 + 0.5 * 0.6) / 1.5 = 1 / 3.
    assert a.duty["HS1"] == pytest.approx(1 / 3, abs=1e-9)
    assert mismatch(a, "R1", 0.2) == pytest.approx(1 / 3 - 0.2, abs=1e-9)
    assert mismatch(a, "R2", 0.6) == pytest.approx(0.6 - 1 / 3, abs=1e-9)
    assert mismatch(a, "R1", 0.2) < mismatch(a, "R2", 0.6)
    b = shared_only(0.5, 1.0)
    b.update({"R1": SETPOINT - 0.2, "R2": SETPOINT - 0.6})
    assert mismatch(b, "R2", 0.6) < mismatch(b, "R1", 0.2)


@pytest.mark.parametrize("demands", [(0.1, 0.6), (0.9, 0.9), (0.9, 0.2), (0.0, 1.0)])
def test_raising_priority_is_monotone_on_the_reference_layout_with_evenness_zero(
    demands: tuple[float, float],
) -> None:
    """A5, evenness-0 clause: absolute mismatch on the reference layout."""
    last = math.inf
    for p in (0.05, 0.1, 0.2, 0.4, 0.7, 1.0):
        a = ref(p1=p)
        a.update(meas(demands[0], demands[1], 0.0))
        current = mismatch(a, "R1", demands[0])
        assert current <= last + 1e-12
        last = current


def counterexample_allocator(p1: float, e1: float) -> SectionAllocator:
    rooms = {
        "R1": {"priority": p1, "evenness": e1},
        "R2": {"priority": 1.0, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": 0.0},
    }
    # HS3 and HS4 are held shut, so R2 and R3 get half of their floor heating
    # from HS1 and HS2 and nothing from the held sections: coefficients 0.5,
    # as in 1.0.0's counterexample.
    sections = {
        "HS1": {"R1": 0.5, "R2": 0.5},
        "HS2": {"R1": 0.5, "R3": 0.5},
        "HS3": {"R2": 0.5},
        "HS4": {"R3": 0.5},
    }
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    a.hold("HS3", 0.0)
    a.hold("HS4", 0.0)
    a.update({"R1": 20.5, "R2": 20.0, "R3": 21.0})  # demands 0.5 / 1.0 / 0.0
    return a


def room_cost(
    a: SectionAllocator, u: Mapping[str, float], room: str, demand: float
) -> float:
    """A5's per-room quantity: |d - h|^2 + evenness * spread (no priority factor)."""
    serving = [s for s, covered in a.sections.items() if room in covered]
    heat = sum(coef(a, s, room) * u[s] for s in serving)
    mean = sum(u[s] for s in serving) / len(serving)
    squares = sum((u[s] - mean) ** 2 for s in serving)
    return (demand - heat) ** 2 + a.rooms[room].evenness * squares


def combined_cost(a: SectionAllocator, room: str, demand: float) -> float:
    """``room_cost`` of the duties the allocator last issued."""
    assert a.duty is not None
    return room_cost(a, a.duty, room, demand)


def test_priority_with_evenness_raises_the_mismatch_but_not_the_combined_cost() -> None:
    """The halt's counterexample, as evidence for A5's two-part first clause.

    With evenness 1 on R1 and HS1 pinned at 1, a larger priority pulls HS2 harder
    towards HS1, over-serving R1, so its absolute mismatch grows: the evenness-0
    clause cannot be stated for a room with evenness above 0. What still holds
    (A5's general clause) is that R1's combined cost |d - h|^2 + e * spread
    does not rise. The duties are the closed-form minimiser of A10's J.
    """
    mismatches = []
    costs = []
    for p, hs2, miss in (
        (0.1, 0.1538461538, 0.0769230769),
        (0.4, 0.3636363636, 0.1818181818),
    ):
        a = counterexample_allocator(p, 1.0)
        assert a.duty is not None
        assert a.duty["HS1"] == pytest.approx(1.0, abs=1e-9)
        assert a.duty["HS2"] == pytest.approx(hs2, abs=1e-8)
        assert mismatch(a, "R1", 0.5) == pytest.approx(miss, abs=1e-8)
        assert a.duty["HS2"] == pytest.approx(p / (1.5 * p + 0.5), abs=1e-9)
        mismatches.append(mismatch(a, "R1", 0.5))
        costs.append(combined_cost(a, "R1", 0.5))
    assert mismatches[1] > mismatches[0] + 0.1  # the absolute mismatch rises ...
    assert costs[1] < costs[0]  # ... while the combined cost falls
    # Control: with evenness 0 R1's demand is met at both priorities.
    for p in (0.1, 0.4):
        assert mismatch(counterexample_allocator(p, 0.0), "R1", 0.5) < 1e-9


def test_raising_priority_never_increases_the_combined_cost_on_the_counterexample() -> (
    None
):
    """A5, general clause, on the halt's layout: a sweep over R1's priority."""
    sweep = [0.001, 0.01, 0.05, 0.1, 0.2, 0.4, 0.7, 1.0]
    for evenness in (0.0, 0.1, 0.5, 1.0):
        last = math.inf
        for p in sweep:
            current = combined_cost(counterexample_allocator(p, evenness), "R1", 0.5)
            assert current <= last * (1 + 1e-9) + 1e-12
            last = current


@pytest.mark.parametrize("seed", range(40))
def test_raising_a_priority_never_increases_that_rooms_combined_cost_on_random_layouts(
    seed: int,
) -> None:
    """A5, general clause, over random layouts with evenness above 0."""
    rng = np.random.default_rng(5000 + seed)
    a, demand = random_layout(rng, subnormal=False)
    room = str(rng.choice(sorted(a.rooms)))
    rooms = {
        name: {"priority": r.priority, "evenness": max(r.evenness, 0.05)}
        for name, r in a.rooms.items()
    }
    last = math.inf
    for p in sorted(float(x) for x in rng.uniform(0.01, 1.0, size=5)):
        rooms[room]["priority"] = p
        b = SectionAllocator(rooms, a.sections, kp=1.0, ki=0.0)
        current = room_cost(b, b._allocate(demand), room, demand[room])
        assert current <= last * (1 + 1e-9) + 1e-12
        last = current


@pytest.mark.parametrize("demands", [(0.3, 0.9), (0.0, 1.0), (1.0, 0.0), (0.5, 0.5)])
def test_priority_sweep_is_monotone_for_both_rooms_on_a_shared_section(
    demands: tuple[float, float],
) -> None:
    """A5, evenness-0 clause: both demands, priority sweep across the competitor's."""
    last = math.inf
    for p in (0.001, 0.01, 0.02, 0.06, 1.0):  # ratios 0.05 ... 50 to p2 = 0.02
        a = shared_only(p, 0.02)
        a.update({"R1": SETPOINT - demands[0], "R2": SETPOINT - demands[1]})
        current = mismatch(a, "R1", demands[0])
        assert current <= last + 1e-9
        last = current


def test_documented_exception_saturated_section_ties_the_mismatches() -> None:
    # Demands 0 and 1 on one shared section: the duty saturates at 1, so the
    # lower-priority room's mismatch equals the higher-priority room's.
    # It needs coefficients below 1 (a lone shared section has 1 for both
    # rooms and never saturates with a mismatch), so each room's other half is
    # covered by a section held shut.
    rooms = {
        "R1": {"priority": 0.5, "evenness": 0.0},
        "R2": {"priority": 1.0, "evenness": 0.0},
    }
    sections = {
        "HS1": {"R1": 0.5, "R2": 0.5},
        "HS2": {"R1": 0.5},
        "HS3": {"R2": 0.5},
    }
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    a.hold("HS2", 0.0)
    a.hold("HS3", 0.0)
    a.update({"R1": SETPOINT - 0.0, "R2": SETPOINT - 1.0})
    assert a.duty == {"HS1": 1.0, "HS2": 0.0, "HS3": 0.0}
    assert mismatch(a, "R1", 0.0) == pytest.approx(mismatch(a, "R2", 1.0), abs=1e-9)


def test_documented_exception_unequal_coverage_favours_the_larger_coverage() -> None:
    """HS1 gives R1 20 % and R2 80 % of their floor.

    The rest of each room is covered by a section held shut, so the
    coefficients are 0.2 and 0.8 as in 1.0.0's unequal-share conflict.
    """
    rooms = {
        "R1": {"priority": 1.0, "evenness": 0.0},
        "R2": {"priority": 0.5, "evenness": 0.0},
    }
    sections = {"HS1": {"R1": 0.2, "R2": 0.8}, "HS2": {"R1": 0.8}, "HS3": {"R2": 0.2}}
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    a.hold("HS2", 0.0)
    a.hold("HS3", 0.0)
    a.update({"R1": SETPOINT - 1.0, "R2": SETPOINT - 0.0})
    assert mismatch(a, "R1", 1.0) == pytest.approx(0.8889, abs=1e-4)
    assert mismatch(a, "R2", 0.0) == pytest.approx(0.4444, abs=1e-4)
    assert mismatch(a, "R1", 1.0) > mismatch(a, "R2", 0.0)


def test_signed_mismatch_can_grow_when_priority_rises() -> None:
    """Pins why A5 speaks of the absolute mismatch: the signed one can grow."""
    signed = []
    for p in (0.5, 1.0):
        a = shared_only(p, 0.5)
        a.update({"R1": SETPOINT - 0.2, "R2": SETPOINT - 0.6})
        signed.append(0.2 - delivered(a, "R1"))
    assert signed[0] == pytest.approx(-0.2, abs=1e-9)
    assert signed[1] == pytest.approx(0.2 - 1 / 3, abs=1e-9)
    assert signed[1] > signed[0]
    assert abs(signed[1]) < abs(signed[0])


# ---------------------------------------------------------------------------
# T7 / A6: evenness
# ---------------------------------------------------------------------------


def three_section_layout() -> tuple[
    dict[str, dict[str, float]], dict[str, dict[str, float]]
]:
    sections = {
        "HS1": {"R1": 0.4},
        "HS2": {"R1": 0.3, "R2": 0.5},
        "HS3": {"R1": 0.3},
        "HS4": {"R2": 0.5},
    }
    return {k: ref_rooms()[k] for k in ("R1", "R2")}, sections


@pytest.mark.parametrize("demands", [(0.1, 0.6), (0.9, 0.2), (0.3, 0.3), (0.0, 1.0)])
@pytest.mark.parametrize("other", [0.0, 0.5])
@pytest.mark.parametrize("competitor", [1.0, 1e-3, 1e-6])  # R2's priority
def test_raising_evenness_never_widens_the_rooms_spread_reference_layout(
    demands: tuple[float, float], other: float, competitor: float
) -> None:
    last = math.inf
    for e in (0.0, 1e-6, 0.01, 0.1, 0.5, 1.0):
        a = ref(e1=e, e2=other, p2=competitor)
        a.update(meas(demands[0], demands[1], 0.0))
        current = spread(a, "R1")
        assert current <= last + 1e-8
        last = current


@pytest.mark.parametrize("demands", [(0.1, 0.6), (0.9, 0.2)])
@pytest.mark.parametrize("other", [0.0, 0.5])
def test_raising_evenness_never_widens_the_spread_of_a_three_section_room(
    demands: tuple[float, float], other: float
) -> None:
    rooms, sections = three_section_layout()
    rooms["R2"]["evenness"] = other
    last = math.inf
    for e in (0.0, 1e-6, 0.01, 0.1, 0.5, 1.0):
        rooms["R1"]["evenness"] = e
        a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
        a.update({"R1": SETPOINT - demands[0], "R2": SETPOINT - demands[1]})
        current = spread(a, "R1")
        assert current <= last + 1e-8
        last = current


def test_three_section_spread_is_the_sum_of_squares_about_the_mean() -> None:
    # One room, three sections, demand 0.3: with strong evenness all duties
    # equalise at demand / (sum of normalised coefficients) = 0.3 / 1 = 0.3,
    # spread 0.
    third = 1 / 3
    sections = {"A": {"R1": third}, "B": {"R1": third}, "C": {"R1": third}}
    strong = SectionAllocator(one_room(1.0, 1.0), sections, kp=1.0, ki=0.0)
    got = strong._allocate({"R1": 0.3})
    assert list(got.values()) == pytest.approx([0.3, 0.3, 0.3], abs=1e-9)
    # Hand-computed: evenness 0, two dedicated sections: spread of (1.0, 0.0).
    # Pin the metric: sum((u - mean)^2) = 0.5 for u = (1, 0).
    values = [1.0, 0.0]
    mean = sum(values) / 2
    assert sum((v - mean) ** 2 for v in values) == 0.5


def test_hungry_r2_with_r1_evenness_meets_both_demands_evenly() -> None:
    plain = ref()
    plain.update(meas(0.1, 0.6, 0.0))
    even = ref(e1=0.1)
    even.update(meas(0.1, 0.6, 0.0))
    assert even.duty is not None
    assert even.duty["HS1"] > 0.0
    # Exact fits are u1 = (0.1 - 0.3 t) / 0.7, u2 = t, u3 = (0.6 - 0.3 t) / 0.7;
    # R1's spread (u1 - u2)^2 / 2 vanishes at t = 0.1.
    assert even.duty["HS1"] == pytest.approx(0.1, abs=1e-9)
    assert even.duty["HS2"] == pytest.approx(0.1, abs=1e-9)
    assert even.duty["HS3"] == pytest.approx(0.57 / 0.7, abs=1e-9)
    assert delivered(even, "R1") == pytest.approx(0.1, abs=1e-9)
    assert delivered(even, "R2") == pytest.approx(0.6, abs=1e-9)
    assert plain.duty is not None
    gap_plain = abs(plain.duty["HS1"] - plain.duty["HS2"])
    gap_even = abs(even.duty["HS1"] - even.duty["HS2"])
    assert gap_even < gap_plain
    assert spread(even, "R1") < spread(plain, "R1")


def test_evenness_on_a_shared_section_is_a_real_trade_off() -> None:
    """A positive weight may pull the fit off exact demand (worked example C)."""
    a = ref(e1=1.0, e2=1.0)
    a.update(meas(0.1, 0.6, 0.0))
    assert a.duty is not None
    # Hand-solved stationarity (u2 = 0.35, r1 = -r2 = -25 / 198):
    assert a.duty["HS1"] == pytest.approx(34.3 / 198, abs=1e-6)
    assert a.duty["HS2"] == pytest.approx(0.35, abs=1e-6)
    assert a.duty["HS3"] == pytest.approx(104.3 / 198, abs=1e-6)
    assert delivered(a, "R2") == pytest.approx(0.35 + 0.98 * 25 / 198, abs=1e-6)
    assert delivered(a, "R2") < 0.6 - 0.05
    assert delivered(a, "R1") == pytest.approx(0.35 - 0.98 * 25 / 198, abs=1e-6)
    assert delivered(a, "R1") > 0.1 + 0.05


# ---------------------------------------------------------------------------
# T8 / A7: a dedicated section reproduces a standalone floor-heating PI
# ---------------------------------------------------------------------------


def varied_temperatures(n: int) -> list[float]:
    values: list[float] = []
    for i in range(n):
        if 20 <= i < 40:
            values.append(15.0)  # saturated high demand
        elif 60 <= i < 80:
            values.append(27.0)  # saturated low demand
        else:
            values.append(21.0 + 3.0 * math.sin(i / 7.0) + 0.4 * ((i * 37) % 5 - 2))
    return values


@pytest.mark.parametrize("window", [1, 6, 24])
@pytest.mark.parametrize(("e3", "e1"), [(0.0, 0.0), (1.0, 0.3)])
def test_dedicated_room_produces_the_command_sequence_of_a_standalone_controller(
    window: int, e3: float, e1: float
) -> None:
    a = SectionAllocator(
        ref_rooms(e1, 0.0, e3, p3=1e-6),
        ref_sections(),
        kp=0.4,
        ki=0.02,
        setpoint=21.0,
        history_length=window,
    )
    twin = PIController(0.4, 0.02, "floor_heating", 21.0, history_length=window)
    temperatures = varied_temperatures(200)
    for step, t3 in enumerate(temperatures):
        if step == 100:
            a.rooms["R3"].setpoint = 19.5
            a.rooms["R3"].ki = 0.05
            twin.setpoint = 19.5
            twin.ki = 0.05
        measured = {
            "R1": temperatures[(step * 3) % 200],
            "R2": temperatures[(step * 5 + 1) % 200],
            "R3": t3,
        }
        commands = a.update(measured)
        assert commands["HS4"] == twin.update(t3)
        assert a.rooms["R3"].integral == twin.integral
        assert a.rooms["R3"].demand == twin.pi_output
    assert a.history["HS4"] == twin.history


def test_dedicated_room_matches_the_standalone_controller_through_the_finite_guard() -> (
    None
):
    a = SectionAllocator(
        ref_rooms(), ref_sections(), kp=1e308, ki=0.02, history_length=6
    )
    twin = PIController(1e308, 0.02, "floor_heating", 21.0, history_length=6)
    for t3 in (21.0, -1e308, -1e308, 21.0, 1e308, 20.0):
        commands = a.update({"R1": 21.0, "R2": 21.0, "R3": t3})
        assert commands["HS4"] == twin.update(t3)
        assert a.rooms["R3"].integral == twin.integral
        assert a.rooms["R3"].demand == twin.pi_output


def test_a_single_room_single_section_allocator_matches_the_standalone_controller() -> (
    None
):
    a = SectionAllocator(
        one_room(), {"HS1": {"R1": 1.0}}, kp=0.4, ki=0.02, history_length=8
    )
    twin = PIController(0.4, 0.02, "floor_heating", 21.0, history_length=8)
    for t in varied_temperatures(120):
        assert a.update({"R1": t})["HS1"] == twin.update(t)
    assert a.history["HS1"] == twin.history


@pytest.mark.parametrize("coverage", [0.5, 0.3, 1.0, 1e-9, 1.0 - 1e-12])
def test_equivalence_holds_for_a_dedicated_coverage_of_any_size(
    coverage: float,
) -> None:
    """A lone section's coverage is normalised away: 0.5 runs as 1.0 does."""
    half = SectionAllocator(
        one_room(), {"HS1": {"R1": coverage}}, kp=1.0, ki=0.0, history_length=24
    )
    full = SectionAllocator(
        one_room(), {"HS1": {"R1": 1.0}}, kp=1.0, ki=0.0, history_length=24
    )
    for _ in range(48):
        assert half.update({"R1": SETPOINT - 0.3}) == full.update(
            {"R1": SETPOINT - 0.3}
        )
    assert half.history["HS1"] == full.history["HS1"]


def test_other_rooms_do_not_leak_into_a_dedicated_rooms_duty() -> None:
    a = ref(e1=1.0, e2=1.0)
    a.update(meas(0.9, 0.9, 0.35))
    assert a.duty is not None
    assert a.duty["HS4"] == pytest.approx(0.35, abs=1e-12)


# ---------------------------------------------------------------------------
# T9 / A8: dependency and exports
# ---------------------------------------------------------------------------


def test_scipy_and_numpy_are_declared_runtime_dependencies() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert any(d.startswith("scipy") for d in dependencies)
    assert any(d.startswith("numpy") for d in dependencies)


def test_package_and_models_import_together() -> None:
    import scipy  # noqa: F401 - the declared dependency imports

    assert hs.PIController is PIController
    assert hs.Modulator.__name__ == "Modulator"


def test_public_names_are_identical_across_every_import_path() -> None:
    assert hs.SectionAllocator is SectionAllocator
    assert allocator_package.SectionAllocator is SectionAllocator
    assert hs.Room is Room
    assert allocator_package.Room is Room
    assert "SectionAllocator" in hs.__all__
    assert "Room" in hs.__all__
    assert set(allocator_package.__all__) == {"SectionAllocator", "Room"}


# ---------------------------------------------------------------------------
# T10 / A9: to_dict and from_dict
# ---------------------------------------------------------------------------


def run_some(a: SectionAllocator, steps: int) -> None:
    temperatures = varied_temperatures(steps + 3)
    for i in range(steps):
        a.update(
            {
                "R1": temperatures[i],
                "R2": temperatures[i + 1],
                "R3": temperatures[i + 2],
            }
        )


def test_to_dict_has_exactly_the_documented_shape_and_order() -> None:
    a = ref(e1=0.1, history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    d = a.to_dict()
    assert list(d) == ["history_length", "rooms", "sections"]
    assert d["history_length"] == 4
    rooms = d["rooms"]
    assert isinstance(rooms, dict)
    assert list(rooms) == ["R1", "R2", "R3"]
    assert list(rooms["R1"]) == [
        "priority",
        "evenness",
        "setpoint",
        "kp",
        "ki",
        "integral",
    ]
    sections = d["sections"]
    assert isinstance(sections, dict)
    assert list(sections) == ["HS1", "HS2", "HS3", "HS4"]
    assert list(sections["HS2"]) == ["coverage", "history", "hold"]
    assert sections["HS2"]["coverage"] == {"R1": 0.3, "R2": 0.3}
    assert sections["HS2"]["hold"] is None
    assert isinstance(sections["HS2"]["history"], list)
    assert rooms["R1"]["evenness"] == 0.1
    assert json.loads(json.dumps(d)) == d


@pytest.mark.parametrize("through_json", [False, True])
@pytest.mark.parametrize("steps", [0, 3, 30])
def test_a_restored_allocator_produces_the_same_next_fifty_commands(
    through_json: bool, steps: int
) -> None:
    source = SectionAllocator(
        ref_rooms(0.1, 0.0, 0.0),
        ref_sections(),
        kp=0.4,
        ki=0.02,
        history_length=6,
    )
    run_some(source, steps)
    snapshot_ = source.to_dict()
    if through_json:
        snapshot_ = json.loads(json.dumps(snapshot_))
    restored = SectionAllocator.from_dict(snapshot_)
    assert restored.to_dict() == source.to_dict()
    assert json.dumps(restored.to_dict()) == json.dumps(source.to_dict())
    assert restored.duty is None
    assert all(room.demand is None for room in restored.rooms.values())
    temperatures = varied_temperatures(60)
    for i in range(50):
        measured = {
            "R1": temperatures[i],
            "R2": temperatures[i + 2],
            "R3": temperatures[i + 5],
        }
        assert restored.update(measured) == source.update(measured)
    assert restored.to_dict() == source.to_dict()


def test_from_dict_keeps_runtime_changes_to_settings() -> None:
    a = ref(history_length=4)
    a.rooms["R2"].kp = 0.77
    a.rooms["R2"].setpoint = 19.0
    a.rooms["R3"].ki = 0.5
    restored = SectionAllocator.from_dict(a.to_dict())
    assert restored.rooms["R2"].kp == 0.77
    assert restored.rooms["R2"].setpoint == 19.0
    assert restored.rooms["R3"].ki == 0.5


def test_from_dict_accepts_mapping_types_and_does_not_mutate_the_input() -> None:
    a = ref(history_length=4)
    run_some(a, 5)
    data = a.to_dict()
    frozen = copy.deepcopy(data)

    def proxied(value: object) -> object:
        if isinstance(value, dict):
            return MappingProxyType({k: proxied(v) for k, v in value.items()})
        if isinstance(value, list):
            return tuple(value)
        return value

    restored = SectionAllocator.from_dict(proxied(data))  # type: ignore[arg-type]  # a Mapping of mappings
    assert restored.to_dict() == frozen
    ordered = OrderedDict(data)
    SectionAllocator.from_dict(ordered)
    assert data == frozen


def test_from_dict_returns_the_subclass_it_was_called_on() -> None:
    class Sub(SectionAllocator):
        pass

    restored = Sub.from_dict(Sub(ref_rooms(), ref_sections()).to_dict())
    assert type(restored) is Sub


def test_from_dict_accepts_fractional_history_entries_as_the_modulator_does() -> None:
    data = ref(history_length=4).to_dict()
    data["sections"]["HS1"]["history"] = [0.5, 0.25]  # type: ignore[index]  # object-typed snapshot
    restored = SectionAllocator.from_dict(data)
    assert restored.history["HS1"] == (0.5, 0.25)


def _mutated(path: list[object], value: object) -> dict[str, object]:
    data = ref(history_length=4).to_dict()
    target: object = data
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]  # object-typed snapshot
    target[path[-1]] = value  # type: ignore[index]  # object-typed snapshot
    return data


def _without(path: list[object]) -> dict[str, object]:
    data = ref(history_length=4).to_dict()
    target: object = data
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]  # object-typed snapshot
    del target[path[-1]]  # type: ignore[attr-defined]  # object-typed snapshot
    return data


@pytest.mark.parametrize(
    ("data", "error", "match"),
    [
        (_without(["rooms"]), ValueError, "rooms"),
        (_without(["history_length"]), ValueError, "history_length"),
        (_without(["sections"]), ValueError, "sections"),
        (
            {**_without(["rooms"]), "extra": 1},
            ValueError,
            "missing keys.*rooms",
        ),
        (
            {**ref(history_length=4).to_dict(), "extra": 1},
            ValueError,
            "unknown keys.*extra",
        ),
        (_mutated(["history_length"], True), TypeError, "history_length"),
        (_mutated(["history_length"], 0), ValueError, "history_length"),
        (
            _mutated(["history_length"], sys.maxsize + 1),
            OverflowError,
            "history_length",
        ),
        (_mutated(["rooms"], []), TypeError, "rooms must be a mapping"),
        (_mutated(["rooms"], {}), ValueError, "rooms must not be empty"),
        (_mutated(["sections"], []), TypeError, "sections must be a mapping"),
        (
            _mutated(["rooms", "R1"], []),
            TypeError,
            re.escape("rooms['R1'] must be a mapping"),
        ),
        (
            _without(["rooms", "R1", "integral"]),
            ValueError,
            re.escape("rooms['R1']: ") + ".*integral",
        ),
        (
            _mutated(["rooms", "R1", "extra"], 1),
            ValueError,
            re.escape("rooms['R1']: ") + ".*extra",
        ),
        (
            _mutated(["rooms", "R1", "integral"], float("nan")),
            ValueError,
            re.escape("rooms['R1'].integral"),
        ),
        (
            _mutated(["rooms", "R1", "kp"], "x"),
            TypeError,
            re.escape("rooms['R1'].kp"),
        ),
        (
            _mutated(["rooms", "R2", "setpoint"], 10**400),
            OverflowError,
            re.escape("rooms['R2'].setpoint"),
        ),
        (
            _mutated(["rooms", "R1", "ki"], None),
            TypeError,
            re.escape("rooms['R1'].ki"),
        ),
        (
            _mutated(["rooms", "R1", "priority"], 0),
            ValueError,
            re.escape("rooms['R1'].priority"),
        ),
        (
            _mutated(["rooms", "R1", "evenness"], -1.0),
            ValueError,
            re.escape("rooms['R1'].evenness"),
        ),
        (_mutated(["rooms", "R1"], {}), ValueError, re.escape("rooms['R1']: ")),
        (
            _mutated(["sections", "HS1"], []),
            TypeError,
            re.escape("sections['HS1'] must be a mapping"),
        ),
        (
            _without(["sections", "HS1", "history"]),
            ValueError,
            re.escape("sections['HS1']: ") + ".*history",
        ),
        (
            _mutated(["sections", "HS1", "coverage"], []),
            TypeError,
            re.escape("sections['HS1']['coverage'] must be a mapping"),
        ),
        (
            _mutated(["sections", "HS1", "coverage"], {"R1": 0}),
            ValueError,
            re.escape("sections['HS1']['R1']"),
        ),
        (
            _mutated(["sections", "HS1", "coverage"], {"RX": 1.0}),
            ValueError,
            "'RX'",
        ),
        (
            _mutated(["sections", "HS1", "hold"], float("nan")),
            ValueError,
            re.escape("sections['HS1'].hold"),
        ),
        (
            _mutated(["sections", "HS1", "hold"], 1.5),
            ValueError,
            re.escape("sections['HS1'].hold"),
        ),
        (
            _mutated(["sections", "HS1", "hold"], True),
            TypeError,
            re.escape("sections['HS1'].hold"),
        ),
        (
            _mutated(["sections", "HS1", "hold"], "x"),
            TypeError,
            re.escape("sections['HS1'].hold"),
        ),
        (
            _mutated(["sections", "HS1", "hold"], 10**400),
            OverflowError,
            re.escape("sections['HS1'].hold"),
        ),
        (
            _without(["sections", "HS1", "hold"]),
            ValueError,
            re.escape("sections['HS1']: ") + ".*hold",
        ),
        (
            _mutated(["sections", "HS2", "history"], [0.0] * 5),
            ValueError,
            re.escape("sections['HS2'].history"),
        ),
        (
            _mutated(["sections", "HS2", "history"], [2.0]),
            ValueError,
            re.escape("sections['HS2'].history[0]"),
        ),
        (
            _mutated(["sections", "HS2", "history"], ["x"]),
            TypeError,
            re.escape("sections['HS2'].history[0]"),
        ),
        (
            _mutated(["sections", "HS2", "history"], "01"),
            TypeError,
            re.escape("sections['HS2'].history"),
        ),
        (_mutated(["sections", "HS1", "history"], 1.0), TypeError, "history"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 25 else None,
)
def test_from_dict_refuses_a_malformed_snapshot_naming_the_path(
    data: object, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        SectionAllocator.from_dict(data)  # type: ignore[arg-type]  # deliberate misuse


@pytest.mark.parametrize("data", ['{"history_length": 4}', [], None, 3, {1, 2}])
def test_from_dict_refuses_a_non_mapping_naming_its_type(data: object) -> None:
    with pytest.raises(TypeError, match=type(data).__name__):
        SectionAllocator.from_dict(data)  # type: ignore[arg-type]  # deliberate misuse


def test_from_dict_reports_a_missing_key_before_an_unknown_one() -> None:
    data = {**_without(["rooms"]), "extra": 1}
    with pytest.raises(ValueError, match="missing") as caught:
        SectionAllocator.from_dict(data)
    assert "unknown" not in str(caught.value)


def test_from_dict_gives_the_same_errors_as_the_constructor_for_the_layout() -> None:
    data = _mutated(["sections", "HS2", "coverage"], {"R1": 0.9, "R2": 0.9})
    with pytest.raises(ValueError, match=re.escape("rooms['R1'] coverages sum to")):
        SectionAllocator.from_dict(data)


def test_from_dict_refuses_a_1_0_0_snapshot_naming_the_missing_keys() -> None:
    data = ref(history_length=4).to_dict()
    entry = data["sections"]["HS1"]  # type: ignore[index]  # object-typed snapshot
    old = {"shares": entry["coverage"], "history": entry["history"]}
    data["sections"]["HS1"] = old  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(ValueError, match=re.escape("sections['HS1']: ")) as caught:
        SectionAllocator.from_dict(data)
    assert "['coverage', 'hold']" in str(caught.value)


def test_from_dict_rejects_a_room_no_section_covers() -> None:
    data = ref(history_length=4).to_dict()
    del data["sections"]["HS4"]  # type: ignore[attr-defined]  # object-typed snapshot
    with pytest.raises(ValueError, match=re.escape("['R3']")):
        SectionAllocator.from_dict(data)


def test_from_dict_with_mixed_type_unknown_keys_raises_value_error_not_type_error() -> (
    None
):
    data: dict[object, object] = {k: v for k, v in ref().to_dict().items()}
    data.update({"extra": 1, 2: 2})
    with pytest.raises(ValueError, match="unknown keys"):
        SectionAllocator.from_dict(data)  # type: ignore[arg-type]  # deliberate misuse


def test_a_failed_from_dict_produces_nothing_and_leaves_the_source_alone() -> None:
    source = ref(history_length=4)
    run_some(source, 4)
    before = source.to_dict()
    bad = source.to_dict()
    bad["rooms"]["R3"]["integral"] = float("nan")  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(ValueError, match="integral"):
        SectionAllocator.from_dict(bad)
    assert source.to_dict() == before


# ---------------------------------------------------------------------------
# A1 ranges: priority in (0, 1], evenness in [0, 1]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        0,
        0.0,
        -0.0,
        -1,
        math.nextafter(1.0, 2.0),
        1.0000001,
        1.5,
        2,
        Fraction(3, 2),
        1e300,
    ],
    ids=repr,
)
def test_construction_refuses_a_priority_outside_zero_exclusive_to_one(
    value: object,
) -> None:
    rooms = ref_rooms()
    rooms["R1"]["priority"] = value  # type: ignore[assignment]  # deliberate misuse
    expected = f"rooms['R1'].priority must be in (0, 1], got {value!r}."
    with pytest.raises(ValueError, match=re.escape(expected)):
        build(rooms=rooms)


@pytest.mark.parametrize(
    "value",
    [-5e-324, -1, math.nextafter(1.0, 2.0), 2, Fraction(3, 2), 1e6, 1e300],
    ids=repr,
)
def test_construction_refuses_an_evenness_outside_zero_to_one(value: object) -> None:
    rooms = ref_rooms()
    rooms["R2"]["evenness"] = value  # type: ignore[assignment]  # deliberate misuse
    expected = f"rooms['R2'].evenness must be in [0, 1], got {value!r}."
    with pytest.raises(ValueError, match=re.escape(expected)):
        build(rooms=rooms)


@pytest.mark.parametrize(
    "priority",
    [1.0, 1, Fraction(1, 1), Fraction(1, 3), 5e-324, math.nextafter(1.0, 0.0)],
    ids=repr,
)
@pytest.mark.parametrize(
    "evenness",
    [0.0, -0.0, 0, 1.0, 1, 5e-324, math.nextafter(1.0, 0.0)],
    ids=repr,
)
def test_construction_accepts_every_range_edge_and_stores_floats(
    priority: float, evenness: float
) -> None:
    a = build(
        rooms=one_room(priority, evenness),
        sections={"HS1": {"R1": 1}},
        kp=1.0,
        ki=0.0,
    )
    room = a.rooms["R1"]
    assert type(room.priority) is float
    assert type(room.evenness) is float
    assert room.priority == float(priority)
    assert room.evenness == float(evenness)
    assert math.copysign(1.0, room.evenness) == 1.0
    assert set(a.update({"R1": 20.9}).values()) <= {0.0, 1.0}


def test_room_built_directly_reports_the_range_without_a_path() -> None:
    kwargs = {"kp": 0.3, "ki": 0.0, "setpoint": 21.0}
    with pytest.raises(ValueError) as priority_error:
        Room("R1", priority=0.0, evenness=0.0, **kwargs)
    assert str(priority_error.value) == "priority must be in (0, 1], got 0.0."
    with pytest.raises(ValueError) as evenness_error:
        Room("R1", priority=1.0, evenness=1.5, **kwargs)
    assert str(evenness_error.value) == "evenness must be in [0, 1], got 1.5."


@pytest.mark.parametrize(
    ("key", "value"),
    [("priority", 2.0), ("priority", 0.0), ("evenness", math.nextafter(1.0, 2.0))]
    + [("evenness", -0.1), ("priority", 1.5), ("evenness", 2)],
)
def test_from_dict_refuses_an_out_of_range_weight_naming_the_path(
    key: str, value: float
) -> None:
    """A snapshot saved before the ranges were defined is refused cleanly."""
    source = ref(history_length=4)
    run_some(source, 3)
    before = source.to_dict()
    bad = source.to_dict()
    bad["rooms"]["R1"][key] = value  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(ValueError, match=re.escape(f"rooms['R1'].{key} must be in")):
        SectionAllocator.from_dict(bad)
    assert source.to_dict() == before


# ---------------------------------------------------------------------------
# A10 / A11 / A12 (T11): the cost, the ratio property, the worked examples
# ---------------------------------------------------------------------------


def cost(
    a: SectionAllocator, u: Mapping[str, float], demand: Mapping[str, float]
) -> float:
    """A10's J written independently from a layout's public surface."""
    total = 0.0
    for name, room in a.rooms.items():
        serving = [s for s, covered in a.sections.items() if name in covered]
        heat = sum(coef(a, s, name) * u[s] for s in serving)
        total += room.priority * (demand[name] - heat) ** 2
        if len(serving) > 1:
            mean = sum(u[s] for s in serving) / len(serving)
            total += (
                room.priority * room.evenness * sum((u[s] - mean) ** 2 for s in serving)
            )
    return total


def gradient(
    a: SectionAllocator, u: Mapping[str, float], demand: Mapping[str, float]
) -> dict[str, float]:
    """Analytic gradient of ``cost`` divided by the largest priority.

    Priorities are normalised first so subnormal ones do not lose their digits.
    """
    top = max(room.priority for room in a.rooms.values())
    grad = {s: 0.0 for s in a.sections}
    for name, room in a.rooms.items():
        serving = [s for s, covered in a.sections.items() if name in covered]
        heat = sum(coef(a, s, name) * u[s] for s in serving)
        mean = sum(u[s] for s in serving) / len(serving)
        for s in serving:
            grad[s] += (
                -2 * (room.priority / top) * (demand[name] - heat) * coef(a, s, name)
            )
            if len(serving) > 1:
                grad[s] += 2 * (room.priority / top) * room.evenness * (u[s] - mean)
    return grad


A12_CASES = {
    # name: (demands R1/R2, p1, p2, e1, e2, (HS1, HS2, HS3))
    # Recomputed for coverages 0.7 / 0.3 and 0.3 / 0.7 by an exhaustive
    # active-set solve of J, independent of the allocator (B and C also by
    # hand, see the hungry-R2 and trade-off tests).
    "B": ((0.1, 0.6), 1.0, 1.0, 0.1, 0.0, (0.1, 0.1, 0.81428571)),
    "C": ((0.1, 0.6), 1.0, 1.0, 1.0, 1.0, (0.17323232, 0.35, 0.52676768)),
    "D": ((0.1, 0.6), 1.0, 1.0, 1.0, 0.1, (0.12269171, 0.17746479, 0.72519562)),
    "E": ((0.1, 0.6), 0.3, 1.0, 1.0, 1.0, (0.21266511, 0.48461538, 0.56620047)),
    "G": ((0.3, 1.0), 1.0, 1.0, 0.1, 0.0, (0.19776876, 0.64503043, 1.0)),
}


@pytest.mark.parametrize("case", A12_CASES)
def test_worked_examples_hold_within_a_thousandth(case: str) -> None:
    demands, p1, p2, e1, e2, want = A12_CASES[case]
    a = ref(e1=e1, e2=e2, p1=p1, p2=p2)
    a.update(meas(demands[0], demands[1], 0.0))
    assert a.duty is not None
    got = (a.duty["HS1"], a.duty["HS2"], a.duty["HS3"])
    assert got == pytest.approx(want, abs=1e-3)
    assert a.duty["HS4"] == 0.0
    # The same duties through the private allocation path, with the exact demands.
    assert a._allocate(
        {"R1": demands[0], "R2": demands[1], "R3": 0.0}
    ) == pytest.approx(
        {"HS1": want[0], "HS2": want[1], "HS3": want[2], "HS4": 0.0}, abs=1e-3
    )


@pytest.mark.parametrize("case", ["A", "F"])
def test_worked_examples_with_many_exact_fits_deliver_each_demand(case: str) -> None:
    demands = {"A": (0.1, 0.6), "F": (0.3, 1.0)}[case]
    a = ref()
    a.update(meas(demands[0], demands[1], 0.0))
    assert a.duty is not None
    assert all(0.0 <= v <= 1.0 for v in a.duty.values())
    assert delivered(a, "R1") == pytest.approx(demands[0], abs=1e-9)
    assert delivered(a, "R2") == pytest.approx(demands[1], abs=1e-9)


def random_layout(
    rng: np.random.Generator, subnormal: bool = True
) -> tuple[SectionAllocator, dict[str, float]]:
    """A random valid layout and demands, covering every A10 corner."""
    while True:
        n_rooms = int(rng.integers(2, 6))
        n_sections = int(rng.integers(2, 7))
        names = [f"R{i}" for i in range(n_rooms)]
        rooms = {}
        for name in names:
            kind = int(rng.integers(0, 4 if subnormal else 3))
            priority = (
                1.0,
                float(rng.uniform(0.01, 1.0)),
                10 ** float(rng.uniform(-6, 0)),
                5e-324,
            )[kind]
            evenness = (0.0, 1.0, float(rng.random()), float(rng.random()))[
                int(rng.integers(0, 4))
            ]
            rooms[name] = {"priority": min(priority, 1.0), "evenness": evenness}
        sections: dict[str, dict[str, float]] = {}
        for j in range(n_sections):
            covered = rng.choice(
                names, size=int(rng.integers(1, min(3, n_rooms) + 1)), replace=False
            )
            weights = rng.dirichlet(np.ones(len(covered))) * float(
                rng.uniform(0.2, 1.0)
            )
            sections[f"S{j}"] = {
                str(r): float(max(w, 1e-3))
                for r, w in zip(covered, weights, strict=True)
            }
        if not all(any(r in entry for entry in sections.values()) for r in names):
            continue
        for r in names:  # a room's coverages sum to at most 1
            total = math.fsum(c[r] for c in sections.values() if r in c)
            if total > 1.0:
                for entry in sections.values():
                    if r in entry:
                        entry[r] /= total
        demand = {n: float(rng.choice([0.0, 1.0, rng.random()])) for n in names}
        return SectionAllocator(rooms, sections, kp=1.0, ki=0.0), demand


@pytest.mark.parametrize("seed", range(60))
def test_allocation_satisfies_the_kkt_conditions_of_the_documented_cost(
    seed: int,
) -> None:
    """J is a convex quadratic, so KKT is necessary and sufficient (A10)."""
    a, demand = random_layout(np.random.default_rng(seed))
    u = a._allocate(demand)
    grad = gradient(a, u, demand)
    for section, value in u.items():
        assert 0.0 <= value <= 1.0
        scaled = grad[section]  # already divided by the largest priority
        if 1e-9 < value < 1 - 1e-9:
            assert abs(scaled) <= 1e-6
        elif value <= 1e-9:
            assert scaled >= -1e-6
        else:
            assert scaled <= 1e-6


@pytest.mark.parametrize("seed", range(60))
def test_allocation_cost_is_not_beaten_by_an_independent_solve(seed: int) -> None:
    from scipy.optimize import minimize

    a, demand = random_layout(np.random.default_rng(1000 + seed))
    u = a._allocate(demand)
    names = list(u)
    top = max(room.priority for room in a.rooms.values())

    def objective(x: np.ndarray) -> float:
        return cost(a, dict(zip(names, x, strict=True)), demand)

    starts = [np.array([u[n] for n in names]), np.full(len(names), 0.5)]
    best = min(
        minimize(
            objective,
            x0,
            method="L-BFGS-B",
            bounds=[(0.0, 1.0)] * len(names),
            options={"ftol": 1e-15, "gtol": 1e-12},
        ).fun
        for x0 in starts
    )
    # J is normalised by the largest priority: with every priority tiny, an
    # absolute 1e-9 would pass for any duties (A10's tolerance would be vacuous).
    assert (objective(np.array([u[n] for n in names])) - best) / top <= 1e-9


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize("factor", [0.3, 0.7, 1e-3])
def test_random_layouts_are_unchanged_by_scaling_every_priority(
    seed: int, factor: float
) -> None:
    a, demand = random_layout(np.random.default_rng(2000 + seed), subnormal=False)
    rooms = {
        name: {"priority": room.priority * factor, "evenness": room.evenness}
        for name, room in a.rooms.items()
    }
    scaled = SectionAllocator(rooms, a.sections, kp=1.0, ki=0.0)
    assert scaled._allocate(demand) == pytest.approx(a._allocate(demand), abs=1e-9)


def test_the_higher_priority_room_ends_with_an_exact_mismatch_ratio() -> None:
    rooms = {
        "R1": {"priority": 0.8, "evenness": 0.0},
        "R2": {"priority": 0.2, "evenness": 0.0},
    }
    a = SectionAllocator(rooms, {"HS1": {"R1": 0.5, "R2": 0.5}}, kp=1.0, ki=0.0)
    a.update({"R1": SETPOINT - 0.2, "R2": SETPOINT - 0.6})
    assert a.duty is not None
    assert a.duty["HS1"] == pytest.approx(0.28, abs=1e-9)  # (0.8 * 0.2 + 0.2 * 0.6)
    assert mismatch(a, "R1", 0.2) == pytest.approx(0.08, abs=1e-9)
    assert mismatch(a, "R2", 0.6) == pytest.approx(0.32, abs=1e-9)
    assert mismatch(a, "R1", 0.2) / mismatch(a, "R2", 0.6) == pytest.approx(0.25)


def test_a_dedicated_share_one_room_matches_the_standalone_controller_in_a_conflict() -> (
    None
):
    """A7 at the low end of the priority range, with R1 and R2 in conflict."""
    rooms = {
        "R1": {"priority": 1.0, "evenness": 1.0},
        "R2": {"priority": 1.0, "evenness": 1.0},
        "R3": {"priority": 0.01, "evenness": 1.0},
    }
    a = SectionAllocator(rooms, ref_sections(), kp=0.4, ki=0.02)
    twin = PIController(0.4, 0.02, "floor_heating", 21.0, history_length=24)
    for step in range(60):
        t3 = 19.0 + 3.0 * ((step * 7) % 11) / 10
        if step == 30:
            a.rooms["R3"].setpoint = 20.0
            twin.setpoint = 20.0
        commands = a.update({"R1": 18.0, "R2": 17.0, "R3": t3})
        assert commands["HS4"] == twin.update(t3)
    assert a.history["HS4"] == twin.history


# ---------------------------------------------------------------------------
# Round 1 (feat/allocator-home-assistant), step 5: normalisation (A2), holds
# (A5-A9) and the hold snapshot (A10), on the plan's E layout. Every expected
# number is derived by hand from J (see the comments), never read back.
#
# E layout: HS1 {R1 0.5}, HS2 {R2 1.0}, HS3 {R3 0.3}, HS4 {R3 0.7, R4 0.4},
# HS5 {R4 0.6}; priorities 1, evenness 0, kp=1, ki=0.
# ---------------------------------------------------------------------------


def e_rooms(e3: float = 0.0, e4: float = 0.0) -> dict[str, dict[str, float]]:
    return {
        "R1": {"priority": 1.0, "evenness": 0.0},
        "R2": {"priority": 1.0, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": e3},
        "R4": {"priority": 1.0, "evenness": e4},
    }


def e_sections() -> dict[str, dict[str, float]]:
    return {
        "HS1": {"R1": 0.5},
        "HS2": {"R2": 1.0},
        "HS3": {"R3": 0.3},
        "HS4": {"R3": 0.7, "R4": 0.4},
        "HS5": {"R4": 0.6},
    }


def e_layout(
    e3: float = 0.0, e4: float = 0.0, history_length: int = 24
) -> SectionAllocator:
    return SectionAllocator(
        e_rooms(e3, e4),
        e_sections(),
        history_length=history_length,
        kp=1.0,
        ki=0.0,
    )


def e_meas(d3: float, d4: float = 0.0) -> dict[str, float]:
    """Temperatures giving R1 and R2 no demand, R3 demand d3 and R4 demand d4."""
    return {"R1": SETPOINT, "R2": SETPOINT, "R3": SETPOINT - d3, "R4": SETPOINT - d4}


def pair(coverage: float, evenness: float = 0.0) -> SectionAllocator:
    """One room R covered by two sections, each by ``coverage``."""
    return SectionAllocator(
        {"R": {"priority": 1.0, "evenness": evenness}},
        {"HS1": {"R": coverage}, "HS2": {"R": coverage}},
        kp=1.0,
        ki=0.0,
    )


def duty_of(a: SectionAllocator) -> dict[str, float]:
    duty = a.duty
    assert duty is not None
    return duty


def approx_duty(**levels: float) -> object:
    return pytest.approx(levels, abs=1e-9)


# --- A2: normalisation ------------------------------------------------------


def test_normalisation_e1_free_allocation_of_the_reference_layout() -> None:
    # R3 demand 0.5: HS3 full gives 0.3, HS4 supplies the remaining 0.2 at 0.7
    # normalised weight 0.7 -> 0.2 / 0.7 ... in total 14/65, hand-derived from
    # minimising (0.5 - 0.3a - 0.7b)^2 + (0 - 0.4b - 0.6c)^2 with a = 1.
    a = e_layout()
    a.update(e_meas(0.5))
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=1.0, HS4=14 / 65, HS5=0.0)
    assert duty_of(a)["HS4"] == pytest.approx(0.2154, abs=1e-3)


def test_normalisation_e3_closed_form_is_relative_to_the_covered_part() -> None:
    a = e_layout()
    a.update({**e_meas(0.0), "R1": 20.6})
    demand = a.rooms["R1"].demand
    assert demand == pytest.approx(0.4, abs=1e-12)
    assert duty_of(a)["HS1"] == demand  # min(1, demand), not demand / 0.5
    assert duty_of(a)["HS1"] != pytest.approx(0.8)


def test_closed_form_saturates_at_one_not_at_the_coverage() -> None:
    a = e_layout()
    a.update({**e_meas(0.0), "R1": 18.0})  # demand clamps to 1.0
    assert duty_of(a)["HS1"] == 1.0


@pytest.mark.parametrize("coverage", [0.2, 0.5, 0.9, 1.0])
def test_a_room_summing_below_one_equals_the_same_layout_scaled_to_one(
    coverage: float,
) -> None:
    # coverage c per section, room total 2c; scaled layout is 0.5 each.
    scaled = pair(0.5, evenness=1.0)
    a = pair(coverage / 2, evenness=1.0)
    scaled.update({"R": 20.7})
    a.update({"R": 20.7})
    assert duty_of(a) == approx_duty(**duty_of(scaled))
    # Demand 0.3 split evenly: both sections at 0.3 (hand: minimise
    # (0.3 - 0.5(u1+u2))^2 + (u1-u2)^2/2 -> u1 = u2 = 0.3).
    assert duty_of(a) == approx_duty(HS1=0.3, HS2=0.3)


def test_unequal_totals_with_evenness_are_normalised_not_raw() -> None:
    # A 0.2, B 0.3 (total 0.5) must match A 0.4, B 0.6 bit for bit
    # (0.2/0.5 == 0.4 and 0.3/0.5 == 0.6 in floats).
    def build_pair(a_cov: float, b_cov: float) -> SectionAllocator:
        return SectionAllocator(
            {"R": {"priority": 1.0, "evenness": 1.0}},
            {"A": {"R": a_cov}, "B": {"R": b_cov}},
            kp=1.0,
            ki=0.0,
        )

    low = build_pair(0.2, 0.3)
    high = build_pair(0.4, 0.6)
    low.update({"R": 20.7})
    high.update({"R": 20.7})
    assert duty_of(low) == duty_of(high)
    # Hand: normalised 0.4/0.6, demand 0.3, evenness 1:
    # minimise (0.3 - 0.4a - 0.6b)^2 + (a - b)^2/2 -> a = b = 0.3.
    assert duty_of(low) == approx_duty(A=0.3, B=0.3)


def test_construction_normalises_subnormal_coverages_without_blow_up() -> None:
    a = SectionAllocator(
        {"R": {"priority": 1.0, "evenness": 0.0}},
        {"HS1": {"R": 5e-324}, "HS2": {"R": 1e-323}},
        kp=1.0,
        ki=0.0,
    )
    assert a.sections == {"HS1": {"R": 5e-324}, "HS2": {"R": 1e-323}}
    a.update({"R": 20.0})  # demand 1.0; normalised 1/3 and 2/3
    duty = duty_of(a)
    assert math.isfinite(duty["HS1"]) and math.isfinite(duty["HS2"])
    assert (1 / 3) * duty["HS1"] + (2 / 3) * duty["HS2"] == pytest.approx(1.0)
    assert duty == approx_duty(HS1=1.0, HS2=1.0)


def test_sections_returns_the_coverages_as_given_not_normalised() -> None:
    sections = e_sections()
    sections["HS2"] = {"R2": 1}
    a = SectionAllocator(e_rooms(), sections, kp=1.0, ki=0.0)
    assert a.sections == {
        "HS1": {"R1": 0.5},
        "HS2": {"R2": 1.0},
        "HS3": {"R3": 0.3},
        "HS4": {"R3": 0.7, "R4": 0.4},
        "HS5": {"R4": 0.6},
    }
    assert type(a.sections["HS2"]["R2"]) is float
    assert a.to_dict()["sections"]["HS1"]["coverage"] == {"R1": 0.5}  # type: ignore[index]  # object-typed snapshot


def test_sections_pair_reports_the_given_coverage() -> None:
    a = pair(0.2)
    assert a.sections["HS1"] == {"R": 0.2}
    assert a.to_dict()["sections"]["HS1"]["coverage"] == {"R": 0.2}  # type: ignore[index]  # object-typed snapshot


def test_e4_evenness_spreads_the_demand_over_the_three_loops() -> None:
    # R3 = R4 = 0.5, evenness 1: HS3 = HS4 = HS5 = 0.5 (hand: u = 0.5 gives
    # h_R3 = 0.3*0.5 + 0.7*0.5 = 0.5 normalised and zero spread).
    a = e_layout(e3=1.0, e4=1.0)
    a.update(e_meas(0.5, 0.5))
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=0.5, HS4=0.5, HS5=0.5)


# --- A5: hold / holds -------------------------------------------------------


def test_holds_is_complete_fresh_and_in_constructor_order() -> None:
    a = e_layout()
    a.hold("HS5", 1.0)
    a.hold("HS1", 0.0)
    handed_out = a.holds
    handed_out["HS2"] = 0.7
    assert list(a.holds) == ["HS1", "HS2", "HS3", "HS4", "HS5"]
    assert a.holds == {"HS1": 0.0, "HS2": None, "HS3": None, "HS4": None, "HS5": 1.0}
    assert a.holds is not a.holds
    assert a.holds["HS2"] is None


def test_a_fresh_allocator_holds_nothing() -> None:
    assert e_layout().holds == dict.fromkeys(["HS1", "HS2", "HS3", "HS4", "HS5"])


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (0, 0.0),
        (1, 1.0),
        (0.5, 0.5),
        (Fraction(1, 4), 0.25),
        (np.float64(0.5), 0.5),
        (np.int64(1), 1.0),
        (-0.0, 0.0),
        (1.0, 1.0),
    ],
    ids=repr,
)
def test_hold_accepts_numeric_variants_and_stores_plain_floats(
    value: object, stored: float
) -> None:
    a = e_layout()
    a.hold("HS3", value)  # type: ignore[arg-type]  # numeric variants beyond float
    held = a.holds["HS3"]
    assert held == stored
    assert type(held) is float
    assert math.copysign(1.0, held) == 1.0


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        (True, TypeError),
        (False, TypeError),
        (np.bool_(True), TypeError),
        ("0.5", TypeError),
        ([0.5], TypeError),
        (1j, TypeError),
        (Decimal("0.5"), TypeError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (float("-inf"), ValueError),
        (1.5, ValueError),
        (-0.5, ValueError),
        (math.nextafter(1.0, 2.0), ValueError),
        (-5e-324, ValueError),
        (Fraction(3, 2), ValueError),
        (10**400, OverflowError),
        (-(10**400), OverflowError),
    ],
    ids=repr,
)
@pytest.mark.parametrize("previous", [None, 0.25])
def test_hold_refuses_a_bad_level_naming_the_section_and_changes_nothing(
    bad: object, error: type[Exception], previous: float | None
) -> None:
    a = e_layout(history_length=4)
    a.update(e_meas(0.5))
    a.hold("HS3", previous)
    before = (a.to_dict(), a.duty, a.holds, a.history)
    with pytest.raises(error, match=re.escape("holds['HS3']")):
        a.hold("HS3", bad)  # type: ignore[arg-type]  # deliberate misuse
    assert (a.to_dict(), a.duty, a.holds, a.history) == before
    assert a.holds["HS3"] == previous


def test_hold_range_error_repeats_the_callers_own_value() -> None:
    a = e_layout()
    with pytest.raises(ValueError, match=re.escape("Fraction(3, 2)")) as caught:
        a.hold("HS3", Fraction(3, 2))  # type: ignore[arg-type]  # numeric variant
    assert "holds['HS3']" in str(caught.value)


@pytest.mark.parametrize(
    ("section", "level", "error", "pattern"),
    [
        (1, "x", TypeError, "section must be a str"),
        (None, 0.5, TypeError, "section must be a str"),
        (["HS1"], 0.5, TypeError, "section must be a str"),
        ("HS9", float("nan"), ValueError, r"unknown section 'HS9'"),
        ("", 0.5, ValueError, r"unknown section ''"),
        ("hs1", 0.5, ValueError, r"unknown section 'hs1'"),
        ("HS1 ", 0.5, ValueError, r"unknown section 'HS1 '"),
        ("R1", 0.5, ValueError, r"unknown section 'R1'"),  # a room is not a section
        ("HS9", None, ValueError, r"unknown section 'HS9'"),
    ],
    ids=repr,
)
def test_hold_checks_the_section_before_the_level(
    section: object, level: object, error: type[Exception], pattern: str
) -> None:
    a = e_layout()
    before = a.to_dict()
    with pytest.raises(error, match=pattern) as caught:
        a.hold(section, level)  # type: ignore[arg-type]  # deliberate misuse
    assert "holds[" not in str(caught.value)
    assert a.to_dict() == before


def test_an_unknown_section_error_lists_the_known_sections() -> None:
    a = e_layout()
    with pytest.raises(
        ValueError, match=re.escape("['HS1', 'HS2', 'HS3', 'HS4', 'HS5']")
    ):
        a.hold("HS9", 0.5)


def test_hold_release_and_repeat_are_no_ops() -> None:
    a = e_layout()
    a.hold("HS1", None)  # releasing what was never held is not an error
    assert a.holds["HS1"] is None
    a.hold("HS1", 0.5)
    first = a.to_dict()
    a.hold("HS1", 0.5)
    assert a.to_dict() == first
    a.hold("HS1", None)
    assert a.holds["HS1"] is None
    a.hold("HS1", None)
    assert a.holds["HS1"] is None


def test_hold_zero_is_a_hold_and_not_a_release() -> None:
    a = e_layout()
    a.hold("HS1", 0)
    a.hold("HS3", 0)
    assert a.holds["HS1"] == 0.0
    assert a.holds["HS1"] is not None
    assert a.holds["HS3"] is not None


def test_hold_takes_effect_only_on_the_next_update() -> None:
    a = e_layout(history_length=4)
    a.update(e_meas(0.5))
    duty_before, history_before = a.duty, a.history
    assert duty_of(a)["HS3"] == 1.0
    a.hold("HS3", 0.0)
    assert a.duty == duty_before
    assert a.history == history_before
    a.update(e_meas(0.5))
    # E2: HS3 shut, HS4 compensates: minimise (0.5 - 0.7b)^2 + (0.4b)^2 -> 7/13.
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=0.0, HS4=7 / 13, HS5=0.0)


# --- A6: a held section ------------------------------------------------------


def test_e2_free_sections_compensate_for_a_shut_section() -> None:
    a = e_layout()
    a.hold("HS3", 0.0)
    a.update(e_meas(0.5))
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=0.0, HS4=7 / 13, HS5=0.0)
    assert duty_of(a)["HS4"] == pytest.approx(0.538, abs=1e-3)


def test_release_reallocates_exactly_like_a_fresh_allocator_e1() -> None:
    a = e_layout()
    a.hold("HS3", 0.0)
    a.update(e_meas(0.5))
    a.hold("HS3", None)
    a.update(e_meas(0.5))
    # Fresh twin with a PI integral of 0 (ki=0): same demand, same matrix.
    fresh = e_layout()
    fresh.update(e_meas(0.5))
    assert duty_of(a) == duty_of(fresh)
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=1.0, HS4=14 / 65, HS5=0.0)


@pytest.mark.parametrize(
    ("section", "level", "temperatures"),
    [
        ("HS3", 1.0, e_meas(-1.0)),  # R3 too hot, still on
        ("HS3", 0.0, e_meas(1.0)),  # R3 full demand, still off
        ("HS1", 1.0, {**e_meas(0.0), "R1": 22.0}),  # closed form, hot
        ("HS1", 0.0, {**e_meas(0.0), "R1": 20.0}),  # closed form, cold
        ("HS4", 0.0, e_meas(1.0, 1.0)),  # multi-room section
        ("HS4", 1.0, e_meas(0.0, 0.0)),
    ],
    ids=[
        "hs3-on-hot",
        "hs3-off-cold",
        "hs1-on-hot",
        "hs1-off-cold",
        "hs4-off",
        "hs4-on",
    ],
)
def test_a_section_held_at_a_bound_commands_exactly_its_level(
    section: str, level: float, temperatures: dict[str, float]
) -> None:
    a = e_layout(history_length=4)
    a.hold(section, level)
    for _ in range(10):
        commands = a.update(temperatures)
        assert commands[section] == level
        assert duty_of(a)[section] == level
    assert a.history[section] == (level,) * 4


def test_a_held_level_ignores_the_demand_at_every_step() -> None:
    a = e_layout(history_length=6)
    a.hold("HS3", 1.0)
    for i in range(30):
        a.update(e_meas(-1.0 + 2.0 * (i % 7) / 6))
        assert duty_of(a)["HS3"] == 1.0
    assert a.history["HS3"] == (1.0,) * 6


def test_a_held_quarter_level_fills_the_first_window_with_the_level_as_its_mean() -> (
    None
):
    # T6: from an empty 24-slot window, the modulation of 0.25 puts exactly 6
    # slots on (the quarter-level pattern pinned in test_modulator), so the
    # window mean is 0.25 and duty reports 0.25 at every step.
    a = e_layout()
    a.hold("HS1", 0.25)
    commands: list[float] = []
    for _ in range(24):
        commands.append(a.update(e_meas(0.5))["HS1"])
        assert duty_of(a)["HS1"] == 0.25
    assert set(commands) <= {0.0, 1.0}
    assert sum(commands) / 24 == 0.25
    assert a.history["HS1"] == tuple(commands)


def test_a_held_fractional_level_is_modulated_like_a_standalone_modulator() -> None:
    # A6 main clause: the commands are the floor-heating modulation of the
    # level. The 0/1 pattern tracks the level only to within about a slot (a
    # window of 4 at 0.25 cycles 1,0,0,0,0 with period 5, realised 0.2), so
    # the commands are compared to a modulator twin, not to a window mean.
    a = e_layout(history_length=4)
    a.hold("HS1", 0.25)
    twin = hs.Modulator("floor_heating", history_length=4, fixed_output=0.25)
    got = [a.update(e_meas(0.5))["HS1"] for _ in range(12)]
    want = [twin.command(0.0) for _ in range(12)]
    assert got == want
    assert got == [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    assert a.history["HS1"] == twin.history
    assert duty_of(a)["HS1"] == 0.25


def test_a_held_level_on_a_two_slot_window_pins_the_modulators_one_slot_error() -> None:
    # history_length 2, hold 0.5: commands 1,0,0,1,0,0,... (window mean 1/3)
    # while duty reports the level 0.5.
    a = SectionAllocator(
        {"R": {"priority": 1.0, "evenness": 0.0}},
        {"HS1": {"R": 1.0}},
        history_length=2,
        kp=1.0,
        ki=0.0,
    )
    a.hold("HS1", 0.5)
    got = []
    for _ in range(9):
        got.append(a.update({"R": 20.0})["HS1"])
        assert duty_of(a)["HS1"] == 0.5
    assert got == [1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def test_a_held_duty_is_a_plain_float_and_positive_zero() -> None:
    a = e_layout()
    a.hold("HS1", Fraction(1, 4))  # type: ignore[arg-type]  # numeric variant
    a.hold("HS2", -0.0)
    a.hold("HS3", 1)
    a.update(e_meas(0.5))
    duty = duty_of(a)
    assert duty["HS1"] == 0.25
    assert duty["HS3"] == 1.0
    for name in ("HS1", "HS2", "HS3"):
        assert type(duty[name]) is float
    assert math.copysign(1.0, duty["HS2"]) == 1.0


def test_a_zero_hold_is_a_hold_in_both_the_closed_form_and_a_matrix_component() -> None:
    a = e_layout(history_length=4)
    a.hold("HS1", 0)  # closed form (one room, one section)
    a.hold("HS3", 0)  # matrix component
    for _ in range(30):
        commands = a.update({**e_meas(1.0, 1.0), "R1": 15.0})
        assert commands["HS1"] == 0.0
        assert commands["HS3"] == 0.0
        assert duty_of(a)["HS1"] == 0.0
        assert duty_of(a)["HS3"] == 0.0
    assert a.history["HS1"] == (0.0,) * 4


def test_a_held_closed_form_section_is_independent_of_its_room_demand() -> None:
    a = e_layout()
    a.hold("HS2", 0.75)
    a.update({**e_meas(0.0), "R2": 10.0})
    assert duty_of(a)["HS2"] == 0.75


def test_a_hold_changed_mid_run_takes_effect_on_the_next_update() -> None:
    a = e_layout(history_length=4)
    levels = [0.0, 0.0, 1.0, 1.0, None, None]
    seen = []
    for level in levels:
        a.hold("HS3", level)
        a.update(e_meas(0.5))
        seen.append(duty_of(a)["HS3"])
    assert seen[:4] == [0.0, 0.0, 1.0, 1.0]
    assert seen[4:] == [1.0, 1.0]  # free again: E1 gives HS3 1.0
    assert a.holds["HS3"] is None


def test_the_held_contribution_is_subtracted_from_every_room_a_section_covers() -> None:
    # HS4 held at 0.5 covers R3 (0.7) and R4 (0.4). Demands R3 0.65, R4 0.38.
    # Hand: HS3 = (0.65 - 0.35) / 0.3 = 1.0 (R3's remainder), HS5 =
    # (0.38 - 0.2) / 0.6 = 0.3; HS1, HS2 idle.
    a = e_layout()
    a.hold("HS4", 0.5)
    a.update(e_meas(0.65, 0.38))
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=1.0, HS4=0.5, HS5=0.3)


def test_free_sections_do_not_chase_a_demand_the_hold_already_oversupplies() -> None:
    # HS3 held at 1.0 gives R3 0.3 against demand 0.15: R3's residual is
    # negative, so HS4 stays at its lower bound and HS5 serves R4's 0.4
    # alone, which takes HS5 to two thirds.
    a = e_layout()
    a.hold("HS3", 1.0)
    a.update(e_meas(0.15, 0.4))
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=1.0, HS4=0.0, HS5=2 / 3)


@pytest.mark.parametrize("coverage", [0.2, 0.5])
def test_a_held_contribution_uses_the_normalised_coverage_not_the_raw_one(
    coverage: float,
) -> None:
    # Two sections of equal coverage: each normalised to 0.5. HS1 held at 1.0
    # supplies 0.5, so demand 0.5 needs no HS2 and demand 0.75 needs HS2 at 0.5.
    a = pair(coverage)
    a.hold("HS1", 1.0)
    a.update({"R": 20.5})
    assert duty_of(a)["HS2"] == pytest.approx(0.0, abs=1e-12)
    b = pair(coverage)
    b.hold("HS1", 1.0)
    b.update({"R": 20.25})
    assert duty_of(b)["HS2"] == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize("coverage", [0.2, 0.5])
def test_hold_with_a_fraction_is_invariant_to_the_room_total(coverage: float) -> None:
    # HS1 held at 0.25 supplies 0.125; demand 0.6 -> HS2 = (0.6 - 0.125) / 0.5.
    a = pair(coverage)
    a.hold("HS1", 0.25)
    a.update({"R": 20.4})
    assert duty_of(a)["HS2"] == pytest.approx(0.95, abs=1e-9)


@pytest.mark.parametrize("held_level", [0.0, 0.5, 1.0])
def test_evenness_rows_use_the_held_level(held_level: float) -> None:
    # One room, evenness 1, two sections of coverage 0.5 (normalised 0.5).
    # J = (d - 0.5L - 0.5u)^2 + 1 * [(L - m)^2 + (u - m)^2], m = (L+u)/2,
    #   = (d - 0.5L - 0.5u)^2 + (L - u)^2 / 2.
    # d = 0.5: dJ/du = -(d - 0.5L - 0.5u) - (L - u) = 0
    #   -> u = (2L - 0.5 + 0.5 L ... ) solved below by hand for each L:
    #   L=1.0: 0.5(0.5-0.5-0.5u)... = 0.25u - 0 ... gives u = 2/3
    #   L=0.0: gives u = 1/3
    #   L=0.5: both sit at the demand: u = 0.5
    want = {1.0: 2 / 3, 0.0: 1 / 3, 0.5: 0.5}[held_level]
    a = pair(0.5, evenness=1.0)
    a.hold("HS1", held_level)
    a.update({"R": 20.5})
    assert duty_of(a)["HS1"] == held_level
    assert duty_of(a)["HS2"] == pytest.approx(want, abs=1e-9)


def test_evenness_rows_use_the_held_level_on_the_reference_layout() -> None:
    # R3, R4 evenness 1, HS4 held at 0, demands 0.5 each:
    # R3 minimises (0.5 - 0.3u)^2 + (u^2)/2 -> u = 0.3 / 1.18 = 15/59 (HS3);
    # R4 minimises (0.5 - 0.6u)^2 + u^2/2 -> u = 0.6 / 1.72 = 15/43 (HS5).
    a = e_layout(e3=1.0, e4=1.0)
    a.hold("HS4", 0.0)
    a.update(e_meas(0.5, 0.5))
    assert duty_of(a) == approx_duty(
        HS1=0.0, HS2=0.0, HS3=15 / 59, HS4=0.0, HS5=15 / 43
    )


@pytest.mark.parametrize("level", [0.0, 1.0])
def test_a_single_free_column_with_a_zero_residual_converges(level: float) -> None:
    # One free column whose right-hand side is exactly zero (or whose optimum
    # sits on its bound) must not trip the solver's status check.
    a = pair(0.5)
    a.hold("HS1", level)
    a.update({"R": 21.0 if level == 0.0 else 20.5})
    assert duty_of(a)["HS2"] == pytest.approx(0.0, abs=1e-12)


# --- A7: no solver for an all-held component ---------------------------------


def test_an_all_held_component_calls_no_solver_e5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("solver called")

    monkeypatch.setattr(allocator_module, "lsq_linear", refuse)
    a = e_layout()
    a.hold("HS3", 1.0)
    a.hold("HS4", 0.0)
    a.hold("HS5", 0.5)
    a.update(e_meas(0.5))
    assert duty_of(a) == {"HS1": 0.0, "HS2": 0.0, "HS3": 1.0, "HS4": 0.0, "HS5": 0.5}
    # E5: the heat the rooms get is 0.3 (R3) and 0.3 (R4) -- normalised
    # coverage times level, from the public coverages only.
    assert delivered(a, "R3") == pytest.approx(0.3)
    assert delivered(a, "R4") == pytest.approx(0.3)
    # Converse: with one free section the solver is reached.
    a.hold("HS5", None)
    with pytest.raises(AssertionError, match="solver called"):
        a.update(e_meas(0.5))


def test_only_components_with_a_free_section_call_the_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = allocator_module.lsq_linear
    calls: list[int] = []

    def spy(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real(*args, **kwargs)  # type: ignore[operator]  # mypy resolves scipy.optimize.lsq_linear to the submodule, not the function

    monkeypatch.setattr(allocator_module, "lsq_linear", spy)
    a = e_layout()
    a.hold("HS3", 1.0)
    a.hold("HS4", 0.0)
    a.hold("HS5", 0.5)
    commands = a.update({**e_meas(0.5, 0.5), "R1": 20.0, "R2": 20.0})
    assert calls == []
    assert commands["HS3"] == 1.0
    assert commands["HS1"] == 1.0  # the unrelated closed-form components still run
    assert commands["HS2"] == 1.0
    a.hold("HS5", None)
    a.update(e_meas(0.5, 0.5))
    assert calls == [1]


# --- A8: room PI keeps running during a hold ----------------------------------


def test_every_rooms_pi_equals_an_unheld_twins_step_for_step() -> None:
    held = SectionAllocator(
        e_rooms(), e_sections()
    )  # default gains: the integral moves
    twin = SectionAllocator(e_rooms(), e_sections())
    temperatures = varied_temperatures(43)
    for step in range(40):
        if step == 5:
            held.hold("HS3", 0.0)
        if step == 15:
            held.hold("HS3", 1.0)
        if step == 25:
            held.hold("HS3", None)
        sample = {
            "R1": temperatures[step],
            "R2": temperatures[step + 1],
            "R3": temperatures[step + 2],
            "R4": temperatures[step + 3] - 1.0,
        }
        held.update(sample)
        twin.update(sample)
        for name in held.rooms:
            assert held.rooms[name].integral == twin.rooms[name].integral
            assert held.rooms[name].demand == twin.rooms[name].demand


# --- A9: a raising update changes nothing --------------------------------------


def _fail_solver(*_args: object, **_kwargs: object) -> object:
    raise ValueError("boom")


@pytest.mark.parametrize("failure", ["solver", "nan", "text", "missing"])
def test_a_raising_update_leaves_holds_windows_rooms_and_duty_unchanged(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = e_layout(history_length=4)
    a.hold("HS3", 0.25)
    a.hold("HS4", 0.0)  # HS5 stays free, so the solver runs
    a.update(e_meas(0.5, 0.5))
    before = (a.to_dict(), a.duty, a.holds, a.history)
    temperatures = e_meas(0.6, 0.4)
    error: type[Exception]
    if failure == "solver":
        monkeypatch.setattr(allocator_module, "lsq_linear", _fail_solver)
        error = ValueError
    elif failure == "nan":
        temperatures["R4"] = float("nan")
        error = ValueError
    elif failure == "text":
        temperatures["R4"] = "warm"  # type: ignore[assignment]  # deliberate misuse
        error = TypeError
    else:
        del temperatures["R2"]
        error = ValueError
    with pytest.raises(error):
        a.update(temperatures)
    assert (a.to_dict(), a.duty, a.holds, a.history) == before


# --- A10: hold in the snapshot ------------------------------------------------


def test_to_dict_reflects_a_hold_set_without_an_update() -> None:
    a = e_layout()
    a.hold("HS2", 0.5)
    d = a.to_dict()
    sections = d["sections"]
    assert isinstance(sections, dict)
    assert sections["HS2"] == {"coverage": {"R2": 1.0}, "history": [], "hold": 0.5}
    assert [sections[n]["hold"] for n in ("HS1", "HS3", "HS4", "HS5")] == [None] * 4
    assert json.loads(json.dumps(d)) == d


def test_to_dict_writes_a_zero_hold_as_zero_and_plain_floats() -> None:
    sections = e_sections()
    sections["HS5"] = {"R4": np.float64(0.6)}
    a = SectionAllocator(e_rooms(), sections, kp=1.0, ki=0.0)
    a.hold("HS1", 0)
    a.hold("HS2", Fraction(1, 3))  # type: ignore[arg-type]  # numeric variant
    d = a.to_dict()
    out = d["sections"]
    assert isinstance(out, dict)
    assert out["HS1"]["hold"] == 0.0
    assert out["HS1"]["hold"] is not None
    assert type(out["HS1"]["hold"]) is float
    assert out["HS2"]["hold"] == 1 / 3
    assert type(out["HS5"]["coverage"]["R4"]) is float
    assert json.loads(json.dumps(d)) == d


@pytest.mark.parametrize("through_json", [False, True])
def test_a_restored_allocator_with_holds_produces_the_same_next_fifty(
    through_json: bool,
) -> None:
    source = SectionAllocator(e_rooms(0.1), e_sections(), history_length=6)
    source.hold("HS1", 0.25)
    source.hold("HS2", 1.0)
    source.hold("HS4", 0.0)
    temperatures = varied_temperatures(60)

    def sample(i: int) -> dict[str, float]:
        return {
            "R1": temperatures[i],
            "R2": temperatures[i + 1],
            "R3": temperatures[i + 2],
            "R4": temperatures[i + 3],
        }

    for i in range(7):  # a partly filled fractional window
        source.update(sample(i))
    snapshot = source.to_dict()
    if through_json:
        snapshot = json.loads(json.dumps(snapshot))
    restored = SectionAllocator.from_dict(snapshot)
    assert restored.holds == source.holds
    assert restored.duty is None
    assert restored.to_dict() == source.to_dict()
    for i in range(7, 57):
        if i == 27:
            source.hold("HS4", None)
            restored.hold("HS4", None)
        assert restored.update(sample(i)) == source.update(sample(i))
        assert restored.duty == source.duty


def test_from_dict_reads_an_int_zero_hold_as_a_hold_not_none() -> None:
    data = e_layout(history_length=4).to_dict()
    data["sections"]["HS3"]["hold"] = 0  # type: ignore[index]  # object-typed snapshot
    restored = SectionAllocator.from_dict(data)
    assert restored.holds["HS3"] == 0.0
    assert restored.holds["HS3"] is not None
    assert type(restored.holds["HS3"]) is float
    restored.update(e_meas(0.5))
    assert duty_of(restored)["HS3"] == 0.0


def test_from_dict_reads_a_negative_zero_hold_as_positive_zero() -> None:
    text = json.dumps(e_layout(history_length=4).to_dict()).replace(
        '"hold": null', '"hold": -0.0', 1
    )
    assert '"hold": -0.0' in text
    restored = SectionAllocator.from_dict(json.loads(text))
    assert restored.holds["HS1"] == 0.0
    assert math.copysign(1.0, restored.holds["HS1"]) == 1.0


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        (True, TypeError),
        ("0.5", TypeError),
        ([0.5], TypeError),
        (float("nan"), ValueError),
        (1.5, ValueError),
        (-1e-300, ValueError),
        (10**400, OverflowError),
    ],
    ids=repr,
)
def test_from_dict_refuses_a_bad_hold_with_its_snapshot_path(
    bad: object, error: type[Exception]
) -> None:
    data = e_layout(history_length=4).to_dict()
    data["sections"]["HS1"]["hold"] = bad  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(error) as caught:
        SectionAllocator.from_dict(data)
    assert str(caught.value).startswith("sections['HS1'].hold")


def test_from_dict_range_error_for_a_hold_has_the_exact_message() -> None:
    data = e_layout(history_length=4).to_dict()
    data["sections"]["HS1"]["hold"] = 1.5  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(
        ValueError,
        match=re.escape("sections['HS1'].hold must be in [0.0, 1.0], got 1.5."),
    ):
        SectionAllocator.from_dict(data)


def test_from_dict_does_not_renormalise_a_partial_coverage_layout() -> None:
    a = e_layout()
    restored = SectionAllocator.from_dict(a.to_dict())
    assert restored.sections == a.sections
    assert restored.sections["HS1"] == {"R1": 0.5}
    temperatures = {**e_meas(0.5), "R1": 20.6}
    assert restored.update(temperatures) == a.update(temperatures)
    assert restored.duty == a.duty


@pytest.mark.parametrize(
    ("faults", "error", "needle"),
    [
        (
            [
                (["sections", "HS1", "hold"], float("nan")),
                (["sections", "HS2", "coverage"], {"R2": 1.5}),
            ],
            ValueError,
            "sections['HS2']['R2']",
        ),
        (
            [
                (["sections", "HS1", "hold"], float("nan")),
                (["rooms", "R1", "integral"], "x"),
            ],
            TypeError,
            "rooms['R1'].integral",
        ),
        (
            [
                (["sections", "HS1", "hold"], 2.0),
                (["sections", "HS1", "history"], [2.0]),
            ],
            ValueError,
            "sections['HS1'].hold",
        ),
    ],
    ids=["layout-before-hold", "room-before-hold", "hold-before-history"],
)
def test_from_dict_reports_a_layout_then_room_then_hold_then_history_error(
    faults: list[tuple[list[str], object]], error: type[Exception], needle: str
) -> None:
    data = e_layout(history_length=4).to_dict()
    for path, value in faults:
        node: dict[str, object] = data
        for key in path[:-1]:
            node = node[key]  # type: ignore[assignment]  # object-typed snapshot
        node[path[-1]] = value
    with pytest.raises(error) as caught:
        SectionAllocator.from_dict(data)
    assert needle in str(caught.value)
    if "hold" not in needle:
        assert ".hold" not in str(caught.value)


def test_from_dict_reports_a_layout_error_before_a_hold_error() -> None:
    data = e_layout(history_length=4).to_dict()
    data["sections"]["HS1"]["coverage"] = {"R1": 0}  # type: ignore[index]  # object-typed snapshot
    data["sections"]["HS1"]["hold"] = float("nan")  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(ValueError, match=re.escape("sections['HS1']['R1']")) as caught:
        SectionAllocator.from_dict(data)
    assert ".hold" not in str(caught.value)


def test_a_1_0_0_snapshot_is_refused_for_coverage_and_hold_only() -> None:
    data = e_layout(history_length=4).to_dict()
    entry = data["sections"]["HS1"]  # type: ignore[index]  # object-typed snapshot
    data["sections"]["HS1"] = {"shares": entry["coverage"], "history": entry["history"]}  # type: ignore[index]  # object-typed snapshot
    with pytest.raises(ValueError, match=re.escape("sections['HS1']: ")) as caught:
        SectionAllocator.from_dict(data)
    assert "['coverage', 'hold']" in str(caught.value)
    assert "shares" not in str(caught.value)


# --- A11: version --------------------------------------------------------------


def test_the_package_version_is_1_1_0() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == "1.1.0"


# ---------------------------------------------------------------------------
# main(): the showcase runs
# ---------------------------------------------------------------------------


def test_main_runs_and_reports_each_section(capsys: pytest.CaptureFixture[str]) -> None:
    allocator_module.main()
    out = capsys.readouterr().out
    assert "Example E1" in out
    assert "Snapshot round trip" in out
    assert "Invalid layout" in out


@pytest.mark.parametrize(
    ("weight", "scale", "want"),
    [
        (5e-324, 1.0, math.sqrt(1e-12)),
        (1e-12, 1.0, 1e-6),
        (math.nextafter(1e-12, 0.0), 1.0, 1e-6),  # one ULP below the floor
        (5e-324, 5e-324, 1.0),
        (1.0, 1.0, 1.0),
        (0.25, 1.0, 0.5),
    ],
)
def test_row_weight_never_vanishes_or_overflows(
    weight: float, scale: float, want: float
) -> None:
    value = allocator_module._row_weight(weight, scale)
    assert 0.0 < value <= 1.0
    assert value == pytest.approx(want, rel=1e-12)


# ---------------------------------------------------------------------------
# Round 2 (feat/allocator-home-assistant), step 5: a room without a temperature
# (B1-B5). ``None`` means no reading: that room's PI takes no step and the
# allocation uses its last demand, or 0.0 before its first. Expected numbers
# are derived by hand or from a standalone PIController, never read back.
# ---------------------------------------------------------------------------

E_ROOMS = ("R1", "R2", "R3", "R4")


def e_none(**overrides: float | None) -> dict[str, float | None]:
    """E-layout temperatures at the setpoint, R3 ``None``, with overrides."""
    base: dict[str, float | None] = {
        "R1": SETPOINT,
        "R2": SETPOINT,
        "R3": None,
        "R4": SETPOINT,
    }
    base.update(overrides)
    return base


def _drive(
    a: SectionAllocator, room: str, readings: list[float | None]
) -> list[tuple[float, float | None]]:
    """Feed one room the readings (others at the setpoint); return its state."""
    states = []
    for reading in readings:
        step: dict[str, float | None] = {r: SETPOINT for r in a.rooms}
        step[room] = reading
        a.update(step)
        states.append((a.rooms[room].integral, a.rooms[room].demand))
    return states


# --- B1: twin comparison ----------------------------------------------------


@pytest.mark.parametrize("prior", [True, False], ids=["prior-reading", "fresh"])
def test_update_none_room_is_untouched_while_other_rooms_step_like_a_twin(
    prior: bool,
) -> None:
    a = ref(history_length=4)
    twin = ref(history_length=4)
    a.rooms["R3"].ki = twin.rooms["R3"].ki = 0.1
    if prior:
        a.update(meas(0.1, 0.6, 0.3))
        twin.update(meas(0.1, 0.6, 0.3))
    before = {n: (r.integral, r.demand) for n, r in a.rooms.items()}
    a.update({"R1": 17.0, "R2": 19.0, "R3": None})
    twin.update({"R1": 17.0, "R2": 19.0, "R3": 17.0})
    for name in ("R1", "R2"):
        assert a.rooms[name].integral == twin.rooms[name].integral
        assert a.rooms[name].demand == twin.rooms[name].demand
    assert (a.rooms["R3"].integral, a.rooms["R3"].demand) == before["R3"]
    if not prior:
        assert a.rooms["R3"].demand is None


# --- B2: the allocation uses the last demand --------------------------------


def test_update_none_reallocates_the_last_demand_on_the_e_layout() -> None:
    a = e_layout()
    a.update(e_meas(0.5))
    first = duty_of(a)
    assert first == approx_duty(HS1=0.0, HS2=0.0, HS3=1.0, HS4=14 / 65, HS5=0.0)
    a.update(e_none())
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=1.0, HS4=14 / 65, HS5=0.0)
    assert a.rooms["R3"].demand == pytest.approx(0.5)


def test_update_none_on_a_fresh_room_allocates_zero_and_reports_no_demand() -> None:
    a = e_layout()
    a.update({**e_none(), "R4": SETPOINT - 0.5})
    # R3 allocated as demand 0: HS3 = HS4 = 0 forces HS5 = 0.5 / 0.6.
    assert duty_of(a) == approx_duty(HS1=0.0, HS2=0.0, HS3=0.0, HS4=0.0, HS5=5 / 6)
    assert a.rooms["R3"].demand is None


def test_update_none_after_from_dict_allocates_zero_not_the_pre_snapshot_demand() -> (
    None
):
    a = e_layout()
    a.update(e_meas(0.5))
    restored = SectionAllocator.from_dict(a.to_dict())
    step = {**e_none(), "R4": SETPOINT - 0.5}
    restored.update(step)
    assert duty_of(restored) == approx_duty(
        HS1=0.0, HS2=0.0, HS3=0.0, HS4=0.0, HS5=5 / 6
    )
    assert restored.rooms["R3"].demand is None
    a.update(step)  # the live allocator still heats R3 with its last demand
    live = duty_of(a)
    assert 0.3 * live["HS3"] + 0.7 * live["HS4"] == pytest.approx(0.5)


def test_update_none_reuses_a_saturated_last_demand_of_one() -> None:
    a = e_layout()
    a.update(e_meas(2.0))
    first = duty_of(a)
    # Minimise (1 - 0.3 u3 - 0.7 u4)^2 + (0.4 u4 + 0.6 u5)^2: u3 = 1, u5 = 0,
    # 0.98 (1 - u4) = 0.32 u4.
    assert first["HS3"] == 1.0
    assert first["HS4"] == pytest.approx(0.98 / 1.30, abs=1e-9)
    assert first["HS5"] == 0.0
    a.update(e_none())
    assert a.rooms["R3"].demand == 1.0
    assert duty_of(a) == first


@pytest.mark.parametrize("prior", [True, False], ids=["after-reading", "fresh"])
def test_update_none_on_the_closed_form_path_uses_last_demand_or_zero(
    prior: bool,
) -> None:
    a = ref()
    if prior:
        a.update(meas(0.0, 0.0, 0.3))
    a.update({"R1": SETPOINT, "R2": SETPOINT, "R3": None})
    expected = 0.3 if prior else 0.0
    assert duty_of(a)["HS4"] == pytest.approx(expected)
    assert a.rooms["R3"].demand == (pytest.approx(0.3) if prior else None)
    assert a.history["HS4"][-1] in (0.0, 1.0)


def test_update_none_on_the_e3_closed_form_keeps_the_last_demand() -> None:
    a = e_layout()
    a.update({**e_meas(0.0), "R1": 20.6})
    a.update({**e_meas(0.0), "R1": None})
    assert duty_of(a)["HS1"] == pytest.approx(0.4)
    assert a.rooms["R1"].demand == pytest.approx(0.4)


@pytest.mark.parametrize(("attr", "value"), [("setpoint", 25.0), ("kp", 5.0)])
def test_update_none_defers_a_setting_change_to_the_next_real_reading(
    attr: str, value: float
) -> None:
    a = ref()
    a.update(meas(0.0, 0.0, 0.3))
    demand = a.rooms["R3"].demand
    setattr(a.rooms["R3"], attr, value)
    a.update({"R1": SETPOINT, "R2": SETPOINT, "R3": None})
    assert a.rooms["R3"].demand == demand
    assert duty_of(a)["HS4"] == pytest.approx(0.3)
    a.update(meas(0.0, 0.0, 0.3))
    assert a.rooms["R3"].demand == 1.0  # 4.3 or 5 * 0.3 + integral: clamped


def test_update_none_after_from_dict_keeps_the_integral_and_allocates_zero() -> None:
    a = ref()
    a.rooms["R3"].ki = 0.5
    a.update(meas(0.0, 0.0, 0.3))
    b = SectionAllocator.from_dict(a.to_dict())
    integral = b.rooms["R3"].integral
    assert integral != 0.0
    b.update({"R1": SETPOINT, "R2": SETPOINT, "R3": None})
    assert b.rooms["R3"].integral == integral
    assert b.rooms["R3"].demand is None
    assert duty_of(b)["HS4"] == 0.0


def test_update_consecutive_none_steps_repeat_the_same_duty() -> None:
    a = e_layout(history_length=4)
    a.rooms["R3"].ki = 0.1
    a.update(e_meas(0.5))
    integral = a.rooms["R3"].integral
    first = None
    for _ in range(5):
        a.update(e_none())
        first = first or duty_of(a)
        assert duty_of(a) == first
        assert a.rooms["R3"].integral == integral
    assert all(len(w) == 4 for w in a.history.values())
    assert all(c in (0.0, 1.0) for w in a.history.values() for c in w)


# --- B3: equals a standalone PI fed only the real readings ------------------


@pytest.mark.parametrize(
    "readings",
    [
        [None, 20.0, None, None, 19.5, 22.0, None],
        [None, None],
        [20.0, None],
        [19.0, 19.0, 19.0, None, None, None, None, None, 19.0],
        [None, None, 20.0, None, 19.5, None, None],
    ],
    ids=["interleaved", "never-read", "trailing", "outage", "edges"],
)
def test_update_none_room_matches_a_standalone_pi_fed_only_real_readings(
    readings: list[float | None],
) -> None:
    a = SectionAllocator(
        ref_rooms(),
        ref_sections(),
        kp=0.3,
        ki=0.015,
        history_length=4,
    )
    pi = PIController(0.3, 0.015, "radiator", 21.0, history_length=1)
    for reading in readings:
        _drive(a, "R3", [reading])
        if reading is not None:
            pi.update(reading)
        assert a.rooms["R3"].integral == pi.integral
        assert a.rooms["R3"].demand == pi.pi_output


def test_update_single_room_none_at_start_and_end_matches_a_standalone_pi() -> None:
    a = SectionAllocator(
        {"R": {"priority": 1.0, "evenness": 0.0}},
        {"HS": {"R": 1.0}},
        kp=0.3,
        ki=0.015,
    )
    pi = PIController(0.3, 0.015, "radiator", 21.0, history_length=1)
    for reading in [None, None, 20.0, None, 19.5, None, None]:
        a.update({"R": reading})
        if reading is not None:
            pi.update(reading)
        assert a.rooms["R"].integral == pi.integral
        assert a.rooms["R"].demand == pi.pi_output


# --- B4: every room None, holds, refusals unchanged -------------------------


def test_update_none_for_every_room_on_a_fresh_allocator_issues_closed_commands() -> (
    None
):
    a = e_layout(history_length=4)
    commands = a.update(dict.fromkeys(E_ROOMS))
    zeros = dict.fromkeys(["HS1", "HS2", "HS3", "HS4", "HS5"], 0.0)
    assert commands == zeros
    assert list(commands) == list(zeros)
    assert a.duty == zeros
    assert all(r.demand is None and r.integral == 0.0 for r in a.rooms.values())
    assert all(w == (0.0,) for w in a.history.values())


def test_update_all_none_still_applies_holds_and_reuses_last_demands() -> None:
    a = e_layout()
    a.update(e_meas(0.5))
    a.hold("HS1", 1.0)
    before = {n: (r.integral, r.demand) for n, r in a.rooms.items()}
    commands = a.update(dict.fromkeys(E_ROOMS))
    assert duty_of(a) == approx_duty(HS1=1.0, HS2=0.0, HS3=1.0, HS4=14 / 65, HS5=0.0)
    assert commands["HS1"] == 1.0
    assert a.holds["HS1"] == 1.0
    assert {n: (r.integral, r.demand) for n, r in a.rooms.items()} == before


@pytest.mark.parametrize(
    ("measured", "error", "match"),
    [
        ({"R1": None, "R2": None}, ValueError, re.escape("missing rooms ['R3']")),
        ({"R2": None, "R3": None}, ValueError, re.escape("missing rooms ['R1']")),
        (
            {"R1": None, "R2": None, "R3": None, "R9": None},
            ValueError,
            re.escape("unknown rooms ['R9']"),
        ),
        (
            {"R1": None, "R2": "20", "R3": None},
            TypeError,
            re.escape("measured['R2']"),
        ),
        (
            {"R1": None, "R2": "None", "R3": None},
            TypeError,
            re.escape("measured['R2']"),
        ),
        (
            {"R1": None, "R2": True, "R3": None},
            TypeError,
            re.escape("measured['R2']"),
        ),
        (
            {"R1": None, "R2": float("nan"), "R3": None},
            ValueError,
            re.escape("measured['R2']"),
        ),
        (
            {"R1": float("nan"), "R2": 20.0, "R3": None},
            ValueError,
            re.escape("measured['R1']"),
        ),
        (
            {"R1": None, "R2": 10**400, "R3": None},
            OverflowError,
            re.escape("measured['R2']"),
        ),
        ({"R1": 20.0, "R2": 20.0, "R3": ""}, TypeError, re.escape("measured['R3']")),
        (
            {"R1": 20.0, "R2": 20.0, "R3": "unavailable"},
            TypeError,
            re.escape("measured['R3']"),
        ),
        (
            {"R1": 20.0, "R2": 20.0, "R3": "unknown"},
            TypeError,
            re.escape("measured['R3']"),
        ),
    ],
)
def test_update_refusals_with_none_rooms_present_are_unchanged_and_leave_no_trace(
    measured: dict[str, object], error: type[Exception], match: str
) -> None:
    a = ref(history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    before = snapshot(a)
    history = a.history
    with pytest.raises(error, match=match):
        a.update(measured)  # type: ignore[arg-type]  # deliberate misuse
    assert snapshot(a) == before
    assert a.history == history


def test_update_missing_key_is_not_no_reading_for_a_defaulting_mapping() -> None:
    from collections import defaultdict

    a = ref()
    dd: defaultdict[str, float | None] = defaultdict(
        lambda: None, {"R1": 20.0, "R2": 20.0}
    )
    with pytest.raises(ValueError, match=re.escape("measured is missing rooms ['R3']")):
        a.update(dd)
    assert "R3" not in dd


def test_update_refuses_an_unknown_room_even_when_its_value_is_none() -> None:
    a = ref()
    a.update(meas(0.1, 0.2, 0.3))
    before = a.to_dict()
    with pytest.raises(ValueError, match=re.escape("unknown rooms ['R9']")):
        a.update({**meas(0.1, 0.2, 0.3), "R9": None})
    assert a.to_dict() == before


# --- B5: a raising update changes nothing -----------------------------------


def test_update_none_before_a_first_reading_leaks_nothing_on_solver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = e_layout()
    _patch_solver(monkeypatch, ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        a.update({**e_none(), "R4": SETPOINT - 0.5})
    assert a.rooms["R3"].demand is None
    assert a.duty is None
    assert a.to_dict() == e_layout().to_dict()


def test_update_solver_failure_with_a_none_room_restores_every_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = ref(history_length=4)
    a.update(meas(0.1, 0.6, 0.3))
    before = snapshot(a)
    history, holds = a.history, a.holds
    _patch_solver(monkeypatch, SimpleNamespace(x=np.array([0.1, 0.1, 0.1]), status=0))
    with pytest.raises(ArithmeticError):
        a.update({"R1": 18.0, "R2": 18.0, "R3": None})
    assert snapshot(a) == before
    assert a.history == history
    assert a.holds == holds


# --- Reads, snapshot and ordering -------------------------------------------


class _FlippingMapping(Mapping[str, float | None]):
    """Returns a number for R3 on the first read and ``None`` on the second."""

    def __init__(self, data: dict[str, float | None]) -> None:
        self._data = data
        self.reads = 0

    def __getitem__(self, key: str) -> float | None:
        if key == "R3":
            self.reads += 1
            return self._data["R3"] if self.reads == 1 else None
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data  # a membership test is not a value read

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def test_update_reads_each_temperature_once() -> None:
    a = ref()
    mapping = _FlippingMapping(dict(meas(0.0, 0.0, 0.3)))
    a.update(mapping)
    assert mapping.reads == 1
    assert a.rooms["R3"].demand == pytest.approx(0.3)


def test_to_dict_after_none_steps_round_trips_with_unchanged_integral() -> None:
    a = ref()
    a.rooms["R1"].ki = 0.2
    a.update(meas(0.4, 0.2, 0.3))
    integral = a.rooms["R1"].integral
    a.update({"R1": None, "R2": 20.0, "R3": None})
    assert a.rooms["R1"].integral == integral
    restored = SectionAllocator.from_dict(json.loads(json.dumps(a.to_dict())))
    assert restored.to_dict() == a.to_dict()


def test_update_error_names_the_bad_room_not_the_none_room() -> None:
    a = ref()
    with pytest.raises(ValueError, match=re.escape("measured['R2']")):
        a.update({"R1": None, "R2": float("nan"), "R3": 20.0})


# ---------------------------------------------------------------------------
# Round 3 (feat/allocator-home-assistant), step 5: with_layout (C1-C6)
# ---------------------------------------------------------------------------

STEPS = [
    {"R1": 19.0, "R2": 20.0, "R3": 22.0},
    {"R1": 18.5, "R2": 21.5, "R3": 20.0},
    {"R1": 20.0, "R2": 19.0, "R3": 21.0},
    {"R1": 19.5, "R2": 20.5, "R3": 19.5},
    {"R1": 17.0, "R2": 22.0, "R3": 20.5},
    {"R1": 21.5, "R2": 18.0, "R3": 23.0},
]


def warm(history_length: int = 4) -> SectionAllocator:
    """A reference allocator with real integrals, a hold and full windows."""
    a = ref(history_length=history_length)
    for room in a.rooms.values():
        room.ki = 0.2
    a.hold("HS2", 0.5)
    for step in STEPS:
        a.update(step)
    return a


def full_state(a: SectionAllocator) -> tuple[object, ...]:
    return (
        a.to_dict(),
        a.duty,
        a.holds,
        a.history,
        {n: r.demand for n, r in a.rooms.items()},
    )


def run50(
    a: SectionAllocator, none_every: int = 0
) -> list[tuple[dict[str, float], object]]:
    """50 deterministic updates; every ``none_every``-th reading of R2 is None."""
    out: list[tuple[dict[str, float], object]] = []
    for i in range(50):
        reading: dict[str, float | None] = {
            "R1": 17.0 + (i * 7 % 11) * 0.5,
            "R2": 18.0 + (i * 5 % 13) * 0.4,
            "R3": 19.0 + (i * 3 % 9) * 0.5,
        }
        if none_every and i % none_every == 0:
            reading["R2"] = None
        out.append((a.update(reading), a.duty))
    return out


def rebuilt(
    a: SectionAllocator, edit: Callable[[dict[str, object]], None]
) -> SectionAllocator:
    """The from_dict twin: the snapshot edited by key names, as a consumer would."""
    data = a.to_dict()
    edit(data)
    return SectionAllocator.from_dict(data)


# --- C1: no-argument call ----------------------------------------------------


def test_with_layout_without_arguments_returns_an_equal_but_distinct_allocator() -> (
    None
):
    a = warm()
    a.update({"R1": None, "R2": 20.0, "R3": 21.0})
    before = full_state(a)
    b = a.with_layout()
    assert b is not a
    assert full_state(a) == before
    assert full_state(b) == before


def test_with_layout_without_arguments_issues_identical_commands_including_none_readings() -> (
    None
):
    a = warm()
    b = a.with_layout()
    assert run50(a, none_every=4) == run50(b, none_every=4)
    assert a.to_dict() == b.to_dict()


def test_with_layout_on_a_never_updated_allocator_has_no_duty_or_demand() -> None:
    b = ref(history_length=3).with_layout(history_length=1)
    assert b.duty is None
    assert all(r.demand is None for r in b.rooms.values())
    assert all(h == () for h in b.history.values())
    assert b.history_length == 1


def test_with_layout_chained_calls_equal_a_single_call() -> None:
    a = warm()
    once = a.with_layout()
    twice = a.with_layout().with_layout()
    assert full_state(once) == full_state(twice)
    assert run50(once) == run50(twice)


def test_with_layout_carries_a_zero_demand_as_zero_not_none() -> None:
    a = ref()
    a.update(meas(-1.0, 0.3, 0.5))
    assert a.rooms["R1"].demand == 0.0
    b = a.with_layout()
    assert b.rooms["R1"].demand == 0.0
    assert b.rooms["R1"].demand is not None


# --- Isolation both ways -----------------------------------------------------


def test_with_layout_new_allocator_shares_no_state_with_the_original() -> None:
    a = warm()
    before = full_state(a)
    b = a.with_layout()
    b.rooms["R1"].setpoint = 25.0
    b.hold("HS1", 1.0)
    b.hold("HS2", None)
    for step in STEPS[:3]:
        b.update(step)
    duty = b.duty
    assert duty is not None
    duty["HS1"] = 99.0
    assert full_state(a) == before
    assert a.rooms["R1"].setpoint == 21.0
    assert a.holds["HS1"] is None


def test_with_layout_original_updates_do_not_reach_the_new_allocator() -> None:
    a = warm()
    b = a.with_layout()
    snap = b.to_dict()
    duty = b.duty
    for step in STEPS:
        a.update(step)
    assert b.to_dict() == snap
    assert b.duty == duty


def test_with_layout_does_not_alias_the_callers_mappings() -> None:
    rooms = ref_rooms()
    sections = ref_sections()
    b = ref().with_layout(rooms, sections)
    rooms["R1"]["priority"] = 0.1
    sections["HS1"]["R1"] = 0.2
    assert b.rooms["R1"].priority == 1.0
    assert b.sections["HS1"]["R1"] == 0.7


# --- C2: priorities, evenness, coverages -------------------------------------


def edited_rooms() -> dict[str, dict[str, float]]:
    return ref_rooms(e1=0.3, e2=0.6, p1=0.5, p3=0.8)


def edited_sections() -> dict[str, dict[str, float]]:
    s = ref_sections()
    s["HS2"] = {"R1": 0.2, "R2": 0.3}
    s["HS1"] = {"R1": 0.8}
    return s


def edit_snapshot_to_new_layout(data: dict[str, object]) -> None:
    rooms = data["rooms"]
    sections = data["sections"]
    assert isinstance(rooms, dict)
    assert isinstance(sections, dict)
    for name, spec in edited_rooms().items():
        rooms[name]["priority"] = spec["priority"]
        rooms[name]["evenness"] = spec["evenness"]
    for name, cov in edited_sections().items():
        sections[name]["coverage"] = cov


def test_with_layout_layout_change_carries_all_state() -> None:
    a = warm()
    b = a.with_layout(edited_rooms(), edited_sections())
    assert b.duty == a.duty
    assert b.holds == a.holds
    assert b.history == a.history
    for name, room in a.rooms.items():
        new = b.rooms[name]
        assert (new.setpoint, new.kp, new.ki, new.integral, new.demand) == (
            room.setpoint,
            room.kp,
            room.ki,
            room.integral,
            room.demand,
        )
    assert b.rooms["R1"].priority == 0.5
    assert b.rooms["R2"].evenness == 0.6
    assert b.sections == edited_sections()
    assert a.sections == ref_sections()
    assert a.rooms["R1"].priority == 1.0


def test_with_layout_layout_change_matches_the_from_dict_rebuild_for_real_readings() -> (
    None
):
    a = warm()
    b = a.with_layout(edited_rooms(), edited_sections())
    twin = rebuilt(a, edit_snapshot_to_new_layout)
    assert b.to_dict() == twin.to_dict()
    assert run50(b) == run50(twin)


def test_with_layout_none_reading_allocates_the_carried_demand_unlike_from_dict() -> (
    None
):
    a = warm()
    demand = a.rooms["R2"].demand
    assert demand is not None
    assert demand > 0.0
    reading = {"R1": 20.0, "R2": None, "R3": 21.0}
    b = a.with_layout(edited_rooms(), edited_sections())
    twin = rebuilt(a, edit_snapshot_to_new_layout)
    b.update(reading)
    twin.update(reading)
    assert b.rooms["R2"].demand == demand
    assert twin.rooms["R2"].demand is None
    assert b.rooms["R2"].integral == a.rooms["R2"].integral
    assert b.duty != twin.duty  # R2 asks for heat in one and for none in the other


def test_with_layout_coverage_only_change_still_carries_duty_when_names_match() -> None:
    a = warm()
    assert a.duty is not None
    b = a.with_layout(sections={**ref_sections(), "HS2": {"R1": 0.3, "R2": 0.2}})
    assert b.duty == a.duty


def test_with_layout_duty_carries_for_reordered_rooms_and_mapping_proxies() -> None:
    a = warm()
    reordered = dict(reversed(list(ref_rooms().items())))
    assert a.with_layout(rooms=reordered).duty == a.duty
    assert a.with_layout(sections=MappingProxyType(ref_sections())).duty == a.duty


def test_with_layout_duty_carries_when_only_history_length_changes() -> None:
    a = warm()
    b = a.with_layout(history_length=1)
    assert b.duty == a.duty
    assert b.history_length == 1
    assert b.history == {s: h[-1:] for s, h in a.history.items()}


# --- C3: history_length ------------------------------------------------------


def window_allocator() -> SectionAllocator:
    a = ref(history_length=3)
    for level in (1.0, 0.0, 0.0):
        a.hold("HS4", level)
        a.update(meas(0, 0, 0))
    assert a.history["HS4"] == (1.0, 0.0, 0.0)
    return a


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, (0.0,)), (2, (0.0, 0.0)), (3, (1.0, 0.0, 0.0)), (4, (1.0, 0.0, 0.0))],
)
def test_with_layout_keeps_the_newest_slots_at_every_window_boundary(
    n: int, expected: tuple[float, ...]
) -> None:
    b = window_allocator().with_layout(history_length=n)
    assert b.history["HS4"] == expected
    assert b.holds["HS4"] == 0.0
    assert b.history_length == n


def test_with_layout_a_longer_window_is_not_full_and_a_shrink_is_irreversible() -> None:
    a = window_allocator()
    grown = a.with_layout(history_length=6)
    assert grown.history["HS4"] == (1.0, 0.0, 0.0)
    assert not grown._modulators["HS4"].is_history_full
    assert a.with_layout(history_length=1).with_layout(history_length=3).history[
        "HS4"
    ] == (0.0,)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 10])
def test_with_layout_history_length_matches_the_trimmed_from_dict_rebuild(
    n: int,
) -> None:
    a = warm()

    def edit(data: dict[str, object]) -> None:
        data["history_length"] = n
        sections = data["sections"]
        assert isinstance(sections, dict)
        for spec in sections.values():
            spec["history"] = spec["history"][-n:]

    b = a.with_layout(history_length=n)
    twin = rebuilt(a, edit)
    assert b.to_dict() == twin.to_dict()
    assert run50(b) == run50(twin)


def test_with_layout_a_fractional_hold_continues_the_pattern_over_a_grown_window() -> (
    None
):
    a = warm()
    b = a.with_layout(history_length=10)
    assert b.holds["HS2"] == 0.5
    assert b.history["HS2"] == a.history["HS2"]
    assert len(b.history["HS2"]) == 4
    expected = hs.Modulator._from_dict(
        {
            "mode": "floor_heating",
            "history_length": 10,
            "fixed_output": 0.5,
            "history": list(a.history["HS2"]),
        }
    )
    for step in STEPS:
        assert b.update(step)["HS2"] == expected.command(0.0)


def test_with_layout_history_length_limits_match_the_constructor() -> None:
    a = warm()
    ok = a.with_layout(history_length=sys.maxsize)
    assert ok.history == a.history
    for value, error in ((sys.maxsize + 1, OverflowError), (2.0, TypeError)):
        with pytest.raises(error) as via_method:
            a.with_layout(history_length=value)  # type: ignore[arg-type]  # deliberate misuse
        with pytest.raises(error) as via_constructor:
            SectionAllocator(ref_rooms(), ref_sections(), history_length=value)  # type: ignore[arg-type]  # deliberate misuse
        assert str(via_method.value) == str(via_constructor.value)


# --- C4: adding and removing -------------------------------------------------


def with_r4() -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    rooms = {**ref_rooms(), "R4": {"priority": 1.0, "evenness": 0.0}}
    sections = {**ref_sections(), "HS5": {"R4": 1.0}}
    return rooms, sections


def test_with_layout_new_room_and_section_start_fresh_and_names_keep_state() -> None:
    a = warm()
    rooms, sections = with_r4()
    b = a.with_layout(rooms, sections)
    r4 = b.rooms["R4"]
    assert (r4.kp, r4.ki, r4.setpoint, r4.integral, r4.demand) == (
        0.3,
        0.015,
        21.0,
        0.0,
        None,
    )
    assert b.history["HS5"] == ()
    assert b.holds["HS5"] is None
    assert b.duty is None
    for name in ("R1", "R2", "R3"):
        assert b.rooms[name].integral == a.rooms[name].integral
        assert b.rooms[name].kp == 1.0
    assert b.history["HS1"] == a.history["HS1"]
    assert b.holds["HS2"] == 0.5


def test_with_layout_new_room_takes_the_class_defaults_not_the_originals_gains() -> (
    None
):
    a = SectionAllocator(ref_rooms(), ref_sections(), kp=0.5, ki=0.05, setpoint=19.0)
    rooms, sections = with_r4()
    b = a.with_layout(rooms, sections)
    r4 = b.rooms["R4"]
    assert (r4.kp, r4.ki, r4.setpoint) == (0.3, 0.015, 21.0)
    assert (b.rooms["R1"].kp, b.rooms["R1"].ki, b.rooms["R1"].setpoint) == (
        0.5,
        0.05,
        19.0,
    )


def test_with_layout_removing_a_room_and_its_section_drops_their_state() -> None:
    a = warm()
    rooms = {k: v for k, v in ref_rooms().items() if k != "R3"}
    sections = {k: v for k, v in ref_sections().items() if k != "HS4"}
    b = a.with_layout(rooms, sections)
    assert "R3" not in b.rooms
    assert "HS4" not in b.history
    assert b.duty is None
    with pytest.raises(
        ValueError, match=re.escape("measured has unknown rooms ['R3']")
    ):
        b.update({"R1": 20.0, "R2": 20.0, "R3": 20.0})
    with pytest.raises(ValueError, match=re.escape("measured is missing rooms ['R3']")):
        a.update({"R1": 20.0, "R2": 20.0})


def test_with_layout_a_removed_then_readded_room_and_section_have_no_ghost_state() -> (
    None
):
    a = warm()
    rooms = {k: v for k, v in ref_rooms().items() if k != "R3"}
    sections = {k: v for k, v in ref_sections().items() if k != "HS4"}
    c = a.with_layout(rooms, sections).with_layout(ref_rooms(), ref_sections())
    assert c.rooms["R3"].integral == 0.0
    assert c.rooms["R3"].demand is None
    assert c.rooms["R3"].setpoint == 21.0
    assert c.rooms["R3"].kp == 0.3
    assert c.history["HS4"] == ()
    assert c.holds["HS4"] is None
    assert c.duty is None


def test_with_layout_a_section_keeps_its_state_after_its_room_is_removed() -> None:
    a = ref(history_length=4)
    a.hold("HS4", 0.0)
    a.update(meas(0.2, 0.3, 0.5))
    a.update(meas(0.2, 0.3, 0.5))
    rooms = {k: v for k, v in ref_rooms().items() if k != "R3"}
    sections = {
        "HS1": {"R1": 0.7},
        "HS2": {"R1": 0.3, "R2": 0.3},
        "HS3": {"R2": 0.4},
        "HS4": {"R2": 0.3},
    }
    b = a.with_layout(rooms, sections)
    assert b.history["HS4"] == (0.0, 0.0)
    assert b.holds["HS4"] == 0.0
    assert b.duty == a.duty


def test_with_layout_reordered_sections_give_no_duty_but_keep_state_by_name() -> None:
    a = warm()
    b = a.with_layout(sections=dict(reversed(list(ref_sections().items()))))
    assert b.duty is None
    assert b.holds == {s: a.holds[s] for s in b.holds}
    assert b.holds["HS2"] == 0.5
    assert b.history["HS1"] == a.history["HS1"]


def test_with_layout_room_names_match_exactly() -> None:
    a = warm()
    rooms = {("r1" if k == "R1" else k): v for k, v in ref_rooms().items()}
    sections = {
        "HS1": {"r1": 0.7},
        "HS2": {"r1": 0.3, "R2": 0.3},
        "HS3": {"R2": 0.7},
        "HS4": {"R3": 1.0},
    }
    b = a.with_layout(rooms, sections)
    assert b.rooms["r1"].kp == 0.3
    assert b.rooms["r1"].integral == 0.0
    assert b.rooms["r1"].demand is None
    assert b.rooms["R2"].kp == 1.0


# --- C5: refusals ------------------------------------------------------------


def uncovered() -> dict[str, dict[str, float]]:
    return {"HS1": {"R1": 0.7}}


REFUSALS: list[tuple[str, dict[str, object]]] = [
    ("sum_above_one", {"sections": {**ref_sections(), "HS5": {"R1": 0.5}}}),
    ("unknown_room", {"sections": {**ref_sections(), "HS5": {"R9": 1.0}}}),
    ("uncovered_room", {"sections": uncovered()}),
    ("bad_priority", {"rooms": ref_rooms(p1=0.0)}),
    ("bad_evenness", {"rooms": ref_rooms(e1=1.5)}),
    ("bool_history", {"history_length": True}),
    ("zero_history", {"history_length": 0}),
    ("float_history", {"history_length": 1.5}),
    ("empty_rooms", {"rooms": {}}),
    ("empty_sections", {"sections": {}}),
    (
        "rooms_without_r3_default_sections",
        {"rooms": {k: v for k, v in ref_rooms().items() if k != "R3"}},
    ),
    (
        "bad_layout_wins_over_bad_history",
        {"sections": {"HS1": {"R9": 1.0}}, "history_length": 0},
    ),
    ("history_false", {"history_length": False}),
]


@pytest.mark.parametrize(("label", "kwargs"), REFUSALS, ids=[r[0] for r in REFUSALS])
def test_with_layout_refuses_what_the_constructor_refuses_and_changes_nothing(
    label: str,  # noqa: ARG001 - parametrize id
    kwargs: dict[str, object],
) -> None:
    a = warm()
    twin = warm()
    before = full_state(a)
    args = {
        "rooms": ref_rooms(),
        "sections": ref_sections(),
        "history_length": a.history_length,
        "kp": 1.0,
        "ki": 0.0,
    }
    args.update(kwargs)
    with pytest.raises(Exception) as direct:  # noqa: PT011 - class compared below
        SectionAllocator(**args)  # type: ignore[arg-type]  # deliberate misuse
    with pytest.raises(type(direct.value)) as via_method:
        a.with_layout(**kwargs)  # type: ignore[arg-type]  # deliberate misuse
    assert str(via_method.value) == str(direct.value)
    assert full_state(a) == before
    assert run50(a) == run50(twin)


def test_with_layout_rooms_without_a_room_the_default_sections_use_names_sections_path() -> (
    None
):
    rooms = {k: v for k, v in ref_rooms().items() if k != "R3"}
    with pytest.raises(
        ValueError, match=re.escape("sections['HS4'] names unknown room 'R3'")
    ):
        ref().with_layout(rooms=rooms)


def test_with_layout_an_empty_mapping_is_not_treated_as_keep() -> None:
    with pytest.raises(ValueError, match="rooms must not be empty"):
        ref().with_layout(rooms={})
    with pytest.raises(ValueError, match="sections must not be empty"):
        ref().with_layout(sections={})


def test_with_layout_history_length_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        ref().with_layout(None, None, 3)  # type: ignore[call-arg]  # deliberate misuse


def test_with_layout_failed_call_leaves_duty_and_demands_and_next_commands_alone() -> (
    None
):
    a = warm()
    twin = warm()
    with pytest.raises(
        ValueError, match=re.escape("rooms ['R2', 'R3'] are covered by no section")
    ):
        a.with_layout(sections=uncovered())
    assert a.duty == twin.duty
    assert {n: r.demand for n, r in a.rooms.items()} == {
        n: r.demand for n, r in twin.rooms.items()
    }
    assert run50(a) == run50(twin)


# --- Interaction with update -------------------------------------------------


def test_with_layout_carried_duty_survives_a_failed_first_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = warm()
    b = a.with_layout(history_length=2)

    def boom(*_args: object, **_kwargs: object) -> dict[str, float]:
        raise ArithmeticError("solver")

    monkeypatch.setattr(b, "_allocate", boom)
    before = full_state(b)
    with pytest.raises(ArithmeticError):
        b.update(STEPS[0])
    assert full_state(b) == before
    assert b.duty == a.duty
    assert {n: r.demand for n, r in b.rooms.items()} == {
        n: r.demand for n, r in a.rooms.items()
    }


def test_with_layout_none_reading_after_a_coverage_change_reuses_the_carried_demand() -> (
    None
):
    a = warm()
    demand = a.rooms["R1"].demand
    b = a.with_layout(sections={**ref_sections(), "HS2": {"R1": 0.2, "R2": 0.3}})
    b.update({"R1": None, "R2": 20.0, "R3": 22.0})
    assert b.rooms["R1"].demand == demand
    assert b.rooms["R1"].integral == a.rooms["R1"].integral


# --- C6: subclass and exports ------------------------------------------------


class _Sub(SectionAllocator):
    pass


def test_with_layout_returns_the_subclass() -> None:
    a = _Sub(ref_rooms(), ref_sections(), kp=1.0, ki=0.0)
    assert type(a.with_layout()) is _Sub


def test_with_layout_does_not_mutate_the_callers_arguments() -> None:
    rooms, sections = edited_rooms(), edited_sections()
    rooms_copy, sections_copy = copy.deepcopy(rooms), copy.deepcopy(sections)
    warm().with_layout(rooms, sections, history_length=2)
    assert rooms == rooms_copy
    assert sections == sections_copy


def test_with_layout_docs_no_longer_claim_a_fixed_layout() -> None:
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    assert "with_layout" in readme
    assert "nothing is reset" not in readme
    assert "same order" in readme
    assert "fixed layout" not in (SectionAllocator.__doc__ or "")
    assert "fixed physical layout" not in (allocator_module.__doc__ or "")
