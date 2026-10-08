"""Multi-room control of on/off heating sections that may serve several rooms.

This module provides :class:`SectionAllocator`, built once from a fixed
physical layout: named rooms (each with a priority and an evenness weight)
and named sections (each with the *coverage* it gives each room it serves:
the fraction of that room's floor heating the section provides). A room's
coverages sum to at most 1; the uncovered remainder is an outside
disturbance, like the weather. Internally every room's coverages are
normalised by its controlled total, so a demand of 1.0 means "every loop of
this room fully on". Every :meth:`SectionAllocator.update` works in three
stages:

1. **Room demand.** Each :class:`Room` runs the existing PI control law (a
   composed radiator-mode
   :class:`~heatingsystem.pi_controller.pi_controller.PIController`) and
   yields a demand in ``[0, 1]``.
2. **Allocation.** Section duty cycles in ``[0, 1]`` are chosen by bounded
   weighted least squares, minimising exactly
   ``J(u) = sum_r p_r * (d_r - h_r)**2 + sum_r p_r * e_r * spread_r``: the
   priority-weighted squared mismatch between each room's demand ``d_r`` and
   the heat ``h_r`` it receives (the sum of normalised coverage times duty
   over its sections), plus, per room, priority times evenness times the *spread* of
   duty among the sections serving it, ``sum((u_s - mean)**2)``. Priorities
   lie in ``(0, 1]`` (only their ratios matter) and evenness weights in
   ``[0, 1]``.
   Rooms and sections that share no variable are solved separately; a
   component of one room and one section has a closed form. A section that
   is *held* (:meth:`SectionAllocator.hold`) is a constant in the problem:
   its contribution is subtracted from each room's demand, its column
   leaves the matrix, and a component whose sections are all held calls no
   solver.
3. **Actuation.** Each section owns a floor-heating
   :class:`~heatingsystem.modulator.modulator.Modulator` that turns its
   allocated duty into 0.0 / 1.0 commands over a rolling window.

See :mod:`heatingsystem._validation` for the numeric contract behind every
number accepted here.
"""

import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

import numpy as np
from scipy.optimize import lsq_linear

from heatingsystem import _validation
from heatingsystem.modulator.modulator import OUTPUT_MAX, OUTPUT_MIN, Modulator
from heatingsystem.pi_controller.pi_controller import PIController

# Tolerance on a room's coverage sum, so 0.1-style float sums that should be
# exactly 1.0 are accepted.
_COVERAGE_SUM_TOLERANCE: float = 1e-9

# Smallest weight, relative to the largest priority in a component, that still
# reaches the solver. A safety net: it applies to any row weight (a priority,
# or priority times evenness) below 1e-12 of the largest priority, so a
# priority more than 1e12 below the largest, or a priority-times-evenness
# product that small, acts as 1e-12 (about 1e-12 of change in J).
# Dividing by the largest weight is exact in real arithmetic, but a
# row weighted below about 1e-15 of the rest is lost to round-off (and one below
# 1e-300 underflows to zero), so a feasible demand of a very low-priority room
# would silently stop being met. Flooring the ratio keeps every row in play; it
# changes the minimiser only where a weight is more than 1e12 below the largest.
_MIN_WEIGHT_RATIO: float = 1e-12

# bvls stops at max_iter == number of variables by default, which it can reach
# on a converged problem and report as status 0; allow generous headroom.
_SOLVER_ITERATIONS_PER_SECTION: int = 20

_ROOM_KEYS: frozenset[str] = frozenset({"priority", "evenness"})
_SNAPSHOT_KEYS: frozenset[str] = frozenset({"history_length", "rooms", "sections"})
_ROOM_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {"priority", "evenness", "setpoint", "kp", "ki", "integral"}
)
_SECTION_SNAPSHOT_KEYS: frozenset[str] = frozenset({"coverage", "history", "hold"})


def _reraise(prefix: str, exc: Exception) -> Exception:
    """Build the same exception class with a path prefixed to its message.

    Args:
        prefix: The path to put in front of the message.
        exc: The original exception.

    Returns:
        A new exception of the same class, to be raised ``from`` the
        original.
    """
    return type(exc)(f"{prefix}{exc}")


