"""Optional Linux device adapters. Discovery never changes system configuration.

The runner and filesystem root are injectable so desktop QA cannot affect the
host's display, speakers or power. No GPIO wiring or optional Pi library is
assumed. A successful shutdown means only that systemd accepted the request.
"""

import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import wave


class HardwareUnavailable(RuntimeError):
    """The requested hardware action is unavailable or was rejected."""


class DeviceIO:
    def __init__(self, sysfs_root="/sys", state_dir=None, *,
                 runner=subprocess.run, which=shutil.which, writable=None):
        self.sysfs_root = Path(sysfs_root)
        state_home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        self.state_dir = Path(state_dir) if state_dir is not None else state_home / "JunshuCamera/r4"
        self._runner = runner
        self._which = which
        self._writable = writable or self._can_write
        self._audio_lock = threading.Lock()

    @staticmethod
    def _can_write(path):
        try:
            return bool(path.stat().st_mode & 0o222) and os.access(path, os.W_OK)
        except OSError:
            return False

    @staticmethod
    def _text(path):
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None

    @classmethod
    def _integer(cls, path):
        try:
            return int(cls._text(path))
        except (ValueError, TypeError):
            return None

    def _children(self, relative):
        try:
            return sorted((self.sysfs_root / relative).iterdir())
        except OSError:
            return []

    def _run(self, args):
        try:
            return self._runner(args, capture_output=True, text=True,
                                timeout=5, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HardwareUnavailable(f"Device command failed: {exc}") from exc

    def _backlight(self):
        for device in self._children("class/backlight"):
            maximum = self._integer(device / "max_brightness")
            brightness = device / "brightness"
            if maximum is not None and maximum > 0 and brightness.is_file() and self._writable(brightness):
                return brightness, maximum
        return None

    def _battery(self):
        for device in self._children("class/power_supply"):
            if self._text(device / "type") != "Battery":
                continue
            if self._text(device / "present") == "0":
                continue
            percent = self._integer(device / "capacity")
            if percent is None or not 0 <= percent <= 100:
                percent = None
                for prefix in ("energy", "charge"):
                    now = self._integer(device / f"{prefix}_now")
                    full = self._integer(device / f"{prefix}_full")
                    if now is not None and full is not None and full > 0 and 0 <= now <= full:
                        percent = round(now * 100 / full)
                        break
            return device, {"percent": percent,
                            "charging": self._text(device / "status") == "Charging"}
        return None, {"percent": None, "charging": False}

    def read_battery(self):
        """Read the first present battery; unknown is never fabricated as 100%."""
        return self._battery()[1]

    def _audio_output(self):
        pactl, paplay = self._which("pactl"), self._which("paplay")
        if pactl and paplay:
            try:
                result = self._run([pactl, "-f", "json", "list", "sinks"])
                sinks = json.loads(result.stdout) if result.returncode == 0 else []
                for sink in sinks if isinstance(sinks, list) else []:
                    if not isinstance(sink, dict):
                        continue
                    properties = sink.get("properties", {})
                    name = sink.get("name", "")
                    if not isinstance(properties, dict) or not isinstance(name, str):
                        continue
                    physical = ("alsa.card" in properties or
                                properties.get("device.api") in ("alsa", "bluez", "bluez5") or
                                properties.get("device.bus") in ("usb", "pci", "platform", "bluetooth"))
                    if name and name != "auto_null" and physical:
                        return [paplay, f"--device={name}"], name
            except (HardwareUnavailable, ValueError, TypeError):
                pass
        aplay = self._which("aplay")
        if aplay:
            try:
                result = self._run([aplay, "-l"])
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        match = re.match(r"card\s+(\d+):.*?\bdevice\s+(\d+):", line)
                        if match and not re.search(r"dummy|loopback", line, re.IGNORECASE):
                            card, device = match.groups()
                            if (self.sysfs_root / f"class/sound/pcmC{card}D{device}p").is_dir():
                                target = f"plughw:{card},{device}"
                                return [aplay, "-q", "-D", target], target
            except HardwareUnavailable:
                pass
        return None

    def _power_command(self):
        busctl, systemctl = self._which("busctl"), self._which("systemctl")
        if not busctl or not systemctl:
            return None
        try:
            result = self._run([busctl, "--system", "call", "org.freedesktop.login1",
                                "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                                "CanPowerOff"])
            if result.returncode == 0 and result.stdout.strip() == 's "yes"':
                return [systemctl, "--no-ask-password", "poweroff"]
        except HardwareUnavailable:
            pass
        return None

    def capabilities(self):
        """Fresh discovery. Availability does not claim an action already worked."""
        backlight = self._backlight()
        battery_path, battery = self._battery()
        audio = self._audio_output()
        power = self._power_command()
        return {
            "brightness": backlight is not None,
            "sound": audio is not None,
            "battery": battery["percent"] is not None,
            "shutdown": power is not None,
            "details": {
                "brightness": str(backlight[0]) if backlight else "No writable sysfs backlight",
                "sound": audio[1] if audio else "No supported physical audio output discovered",
                "battery": str(battery_path) if battery_path else "No present battery",
                "shutdown": "Systemd request authorized" if power else "No unprompted power-off authorization",
            },
        }

    def set_brightness(self, percent):
        """Clamp to 0–100 and write only a discovered writable hardware backlight."""
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise TypeError("Brightness must be numeric")
        if not math.isfinite(percent):
            raise ValueError("Brightness must be finite")
        device = self._backlight()
        if device is None:
            raise HardwareUnavailable("No writable hardware backlight")
        percent = max(0, min(100, percent))
        path, maximum = device
        try:
            path.write_text(str(round(maximum * percent / 100)), encoding="ascii")
        except OSError as exc:
            raise HardwareUnavailable(f"Backlight write failed: {exc}") from exc
        return percent

    def _sound_file(self, success):
        path = self.state_dir / "audio" / ("r4-success-v1.wav" if success else "r4-failure-v1.wav")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise HardwareUnavailable("Audio state file must not be a symlink")
            if path.exists():
                return path
            rate = 22050
            duration = 0.13 if success else 0.22
            frames = int(rate * duration)
            samples = bytearray()
            for i in range(frames):
                t = i / rate
                frequency = (880 if t < duration / 2 else 1174) if success else (330 if t < duration / 2 else 220)
                envelope = min(1, i / (rate * 0.01), (frames - i) / (rate * 0.02))
                sample = round(3276 * envelope * math.sin(2 * math.pi * frequency * t))
                samples.extend(struct.pack("<h", sample))
            with path.open("xb") as output:
                with wave.open(output, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(rate)
                    audio.writeframes(samples)
            return path
        except OSError as exc:
            raise HardwareUnavailable(f"Cannot persist audio cue: {exc}") from exc

    def play_sound(self, success):
        """Play a quiet, short result cue; retain generated WAVs and failed files."""
        if not isinstance(success, bool):
            raise TypeError("Sound result must be a boolean")
        output = self._audio_output()
        if output is None:
            raise HardwareUnavailable("No supported physical audio output discovered")
        with self._audio_lock:
            path = self._sound_file(success)
            result = self._run(output[0] + [str(path)])
        if result.returncode != 0:
            raise HardwareUnavailable(f"Sound playback rejected: {result.stderr.strip()}")
        return path

    def shutdown(self):
        """Request power-off without sudo/prompts; acknowledgement is not completion."""
        command = self._power_command()
        if command is None:
            raise HardwareUnavailable("System power-off is not authorized without interaction")
        result = self._run(command)
        if result.returncode != 0:
            raise HardwareUnavailable(f"Power-off request rejected: {result.stderr.strip()}")
        return {"acknowledged": True}

    def close(self):
        """No persistent process or file handles are owned; retained cues stay put."""
