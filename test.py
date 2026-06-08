"""Test script for the project."""

import heatingsystem as hs


def main() -> None:
    """Main function to run the test."""
    system = hs.PIController()
    print("PI Controller initialized with Kp =", system.kp, "and Ki =", system.ki)


if __name__ == "__main__":
    main()
