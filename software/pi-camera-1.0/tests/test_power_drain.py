"""Power preparation cannot lose accepted work or reopen a closed capture service."""
import threading
import unittest
from concurrent.futures import Future
import test_capture_r4 as capture_tests
from camera_r4.capture import CaptureBlocked
from camera_r4.state import AppSettings
from camera_r4.power_bridge import drain
import time


class PowerDrainTests(unittest.TestCase):
    make_service = capture_tests.CaptureTests.make_service

    def test_prepare_timeout_preserves_photo_and_allows_later_capture(self):
        camera, store, service = self.make_service()
        shot = service.request(AppSettings())
        self.assertTrue(camera.started.wait(1))
        with self.assertRaises(TimeoutError):
            service.prepare_shutdown(.01)
        self.assertFalse(shot.done())
        camera.release.set()
        self.assertTrue(shot.result(5).path.exists())
        self.assertTrue(service.request(AppSettings()).result(5).path.exists())
        self.assertEqual(len(store.list_photos()), 2)
        service.close()

    def test_prepared_gate_resets_on_cancel_but_closed_never_reopens(self):
        camera, store, service = self.make_service()
        camera.release.set()
        service.prepare_shutdown()
        with self.assertRaises(CaptureBlocked): service.request(AppSettings())
        service.cancel_shutdown()
        self.assertTrue(service.request(AppSettings()).result(5).path.exists())
        service.close()
        service.cancel_shutdown()
        with self.assertRaises(CaptureBlocked): service.request(AppSettings())

    def test_cancel_while_capturing_does_not_interrupt_durable_save(self):
        camera, store, service = self.make_service()
        shot = service.request(AppSettings())
        cancel = threading.Event();cancel.set()
        with self.assertRaisesRegex(CaptureBlocked, '取消'):
            service.prepare_shutdown(.5, cancel)
        camera.release.set()
        self.assertTrue(shot.result(5).path.exists())
        service.close()

    def test_settings_error_and_timeout_never_reach_ready(self):
        camera, store, service = self.make_service()
        error = Future();error.set_exception(OSError('settings full'))
        with self.assertRaisesRegex(OSError, 'settings full'):
            drain(service, [error], threading.Event(), time.monotonic()+.5)
        with self.assertRaises(TimeoutError):
            drain(service, [Future()], threading.Event(), time.monotonic()+.01)
        camera.release.set()
        self.assertTrue(service.request(AppSettings()).result(5).path.exists())
        service.close()


if __name__ == '__main__': unittest.main()
