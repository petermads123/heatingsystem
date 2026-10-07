"""Tests for SectionAllocator and Room (heatingsystem.allocator)."""

import copy
import json
import math
import re
import sys
import tomllib
from collections import OrderedDict
from collections.abc import Callable, Mapping
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
# Helpers and the reference layout: R1 <- HS1 (1.0) + HS2 (0.5);
# R2 <- HS2 (0.5) + HS3 (1.0); R3 <- HS4 (1.0). kp=1, ki=0 makes a room's demand
# exactly clamp(setpoint - measured).
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
        "HS1": {"R1": 1.0},
        "HS2": {"R1": 0.5, "R2": 0.5},
        "HS3": {"R2": 1.0},
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


def delivered(a: SectionAllocator, room: str) -> float:
    duty = a.duty
    assert duty is not None
    return sum(
        shares.get(room, 0.0) * duty[name] for name, shares in a.sections.items()
    )


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


def with_share(
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
def test_construction_refuses_a_bad_share_naming_its_path(
    value: object, error: type[Exception]
) -> None:
    with pytest.raises(error, match=re.escape("sections['HS1']['R1']")):
        build(sections=with_share(value))


def test_construction_refuses_a_share_sum_above_one_naming_the_section() -> None:
    sections = ref_sections()
    sections["HS2"] = {"R1": 0.7, "R2": 0.3 + 1e-6}
    with pytest.raises(ValueError, match=re.escape("sections['HS2'] shares must sum")):
        build(sections=sections)


def test_construction_accepts_share_sums_that_are_one_up_to_float_error() -> None:
    sections = ref_sections()
    sections["HS2"] = {"R1": 0.1 * 3, "R2": 0.7}
    a = build(sections=sections)
    assert a.sections["HS2"] == {"R1": 0.1 * 3, "R2": 0.7}
    ten = {"HS1": {f"R{i}": 0.1 for i in range(10)}}
    build(
        rooms={f"R{i}": {"priority": 1, "evenness": 0} for i in range(10)}, sections=ten
    )
    thirds = {"HS1": {"R1": 1 / 3, "R2": 1 / 3, "R3": 1 / 3}}
    build(sections=thirds)


@pytest.mark.parametrize(("extra", "accepted"), [(1e-9, True), (2e-9, False)])
def test_share_sum_tolerance_boundary_is_one_nanounit(
    extra: float, accepted: bool
) -> None:
    sections = {"HS1": {"R1": 1.0, "R2": extra}, "HS2": {"R2": 1.0}}
    rooms = {k: ref_rooms()[k] for k in ("R1", "R2")}
    if accepted:
        build(rooms=rooms, sections=sections)
    else:
        with pytest.raises(ValueError, match=re.escape("sections['HS1'] shares must")):
            build(rooms=rooms, sections=sections)


def test_construction_refuses_a_section_naming_an_unknown_room() -> None:
    sections = ref_sections()
    sections["HS1"] = {"RX": 1.0}
    with pytest.raises(ValueError, match="'RX'"):
        build(sections=sections)


def test_construction_refuses_a_non_string_room_in_a_share_mapping() -> None:
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
    sections = {"HS1": {"R1": 5e-324}, "HS2": {"R1": 1.0}}
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
        "Stue/Køkken ☀": {"Stue/Køkken ☀": 1.0},
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


def test_error_order_priority_before_share_and_share_before_history_length() -> None:
    rooms = ref_rooms()
    rooms["R1"]["priority"] = 0.0
    with pytest.raises(ValueError, match=re.escape("rooms['R1'].priority")):
        build(rooms=rooms, sections=with_share(0.0))
    with pytest.raises(ValueError, match=re.escape("sections['HS1']['R1']")):
        build(sections=with_share(0.0), history_length=0)
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
    assert a.sections["HS1"]["R1"] == 1.0
    assert a.sections["HS2"]["R1"] == 0.5
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
    assert a.sections["HS2"]["R1"] == 0.5


def test_room_does_not_expose_the_pi_controller_surface() -> None:
    room = ref().rooms["R1"]
    for name in ("mode", "fixed_output", "update", "reset", "history", "pi_output"):
        assert not hasattr(room, name)


def test_to_dict_containers_are_fresh_and_do_not_reach_back() -> None:
    a = ref(history_length=4)
    a.update(meas(0.1, 0.6, 0.0))
    d = a.to_dict()
    d["sections"]["HS1"]["history"].append(1.0)  # type: ignore[index]  # object-typed snapshot
    d["sections"]["HS1"]["shares"]["R1"] = 0.1  # type: ignore[index]  # object-typed snapshot
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
        ({"R1": 20.0, "R2": 20.0, "R3": None}, TypeError, re.escape("measured['R3']")),
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
    sections = {"HS1": {"R1": 1.0}, "HS2": {"R1": 0.5}}
    a = SectionAllocator(one_room(priority, evenness), sections, kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.6})
    assert got["HS1"] == pytest.approx(0.4, abs=1e-9)
    assert got["HS2"] == pytest.approx(0.4, abs=1e-9)


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
    got = a._allocate({"R1": 0.1, "R2": 0.6, "R3": 0.0})
    assert got["HS1"] == pytest.approx(got["HS2"], abs=1e-6)
    assert got["HS1"] == pytest.approx(1 / 15, abs=1e-6)


