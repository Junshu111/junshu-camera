"""Preview pacing and UI responsiveness; hardware/Qt boundaries use retained fixtures."""
from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch

from camera_r4.camera import PiCamera
from camera_r4.state import AppSettings
from test_framing_camera import Device
import test_gui_r4 as ui
from camera_r4 import gui
from PIL import Image


class PacedDevice(Device):
    """Model queue=False: each request waits for a fresh sensor period."""
    def configure(self, config):
        super().configure(config)
        self.camera_controls['FrameDurationLimits'] = (10000, 2000000, 33333)

    def capture_request(self, wait):
        time.sleep(self.controls.get('FrameDurationLimits', (33333,))[0] / 1e6)
        return super().capture_request(wait)


class PreviewPacingTests(unittest.TestCase):
    def test_short_exposure_stream_is_not_held_below_twenty_fps(self):
        device = PacedDevice()
        camera = PiCamera(factory=lambda: device)
        camera.configure(AppSettings(exposure_mode='manual', shutter_us=8000, iso=200))
        try:
            camera.start()
            deadline = time.monotonic() + 4
            while not camera.snapshot().ready and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(camera.snapshot().ready)
            start = camera.snapshot().sequence
            time.sleep(.85)
            delivered = camera.snapshot().sequence - start
            print('paced fixture frames in 0.85s:', delivered, flush=True)
            self.assertGreaterEqual(delivered, 17, 'software must not add a second per-frame delay')
        finally:
            camera.close()

    def test_long_exposure_and_sensor_limits_are_respected(self):
        device = PacedDevice()
        camera = PiCamera(factory=lambda: device)
        preview, still = camera._configs(device)
        try:
            camera._set_mode(device, preview, AppSettings(exposure_mode='manual', shutter_us=125000))
            self.assertGreaterEqual(device.controls['FrameDurationLimits'][0], 126000)
            device.stop()
            camera._set_mode(device, still, AppSettings(exposure_mode='manual', shutter_us=8000))
            self.assertGreaterEqual(device.controls['FrameDurationLimits'][0],66667,'still timing stays conservative')
            device.camera_controls['FrameDurationLimits'] = (50000, 200000, 50000)
            camera._refresh_controls(device)
            short = camera._command(AppSettings(exposure_mode='manual', shutter_us=8000))
            self.assertGreaterEqual(short['FrameDurationLimits'][0], 50000)
        finally:
            device.stop()
            device.close()

    def test_fast_preview_does_not_accelerate_metering_feedback(self):
        device = Device()
        camera = PiCamera(factory=lambda: device)
        camera._refresh_controls(device)
        settings = AppSettings()
        metadata = {'ExposureTime': 8000, 'AnalogueGain': 2.}
        sample = Image.new('RGB', (820, 616), (60, 60, 60))
        with patch('camera_r4.camera.time.monotonic', return_value=10.):
            camera._meter(device, sample, metadata, settings)
        with patch('camera_r4.camera.time.monotonic', return_value=10.02):
            camera._meter(device, sample, metadata, settings)
        commands = [entry for entry in device.calls if isinstance(entry, tuple)]
        self.assertEqual(len(commands), 1, 'faster preview must not repeatedly steer delayed exposure metadata')
        with patch('camera_r4.camera.time.monotonic', return_value=10.12):
            camera._meter(device, sample, metadata, settings)
        self.assertEqual(len([entry for entry in device.calls if isinstance(entry, tuple)]), 2)


class PreviewResponsivenessTests(unittest.TestCase):
    setUp = ui.R4UITests.setUp
    tearDown = ui.R4UITests.tearDown
    until = ui.R4UITests.until

    def test_image_processing_runs_off_the_touch_thread(self):
        self.until(lambda: self.window._context is not None)
        threads = []
        original = gui.preview_context

        def traced(*args, **kwargs):
            threads.append(threading.get_ident())
            return original(*args, **kwargs)

        with patch('camera_r4.gui.preview_context', side_effect=traced):
            self.window._preview_key = None
            self.window.tick()
            self.until(lambda: bool(threads))
        self.assertNotIn(threading.get_ident(), threads, 'preview conversion blocks touch handling')

    def test_slow_preview_keeps_touch_responsive_and_does_not_queue_old_frames(self):
        self.until(lambda: self.window._context is not None)
        original = gui.preview_context
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def slow(image, settings):
            seen.append(image.getpixel((0, 0)))
            if len(seen) == 1:
                entered.set()
                release.wait(.5)
            return original(image, settings)

        try:
            with patch('camera_r4.gui.preview_context', side_effect=slow):
                self.window._preview_key = None
                start = time.monotonic()
                self.window.tick()
                self.assertLess(time.monotonic() - start, .1)
                self.assertTrue(entered.wait(1))
                self.window.open_panel()
                self.assertEqual(self.window.panel, 'menu')
                snapshot = self.camera.snapshot
                for seq in range(2, 8):
                    self.camera.image = Image.new('RGB', (820, 616), (seq, 0, 0))
                    self.camera.snapshot = lambda seq=seq: replace(snapshot(), sequence=seq)
                    self.window.tick()
                release.set()
                self.until(lambda: self.window._preview_key is not None and self.window._preview_key[0] == 7)
                self.assertEqual(seen, [(40, 110, 160), (7, 0, 0)])
        finally:
            release.set()

    def test_changed_settings_reject_an_inflight_preview(self):
        self.until(lambda: self.window._context is not None)
        original = gui.preview_context
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow(image, settings):
            entered.set()
            release.wait(.5)
            result = original(image, settings)
            finished.set()
            return result

        try:
            with patch('camera_r4.gui.preview_context', side_effect=slow):
                self.window._preview_key = None
                self.window.tick()
                self.assertTrue(entered.wait(1))
                self.window.settings = replace(self.window.settings, ratio='1:1')
                self.window._apply_camera(self.window.settings)
                self.camera.stale = True
                self.window._context = None
                release.set()
                self.assertTrue(finished.wait(1))
                self.window.tick()
                self.assertIsNone(self.window._context)
                self.camera.stale = False
                self.until(lambda: self.window._capture_image is not None and self.window._capture_image.height() == 480)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
