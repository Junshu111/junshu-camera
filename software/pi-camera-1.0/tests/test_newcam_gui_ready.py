"""Qt offscreen proof that the logo is released only after a real preview paint."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import tempfile
import time
import unittest
from pathlib import Path

from PyQt5.QtWidgets import QApplication
from camera_r4.gui import CameraWindow
from camera_r4.loading import StartupGate,create_loading_window
from camera_r4.storage import JPEGStore
from test_gui_r4 import CameraDouble,DeviceDouble

app=QApplication.instance() or QApplication([])
QA=Path(__file__).resolve().parents[2]/'qa/newcam-1.0-20261028/gui-fixtures'


class FirstPaintTest(unittest.TestCase):
    def test_late_handoff_callback_still_receives_a_live_repaint(self):
        QA.mkdir(parents=True,exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='late-',dir=QA))
        camera=CameraDouble()
        window=CameraWindow(camera,JPEGStore(root/'photos'),root/'settings.json',device=DeviceDouble())
        window.show()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not window.first_preview_ready:
            window.tick();app.processEvents();time.sleep(.01)
        self.assertTrue(window.first_preview_ready)
        app.processEvents()
        hit=[]
        window.on_first_preview_painted=lambda:hit.append('painted')
        window.update();app.processEvents();app.processEvents()
        self.assertEqual(hit,['painted'])
        window.request_exit(confirmed=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not window.exit_complete:
            window.tick();app.processEvents();time.sleep(.01)
        self.assertTrue(window.exit_complete)
        window.deleteLater();app.processEvents()

    def test_real_qt_splash_handoff_keeps_camera_window_visible(self):
        QA.mkdir(parents=True,exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='handoff-',dir=QA))
        camera=CameraDouble()
        splash=create_loading_window(Path(__file__).resolve().parents[1]/'assets/boot-logo.png')
        splash.showFullScreen();app.processEvents()
        window=CameraWindow(camera,JPEGStore(root/'photos'),root/'settings.json',device=DeviceDouble())
        gate=StartupGate(window,camera,splash,app)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not gate.finished:
            window.tick();gate.poll();app.processEvents();time.sleep(.01)
        self.assertTrue(gate.finished)
        self.assertTrue(window.isVisible())
        self.assertFalse(splash.isVisible())
        window.request_exit(confirmed=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not window.exit_complete:
            window.tick();app.processEvents();time.sleep(.01)
        self.assertTrue(window.exit_complete)
        window.deleteLater();splash.deleteLater();app.processEvents()

    def test_first_preview_callback_waits_for_prepared_frame_and_fires_once(self):
        QA.mkdir(parents=True,exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='case-',dir=QA))
        camera=CameraDouble();camera.stale=True
        window=CameraWindow(camera,JPEGStore(root/'photos'),root/'settings.json',device=DeviceDouble())
        painted=[]
        window.on_first_preview_painted=lambda:painted.append('live')
        window.show()
        for _ in range(8):
            app.processEvents();window.tick()
        self.assertFalse(getattr(window,'first_preview_ready',False))
        self.assertEqual(painted,[])
        camera.stale=False
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not painted:
            app.processEvents();window.tick();time.sleep(.01)
        self.assertTrue(getattr(window,'first_preview_ready',False))
        self.assertEqual(painted,['live'])
        for _ in range(5):app.processEvents();window.tick()
        self.assertEqual(painted,['live'])
        window.request_exit(confirmed=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not window.exit_complete:
            app.processEvents();window.tick();time.sleep(.01)
        self.assertTrue(window.exit_complete)
        window.deleteLater();app.processEvents()


if __name__=='__main__':unittest.main()
