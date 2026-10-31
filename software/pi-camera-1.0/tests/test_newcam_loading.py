"""Start-screen handoff tests. No camera hardware or runtime files are changed."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import time
import unittest
import importlib.util
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def load():
    path=APP/'camera_r4/loading.py'
    assert path.is_file(), 'loading.py must exist'
    spec=importlib.util.spec_from_file_location('newcam_loading',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowDouble:
    def __init__(self):
        self.first_preview_ready = False
        self.exit_complete = False
        self.page='live'
        self.message=''
        self.exit_job=None
        self.calls = []
        self.on_first_preview_painted = None
    def showFullScreen(self):
        self.calls.append('show')
    def request_exit(self, confirmed=False):
        self.calls.append(('exit',confirmed))


class SplashDouble:
    def __init__(self):self.closed = False
    def close(self):self.closed = True


class AppDouble:
    def __init__(self):self.code = None
    def exit(self, code):self.code = code


class FrameDouble:
    def __init__(self,error=None):self.error=error


class CameraDouble:
    def __init__(self):self.error=None
    def snapshot(self):return FrameDouble(self.error)


class LoadingTests(unittest.TestCase):
    def test_logo_validation_accepts_exact_rgb_asset_and_rejects_bad_header(self):
        path = APP/'assets/boot-logo.png'
        before = path.read_bytes()
        self.assertEqual(load().logo_dimensions(path),(480,800))
        self.assertEqual(path.read_bytes(),before)
        bad=APP.parent/'qa/newcam-1.0-20261028/bad-logo-fixture.png'
        bad.parent.mkdir(parents=True,exist_ok=True)
        bad.write_bytes(b'not a PNG')
        with self.assertRaises(ValueError):load().logo_dimensions(bad)

    def test_camera_only_shows_after_prepared_preview_and_closes_logo_after_paint(self):
        window, camera, splash, qt = WindowDouble(),CameraDouble(),SplashDouble(),AppDouble()
        gate = load().StartupGate(window,camera,splash,qt)
        gate.poll()
        self.assertEqual(window.calls,[])
        self.assertFalse(splash.closed)
        window.first_preview_ready=True
        gate.poll()
        self.assertEqual(window.calls,['show'])
        self.assertFalse(splash.closed)
        window.on_first_preview_painted()
        self.assertTrue(splash.closed)
        self.assertIsNone(qt.code)

    def test_error_and_startup_timeout_release_logo_after_cleanup(self):
        for failure in ('camera','timeout'):
            window,camera,splash,qt=WindowDouble(),CameraDouble(),SplashDouble(),AppDouble()
            gate=load().StartupGate(window,camera,splash,qt,timeout_seconds=45)
            if failure=='camera':camera.error='CM2 unavailable'
            else:gate.started_at=time.monotonic()-46
            gate.poll()
            self.assertEqual(window.calls,['show',('exit',True)])
            self.assertEqual(window.page,'error')
            gate.poll()
            self.assertEqual(window.calls,['show',('exit',True)])
            self.assertTrue(splash.closed)
            window.exit_complete=True
            gate.poll()
            self.assertTrue(splash.closed)
            self.assertEqual(qt.code,1)

    def test_cleanup_failure_never_leaves_logo_covering_error(self):
        window,camera,splash,qt=WindowDouble(),CameraDouble(),SplashDouble(),AppDouble()
        camera.error='CM2 missing'
        gate=load().StartupGate(window,camera,splash,qt)
        gate.poll()
        window.page='error';window.message='退出资源失败';window.exit_job=None
        gate.poll()
        self.assertTrue(splash.closed)
        self.assertEqual(window.calls[0],'show')
        self.assertEqual(window.page,'error')
        self.assertIsNone(qt.code)

    def test_user_exit_during_loading_releases_logo_without_waiting_for_frame(self):
        window,camera,splash,qt=WindowDouble(),CameraDouble(),SplashDouble(),AppDouble()
        gate=load().StartupGate(window,camera,splash,qt)
        window.exit_complete=True
        gate.poll()
        self.assertTrue(splash.closed)
        self.assertEqual(qt.code,0)


if __name__=='__main__':unittest.main()
