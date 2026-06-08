# Heating System
This repo is for implementing various heating system models.
Install via:
    pip install git+https://github.com/petermads123/heatingsystem.git@main
or in dependecies in pyproject.toml:
    "heatingsystem @ git+https://github.com/petermads123/heatingsystem.git@main"

## PIController
A basic PI-controlled heating system


## Development
1. Have git installed on your machine.
2. Have python installed on your machine. At least the version specified in pyproject.toml
3. Have the python extension installed in your VSCode
4. CTRL + Shift + P -> Python: Create Environment -> Venv -> Select python version
5. Download dependencies by running "pip install -e." in the powershell terminal. Environment should activate automatically when powershell terminal is launched.

### Ruff
    For proper development, please have Ruff installed.
    After installement; go to File -> Preferences -> Settings.
        Turn on "Format on save".

## PIController usage

Install the package (see above), then:

```python
import heatingsystem as hs

# --- Radiator controller (continuous output in [0.0, 1.0]) ---
radiator = hs.PIController(
    kp=0.3,
    ki=0.015,
    mode=hs.HeatingMode.RADIATOR,   # or just mode="radiator"
    setpoint=21.0,                  # target room temperature in °C
    history_length=24,              # rolling window length (default 24 steps)
)

# In Home Assistant / AppDaemon this callback fires every 5 minutes.
# Pass the current room temperature; get back a valve-open fraction [0, 1].
measured_temp = 19.5               # e.g. read from a sensor entity
output = radiator.update(measured_temp)
print(f"Radiator valve: {output:.2f}")   # e.g. 0.46

# Override the setpoint for a single call (does not change the stored setpoint):
output = radiator.update(measured_temp, setpoint=22.0)

# Inspect the last 24 issued commands (oldest first) and the mean ON-fraction:
print(radiator.history)       # tuple of floats, length <= history_length
print(radiator.duty_cycle)    # mean of the rolling window; 0.0 if empty
print(radiator.is_history_full)  # True once history_length steps have been issued

# --- Floor-heating controller (binary output: 0.0 or 1.0) ---
# Uses the same internal PI logic but quantises to on/off each step.
# Over a full window the duty_cycle converges to the equivalent continuous output.
floor = hs.PIController(
    kp=0.3,
    ki=0.015,
    mode=hs.HeatingMode.FLOOR_HEATING,   # or mode="floor_heating"
    setpoint=21.0,
)

measured_temp = 20.0
command = floor.update(measured_temp)   # 0.0 (off) or 1.0 (on)
print(f"Floor heating: {'ON' if command else 'OFF'}")
print(f"Duty cycle over window: {floor.duty_cycle:.2f}")

# --- Reset (e.g. on controller restart or setpoint change) ---
radiator.reset()   # zeroes the integral accumulator and clears the history window
```