# ---------------------------------------------------------------------------
# T5 / A4: evenness 0 meets feasible demands exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "demands",
    [
        (0.1, 0.6, 0.0),
        (0.3, 0.8, 0.5),
        (0.0, 0.0, 0.0),
        (0.9, 0.2, 1.0),
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
    sections = {
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
    sections[f"S{n - 1}"] = {f"R{n - 1}": 1.0}
    u = {name: 0.1 + 0.4 * ((i * 7) % 11) / 10 for i, name in enumerate(sections)}
    demand = {
        r: sum(shares.get(r, 0.0) * u[s] for s, shares in sections.items())
        for r in rooms
    }
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    got = a._allocate(demand)
    for room in rooms:
        d = sum(shares.get(room, 0.0) * got[s] for s, shares in sections.items())
        assert d == pytest.approx(demand[room], abs=1e-9)


@pytest.mark.parametrize(("demand", "duty"), [(0.0, 0.0), (0.3, 0.6), (0.6, 1.0)])
def test_closed_form_component_gives_min_of_one_and_demand_over_share(
    demand: float, duty: float
) -> None:
    a = SectionAllocator(one_room(), {"S": {"R1": 0.5}}, kp=1.0, ki=0.0)
    a.update({"R1": SETPOINT - demand})
    assert a.duty is not None
    assert a.duty["S"] == pytest.approx(duty, abs=1e-12)
    assert allocate(a, R1=demand)["S"] == min(1.0, demand / 0.5)


def test_closed_form_gives_exact_values_for_exact_demands() -> None:
    a = SectionAllocator(one_room(), {"S": {"R1": 0.5}}, kp=1.0, ki=0.0)
    assert allocate(a, R1=0.0) == {"S": 0.0}
    assert allocate(a, R1=0.25) == {"S": 0.5}
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
        one_room(), {"A": {"R1": 1.0}, "B": {"R1": 0.5}}, kp=1.0, ki=0.0
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
    third = 1 / 3
    sections = {"HS": {"R1": third, "R2": third, "R3": third}}
    weighted = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    got = weighted._allocate({"R1": 0.1, "R2": 0.2, "R3": 0.3})
    assert got["HS"] == pytest.approx(0.75, abs=1e-9)
    for room in rooms.values():
        room["priority"] = 1.0
    equal = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    assert equal._allocate({"R1": 0.1, "R2": 0.2, "R3": 0.3})["HS"] == pytest.approx(
        0.6, abs=1e-9
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


def test_tiny_shares_stay_finite_and_in_range() -> None:
    rooms = {k: ref_rooms()[k] for k in ("R1", "R2")}
    sections = {"HS1": {"R1": 1e-300, "R2": 1e-300}, "HS2": {"R1": 1.0}}
    # R2's only section is HS1, whose share is 1e-300: R2's demand is unreachable.
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.5, "R2": 0.5})
    assert all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in got.values())
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
    sections = {"HS1": {"R1": 1.0}, "HS2": {"R1": 0.5}}
    a = SectionAllocator(one_room(priority, evenness), sections, kp=1.0, ki=0.0)
    got = a._allocate({"R1": 0.6})
    assert got["HS1"] == pytest.approx(0.4, abs=1e-9)
    assert got["HS2"] == pytest.approx(0.4, abs=1e-9)


