"""Retained fixtures for the Pi umask=0002 session-install failure."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import unittest

BASE = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, BASE/path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class PermissionRepairTests(unittest.TestCase):
    def setUp(self):
        fixture = load('pi-camera-1.0/tests/test_newcam_session.py', 'repair_fixture')
        self.case = fixture.SessionTests('test_enable_defers_desktop_preserves_other_xdg_and_hides_old_entry')
        self.case.setUp()
        self.core = fixture.load()
        self.parent = self.case.home/'.local/state/junshu-camera'
        self.parent.mkdir(parents=True)
        for p in (self.case.home/'.local', self.case.home/'.local/state', self.parent):
            p.chmod(0o755)
        self.base = self.parent/'newcam-session'

    def reproduce(self):
        previous = os.umask(0o002)
        try:
            with self.assertRaisesRegex(ValueError, 'newcam-session'):
                self.core.configure('enable', root=self.case.root,
                                    expected_manifest_sha256=self.case.digest)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o775)

    def repair(self):
        helper = load('tools/camera_v1_session_permissions.py', 'permission_helper')
        return helper.repair(self.core, self.case.digest, root=self.case.root)

    def test_repair_then_enable_verify_repeat_and_rollback_under_umask002(self):
        self.reproduce()
        report = self.repair()
        self.assertTrue(report['changed'])
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.base/'mutation.lock').stat().st_mode), 0o600)
        self.assertEqual(json.loads(Path(report['permission_backup']).read_text())['directory_mode'], '0o775')
        self.assertFalse(self.repair()['changed'])
        remote = load('tools/camera_v1_remote.py', 'umask_remote')
        previous = os.umask(0o002)
        try:
            namespace = {'__name__': 'fixture'}
            exec(remote._trusted_action_source(BASE/'pi-camera-1.0/session_setup.py', remote.APP_PREFIX), namespace)
            for action in ('enable', 'verify', 'enable', 'rollback', 'rollback'):
                result = namespace['configure'](action, root=self.case.root,
                                                expected_manifest_sha256=self.case.digest)
                self.assertTrue(result['ok'])
        finally:
            os.umask(previous)
        self.assertEqual(self.case.old.read_bytes(), self.core.OLD)

    def test_safe_new_install_under_loose_umask_needs_no_permission_repair(self):
        self.assertFalse(self.repair()['changed'])
        remote = load('tools/camera_v1_remote.py', 'fresh_umask_remote')
        previous = os.umask(0o002)
        try:
            namespace = {'__name__': 'fixture'}
            exec(remote._trusted_action_source(BASE/'pi-camera-1.0/session_setup.py', remote.APP_PREFIX), namespace)
            namespace['configure']('enable', root=self.case.root, expected_manifest_sha256=self.case.digest)
            self.assertTrue(namespace['configure']('verify', root=self.case.root)['configured'])
        finally:
            os.umask(previous)

    def test_unknown_contents_or_mode_refuse_without_chmod(self):
        self.reproduce()
        (self.base/'state.json').write_text('unknown')
        with self.assertRaises(ValueError): self.repair()
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o775)
        (self.base/'state.json').rename(self.parent/'retained-unknown-state.json')
        self.base.chmod(0o777)
        with self.assertRaises(ValueError): self.repair()
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o777)

    def test_symlink_lock_or_busy_lock_refuse(self):
        self.reproduce()
        import fcntl
        with (self.base/'mutation.lock').open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(ValueError): self.repair()
        (self.base/'mutation.lock').rename(self.parent/'retained-lock')
        (self.base/'mutation.lock').symlink_to(self.parent/'retained-lock')
        with self.assertRaises((ValueError, OSError)): self.repair()
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o775)

    def test_repair_install_reuses_target_and_continues_only_after_valid_repair(self):
        from unittest.mock import patch
        import contextlib, io
        remote = load('tools/camera_v1_remote.py', 'repair_cli')
        events = []
        class Ops:
            def __init__(self, target, release): events.append(('target', target))
            def repair_session_permissions(self):
                events.append('repair')
                return {'ok': True, 'changed': True}
        with patch.object(remote, 'RemoteOps', Ops), \
             patch.object(remote.network, 'target_input', return_value='junshu@camera-test.local'), \
             patch.object(remote.network, 'save_report'), \
             patch.object(remote, 'coordinate_install', side_effect=lambda ops: events.append('install') or {'success':True, 'status':'staged'}) as install, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(remote.main(['repair-install']), 0)
        self.assertEqual(events, [('target', 'junshu@camera-test.local'), 'repair', 'install'])
        with patch.object(remote, 'RemoteOps', Ops), \
             patch.object(Ops, 'repair_session_permissions', return_value={'ok':False, 'error':'unknown directory'}), \
             patch.object(remote.network, 'target_input', return_value='junshu@camera-test.local'), \
             patch.object(remote.network, 'save_report'), \
             patch.object(remote, 'coordinate_install') as install, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(remote.main(['repair-install']), 1)
            install.assert_not_called()

    def test_package_or_parent_or_startup_drift_refuse_before_repair(self):
        self.reproduce()
        original=(self.case.app/'run_camera.py').read_bytes()
        (self.case.app/'run_camera.py').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.repair()
        (self.case.app/'run_camera.py').write_bytes(original)
        self.parent.chmod(0o775)
        with self.assertRaises(ValueError): self.repair()
        self.parent.chmod(0o755)
        self.case.old.write_bytes(self.core.OLD_HIDDEN)
        with self.assertRaises(ValueError): self.repair()
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o775)


if __name__ == '__main__': unittest.main()
