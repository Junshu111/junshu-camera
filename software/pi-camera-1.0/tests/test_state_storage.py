"""Real settings/JPEG behavior; persistent fixtures are intentionally retained."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from camera_r4.state import AppSettings, load_settings, save_settings
from camera_r4.storage import JPEGStore, read_photo, DeleteConfirmationRequired


class SettingsTests(unittest.TestCase):
    def test_switches_and_committed_values_persist_but_session_controls_reset(self):
        path = Path(tempfile.mkdtemp(prefix='r4-settings-')) / 'settings.json'
        desired = replace(AppSettings(), fullscreen=False, sound=False, hdr=True,
                          beauty=True, histogram=False, mirror=True, ratio='1:1',
                          ev=1.3, brightness=28, self_timer=10, ael=True,
                          locked_exposure_us=7000, locked_gain=4.0)
        save_settings(path, desired)
        actual = load_settings(path)
        self.assertEqual(actual, replace(desired, self_timer=0, ael=False,
                                        locked_exposure_us=0, locked_gain=0.0))
        payload = json.loads(path.read_text())
        self.assertNotIn('ael', payload['settings'])
        self.assertNotIn('self_timer', payload['settings'])

    def test_missing_current_file_migrates_legacy_without_modifying_legacy(self):
        directory = Path(tempfile.mkdtemp(prefix='r4-migration-'))
        legacy = directory / 'v020.json'
        source = {'version': 1, 'framing': {'ratio': '3:4', 'zoom': 2,
                  'rotation': 90, 'mirror': True, 'fullscreen': False}, 'review_seconds': 10}
        legacy.write_text(json.dumps(source))
        result = load_settings(directory / 'r4.json', legacy_path=legacy)
        self.assertEqual((result.ratio, result.zoom, result.rotation, result.review_seconds),
                         ('3:4', 2, 90, 10))
        self.assertTrue(result.sound)
        self.assertEqual(json.loads(legacy.read_text()), source)

    def test_invalid_and_unknown_settings_are_not_silently_accepted(self):
        for change in ({'ratio': 'bad'}, {'zoom': 0}, {'brightness': 101}, {'rotation': 13},
                       {'iso': -1}, {'hdr': 'true'}, {'ev': float('nan')},
                       {'self_timer': -1}, {'review_seconds': 1}, {'wb_kelvin': 0},
                       {'zoom': 1.0}, {'rotation': 90.0}, {'self_timer': 3.0}):
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                replace(AppSettings(), **change)
        directory = Path(tempfile.mkdtemp(prefix='r4-invalid-settings-'))
        current = directory / 'settings.json'
        current.write_text('{"version": 999, "settings": {}}')
        with self.assertRaises(ValueError):
            load_settings(current)
        self.assertEqual(load_settings(directory / 'missing.json'), AppSettings())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='r4-jpeg-'))
        self.store = JPEGStore(self.directory)

    def test_commit_is_decodable_and_digest_matches_saved_bytes(self):
        photo = self.store.commit(Image.new('RGB', (120, 200), 'orange'), {'ExposureTime': 4000})
        self.assertEqual((photo.width, photo.height), (120, 200))
        self.assertEqual(photo.sha256, hashlib.sha256(photo.path.read_bytes()).hexdigest())
        self.assertEqual(photo.byte_count, photo.path.stat().st_size)
        with Image.open(photo.path) as image:
            image.load()
            self.assertEqual(image.format, 'JPEG')
        self.assertEqual(self.store.list_photos(), [photo])

    def test_gallery_excludes_invalid_pending_and_symlink_files(self):
        good = self.store.commit(Image.new('RGB', (9, 16)))
        (self.directory / 'JC_bad.JPG').write_bytes(b'broken jpeg')
        (self.directory / '.pending-retained.tmp').write_bytes(b'incomplete')
        (self.directory / 'JC_link.JPG').symlink_to(good.path)
        self.assertEqual(self.store.list_photos(), [good])
        with self.assertRaises(ValueError):
            read_photo(self.directory / 'JC_link.JPG')

    def test_failed_write_keeps_pending_file_outside_gallery(self):
        with patch('camera_r4.storage.os.fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.commit(Image.new('RGB', (9, 16)))
        self.assertTrue(list(self.directory.glob('.pending-*')))
        self.assertEqual(self.store.list_photos(), [])

    def test_delete_requires_confirmation_and_does_not_delete_during_qa(self):
        photo = self.store.commit(Image.new('RGB', (9, 16)))
        calls = []
        store = JPEGStore(self.directory, unlink=calls.append)
        plan = store.plan_delete([photo.path])
        self.assertEqual(plan.total_bytes, photo.byte_count)
        self.assertEqual(plan.paths, (photo.path,))
        with self.assertRaises(DeleteConfirmationRequired):
            store.delete(plan)
        self.assertEqual(calls, [])
        self.assertEqual(store.delete(plan, confirmed=True), [photo.path])
        self.assertTrue(photo.path.exists(), 'QA boundary must never remove fixture files')
        self.assertEqual(calls, [photo.path])

    def test_delete_refuses_outside_root_symlink_and_changed_file_before_any_removal(self):
        outside = Path(tempfile.mkdtemp(prefix='r4-outside-')) / 'JC_other.JPG'
        Image.new('RGB', (9, 16)).save(outside)
        first = self.store.commit(Image.new('RGB', (9, 16), 'red'))
        second = self.store.commit(Image.new('RGB', (9, 16), 'blue'))
        calls = []
        store = JPEGStore(self.directory, unlink=calls.append)
        with self.assertRaises(ValueError):
            store.plan_delete([outside])
        link = self.directory / 'JC_link.JPG'
        link.symlink_to(first.path)
        with self.assertRaises(ValueError):
            store.plan_delete([link])
        plan = store.plan_delete([first.path, second.path])
        Image.new('RGB', (10, 17), 'green').save(second.path)
        with self.assertRaises(ValueError):
            store.delete(plan, confirmed=True)
        self.assertEqual(calls, [], 'Full deletion plan validates before first unlink')

    def test_partial_delete_failure_reports_completed_and_failed_paths(self):
        first = self.store.commit(Image.new('RGB', (9, 16), 'red'))
        second = self.store.commit(Image.new('RGB', (9, 16), 'blue'))
        calls = []
        def boundary(path):
            calls.append(path)
            if path == second.path:
                raise PermissionError('filesystem refused deletion')
        store = JPEGStore(self.directory, unlink=boundary)
        plan = store.plan_delete([first.path, second.path])
        with self.assertRaises(OSError) as raised:
            store.delete(plan, confirmed=True)
        self.assertEqual(getattr(raised.exception, 'removed_paths', None), (first.path,))
        self.assertEqual(getattr(raised.exception, 'failed_path', None), second.path)
        self.assertTrue(first.path.exists())
        self.assertTrue(second.path.exists())

    def test_latest_photo_first_and_duplicate_delete_paths_are_refused(self):
        first = self.store.commit(Image.new('RGB', (9, 16), 'red'))
        second = self.store.commit(Image.new('RGB', (9, 16), 'blue'))
        self.assertEqual([record.path for record in self.store.list_photos()], [second.path, first.path])
        with self.assertRaises(ValueError):
            self.store.plan_delete([first.path, first.path])


if __name__ == '__main__':
    unittest.main()
