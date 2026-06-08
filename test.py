"""Simulate and visualise the :class:`PIController` for both heating modes.

This script runs a small closed-loop simulation: a first-order thermal model of
a room is driven by a :class:`heatingsystem.PIController`, once in ``RADIATOR``
mode (continuous output) and once in ``FLOOR_HEATING`` mode (binary on/off via
duty-cycle modulation).  For each mode it draws a plot showing the measured room
temperature, the (stepped) setpoint, and the controller's actuator state encoded
as a blue (off) -> red (on) background hue.

A setpoint change is injected halfway through the run so the controller's
response to a new target is visible.  Run it directly to produce the figure::

    .venv/Scripts/python.exe test.py
"""

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Colormap, Normalize

import heatingsystem as hs

# --- Simulation configuration -------------------------------------------------
N_STEPS: int = 30  # total number of 5-minute control steps to simulate
HISTORY_LENGTH: int = 6  # controller lookback window (requested)
CHANGE_STEP: int = N_STEPS // 2  # setpoint changes halfway through the run
SETPOINT_INITIAL: float = 21.0  # °C target before the change
SETPOINT_CHANGED: float = 23.0  # °C target after the change
START_TEMP: float = 19.0  # °C initial room temperature

# --- Controller gains ---------------------------------------------------------
KP: float = 0.4  # proportional gain
KI: float = 0.1  # integral gain (per 5-minute step)

# --- First-order thermal-model parameters -------------------------------------
AMBIENT_TEMP: float = 17.0  # °C the room drifts toward with the heater off
HEAT_GAIN: float = 2.0  # °C/step added at full actuator output
LOSS_COEFF: float = 0.2  # fraction of the room-to-ambient gap lost per step

# Diverging colormap: 0.0 (off) -> blue, 1.0 (full on) -> red.
CONTROL_CMAP: Colormap = matplotlib.colormaps["coolwarm"]


def step_temperature(temp: float, command: float) -> float:
    """Advance the room temperature by one step of the thermal model.

    The model is a simple first-order energy balance: the heater adds energy
    proportional to its actuator command while the room continuously loses
    energy toward the ambient temperature.

    Args:
        temp: Current room temperature in °C.
        command: Actuator command in [0.0, 1.0] for this step.

    Returns:
        The room temperature in °C at the next step.
    """
    # Heat added by the actuator minus passive loss toward ambient.
    return temp + HEAT_GAIN * command - LOSS_COEFF * (temp - AMBIENT_TEMP)


def run_simulation(
    mode: hs.HeatingMode,
) -> tuple[list[float], list[float], list[float]]:
    """Run the closed-loop simulation for a single heating mode.

    Args:
        mode: The :class:`heatingsystem.HeatingMode` to simulate.

    Returns:
        A 3-tuple ``(temps, commands, setpoints)`` of per-step lists: the
        measured temperature fed to the controller, the actuator command it
        returned, and the active setpoint at that step.
    """
    # Fresh controller with the requested short lookback window.
    controller = hs.PIController(
        kp=KP,
        ki=KI,
        mode=mode,
        setpoint=SETPOINT_INITIAL,
        history_length=HISTORY_LENGTH,
    )

    temps: list[float] = []
    commands: list[float] = []
    setpoints: list[float] = []

    temp = START_TEMP
    for step in range(N_STEPS):
        # Apply the setpoint change once we pass the halfway point.
        setpoint = SETPOINT_INITIAL if step < CHANGE_STEP else SETPOINT_CHANGED

        # The controller sees the current temperature and returns the command.
        command = controller.update(measured=temp, setpoint=setpoint)

        # Record what was measured/decided this step before the room evolves.
        temps.append(temp)
        commands.append(command)
        setpoints.append(setpoint)

        # Evolve the room for the next step using the chosen command.
        temp = step_temperature(temp, command)

    return temps, commands, setpoints


def plot_mode(
    ax: Axes,
    title: str,
    temps: list[float],
    commands: list[float],
    setpoints: list[float],
) -> None:
    """Draw one mode's simulation onto a given axis.

    The actuator state is shown as a per-step background band coloured from
    blue (off) to red (fully on); the measured temperature and the stepped
    setpoint are overlaid as lines.

    Args:
        ax: The matplotlib axis to draw on.
        title: Title for this subplot.
        temps: Per-step measured temperatures in °C.
        commands: Per-step actuator commands in [0.0, 1.0].
        setpoints: Per-step active setpoints in °C.
    """
    steps = list(range(len(temps)))

    # Background hue: one vertical band per step, coloured by the control state.
    for step, command in zip(steps, commands, strict=True):
        ax.axvspan(
            step - 0.5,
            step + 0.5,
            facecolor=CONTROL_CMAP(command),
            alpha=0.45,
            zorder=0,
        )

    # Measured temperature trajectory.
    ax.plot(
        steps,
        temps,
        color="black",
        marker="o",
        markersize=4,
        linewidth=1.5,
        label="Measured temperature",
        zorder=3,
    )

    # Setpoint drawn as a stepped line so the halfway change is visible.
    ax.step(
        steps,
        setpoints,
        where="mid",
        color="white",
        linestyle="--",
        linewidth=2.0,
        label="Setpoint",
        zorder=2,
    )

    # Mark the moment the setpoint changes.
    ax.axvline(
        CHANGE_STEP - 0.5,
        color="dimgray",
        linestyle=":",
        linewidth=1.0,
        zorder=1,
    )

    ax.set_title(title)
    ax.set_ylabel("Temperature (°C)")
    ax.set_xlim(-0.5, len(temps) - 0.5)
    ax.legend(loc="lower right", framealpha=0.9)


def main() -> None:
    """Run both simulations and render the comparison figure."""
    # Run the same scenario for each mode.
    radiator = run_simulation(hs.HeatingMode.RADIATOR)
    floor = run_simulation(hs.HeatingMode.FLOOR_HEATING)

    # Two stacked subplots sharing the timestep axis.
    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(11, 8), sharex=True
    )

    plot_mode(ax_top, "Radiator (continuous output)", *radiator)
    plot_mode(
        ax_bottom,
        f"Floor heating (binary, duty-cycle over {HISTORY_LENGTH}-step window)",
        *floor,
    )

    ax_bottom.set_xlabel("Timestep (each = 5 minutes)")

    # Shared colourbar explaining the background hue -> control-state mapping.
    mappable = ScalarMappable(norm=Normalize(vmin=0.0, vmax=1.0), cmap=CONTROL_CMAP)
    colorbar = fig.colorbar(mappable, ax=(ax_top, ax_bottom), pad=0.02)
    colorbar.set_label("Control state (0 = off / blue, 1 = on / red)")

    fig.suptitle(
        "PIController closed-loop simulation with a setpoint change", fontsize=13
    )

    # Save an artifact and show the window when running interactively.
    fig.savefig("simulation.png", dpi=120, bbox_inches="tight")
    print("Saved figure to simulation.png")

    # Only open an interactive window when a GUI backend is active; under the
    # headless Agg backend ``show`` would merely emit a warning.
    if matplotlib.get_backend().lower() != "agg":
        plt.show()


if __name__ == "__main__":
    main()
