"""Multi-room control of on/off heating sections that may serve several rooms.

This module provides :class:`SectionAllocator`, built once from a fixed
physical layout: named rooms (each with a priority and an evenness weight)
and named sections (each with the share of its heat that reaches each room
it covers). Every :meth:`SectionAllocator.update` works in three stages:

1. **Room demand.** Each :class:`Room` runs the existing PI control law (a
   composed radiator-mode
   :class:`~heatingsystem.pi_controller.pi_controller.PIController`) and
   yields a demand in ``[0, 1]``.
2. **Allocation.** Section duty cycles in ``[0, 1]`` are chosen by bounded
   weighted least squares, minimising exactly
   ``J(u) = sum_r p_r * (d_r - h_r)**2 + sum_r p_r * e_r * spread_r``: the
   priority-weighted squared mismatch between each room's demand ``d_r`` and
   the heat ``h_r`` it receives (the sum of share times duty over its
   sections), plus, per room, priority times evenness times the *spread* of
   duty among the sections serving it, ``sum((u_s - mean)**2)``. Priorities
   lie in ``(0, 1]`` (only their ratios matter) and evenness weights in
   ``[0, 1]``.
   Rooms and sections that share no variable are solved separately; a
   component of one room and one section has a closed form.
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

# Tolerance on a section's share sum, so 0.1-style float sums that should be
# exactly 1.0 are accepted.
_SHARE_SUM_TOLERANCE: float = 1e-9

# Smallest weight, relative to the largest in a component, that still reaches
# the solver. A safety net for priorities that differ by more than 1e12:
# with priorities in (0, 1] and evenness in [0, 1] it is otherwise inert.
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
_SECTION_SNAPSHOT_KEYS: frozenset[str] = frozenset({"shares", "history"})


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
        """The clamped PI demand of the last update, or ``None`` before one."""
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
        share: The single share of the closed-form case (``0.0`` otherwise).
        scale: The largest priority in the component (a spread weight,
            priority times evenness, never exceeds it), which the matrix
            rows and the demand vector are divided by.
    """

    def __init__(
        self,
        rooms: list[str],
        sections: list[str],
        matrix: np.ndarray | None,
        share: float,
        scale: float,
    ) -> None:
        """Store the precomputed component."""
        self.rooms = rooms
        self.sections = sections
        self.matrix = matrix
        self.share = share
        self.scale = scale