@pytest.mark.parametrize("evenness", [0.0, 1.0, math.nextafter(1.0, 0.0), 5e-324])
def test_evenness_of_a_single_section_room_is_inert(evenness: float) -> None:
    rooms = ref_rooms(e2=evenness)
    sections = {"HS1": {"R1": 1.0}, "HS2": {"R1": 0.5, "R2": 0.5}}
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
    assert a.duty["HS1"] == pytest.approx(2 / 3, abs=1e-9)
    assert mismatch(a, "R1", 0.2) == pytest.approx(1 / 3 - 0.2, abs=1e-9)
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
    sections = {"HS1": {"R1": 0.5, "R2": 0.5}, "HS2": {"R1": 0.5, "R3": 0.5}}
    a = SectionAllocator(rooms, sections, kp=1.0, ki=0.0)
    a.update({"R1": 20.5, "R2": 20.0, "R3": 21.0})  # demands 0.5 / 1.0 / 0.0
    return a


def room_cost(
    a: SectionAllocator, u: Mapping[str, float], room: str, demand: float
) -> float:
    """A5's per-room quantity: |d - h|^2 + evenness * spread (no priority factor)."""
    serving = [s for s, shares in a.sections.items() if room in shares]
    heat = sum(a.sections[s][room] * u[s] for s in serving)
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
    a = shared_only(0.5, 1.0)
    a.update({"R1": SETPOINT - 0.0, "R2": SETPOINT - 1.0})
    assert a.duty == {"HS1": 1.0}
    assert mismatch(a, "R1", 0.0) == pytest.approx(mismatch(a, "R2", 1.0), abs=1e-9)


def test_documented_exception_unequal_shares_favour_the_larger_share() -> None:
    rooms = {
        "R1": {"priority": 1.0, "evenness": 0.0},
        "R2": {"priority": 0.5, "evenness": 0.0},
    }
    a = SectionAllocator(rooms, {"HS1": {"R1": 0.2, "R2": 0.8}}, kp=1.0, ki=0.0)
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
        "HS1": {"R1": 1.0},
        "HS2": {"R1": 0.5, "R2": 0.5},
        "HS3": {"R1": 0.5},
        "HS4": {"R2": 1.0},
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
    # equalise at demand / (sum of shares) = 0.3 / 1.5 = 0.2, spread 0.
    sections = {"A": {"R1": 0.5}, "B": {"R1": 0.5}, "C": {"R1": 0.5}}
    strong = SectionAllocator(one_room(1.0, 1.0), sections, kp=1.0, ki=0.0)
    got = strong._allocate({"R1": 0.3})
    assert list(got.values()) == pytest.approx([0.2, 0.2, 0.2], abs=1e-9)
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
    assert even.duty["HS1"] == pytest.approx(1 / 15, abs=1e-9)
    assert even.duty["HS2"] == pytest.approx(1 / 15, abs=1e-9)
    assert even.duty["HS3"] == pytest.approx(17 / 30, abs=1e-9)
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
    assert a.duty["HS1"] == pytest.approx(0.067, abs=1e-3)
    assert a.duty["HS2"] == pytest.approx(0.233, abs=1e-3)
    assert a.duty["HS3"] == pytest.approx(0.400, abs=1e-3)
    assert delivered(a, "R2") == pytest.approx(0.517, abs=1e-3)
    assert delivered(a, "R2") < 0.6 - 0.05
    assert delivered(a, "R1") == pytest.approx(0.183, abs=1e-3)
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


