"""Accepted shots are single, truthful, and waited on before close."""
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from PIL import Image
from camera_r4.state import AppSettings
from camera_r4.capture import CaptureService, CaptureBlocked, CaptureBusy
from camera_r4.storage import JPEGStore


class ControlledCamera:
    def __init__(self, fail=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail = fail
        self.received = None
        self.last_still_metadata = {'ExposureTime': 8000, 'AnalogueGain': 4.0}

    def capture_image(self, settings):
        self.received = settings
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError('test camera gate expired')
        if self.fail:
            raise OSError('real camera failure')
        return Image.new('RGB', (600, 1000), 'green')


class CaptureTests(unittest.TestCase):
    def make_service(self, fail=False):
        camera = ControlledCamera(fail)
        store = JPEGStore(Path(tempfile.mkdtemp(prefix='r4-capture-')))
        return camera, store, CaptureService(camera, store)

    def test_busy_shot_rejected_then_result_is_real_cropped_jpeg(self):
        camera, store, service = self.make_service()
        desired = replace(AppSettings(), ratio='1:1')
        future = service.request(desired)
        self.assertTrue(camera.started.wait(1))
        self.assertEqual(service.snapshot().phase, 'capturing')
        with self.assertRaises(CaptureBusy):
            service.request(AppSettings())
        camera.release.set()
        photo = future.result(5)
        self.assertEqual((photo.width, photo.height), (600, 600))
        self.assertEqual(camera.received, desired)
        self.assertEqual(service.snapshot().photo, photo)
        self.assertEqual(store.list_photos(), [photo])
        service.close()

    def test_close_waits_for_save_before_closed(self):
        camera, store, service = self.make_service()
        future = service.request(AppSettings())
        self.assertTrue(camera.started.wait(1))
        errors = []
        done = threading.Event()
        def close():
            try:
                service.close(5)
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        closer = threading.Thread(target=close)
        closer.start()
        self.assertFalse(done.wait(.05))
        camera.release.set()
        result = future.result(5)
        self.assertTrue(done.wait(5))
        closer.join(1)
        self.assertEqual(errors, [])
        self.assertEqual(service.snapshot().phase, 'closed')
        self.assertTrue(result.path.exists())
        with self.assertRaises(CaptureBlocked):
            service.request(AppSettings())

    def test_capture_failure_cancels_pending_close_and_requires_recovery(self):
        camera, store, service = self.make_service(fail=True)
        future = service.request(AppSettings())
        self.assertTrue(camera.started.wait(1))
        with self.assertRaises(TimeoutError):
            service.close(.01)
        camera.release.set()
        with self.assertRaisesRegex(OSError, 'real camera failure'):
            future.result(5)
        self.assertEqual(service.snapshot().phase, 'error')
        self.assertEqual(store.list_photos(), [])
        with self.assertRaises(CaptureBlocked):
            service.close()
        service.recover()
        camera.fail = False
        result = service.request(AppSettings()).result(5)
        self.assertTrue(result.path.exists())
        service.close()

    def test_saving_rejects_another_shot_and_close_timeout_keeps_worker_alive(self):
        camera, store, service = self.make_service()
        save_started = threading.Event()
        save_release = threading.Event()
        commit = store.commit
        def delayed_commit(image, metadata=None):
            save_started.set()
            if not save_release.wait(5):
                raise TimeoutError('test save gate expired')
            return commit(image, metadata)
        store.commit = delayed_commit
        camera.release.set()
        future = service.request(AppSettings())
        self.assertTrue(save_started.wait(1))
        self.assertEqual(service.snapshot().phase, 'saving')
        with self.assertRaises(CaptureBusy):
            service.request(AppSettings())
        with self.assertRaises(TimeoutError):
            service.close(.01)
        self.assertFalse(future.done())
        save_release.set()
        result = future.result(5)
        service.close()
        self.assertEqual(service.snapshot().phase, 'closed')
        self.assertTrue(result.path.exists())

    def test_failure_cancels_a_close_already_waiting_in_an_exit_worker(self):
        camera, store, service = self.make_service(fail=True)
        future = service.request(AppSettings())
        self.assertTrue(camera.started.wait(1))
        close_started = threading.Event()
        closed = threading.Event()
        errors = []
        def close():
            close_started.set()
            try:
                service.close(5)
            except Exception as error:
                errors.append(error)
            finally:
                closed.set()
        closer = threading.Thread(target=close)
        closer.start()
        self.assertTrue(close_started.wait(1))
        self.assertFalse(closed.wait(.03))
        camera.release.set()
        with self.assertRaises(OSError):
            future.result(5)
        self.assertTrue(closed.wait(5))
        closer.join(1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CaptureBlocked)
        self.assertEqual(service.snapshot().phase, 'error')
        service.recover()
        camera.fail = False
        self.assertTrue(service.request(AppSettings()).result(5).path.exists())
        service.close()

    def test_save_failure_reported_and_no_false_success(self):
        camera, store, service = self.make_service()
        def failed_commit(image, metadata=None):
            raise OSError('storage full')
        store.commit = failed_commit
        camera.release.set()
        with self.assertRaisesRegex(OSError, 'storage full'):
            service.request(AppSettings()).result(5)
        self.assertEqual(service.snapshot().phase, 'error')
        self.assertIsNone(service.snapshot().photo)
        with self.assertRaises(CaptureBlocked):
            service.request(AppSettings())


if __name__ == '__main__':
    unittest.main()
