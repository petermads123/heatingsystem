"""PI Controller Class."""


class PIController:
    """A simple PI controller for heating systems."""

    def __init__(self, kp: float = 0.3, ki: float = 0.015):
        """Initialize the PI controller with given gains."""
        self.kp = kp
        self.ki = ki
        self.integral = 0.0

    def update(self, setpoint: float, measurement: float, dt: float) -> float:
        """Calculate the control output based on the setpoint and measurement."""
        error = setpoint - measurement
        self.integral += error * dt
        output = self.kp * error + self.ki * self.integral
        return output
