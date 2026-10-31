"""New Cam 1.0 session behavior; all fixture files are retained."""
import hashlib
import fcntl
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
QA = APP.parent / 'qa/newcam-1.0-20261028/session-fixtures'
SYSTEM = (b'/usr/bin/lwrespawn /usr/bin/pcmanfm-pi &\n'
          b'/usr/bin/lwrespawn /usr/bin/wf-panel-pi &\n'
          b'/usr/bin/kanshi &\n/usr/bin/lxsession-xdg-autostart\n')
OLD = (b'[Desktop Entry]\nType=Application\nName=Junshu Camera\n'
       b'Exec=/bin/sh /home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh\n'
       b'Terminal=false\nStartupNotify=false\nX-Junshu-Managed=0.4.0\n')


def load():
    assert (APP/'session_setup.py').is_file(), 'session_setup.py must exist'
    spec = importlib.util.spec_from_file_location('newcam_session_setup', APP/'session_setup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=QA))
        self.home = self.root/'home/junshu'
        self.home.mkdir(parents=True)
        system = self.root/'etc/xdg/labwc/autostart'
        system.parent.mkdir(parents=True)
        system.write_bytes(SYSTEM)
        self.old = self.home/'.config/autostart/junshu-camera-r4-integrated.desktop'
        self.old.parent.mkdir(parents=True)
        self.old.write_bytes(OLD)
        self.old.chmod(0o640)
        self.app = self.home/'junshu-camera-pi-1.0.0'
        self.app.mkdir()
        members = {'run_camera.py': b'print("newcam")\n',
                   'start-session.sh': b'#!/bin/sh\nexit 0\n',
                   'start-autostart.sh': b'#!/bin/sh\nexit 0\n',
                   'session_setup.py': b'print("setup")\n',
                   'assets/boot-logo.png': b'fixture-logo'}
        for name, data in members.items():
            p = self.app/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        manifest = b''.join(hashlib.sha256(data).hexdigest().encode()+b'  '+name.encode()+b'\n' for name,data in members.items())
        (self.app/'MANIFEST.sha256').write_bytes(manifest)
        self.digest = hashlib.sha256(manifest).hexdigest()

    def test_enable_defers_desktop_preserves_other_xdg_and_hides_old_entry(self):
        other = self.home/'.config/autostart/other.desktop'
        other.write_text('[Desktop Entry]\nName=Other\nExec=/usr/bin/true\n')
        result = load().configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        self.assertTrue(result['ok'])
        self.assertTrue(result['changed'])
        hook = (self.home/'.config/labwc/autostart').read_text()
        self.assertIn('/usr/bin/kanshi &', hook)
        self.assertIn('/usr/bin/lxsession-xdg-autostart', hook)
        self.assertIn('/start-session.sh', hook)
        self.assertNotIn('pcmanfm-pi', hook)
        self.assertNotIn('wf-panel-pi', hook)
        self.assertEqual(self.old.read_bytes(), OLD+b'Hidden=true\n')
        self.assertEqual(stat.S_IMODE(self.old.stat().st_mode), 0o640)
        self.assertIn('Name=Other', other.read_text())
        self.assertEqual(load().configure('enable', root=self.root, expected_manifest_sha256=self.digest)['changed'], False)

    def test_unknown_customized_session_or_duplicate_camera_refuses_without_writes(self):
        hook = self.home/'.config/labwc/autostart'
        hook.parent.mkdir(parents=True)
        hook.write_text('# custom\n/usr/bin/lxsession-xdg-autostart\n')
        with self.assertRaises(ValueError):
            load().configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        self.assertEqual(hook.read_text(), '# custom\n/usr/bin/lxsession-xdg-autostart\n')
        self.assertEqual(self.old.read_bytes(), OLD)
        hook.write_bytes(SYSTEM)
        duplicate = self.home/'.config/autostart/second-camera.desktop'
        duplicate.write_text('[Desktop Entry]\nExec=/home/junshu/other/run_camera.py\n')
        with self.assertRaises(ValueError):
            load().configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        self.assertEqual(self.old.read_bytes(), OLD)

    def test_commented_hidden_text_does_not_mask_active_duplicate(self):
        duplicate=self.home/'.config/autostart/second-camera.desktop'
        duplicate.write_text('[Desktop Entry]\n# Hidden=true\nExec=/home/junshu/other/run_camera.py\n')
        with self.assertRaises(ValueError):
            load().configure('enable',root=self.root,expected_manifest_sha256=self.digest)
        self.assertEqual(self.old.read_bytes(),OLD)

    def test_rollback_restores_old_entry_and_delegates_missing_user_hook_without_deletion(self):
        load().configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        result = load().configure('rollback', root=self.root)
        self.assertTrue(result['changed'])
        hook = (self.home/'.config/labwc/autostart').read_text()
        self.assertEqual(hook, '#!/bin/sh\nexec /bin/sh /etc/xdg/labwc/autostart\n')
        self.assertEqual(self.old.read_bytes(), OLD)
        self.assertEqual(stat.S_IMODE(self.old.stat().st_mode), 0o640)
        self.assertTrue(any((self.home/'.local/state/junshu-camera/newcam-session/backups').iterdir()))
        self.assertEqual(load().configure('verify', root=self.root)['configured'], False)

    def test_rollback_without_state_refuses_managed_hook_or_hidden_old_entry(self):
        module = load()
        hook = self.home/'.config/labwc/autostart'
        hook.parent.mkdir(parents=True)
        hook.write_bytes(module.HOOK)
        with self.assertRaises(ValueError):
            module.configure('rollback', root=self.root)
        hook.write_bytes(module.SHIM)
        self.old.write_bytes(module.OLD_HIDDEN)
        with self.assertRaises(ValueError):
            module.configure('rollback', root=self.root)

    def test_rolled_back_retry_refuses_live_drift(self):
        module = load()
        module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        module.configure('rollback', root=self.root)
        hook = self.home/'.config/labwc/autostart'
        hook.write_bytes(module.HOOK)
        with self.assertRaises(ValueError):
            module.configure('rollback', root=self.root)
        hook.write_bytes(module.SHIM)
        self.old.write_bytes(module.OLD_HIDDEN)
        with self.assertRaises(ValueError):
            module.configure('rollback', root=self.root)

    def test_active_verify_refuses_damaged_installed_release(self):
        module = load()
        module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        (self.app/'run_camera.py').write_text('damaged after enable\n')
        with self.assertRaises(ValueError):
            module.configure('verify', root=self.root)

    def test_active_verify_and_repeat_enable_refuse_damaged_backup(self):
        module = load()
        result = module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        backup = self.home/'.local/state/junshu-camera/newcam-session/backups'/result['backup_id']
        (backup/'old.bin').write_bytes(b'damaged backup')
        with self.assertRaises(ValueError):
            module.configure('verify', root=self.root)
        with self.assertRaises(ValueError):
            module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)

    def test_active_verify_and_repeat_enable_refuse_damaged_original_hook_backup(self):
        module = load()
        hook = self.home/'.config/labwc/autostart'
        hook.parent.mkdir(parents=True)
        hook.write_bytes(SYSTEM)
        hook.chmod(0o750)
        result = module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        backup = self.home/'.local/state/junshu-camera/newcam-session/backups'/result['backup_id']
        (backup/'hook.bin').write_bytes(b'damaged original hook')
        with self.assertRaises(ValueError):
            module.configure('verify', root=self.root)
        with self.assertRaises(ValueError):
            module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)

    def test_active_verify_and_rolled_back_retry_succeed_when_intact(self):
        module = load()
        module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        verified = module.configure('verify', root=self.root)
        self.assertTrue(verified['configured'])
        self.assertEqual(verified['manifest_sha256'], self.digest)
        module.configure('rollback', root=self.root)
        self.assertFalse(module.configure('rollback', root=self.root)['changed'])

    def test_active_verify_detects_metadata_or_candidate_backup_damage(self):
        module = load()
        result = module.configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        backup = self.home/'.local/state/junshu-camera/newcam-session/backups'/result['backup_id']
        metadata = backup/'metadata.json'
        original = metadata.read_bytes()
        record = json.loads(original)
        record['release_manifest_sha256'] = '0'*64
        metadata.write_text(json.dumps(record)+'\n')
        with self.assertRaises(ValueError):
            module.configure('verify', root=self.root)
        metadata.write_bytes(original)
        (backup/'candidate-hook.bin').write_bytes(b'damaged candidate')
        with self.assertRaises(ValueError):
            module.configure('verify', root=self.root)

    def test_manifest_tamper_refuses_enable(self):
        (self.app/'run_camera.py').write_text('tampered\n')
        with self.assertRaises(ValueError):
            load().configure('enable', root=self.root, expected_manifest_sha256=self.digest)
        self.assertEqual(self.old.read_bytes(), OLD)

    def test_unlisted_release_file_refuses_enable(self):
        (self.app/'unlisted-launcher.sh').write_text('#!/bin/sh\n')
        with self.assertRaises(ValueError):
            load().configure('enable',root=self.root,expected_manifest_sha256=self.digest)
        self.assertEqual(self.old.read_bytes(),OLD)

    def test_enable_marks_pending_and_hides_old_camera_before_installing_hook(self):
        module=load();real_write=module.write_known
        state=self.home/'.local/state/junshu-camera/newcam-session/state.json'
        hook=self.home/'.config/labwc/autostart'
        def inspect_write(path,data,mode):
            if path==self.old and data==OLD+b'Hidden=true\n':
                self.assertEqual(json.loads(state.read_text())['status'],'pending')
            if path==hook and data==module.HOOK:
                self.assertEqual(self.old.read_bytes(),OLD+b'Hidden=true\n')
            return real_write(path,data,mode)
        with patch.object(module,'write_known',side_effect=inspect_write):
            self.assertTrue(module.configure('enable',root=self.root,expected_manifest_sha256=self.digest)['changed'])

    def test_interrupted_pending_enable_can_rollback_without_old_new_double_start(self):
        module=load()
        module.configure('enable',root=self.root,expected_manifest_sha256=self.digest)
        state=self.home/'.local/state/junshu-camera/newcam-session/state.json'
        record=json.loads(state.read_text());record['status']='pending'
        state.write_text(json.dumps(record)+'\n')
        hook=self.home/'.config/labwc/autostart'
        hook.write_bytes(module.SHIM)
        self.assertEqual(self.old.read_bytes(),OLD+b'Hidden=true\n')
        result=module.configure('rollback',root=self.root)
        self.assertFalse(result['configured'])
        self.assertEqual(self.old.read_bytes(),OLD)
        self.assertEqual(hook.read_bytes(),module.SHIM)

    def test_mutation_lock_refuses_concurrent_enable(self):
        module=load()
        base=self.home/'.local/state/junshu-camera/newcam-session'
        base.mkdir(parents=True)
        lock=base/'mutation.lock'
        with lock.open('a+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                module.configure('enable',root=self.root,expected_manifest_sha256=self.digest)
        self.assertEqual(self.old.read_bytes(),OLD)

    def test_start_session_reveals_desktop_after_normal_or_failed_camera_once(self):
        script = APP/'start-session.sh'
        self.assertTrue(script.exists())
        fake_app = self.root/'app'
        fake_app.mkdir()
        (fake_app/'start-session.sh').write_bytes(script.read_bytes())
        (fake_app/'start-autostart.sh').write_text('#!/bin/sh\nexit "${CAMERA_EXIT:-0}"\n')
        bin_dir = self.root/'bin'
        bin_dir.mkdir()
        respawn = bin_dir/'lwrespawn'
        respawn.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/desktop-calls"\n')
        respawn.chmod(0o755)
        for code in (0, 7):
            env = dict(os.environ, HOME=str(self.home), WAYLAND_DISPLAY='wayland-test', DISPLAY='',
                       PATH=str(bin_dir)+':/usr/bin:/bin', CAMERA_EXIT=str(code))
            result = subprocess.run(['/bin/sh', str(fake_app/'start-session.sh')], env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, code, result.stderr)
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            path = self.home/'desktop-calls'
            if path.exists() and len(path.read_text().splitlines())>=4:
                break
            time.sleep(.01)
        calls = (self.home/'desktop-calls').read_text().splitlines()
        self.assertEqual(calls.count('/usr/bin/pcmanfm-pi'), 2)
        self.assertEqual(calls.count('/usr/bin/wf-panel-pi'), 2)


if __name__ == '__main__':
    unittest.main()