class SectionAllocator:
    """Controller for on/off heating sections that may each serve several rooms.

    Built once from a fixed layout; the layout (rooms, priorities, evenness
    weights, shares) is read-only. Each room's ``setpoint``, ``kp`` and
    ``ki`` can be changed at any time through :attr:`rooms` and take effect
    on the next :meth:`update`.

    Args:
        rooms: Maps each room name to a mapping with exactly the keys
            ``"priority"`` (in ``(0, 1]``) and ``"evenness"`` (in
            ``[0, 1]``).
        sections: Maps each section name to ``{room name: share}``; every
            share in ``(0, 1]``, each section's shares summing to at most 1
            (the rest heats something unmeasured), every room covered by at
            least one section.
        kp: Initial proportional gain of every room.
        ki: Initial integral gain of every room.
        setpoint: Initial setpoint of every room in degrees C.
        history_length: Rolling command-window length of every section; an
            ``int`` of at least 1.

    Raises:
        TypeError: If a mapping is not a ``Mapping``, a name is not a
            ``str``, or a number is not a real number or is a ``bool``.
        ValueError: If a mapping is empty or has the wrong keys, a name is
            empty, a section names an unknown room, a share is outside
            ``(0, 1]``, a section's shares sum above 1 by more than 1e-9, a
            room is covered by no section, a priority is outside ``(0, 1]``,
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
        shares = self._build_shares(sections, built_rooms)

        uncovered = sorted(
            name
            for name in built_rooms
            if not any(name in section for section in shares.values())
        )
        if uncovered:
            raise ValueError(f"rooms {uncovered} are covered by no section.")

        length = _validation.window_length(history_length)

        self._rooms: dict[str, Room] = built_rooms
        self._shares: dict[str, dict[str, float]] = shares
        self._history_length: int = length
        self._modulators: dict[str, Modulator] = {
            name: Modulator("floor_heating", history_length=length) for name in shares
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
    def _build_shares(
        sections: object, rooms: Mapping[str, Room]
    ) -> dict[str, dict[str, float]]:
        """Validate the ``sections`` argument.

        Args:
            sections: The candidate ``sections`` mapping.
            rooms: The already-built rooms, to check names against.

        Returns:
            The shares by section and room name, as floats, in order.

        Raises:
            TypeError: If ``sections`` or a section is not a ``Mapping``, a
                name is not a ``str``, or a share is not a real number.
            ValueError: If ``sections`` or a section is empty, a name is
                empty, a section names an unknown room, a share is outside
                ``(0, 1]``, or a section's shares sum above 1.
            OverflowError: If a share is too large to represent as a float.
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
                share_path = f"{path}[{room_name!r}]"
                share = _validation.finite(share_path, raw)
                if not 0.0 < share <= 1.0:
                    raise ValueError(f"{share_path} must be in (0, 1], got {raw!r}.")
                section[room_name] = share
            total = math.fsum(section.values())
            if total > 1.0 + _SHARE_SUM_TOLERANCE:
                raise ValueError(f"{path} shares must sum to at most 1, got {total}.")
            result[name] = section
        return result

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
        section_nodes = {name: f"section:{name}" for name in self._shares}
        for node in [*room_nodes.values(), *section_nodes.values()]:
            parent[node] = node
        for section_name, covered in self._shares.items():
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
            return _Component(
                rooms, sections, None, self._shares[sections[0]][rooms[0]], 1.0
            )

        column = {name: i for i, name in enumerate(sections)}
        serving = {
            room: [s for s in sections if room in self._shares[s]] for room in rooms
        }
        scale = max(self._rooms[r].priority for r in rooms)

        rows: list[np.ndarray] = []
        for room in rooms:
            row = np.zeros(len(sections))
            weight = _row_weight(self._rooms[room].priority, scale)
            for s in serving[room]:
                row[column[s]] = weight * self._shares[s][room]
            rows.append(row)
        for room in rooms:
            covering = serving[room]
            spread_weight = self._rooms[room].priority * self._rooms[room].evenness
            if len(covering) < 2 or spread_weight <= 0.0:
                continue
            weight = _row_weight(spread_weight, scale)
            for s in covering:
                row = np.zeros(len(sections))
                for other in covering:
                    row[column[other]] -= weight / len(covering)
                row[column[s]] += weight
                rows.append(row)
        return _Component(rooms, sections, np.vstack(rows), 0.0, scale)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self, measured: Mapping[str, float]) -> dict[str, float]:
        """Run one control step and return a command for every section.

        Every temperature is validated before any state changes. If the
        allocation fails, every room's integral and demand are restored and
        the error propagates; no command is issued and no duty is stored.

        Args:
            measured: The temperature in degrees C of every room, keyed by
                room name.

        Returns:
            A fresh dict ``{section: 0.0 or 1.0}`` in the constructor's
            section order.

        Raises:
            TypeError: If ``measured`` is not a ``Mapping``, or a
                temperature is not a real number or is a ``bool``, naming
                ``measured['R1']``.
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
        temperatures = {
            name: _validation.finite(f"measured[{name!r}]", values[name])
            for name in self._rooms
        }

        saved = {
            name: (room._pi._integral, room._pi._pi_output)
            for name, room in self._rooms.items()
        }
        try:
            demand = {
                name: room._step(temperatures[name])
                for name, room in self._rooms.items()
            }
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
        """A fresh deep copy of the layout's shares, as floats."""
        return {name: dict(covered) for name, covered in self._shares.items()}

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
            ``sections`` (per section: ``shares`` and ``history``), in
            constructor order.
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
                    "shares": dict(self._shares[name]),
                    "history": list(modulator.history),
                }
                for name, modulator in self._modulators.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Rebuild an allocator from a :meth:`to_dict` snapshot.

        The layout goes through the constructor, so it raises what the
        constructor raises, naming the constructor's path
        (``sections['HS1']['R1']`` for a bad share). Settings and section
        windows go through the same setters a direct assignment uses; their
        errors keep their class with the snapshot path prefixed, such as
        ``rooms['R1'].integral``.

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
            name: _mapping(f"sections[{name!r}]['shares']", spec["shares"])
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
                allocator._modulators[name] = Modulator._from_dict(
                    {
                        "mode": "floor_heating",
                        "history_length": length,
                        "fixed_output": None,
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
        least squares.

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
        for component in self._components:
            if component.matrix is None:
                section = component.sections[0]
                target = demand[component.rooms[0]]
                found[section] = min(OUTPUT_MAX, target / component.share) + 0.0
                continue
            b = np.zeros(component.matrix.shape[0])
            for i, room in enumerate(component.rooms):
                b[i] = (
                    _row_weight(self._rooms[room].priority, component.scale)
                    * demand[room]
                )
            result = lsq_linear(  # type: ignore[operator]  # mypy resolves scipy.optimize.lsq_linear to the submodule, not the function
                component.matrix,
                b,
                bounds=(OUTPUT_MIN, OUTPUT_MAX),
                method="bvls",
                max_iter=_SOLVER_ITERATIONS_PER_SECTION * len(component.sections),
            )
            solution = result.x
            if int(result.status) < 1 or not bool(np.all(np.isfinite(solution))):
                raise ArithmeticError(
                    f"allocation for sections {component.sections} did not "
                    f"converge to a finite solution (solver status {result.status})."
                )
            for section, value in zip(component.sections, solution, strict=True):
                found[section] = float(min(OUTPUT_MAX, max(OUTPUT_MIN, value))) + 0.0
        return {name: found[name] for name in self._shares}


def main() -> None:
    """Showcase this module's functionality."""
    # The reference layout: HS2 is split 50/50 between R1 and R2.
    rooms = {
        "R1": {"priority": 1.0, "evenness": 0.0},  # priority (0, 1], evenness [0, 1]
        "R2": {"priority": 1.0, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": 0.0},
    }
    sections = {
        "HS1": {"R1": 1.0},
        "HS2": {"R1": 0.5, "R2": 0.5},
        "HS3": {"R2": 1.0},
        "HS4": {"R3": 1.0},
    }
    kp = 1.0
    ki = 0.0
    history_length = 4
    # R2 is cold (demand 0.6), R1 almost warm (demand 0.1), R3 is warm.
    measured = {"R1": 20.9, "R2": 20.4, "R3": 21.0}

    allocator = SectionAllocator(
        rooms, sections, kp=kp, ki=ki, history_length=history_length
    )
    commands = allocator.update(measured)

    print("=== Hungry R2, evenness 0 everywhere ===")
    print(f"  duty     = {allocator.duty}")
    print(f"  commands = {commands}")

    # Worked example B: R1 wants an even floor (evenness 0.1, R2 does not
    # care), so HS1 is pulled up towards HS2 and both demands are still met.
    rooms = {
        "R1": {"priority": 1.0, "evenness": 0.1},
        "R2": {"priority": 1.0, "evenness": 0.0},
        "R3": {"priority": 1.0, "evenness": 0.0},
    }

    even = SectionAllocator(
        rooms, sections, kp=kp, ki=ki, history_length=history_length
    )
    commands = even.update(measured)

    print("\n=== Example B: R1 evenness 0.1, R2 evenness 0 ===")
    print(f"  duty     = {even.duty}")
    print(f"  commands = {commands}")

    # Worked example C: both floors want to be even and pull HS2 in opposite
    # directions, so the demands are traded off against the evenness.
    rooms = {
        "R1": {"priority": 1.0, "evenness": 1.0},
        "R2": {"priority": 1.0, "evenness": 1.0},
        "R3": {"priority": 1.0, "evenness": 0.0},
    }

    both = SectionAllocator(
        rooms, sections, kp=kp, ki=ki, history_length=history_length
    )
    commands = both.update(measured)

    print("\n=== Example C: R1 and R2 evenness 1 ===")
    print(f"  duty     = {both.duty}")
    print(f"  commands = {commands}")

    # A higher priority for R1 only matters when demands cannot all be met;
    # only the ratio between priorities counts (0.8 vs 0.2 is 4 vs 1).
    rooms = {
        "R1": {"priority": 0.8, "evenness": 0.0},
        "R2": {"priority": 0.2, "evenness": 0.0},
    }
    sections = {"HS1": {"R1": 0.5, "R2": 0.5}}
    measured = {"R1": 20.8, "R2": 20.4}

    shared = SectionAllocator(rooms, sections, kp=kp, ki=ki)
    commands = shared.update(measured)

    print("\n=== One shared section, R1 priority 0.8 vs R2 priority 0.2 ===")
    print(f"  duty     = {shared.duty}")
    print(f"  commands = {commands}")

    # Setpoints and gains change at runtime through the room handles.
    new_setpoint = 19.0
    shared.rooms["R1"].setpoint = new_setpoint
    commands = shared.update(measured)

    print(f"\n=== R1 setpoint now {new_setpoint} ===")
    print(f"  demand   = {shared.rooms['R1'].demand}")
    print(f"  commands = {commands}")

    # A snapshot survives a JSON round trip and continues identically.
    text = json.dumps(shared.to_dict())
    restored = SectionAllocator.from_dict(json.loads(text))
    original_next = shared.update(measured)
    restored_next = restored.update(measured)

    print("\n=== Snapshot round trip through JSON ===")
    print(f"  original = {original_next}")
    print(f"  restored = {restored_next}")

    # An invalid layout raises and produces nothing; a priority above 1 is one.
    rooms = {"R1": {"priority": 2.0, "evenness": 0.0}}
    sections = {"HS1": {"R1": 0.8}}

    try:
        SectionAllocator(rooms, sections)
    except ValueError as exc:
        print(f"\n=== Invalid layout ===\n  ValueError: {exc}")


if __name__ == "__main__":
    main()