def _name(where: str, value: object) -> str:
    """Validate a room or section name.

    Args:
        where: What the name is for, used in the error message.
        value: The candidate name.

    Returns:
        ``value``, unchanged.

    Raises:
        TypeError: If ``value`` is not a ``str``.
        ValueError: If ``value`` is empty.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"{where} names must be str, got {value!r} ({type(value).__name__})."
        )
    if not value:
        raise ValueError(f"{where} names must be non-empty, got {value!r}.")
    return value


def _row_weight(weight: float, scale: float) -> float:
    """Square-root row weight of ``weight`` relative to the component's ``scale``.

    Args:
        weight: A row weight: a priority, or priority times evenness.
        scale: The largest priority in the component.

    Returns:
        ``sqrt(max(weight / scale, _MIN_WEIGHT_RATIO))``, so no row vanishes.
    """
    return math.sqrt(max(weight / scale, _MIN_WEIGHT_RATIO))


def _mapping(name: str, value: object) -> Mapping[str, object]:
    """Validate a value as a ``Mapping``.

    Args:
        name: The path of the value, used in the error message.
        value: The candidate.

    Returns:
        ``value``, unchanged.

    Raises:
        TypeError: If ``value`` is not a ``Mapping``.
    """
    if not isinstance(value, Mapping):
        raise TypeError(
            f"{name} must be a mapping, got {value!r} ({type(value).__name__})."
        )
    return value


class Room:
    """One room of a :class:`SectionAllocator`: fixed weights, adjustable PI.

    A handle type built by :class:`SectionAllocator`; constructing one
    directly is internal. It composes a radiator-mode ``PIController`` (one
    slot of history), whose command is the clamped PI demand, so the control
    law, anti-windup and every setter's validation are reused unchanged.

    Args:
        name: The room's name; a non-empty ``str``.
        priority: Weight of this room's mismatch in the allocation, in
            ``(0, 1]``; 1 is the most important, and only ratios between
            rooms matter.
        evenness: How much this room's evenness of duty among its sections
            matters, in ``[0, 1]``; 0 ignores it, 1 makes an uneven floor
            cost as much as the same-sized temperature miss. It is applied
            as priority times evenness.
        kp: Proportional gain.
        ki: Integral gain.
        setpoint: Target temperature in degrees C.

    Raises:
        TypeError: If ``name`` is not a ``str``, or a number is not a real
            number or is a ``bool``.
        ValueError: If ``name`` is empty, ``priority`` is outside
            ``(0, 1]``, ``evenness`` is outside ``[0, 1]``, or a number is
            not finite.
        OverflowError: If a number is too large to represent as a float.
    """

    def __init__(
        self,
        name: str,
        *,
        priority: float,
        evenness: float,
        kp: float,
        ki: float,
        setpoint: float,
    ) -> None:
        """Initialise the room; see the class docstring for the arguments."""
        if not isinstance(name, str):
            raise TypeError(
                f"name must be a str, got {name!r} ({type(name).__name__})."
            )
        if not name:
            raise ValueError(f"name must be non-empty, got {name!r}.")
        weight = _validation.finite("priority", priority)
        if not 0.0 < weight <= 1.0:
            raise ValueError(f"priority must be in (0, 1], got {priority!r}.")
        spread_weight = _validation.finite("evenness", evenness)
        if not 0.0 <= spread_weight <= 1.0:
            raise ValueError(f"evenness must be in [0, 1], got {evenness!r}.")

        self._name = name
        self._priority = weight
        self._evenness = spread_weight
        self._pi = PIController(kp, ki, "radiator", setpoint, history_length=1)

    @property
    def name(self) -> str:
        """The room's name."""
        return self._name

    @property
    def priority(self) -> float:
        """The room's fixed priority weight, in ``(0, 1]``."""
        return self._priority

    @property
    def evenness(self) -> float:
        """The room's fixed evenness weight, in ``[0, 1]``."""
        return self._evenness

    @property
    def setpoint(self) -> float:
        """Target temperature in degrees C (settable, like ``PIController``)."""
        return self._pi.setpoint

    @setpoint.setter
    def setpoint(self, value: float) -> None:
        """Set the setpoint; same contract as ``PIController.setpoint``."""
        self._pi.setpoint = value

    @property
    def kp(self) -> float:
        """Proportional gain (settable, like ``PIController``)."""
        return self._pi.kp

    @kp.setter
    def kp(self, value: float) -> None:
        """Set the proportional gain; same contract as ``PIController.kp``."""
        self._pi.kp = value

    @property
    def ki(self) -> float:
        """Integral gain (settable, like ``PIController``)."""
        return self._pi.ki

    @ki.setter
    def ki(self, value: float) -> None:
        """Set the integral gain; same contract as ``PIController.ki``."""
        self._pi.ki = value

    @property
    def integral(self) -> float:
        """The PI integral accumulator (read-only)."""
        return self._pi.integral

    @property
    def demand(self) -> float | None:
        """The clamped PI demand of the last PI step, or ``None`` before one.

        A ``None`` reading in :meth:`SectionAllocator.update` is not a PI
        step, so it leaves this value unchanged.
        """
        return self._pi.pi_output

    def _step(self, measured: float) -> float:
        """Run one PI step and return the clamped demand.

        Args:
            measured: The room temperature in degrees C.

        Returns:
            The demand in ``[OUTPUT_MIN, OUTPUT_MAX]``.
        """
        return self._pi.update(measured)