def test_equivalence_needs_a_dedicated_share_of_one() -> None:
    """With a dedicated share of 0.5 the section runs twice the demand."""
    half = SectionAllocator(
        one_room(), {"HS1": {"R1": 0.5}}, kp=1.0, ki=0.0, history_length=24
    )
    full = SectionAllocator(
        one_room(), {"HS1": {"R1": 1.0}}, kp=1.0, ki=0.0, history_length=24
    )
    for _ in range(24):
        half.update({"R1": SETPOINT - 0.3})
        full.update({"R1": SETPOINT - 0.3})
    assert sum(half.history["HS1"]) > sum(full.history["HS1"])


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
    assert list(sections["HS2"]) == ["shares", "history"]
    assert sections["HS2"]["shares"] == {"R1": 0.5, "R2": 0.5}
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
            _mutated(["sections", "HS1", "shares"], []),
            TypeError,
            re.escape("sections['HS1']['shares'] must be a mapping"),
        ),
        (
            _mutated(["sections", "HS1", "shares"], {"R1": 0}),
            ValueError,
            re.escape("sections['HS1']['R1']"),
        ),
        (
            _mutated(["sections", "HS1", "shares"], {"RX": 1.0}),
            ValueError,
            "'RX'",
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
    data = _mutated(["sections", "HS2", "shares"], {"R1": 0.9, "R2": 0.9})
    with pytest.raises(ValueError, match=re.escape("sections['HS2'] shares must sum")):
        SectionAllocator.from_dict(data)


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
        serving = [s for s, shares in a.sections.items() if name in shares]
        heat = sum(a.sections[s][name] * u[s] for s in serving)
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
        serving = [s for s, shares in a.sections.items() if name in shares]
        heat = sum(a.sections[s][name] * u[s] for s in serving)
        mean = sum(u[s] for s in serving) / len(serving)
        for s in serving:
            grad[s] += (
                -2 * (room.priority / top) * (demand[name] - heat) * a.sections[s][name]
            )
            if len(serving) > 1:
                grad[s] += 2 * (room.priority / top) * room.evenness * (u[s] - mean)
    return grad


A12_CASES = {
    # name: (demands R1/R2, p1, p2, e1, e2, (HS1, HS2, HS3))
    "B": ((0.1, 0.6), 1.0, 1.0, 0.1, 0.0, (1 / 15, 1 / 15, 17 / 30)),
    "C": ((0.1, 0.6), 1.0, 1.0, 1.0, 1.0, (1 / 15, 7 / 30, 0.4)),
    "D": ((0.1, 0.6), 1.0, 1.0, 1.0, 0.1, (0.067, 0.108, 0.525)),
    "E": ((0.1, 0.6), 0.3, 1.0, 1.0, 1.0, (0.067, 0.323, 0.400)),
    "G": ((0.3, 1.0), 1.0, 1.0, 0.1, 0.0, (0.2, 0.2, 0.9)),
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
        if any(sum(shares.values()) > 1.0 for shares in sections.values()):
            continue
        if not all(any(r in shares for shares in sections.values()) for r in names):
            continue
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
    assert a.duty["HS1"] == pytest.approx(0.56, abs=1e-9)
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
# main(): the showcase runs
# ---------------------------------------------------------------------------


def test_main_runs_and_reports_each_section(capsys: pytest.CaptureFixture[str]) -> None:
    allocator_module.main()
    out = capsys.readouterr().out
    assert "Hungry R2" in out
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
