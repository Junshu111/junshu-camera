#!/usr/bin/env python3
"""Read-only power-button diagnostics; never requests a GPIO line."""
from camera_r4.power_gpio import main

if __name__ == '__main__':
    raise SystemExit(main())
