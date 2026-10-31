"""Device tests use retained private fixtures; never touch host audio or power."""

import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

from camera_r4.platform_io import DeviceIO, HardwareUnavailable


class ProcessBoundary:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.programs = {}

    def which(self, name):
        return self.programs.get(name)

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.responses.get(tuple(args))
        if isinstance(result, Exception):
            raise result
        if result is None:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, *result)


class DeviceIOTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="camera-r4-platform-"))
        self.sysfs = self.root / "sys"
        self.state = self.root / "state"
        self.process = ProcessBoundary()
        self.io = DeviceIO(self.sysfs, self.state, runner=self.process.run,
                           which=self.process.which)

    def write(self, relative, value):
        path = self.sysfs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")
        return path

    def battery(self, capacity="72", status="Discharging", present="1"):
        self.write("class/power_supply/BAT0/type", "Battery")
        self.write("class/power_supply/BAT0/present", present)
        self.write("class/power_supply/BAT0/status", status)
        return self.write("class/power_supply/BAT0/capacity", capacity)

    def backlight(self, maximum=255):
        self.write("class/backlight/panel/max_brightness", maximum)
        return self.write("class/backlight/panel/brightness", 100)

    def alsa(self):
        self.process.programs["aplay"] = "/usr/bin/aplay"
        self.process.responses[("/usr/bin/aplay", "-l")] = (
            0, "**** List of PLAYBACK Hardware Devices ****\n"
            "card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]\n",
            "")
        self.write("class/sound/pcmC0D0p/dev", "116:16")

    def authorized_power(self, answer='s "yes"'):
        self.process.programs.update(busctl="/usr/bin/busctl", systemctl="/usr/bin/systemctl")
        self.process.responses[("/usr/bin/busctl", "--system", "call",
                                "org.freedesktop.login1", "/org/freedesktop/login1",
                                "org.freedesktop.login1.Manager", "CanPowerOff")] = (0, answer, "")

    def test_missing_hardware_is_explicit_and_discovery_has_no_actions(self):
        self.assertEqual(self.io.read_battery(), {"percent": None, "charging": False})
        caps = self.io.capabilities()
        for name in ("brightness", "sound", "battery", "shutdown"):
            self.assertIs(caps[name], False)
        self.assertEqual(self.process.calls, [])
        self.assertFalse(self.state.exists())
        for action in (lambda: self.io.set_brightness(60),
                       lambda: self.io.play_sound(True), self.io.shutdown):
            with self.assertRaises(HardwareUnavailable):
                action()

    def test_battery_reads_real_capacity_and_charging_status(self):
        capacity = self.battery(status="Charging")
        self.assertEqual(self.io.read_battery(), {"percent": 72, "charging": True})
        self.assertTrue(self.io.capabilities()["battery"])
        capacity.write_text("39", encoding="utf-8")
        self.write("class/power_supply/BAT0/status", "Full")
        self.assertEqual(self.io.read_battery(), {"percent": 39, "charging": False})

    def test_absent_invalid_and_nonbattery_power_supply_never_invent_percent(self):
        for invalid in ("unknown", "101", "-1", "NaN", ""):
            self.battery(capacity=invalid)
            self.assertIsNone(self.io.read_battery()["percent"])
        self.battery(capacity="52", present="0")
        self.assertEqual(self.io.read_battery(), {"percent": None, "charging": False})
        self.write("class/power_supply/AC/type", "Mains")
        self.write("class/power_supply/AC/capacity", "100")
        self.assertFalse(self.io.capabilities()["battery"])

    def test_energy_ratio_is_valid_battery_percentage_fallback(self):
        self.battery(capacity="unknown")
        self.write("class/power_supply/BAT0/energy_now", 3000000)
        self.write("class/power_supply/BAT0/energy_full", 4000000)
        self.assertEqual(self.io.read_battery()["percent"], 75)

    def test_brightness_writes_discovered_hardware_and_bounds_input(self):
        brightness = self.backlight(maximum=200)
        self.assertTrue(self.io.capabilities()["brightness"])
        self.io.set_brightness(60)
        self.assertEqual(brightness.read_text().strip(), "120")
        self.io.set_brightness(-4)
        self.assertEqual(brightness.read_text().strip(), "0")
        self.io.set_brightness(150)
        self.assertEqual(brightness.read_text().strip(), "200")
        self.assertFalse(self.state.exists())

    def test_brightness_rejects_nonfinite_input_without_writing(self):
        brightness = self.backlight()
        for bad in (math.nan, math.inf, "60", None, True):
            with self.assertRaises((TypeError, ValueError)):
                self.io.set_brightness(bad)
        self.assertEqual(brightness.read_text(), "100")

    def test_read_only_backlight_is_unavailable_without_permission_changes(self):
        brightness = self.backlight()
        brightness.chmod(0o444)
        self.assertFalse(self.io.capabilities()["brightness"])
        with self.assertRaises(HardwareUnavailable):
            self.io.set_brightness(70)
        self.assertEqual(brightness.read_text(), "100")
        self.assertEqual(brightness.stat().st_mode & 0o777, 0o444)

    def test_brightness_permission_boundary_can_be_injected(self):
        self.backlight()
        denied = DeviceIO(self.sysfs, self.state, runner=self.process.run,
                          which=self.process.which, writable=lambda path: False)
        self.assertFalse(denied.capabilities()["brightness"])
        with self.assertRaises(HardwareUnavailable):
            denied.set_brightness(10)

    def test_audio_requires_output_hardware_in_addition_to_player(self):
        self.process.programs["aplay"] = "/usr/bin/aplay"
        self.assertFalse(self.io.capabilities()["sound"])
        with self.assertRaises(HardwareUnavailable):
            self.io.play_sound(True)
        self.assertFalse(self.state.exists())

    def test_success_failure_audio_are_distinct_valid_retained_wavs(self):
        self.alsa()
        self.assertTrue(self.io.capabilities()["sound"])
        success = self.io.play_sound(True)
        failure = self.io.play_sound(False)
        self.assertNotEqual(success, failure)
        self.assertNotEqual(success.read_bytes(), failure.read_bytes())
        for path in (success, failure):
            self.assertTrue(path.is_relative_to(self.state))
            with wave.open(str(path), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth()), (1, 2))
                self.assertGreater(audio.getnframes(), 100)
                self.assertLess(audio.getnframes() / audio.getframerate(), 0.4)
        plays = [args for args, _ in self.process.calls if "-D" in args]
        self.assertEqual(plays, [["/usr/bin/aplay", "-q", "-D", "plughw:0,0", str(success)],
                                 ["/usr/bin/aplay", "-q", "-D", "plughw:0,0", str(failure)]])
        self.io.close()
        self.assertTrue(success.exists())
        self.assertTrue(failure.exists())

    def test_audio_playback_failure_is_reported_and_wav_retained(self):
        self.alsa()
        self.io.play_sound(True)
        wave_path = next(self.state.glob("**/*success*.wav"))
        command = ("/usr/bin/aplay", "-q", "-D", "plughw:0,0", str(wave_path))
        self.process.responses[command] = (1, "", "device busy")
        with self.assertRaises(HardwareUnavailable):
            self.io.play_sound(True)
        self.assertTrue(wave_path.exists())

    def test_pulse_requires_physical_sink_and_targets_it(self):
        self.process.programs.update(pactl="/usr/bin/pactl", paplay="/usr/bin/paplay")
        query = ("/usr/bin/pactl", "-f", "json", "list", "sinks")
        self.process.responses[query] = (0, json.dumps([{"name": "auto_null", "properties": {}}]), "")
        self.assertFalse(self.io.capabilities()["sound"])
        self.process.responses[query] = (0, json.dumps([{"name": "alsa_output.usb-speaker", "properties": {"device.api": "alsa"}}]), "")
        self.assertTrue(self.io.capabilities()["sound"])
        path = self.io.play_sound(True)
        self.assertIn((["/usr/bin/paplay", "--device=alsa_output.usb-speaker", str(path)],
                       {"capture_output": True, "text": True, "timeout": 5, "check": False}), self.process.calls)

    def test_shutdown_requires_unprompted_authorization_and_reports_only_acknowledgement(self):
        self.authorized_power()
        self.assertTrue(self.io.capabilities()["shutdown"])
        self.assertEqual(self.io.shutdown(), {"acknowledged": True})
        args, kwargs = self.process.calls[-1]
        self.assertEqual(args, ["/usr/bin/systemctl", "--no-ask-password", "poweroff"])
        self.assertEqual(kwargs, {"capture_output": True, "text": True, "timeout": 5, "check": False})

    def test_power_challenge_denied_and_error_never_issue_shutdown(self):
        for answer in ('s "challenge"', 's "no"', "bad response"):
            self.authorized_power(answer)
            self.assertFalse(self.io.capabilities()["shutdown"])
            with self.assertRaises(HardwareUnavailable):
                self.io.shutdown()
        self.assertFalse(any("poweroff" in args for args, _ in self.process.calls))

    def test_shutdown_subprocess_failure_is_not_acknowledged(self):
        self.authorized_power()
        self.process.responses[("/usr/bin/systemctl", "--no-ask-password", "poweroff")] = (1, "", "Access denied")
        with self.assertRaises(HardwareUnavailable):
            self.io.shutdown()

    def test_process_errors_fail_closed_without_host_fallbacks(self):
        self.authorized_power()
        self.process.responses[("/usr/bin/busctl", "--system", "call",
                                "org.freedesktop.login1", "/org/freedesktop/login1",
                                "org.freedesktop.login1.Manager", "CanPowerOff")] = subprocess.TimeoutExpired("busctl", 5)
        self.assertFalse(self.io.capabilities()["shutdown"])
        with self.assertRaises(HardwareUnavailable):
            self.io.shutdown()


if __name__ == "__main__":
    unittest.main()