class _Component:
    """A connected group of rooms and sections that share allocation variables.

    Attributes:
        rooms: Room names in the component, in constructor order.
        sections: Section names in the component, in constructor order.
        matrix: The stacked least-squares matrix for a multi-variable
            component, or ``None`` for the closed-form one-room,
            one-section case.
        scale: The largest priority in the component (a spread weight,
            priority times evenness, never exceeds it), which the matrix
            rows and the demand vector are divided by.
    """

    def __init__(
        self,
        rooms: list[str],
        sections: list[str],
        matrix: np.ndarray | None,
        scale: float,
    ) -> None:
        """Store the precomputed component."""
        self.rooms = rooms
        self.sections = sections
        self.matrix = matrix
        self.scale = scale


class SectionAllocator:
    """Controller for on/off heating sections that may each serve several rooms.

    Built once from a fixed layout; the layout (rooms, priorities, evenness
    weights, coverages) is read-only. Each room's ``setpoint``, ``kp`` and
    ``ki`` can be changed at any time through :attr:`rooms` and take effect
    on the next :meth:`update`.

    Args:
        rooms: Maps each room name to a mapping with exactly the keys
            ``"priority"`` (in ``(0, 1]``) and ``"evenness"`` (in
            ``[0, 1]``).
        sections: Maps each section name to ``{room name: coverage}``: the
            fraction of that room's floor heating the section provides.
            Every coverage is in ``(0, 1]``, each room's coverages sum to at
            most 1 (the rest is an outside disturbance), every room is
            covered by at least one section, and a section may cover
            several rooms with any sum.
        kp: Initial proportional gain of every room.
        ki: Initial integral gain of every room.
        setpoint: Initial setpoint of every room in degrees C.
        history_length: Rolling command-window length of every section; an
            ``int`` of at least 1.

    Raises:
        TypeError: If a mapping is not a ``Mapping``, a name is not a
            ``str``, or a number is not a real number or is a ``bool``.
        ValueError: If a mapping is empty or has the wrong keys, a name is
            empty, a section names an unknown room, a coverage is
            outside ``(0, 1]``, a room's coverages sum above 1 by more than
            1e-9, a room is covered by no section, a priority is outside ``(0, 1]``,
            an evenness is outside ``[0, 1]``, or a number is not finite or out of range.
        OverflowError: If a number is too large to represent as a float.
            Every error names the offending path, such as
            ``sections['HS1']['R1']``.
    """

    def __init__(
        self,
        rooms: Mapping[str, Mapping[str, float]],
        sections: Mapping[str, Mapping[str, float]],
        *,
        kp: float = 0.3,
        ki: float = 0.015,
        setpoint: float = 21.0,
        history_length: int = 24,
    ) -> None:
        """Initialise the allocator; see the class docstring for the arguments."""
        # Shared gains and setpoint are validated first, so a bad one is named
        # as the argument the caller passed rather than as one room's attribute.
        PIController(kp, ki, "radiator", setpoint, history_length=1)
        built_rooms = self._build_rooms(rooms, kp, ki, setpoint)
        coverage = self._build_coverage(sections, built_rooms)

        uncovered = sorted(
            name
            for name in built_rooms
            if not any(name in section for section in coverage.values())
        )
        if uncovered:
            raise ValueError(f"rooms {uncovered} are covered by no section.")
        normalised = self._normalise(coverage, built_rooms)

        length = _validation.window_length(history_length)

        self._rooms: dict[str, Room] = built_rooms
        self._coverage: dict[str, dict[str, float]] = coverage
        self._normalised: dict[str, dict[str, float]] = normalised
        self._history_length: int = length
        self._modulators: dict[str, Modulator] = {
            name: Modulator("floor_heating", history_length=length) for name in coverage
        }
        self._components: list[_Component] = self._split_components()
        self._duty: dict[str, float] | None = None

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_rooms(
        rooms: object, kp: float, ki: float, setpoint: float
    ) -> dict[str, Room]:
        """Validate the ``rooms`` argument and build every :class:`Room`.

        Args:
            rooms: The candidate ``rooms`` mapping.
            kp: Initial proportional gain.
            ki: Initial integral gain.
            setpoint: Initial setpoint.

        Returns:
            The rooms by name, in the mapping's order.

        Raises:
            TypeError: If ``rooms`` or a room spec is not a ``Mapping``, or a
                name is not a ``str``.
            ValueError: If ``rooms`` is empty, a name is empty, or a spec
                has missing or unknown keys, naming the path.
        """
        specs = _mapping("rooms", rooms)
        if not specs:
            raise ValueError("rooms must not be empty.")
        built: dict[str, Room] = {}
        for name, spec in specs.items():
            _name("rooms", name)
            path = f"rooms[{name!r}]"
            try:
                checked = _validation.snapshot_mapping(_mapping(path, spec), _ROOM_KEYS)
            except (TypeError, ValueError) as exc:
                if str(exc).startswith(path):
                    raise
                raise _reraise(f"{path}: ", exc) from exc
            try:
                built[name] = Room(
                    name,
                    priority=checked["priority"],  # type: ignore[arg-type]  # Room validates any object
                    evenness=checked["evenness"],  # type: ignore[arg-type]  # Room validates any object
                    kp=kp,
                    ki=ki,
                    setpoint=setpoint,
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise _reraise(f"{path}.", exc) from exc
        return built

    @staticmethod
    def _build_coverage(
        sections: object, rooms: Mapping[str, Room]
    ) -> dict[str, dict[str, float]]:
        """Validate the ``sections`` argument, entry by entry.

        Args:
            sections: The candidate ``sections`` mapping.
            rooms: The already-built rooms, to check names against.

        Returns:
            The coverages by section and room name, as floats, in order.

        Raises:
            TypeError: If ``sections`` or a section is not a ``Mapping``, a
                name is not a ``str``, or a coverage is not a real number.
            ValueError: If ``sections`` or a section is empty, a name is
                empty, a section names an unknown room, or a coverage is
                outside ``(0, 1]``.
            OverflowError: If a coverage is too large to represent as a float.
        """
        specs = _mapping("sections", sections)
        if not specs:
            raise ValueError("sections must not be empty.")
        result: dict[str, dict[str, float]] = {}
        for name, spec in specs.items():
            _name("sections", name)
            path = f"sections[{name!r}]"
            covered = _mapping(path, spec)
            if not covered:
                raise ValueError(f"{path} must cover at least one room.")
            section: dict[str, float] = {}
            for room_name, raw in covered.items():
                if room_name not in rooms:
                    raise ValueError(
                        f"{path} names unknown room {room_name!r}; "
                        f"known rooms are {list(rooms)}."
                    )
                cover_path = f"{path}[{room_name!r}]"
                cover = _validation.finite(cover_path, raw)
                if not 0.0 < cover <= 1.0:
                    raise ValueError(f"{cover_path} must be in (0, 1], got {raw!r}.")
                section[room_name] = cover
            result[name] = section
        return result

    @staticmethod
    def _normalise(
        coverage: Mapping[str, Mapping[str, float]], rooms: Mapping[str, Room]
    ) -> dict[str, dict[str, float]]:
        """Check each room's coverage sum and normalise by it.

        Args:
            coverage: The validated coverages by section and room name.
            rooms: The rooms, in the order the sums are checked.

        Returns:
            The coverages divided by each room's total, by section and room.

        Raises:
            ValueError: If a room's coverages sum above 1 by more than 1e-9,
                naming the room and the sum.
        """
        totals: dict[str, float] = {}
        for room in rooms:
            total = math.fsum(c[room] for c in coverage.values() if room in c)
            if total > 1.0 + _COVERAGE_SUM_TOLERANCE:
                raise ValueError(
                    f"rooms[{room!r}] coverages sum to {total}, more than 1."
                )
            totals[room] = total
        return {
            name: {room: c / totals[room] for room, c in covered.items()}
            for name, covered in coverage.items()
        }

    def _split_components(self) -> list[_Component]:
        """Split the room-section graph into connected components.

        Returns:
            One precomputed :class:`_Component` per connected group, ordered
            by the first room of each.
        """
        parent: dict[str, str] = {}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        room_nodes = {name: f"room:{name}" for name in self._rooms}
        section_nodes = {name: f"section:{name}" for name in self._coverage}
        for node in [*room_nodes.values(), *section_nodes.values()]:
            parent[node] = node
        for section_name, covered in self._coverage.items():
            for room_name in covered:
                parent[find(room_nodes[room_name])] = find(section_nodes[section_name])

        groups: dict[str, tuple[list[str], list[str]]] = {}
        for room_name, node in room_nodes.items():
            groups.setdefault(find(node), ([], []))[0].append(room_name)
        for section_name, node in section_nodes.items():
            groups[find(node)][1].append(section_name)

        return [self._component(rooms, sections) for rooms, sections in groups.values()]

    def _component(self, rooms: list[str], sections: list[str]) -> _Component:
        """Precompute one component's closed form or stacked matrix.

        The matrix covers every section of the component, held or not; the
        held columns are split off at solve time.

        Args:
            rooms: The component's room names.
            sections: The component's section names.

        Returns:
            The component. Priorities and the spread weights (priority times
            evenness) are divided by the largest priority before their square
            roots are taken, which leaves the minimiser unchanged and cannot
            overflow.
        """
        if len(rooms) == 1 and len(sections) == 1:
            return _Component(rooms, sections, None, 1.0)

        column = {name: i for i, name in enumerate(sections)}
        serving = {
            room: [s for s in sections if room in self._coverage[s]] for room in rooms
        }
        scale = max(self._rooms[r].priority for r in rooms)

        rows: list[np.ndarray] = []
        for room in rooms:
            row = np.zeros(len(sections))
            weight = _row_weight(self._rooms[room].priority, scale)
            for s in serving[room]:
                row[column[s]] = weight * self._normalised[s][room]
            rows.append(row)
        for room in rooms:
            covering = serving[room]
            evenness = self._rooms[room].evenness
            if len(covering) < 2 or evenness <= 0.0:
                continue
            # Ratio first: priority * evenness underflows for subnormal priorities.
            weight = _row_weight((self._rooms[room].priority / scale) * evenness, 1.0)
            for s in covering:
                row = np.zeros(len(sections))
                for other in covering:
                    row[column[other]] -= weight / len(covering)
                row[column[s]] += weight
                rows.append(row)
        return _Component(rooms, sections, np.vstack(rows), scale)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self, measured: Mapping[str, float | None]) -> dict[str, float]:
        """Run one control step and return a command for every section.

        Every temperature is validated before any state changes. If the
        allocation fails, every room's integral and demand are restored and
        the error propagates; no command is issued and no duty is stored.
        Holds are never written by an update. A held section's duty is its
        level whatever the demand, its command is the floor-heating
        modulation of that level (exactly the level for 0.0 and 1.0), and
        the free sections compensate for its contribution. Every room's PI
        keeps stepping during a hold, so its integral can wind up while a
        section is held and cause a demand burst after the release.

        A room whose temperature is ``None`` has no reading this step: its
        PI takes no step (integral and demand untouched), and the allocation
        uses its last demand, or 0.0 if it has never had a reading. Every
        other room steps normally and every section still gets a command.

        Args:
            measured: The temperature in degrees C of every room, keyed by
                room name. ``None`` means no reading for that room; the key
                itself must still be present.

        Returns:
            A fresh dict ``{section: 0.0 or 1.0}`` in the constructor's
            section order.

        Raises:
            TypeError: If ``measured`` is not a ``Mapping``, or a
                temperature is neither ``None`` nor a real number, or is a
                ``bool``, naming ``measured['R1']``.
            ValueError: If a room is missing or unknown (missing named
                first), or a temperature is not finite.
            OverflowError: If a temperature is too large to represent as a
                float.
            ArithmeticError: If the solver returns a non-finite duty or
                does not converge.
        """
        values = _mapping("measured", measured)
        missing = [name for name in self._rooms if name not in values]
        if missing:
            raise ValueError(f"measured is missing rooms {missing}.")
        unknown = sorted((k for k in values if k not in self._rooms), key=repr)
        if unknown:
            raise ValueError(f"measured has unknown rooms {unknown}.")
        temperatures: dict[str, float | None] = {
            name: (
                None
                if values[name] is None
                else _validation.finite(f"measured[{name!r}]", values[name])
            )
            for name in self._rooms
        }

        saved = {
            name: (room._pi._integral, room._pi._pi_output)
            for name, room in self._rooms.items()
        }
        try:
            demand: dict[str, float] = {}
            for name, room in self._rooms.items():
                reading = temperatures[name]
                if reading is not None:
                    demand[name] = room._step(reading)
                elif room.demand is not None:
                    demand[name] = room.demand
                else:
                    demand[name] = 0.0
            duty = self._allocate(demand)
        except BaseException:
            for name, (integral, pi_output) in saved.items():
                self._rooms[name]._pi._integral = integral
                self._rooms[name]._pi._pi_output = pi_output
            raise

        commands = {
            name: modulator.command(duty[name])
            for name, modulator in self._modulators.items()
        }
        self._duty = duty
        return commands

    @property
    def rooms(self) -> Mapping[str, Room]:
        """Read-only view of the :class:`Room` handles, in constructor order."""
        return MappingProxyType(self._rooms)

    @property
    def sections(self) -> dict[str, dict[str, float]]:
        """A fresh deep copy of the coverages as given (not normalised)."""
        return {name: dict(covered) for name, covered in self._coverage.items()}

    @property
    def holds(self) -> dict[str, float | None]:
        """A fresh dict of every section's held level, or ``None`` if free."""
        return {name: m.fixed_output for name, m in self._modulators.items()}

    def hold(self, section: str, level: float | None) -> None:
        """Hold a section at a fixed level, or release it with ``None``.

        The hold takes effect on the next :meth:`update`; it leaves ``duty``
        and ``history`` untouched. Nothing changes if the call raises.

        Args:
            section: The name of a section of the layout.
            level: The level in ``[0, 1]`` to hold the section at (validated
                like ``Modulator.fixed_output``), or ``None`` to release it.

        Raises:
            TypeError: If ``section`` is not a ``str``, or ``level`` is not a
                real number or is a ``bool``, naming ``holds['HS3']``.
            ValueError: If ``section`` is not a section of the layout, or
                ``level`` is not finite or outside ``[0, 1]``.
            OverflowError: If ``level`` is too large to represent as a float.
        """
        if not isinstance(section, str):
            raise TypeError(
                f"section must be a str, got {section!r} ({type(section).__name__})."
            )
        if section not in self._modulators:
            raise ValueError(
                f"unknown section {section!r}; "
                f"known sections are {list(self._modulators)}."
            )
        checked: float | None = None
        if level is not None:
            checked = _validation.level(
                f"holds[{section!r}]", level, OUTPUT_MIN, OUTPUT_MAX
            )
        self._modulators[section].fixed_output = checked

    @property
    def history_length(self) -> int:
        """The rolling command-window length of every section (read-only)."""
        return self._history_length

    @property
    def duty(self) -> dict[str, float] | None:
        """The last allocated duty per section, or ``None`` before an update."""
        return None if self._duty is None else dict(self._duty)

    @property
    def history(self) -> dict[str, tuple[float, ...]]:
        """Each section's command window, oldest first."""
        return {name: m.history for name, m in self._modulators.items()}

    def to_dict(self) -> dict[str, object]:
        """Capture the layout, settings and running state as a snapshot.

        Returns:
            A fresh dict of ``json.dumps``-ready built-in types with the
            keys ``history_length``, ``rooms`` (per room: ``priority``,
            ``evenness``, ``setpoint``, ``kp``, ``ki``, ``integral``) and
            ``sections`` (per section: ``coverage`` as given, ``history`` and
            ``hold``, a level or ``None``), in constructor order.
        """
        return {
            "history_length": self._history_length,
            "rooms": {
                name: {
                    "priority": room.priority,
                    "evenness": room.evenness,
                    "setpoint": room.setpoint,
                    "kp": room.kp,
                    "ki": room.ki,
                    "integral": room.integral,
                }
                for name, room in self._rooms.items()
            },
            "sections": {
                name: {
                    "coverage": dict(self._coverage[name]),
                    "history": list(modulator.history),
                    "hold": modulator.fixed_output,
                }
                for name, modulator in self._modulators.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Rebuild an allocator from a :meth:`to_dict` snapshot.

        The layout goes through the constructor, so it raises what the
        constructor raises, naming the constructor's path
        (``sections['HS1']['R1']`` for a bad coverage). Settings, section holds
        and windows go through the same setters a direct assignment uses; their
        errors keep their class with the snapshot path prefixed, such as
        ``rooms['R1'].integral`` or ``sections['HS1'].hold``. A 1.0.0
        snapshot (sections with ``shares``) is refused for its missing keys.

        Args:
            data: A mapping with exactly the keys ``to_dict`` produces.

        Returns:
            A new allocator producing the same commands as the source from
            here on; ``duty`` and every room's ``demand`` are ``None``.

        Raises:
            TypeError: If ``data`` or a nested mapping is not a ``Mapping``,
                or a value has the wrong type.
            ValueError: If a key is missing or unknown at any level (missing
                named first), or a value is invalid.
            OverflowError: If a number is too large to represent as a float.
        """
        top = _validation.snapshot_mapping(data, _SNAPSHOT_KEYS)
        length = _validation.window_length(top["history_length"])
        rooms = _mapping("rooms", top["rooms"])
        sections = _mapping("sections", top["sections"])

        room_specs: dict[str, Mapping[str, object]] = {}
        for name, spec in rooms.items():
            path = f"rooms[{_name('rooms', name)!r}]"
            try:
                room_specs[name] = _validation.snapshot_mapping(
                    _mapping(path, spec), _ROOM_SNAPSHOT_KEYS
                )
            except (TypeError, ValueError) as exc:
                if str(exc).startswith(path):
                    raise
                raise _reraise(f"{path}: ", exc) from exc
        section_specs: dict[str, Mapping[str, object]] = {}
        for name, spec in sections.items():
            path = f"sections[{_name('sections', name)!r}]"
            try:
                section_specs[name] = _validation.snapshot_mapping(
                    _mapping(path, spec), _SECTION_SNAPSHOT_KEYS
                )
            except (TypeError, ValueError) as exc:
                if str(exc).startswith(path):
                    raise
                raise _reraise(f"{path}: ", exc) from exc

        layout_rooms: dict[str, dict[str, object]] = {
            name: {"priority": spec["priority"], "evenness": spec["evenness"]}
            for name, spec in room_specs.items()
        }
        layout_sections = {
            name: _mapping(f"sections[{name!r}]['coverage']", spec["coverage"])
            for name, spec in section_specs.items()
        }
        allocator = cls(
            layout_rooms,  # type: ignore[arg-type]  # the constructor validates every value
            layout_sections,  # type: ignore[arg-type]  # the constructor validates every value
            history_length=length,
        )

        for name, spec in room_specs.items():
            room = allocator._rooms[name]
            try:
                room.setpoint = spec["setpoint"]  # type: ignore[assignment]  # the setter validates any object
                room.kp = spec["kp"]  # type: ignore[assignment]  # the setter validates any object
                room.ki = spec["ki"]  # type: ignore[assignment]  # the setter validates any object
                room._pi.integral = spec["integral"]  # type: ignore[assignment]  # the setter validates any object
            except (TypeError, ValueError, OverflowError) as exc:
                raise _reraise(f"rooms[{name!r}].", exc) from exc
        for name, spec in section_specs.items():
            try:
                hold = spec["hold"]
                if hold is not None:
                    hold = _validation.level("hold", hold, OUTPUT_MIN, OUTPUT_MAX)
                allocator._modulators[name] = Modulator._from_dict(
                    {
                        "mode": "floor_heating",
                        "history_length": length,
                        "fixed_output": hold,
                        "history": spec["history"],
                    }
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise _reraise(f"sections[{name!r}].", exc) from exc
        return allocator

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def _allocate(self, demand: Mapping[str, float]) -> dict[str, float]:
        """Choose every section's duty for the given room demands.

        Each connected component is solved on its own: a one-room,
        one-section component in closed form, every other one by bounded
        least squares. Held sections are constants: their contribution is
        subtracted from the right-hand side and their columns are left out
        of the solve, so a fully held component calls no solver.

        Args:
            demand: Each room's demand in ``[OUTPUT_MIN, OUTPUT_MAX]``.

        Returns:
            Each section's duty in ``[0, 1]``, in the constructor's section
            order.

        Raises:
            ArithmeticError: If the solver returns a non-finite duty or does
                not converge, naming the component's sections.
        """
        found: dict[str, float] = {}
        holds = self.holds
        for component in self._components:
            if component.matrix is None:
                section = component.sections[0]
                level = holds[section]
                if level is not None:
                    found[section] = level
                else:
                    found[section] = min(OUTPUT_MAX, demand[component.rooms[0]]) + 0.0
                continue
            held = [i for i, s in enumerate(component.sections) if holds[s] is not None]
            free = [i for i, s in enumerate(component.sections) if holds[s] is None]
            b = np.zeros(component.matrix.shape[0])
            for i, room in enumerate(component.rooms):
                b[i] = (
                    _row_weight(self._rooms[room].priority, component.scale)
                    * demand[room]
                )
            for i in held:
                level = holds[component.sections[i]]
                assert level is not None
                found[component.sections[i]] = level
                b -= component.matrix[:, i] * level
            if not free:
                continue
            free_sections = [component.sections[i] for i in free]
            result = lsq_linear(  # type: ignore[operator]  # mypy resolves scipy.optimize.lsq_linear to the submodule, not the function
                component.matrix[:, free],
                b,
                bounds=(OUTPUT_MIN, OUTPUT_MAX),
                method="bvls",
                max_iter=_SOLVER_ITERATIONS_PER_SECTION * len(free),
            )
            solution = result.x
            if int(result.status) < 1 or not bool(np.all(np.isfinite(solution))):
                raise ArithmeticError(
                    f"allocation for sections {free_sections} did not "
                    f"converge to a finite solution (solver status {result.status})."
                )
            for section, value in zip(free_sections, solution, strict=True):
                found[section] = float(min(OUTPUT_MAX, max(OUTPUT_MIN, value))) + 0.0
        return {name: found[name] for name in self._coverage}


def main() -> None:
    """Showcase this module's functionality."""
    # The reference layout: HS4 covers 70 % of R3 and 40 % of R4; R1 is only
    # half covered (the rest is an outside disturbance).
    rooms = {
        "R1": {"priority": 1.0, "evenness": 0.0},  # priority (0, 1], evenness [0, 1]
        "R2": {"priority": 1.0, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": 0.0},
        "R4": {"priority": 1.0, "evenness": 0.0},
    }
    sections = {
        "HS1": {"R1": 0.5},
        "HS2": {"R2": 1.0},
        "HS3": {"R3": 0.3},
        "HS4": {"R3": 0.7, "R4": 0.4},
        "HS5": {"R4": 0.6},
    }
    kp = 1.0
    ki = 0.0
    history_length = 4
    # R3 is half a degree cold (demand 0.5); the others are on target.
    measured = {"R1": 21.0, "R2": 21.0, "R3": 20.5, "R4": 21.0}

    allocator = SectionAllocator(
        rooms, sections, kp=kp, ki=ki, history_length=history_length
    )
    commands = allocator.update(measured)

    print("=== Example E1: R3 demand 0.5, nothing held ===")
    print(f"  duty     = {allocator.duty}")
    print(f"  commands = {commands}")

    # Hold HS3 closed: HS4 and HS5 compensate for R3.
    held_section = "HS3"
    level = 0.0  # 0.0 to 1.0, or None to release
    allocator.hold(held_section, level)
    commands = allocator.update(measured)

    print(f"\n=== Example E2: {held_section} held at {level} ===")
    print(f"  holds    = {allocator.holds}")
    print(f"  duty     = {allocator.duty}")

    # Releasing the hold lets the next update allocate freely again.
    release = None  # None releases a hold
    allocator.hold(held_section, release)
    commands = allocator.update(measured)

    print(f"\n=== {held_section} released ===")
    print(f"  duty     = {allocator.duty}")

    # A dead R3 sensor: None means no reading, so R3 keeps its last demand.
    no_reading = {"R1": 21.0, "R2": 21.0, "R3": None, "R4": 21.0}
    commands = allocator.update(no_reading)

    print("\n=== R3 has no reading: its last demand is allocated again ===")
    print(f"  R3 demand = {allocator.rooms['R3'].demand}")
    print(f"  duty      = {allocator.duty}")

    # Example E3: demand is relative to the covered half of R1.
    measured = {"R1": 20.6, "R2": 21.0, "R3": 21.0, "R4": 21.0}
    fresh = SectionAllocator(rooms, sections, kp=kp, ki=ki)
    commands = fresh.update(measured)

    print("\n=== Example E3: R1 demand 0.4, HS1 covers only half of R1 ===")
    print(f"  duty     = {fresh.duty}")

    # Example E4: R3 and R4 want an even floor, so HS3, HS4 and HS5 level out.
    rooms = {
        "R1": {"priority": 1.0, "evenness": 0.0},
        "R2": {"priority": 1.0, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": 1.0},
        "R4": {"priority": 1.0, "evenness": 1.0},
    }
    measured = {"R1": 21.0, "R2": 21.0, "R3": 20.5, "R4": 20.5}

    even = SectionAllocator(rooms, sections, kp=kp, ki=ki)
    commands = even.update(measured)

    print("\n=== Example E4: R3 and R4 demand 0.5, evenness 1 ===")
    print(f"  duty     = {even.duty}")

    # A snapshot carries the hold through a JSON round trip.
    even.hold("HS2", 1.0)
    text = json.dumps(even.to_dict())
    restored = SectionAllocator.from_dict(json.loads(text))
    original_next = even.update(measured)
    restored_next = restored.update(measured)

    print("\n=== Snapshot round trip through JSON, HS2 held at 1.0 ===")
    print(f"  holds    = {restored.holds}")
    print(f"  original = {original_next}")
    print(f"  restored = {restored_next}")

    # A room's coverages may not sum above 1.
    sections = {"HS1": {"R1": 0.7}, "HS2": {"R1": 0.5}}
    rooms = {"R1": {"priority": 1.0, "evenness": 0.0}}

    try:
        SectionAllocator(rooms, sections)
    except ValueError as exc:
        print(f"\n=== Invalid layout ===\n  ValueError: {exc}")


if __name__ == "__main__":
    main()
