#!/usr/bin/env python3
"""Read-only GPIO check: safe from SSH, never claims a GPIO line."""
from camera_r4.gpio_probe import main
if __name__=='__main__':raise SystemExit(main())
