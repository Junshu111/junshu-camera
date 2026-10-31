"""Retained offline 0.1.1 to 1.0 boot upgrade and exact rollback contracts."""
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

BASE = Path(__file__).resolve().parents[2]
NEW_TOOL = BASE / 'pi-boot-logo-1.0/boot_system.py'
OLD_TEST = BASE / 'qa/r4-boot-logo-20261028/tests/test_boot_system.py'
spec = importlib.util.spec_from_file_location('old_boot_fixture', OLD_TEST)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


class UpgradeTests(unittest.TestCase):
    write = old.BootSystemTest.write
    originals = old.BootSystemTest.originals
    run_old = old.BootSystemTest.run_tool

    @classmethod
    def setUpClass(cls):
        base = BASE / 'qa/r4-boot-logo-20261028/test-fixtures'
        cls.run_dir = base / ('newcam-1.0-' + uuid.uuid4().hex)
        cls.run_dir.mkdir(parents=True)

    def setUp(self):
        old.BootSystemTest.setUp(self)
        self.old_bundle = self.bundle
        self.new_bundle = self.root.parent / (self._testMethodName + '-new-bundle')
        (self.new_bundle / 'theme').mkdir(parents=True)
        for name in ('newcam.plymouth', 'newcam.script', 'logo.png'):
            (self.new_bundle / 'theme' / name).write_bytes(('NEWCAM ' + name + '\n').encode())
        (self.new_bundle / 'VERSION').write_text('1.0.0\n')
        (self.new_bundle / 'boot_system.py').write_bytes(NEW_TOOL.read_bytes())
        names = sorted(('VERSION', 'boot_system.py', 'theme/newcam.plymouth',
                        'theme/newcam.script', 'theme/logo.png'))
        manifest = ''.join(hashlib.sha256((self.new_bundle / name).read_bytes()).hexdigest()
                           + '  ' + name + '\n' for name in names)
        (self.new_bundle / 'MANIFEST.sha256').write_text(manifest)
        self.new_manifest_sha = hashlib.sha256(manifest.encode()).hexdigest()

    def run_new(self, action):
        env = dict(os.environ, JUNSHU_BOOT_LOGO_FIXTURE='1', JUNSHU_BOOT_ROOT=str(self.root),
                   PYTHONDONTWRITEBYTECODE='1')
        args = [sys.executable, str(NEW_TOOL), action, '--root', str(self.root)]
        if action == 'enable':
            args += ['--bundle', str(self.new_bundle),
                     '--expected-manifest-sha256', self.new_manifest_sha]
        result = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result, json.loads(result.stdout)

    def install_old(self):
        # The existing Pi cmdline has no final LF; retain that exact relationship.
        self.write(old.CMDLINE, b'console=tty1 root=PARTUUID=abc-02 rootwait loglevel=7')
        result, report = self.run_old('enable')
        self.assertEqual(result.returncode, 0, report)
        self.old_active = self.originals()
        self.old_state_path = self.root / 'var/lib/junshu-camera-boot-logo-test/state.json'
        self.old_state = self.old_state_path.read_bytes()
        self.write_dual_builder()

    def write_dual_builder(self):
        # The fixture builder must include the selected theme in each generated image.
        self.write('usr/sbin/mkinitramfs', '''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
root=Path(os.environ['JUNSHU_BOOT_ROOT'])
if (root/'fail-mkinitramfs').exists(): raise SystemExit(23)
theme=(root/'etc/plymouth/plymouthd.conf').read_text().split('Theme=',1)[1].splitlines()[0]
names=('newcam.plymouth','newcam.script','logo.png') if theme=='newcam' else ('junshu-camera-test.plymouth','junshu-camera-test.script','page-1.png','page-2.png')
out=Path(sys.argv[sys.argv.index('-o')+1])
out.write_text('IMAGE\\nusr/sbin/plymouthd\\nusr/lib/aarch64-linux-gnu/plymouth/script.so\\n'+''.join('usr/share/plymouth/themes/'+theme+'/'+n+'\\n' for n in names))
''', executable=True)

    def test_active_old_upgrade_verify_repeat_and_exact_rollback(self):
        self.install_old()
        result, report = self.run_new('probe')
        self.assertEqual(result.returncode, 0, report)
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        self.assertTrue(report['changed'])
        self.assertEqual(self.old_state_path.read_bytes(), self.old_state)
        self.assertIn(b'BEGIN JUNSHU NEW CAM 1.0', (self.root / old.CONFIG).read_bytes())
        self.assertEqual((self.root / old.PLYMOUTH_CONF).read_text().split('Theme=')[1].splitlines()[0], 'newcam')
        self.assertTrue((self.root/'var/lib/junshu-camera-boot-1.0.0/state.json').is_file())
        result, report = self.run_new('verify')
        self.assertEqual(result.returncode, 0, report)
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        self.assertFalse(report['changed'])
        result, report = self.run_new('rollback')
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(self.originals(), self.old_active)
        self.assertEqual(self.old_state_path.read_bytes(), self.old_state)
        self.assertFalse((self.root / old.CMDLINE).read_bytes().endswith(b'\n'))
        result, report = self.run_old('verify')
        self.assertEqual(result.returncode, 0, report)
        result, report = self.run_old('rollback')
        self.assertEqual(result.returncode, 0, report)

    def test_fresh_newcam_install_and_rollback_preserve_originals(self):
        self.write_dual_builder()
        before = self.originals()
        result, report = self.run_new('probe')
        self.assertEqual(result.returncode, 0, report)
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        result, report = self.run_new('rollback')
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(self.originals(), before)

    def test_tampered_old_active_or_backup_refuses_before_new_state(self):
        self.install_old()
        (self.root / old.CONFIG).write_bytes((self.root / old.CONFIG).read_bytes() + b'# tamper\n')
        result, report = self.run_new('enable')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertFalse((self.root/'var/lib/junshu-camera-boot-1.0.0/state.json').exists())

    def test_tampered_old_backup_refuses_before_new_state(self):
        self.install_old()
        state = json.loads(self.old_state)
        saved = (self.root/'var/lib/junshu-camera-boot-logo-test/backups'/state['backup_id']/old.CONFIG)
        saved.write_bytes(saved.read_bytes()+b'# tampered backup\n')
        result, report = self.run_new('probe')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertFalse((self.root/'var/lib/junshu-camera-boot-1.0.0/state.json').exists())

    def test_old_state_mode_change_refuses_before_new_state(self):
        self.install_old()
        self.old_state_path.chmod(0o644)
        result, report = self.run_new('probe')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertFalse((self.root/'var/lib/junshu-camera-boot-1.0.0/state.json').exists())

    def test_known_newcam_session_chain_is_accepted(self):
        self.install_old()
        old_entry = self.root/'home/junshu/.config/autostart/junshu-camera-r4-integrated.desktop'
        old_entry.write_bytes(old_entry.read_bytes()+b'Hidden=true\n')
        self.write('home/junshu/.config/labwc/autostart',
                   b'#!/bin/sh\n/usr/bin/kanshi &\n/usr/bin/lxsession-xdg-autostart &\n'
                   b'/bin/sh /home/junshu/junshu-camera-pi-1.0.0/start-session.sh &\n')
        script = self.write('home/junshu/junshu-camera-pi-1.0.0/start-session.sh', '#!/bin/sh\n', executable=True)
        manifest = hashlib.sha256(script.read_bytes()).hexdigest()+'  start-session.sh\n'
        self.write('home/junshu/junshu-camera-pi-1.0.0/MANIFEST.sha256', manifest)
        self.write('home/junshu/.local/state/junshu-camera/newcam-session/state.json',
                   json.dumps({'status':'active','backup_id':uuid.uuid4().hex,
                               'manifest_sha256':hashlib.sha256(manifest.encode()).hexdigest()})+'\n')
        result, report = self.run_new('probe')
        self.assertEqual(result.returncode, 0, report)

    def test_failed_upgrade_restores_old_active_and_old_state(self):
        self.install_old()
        self.write('fail-mkinitramfs', 'fixture failure')
        result, report = self.run_new('enable')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertTrue(report.get('restored_originals'), report)
        self.assertEqual(self.originals(), self.old_active)
        self.assertEqual(self.old_state_path.read_bytes(), self.old_state)
        marker = self.root/'fail-mkinitramfs'
        marker.rename(marker.with_name('fail-mkinitramfs.retained'))
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(self.old_state_path.read_bytes(), self.old_state)

    def test_interrupted_initramfs_stage_restores_old_active(self):
        self.install_old()
        self.write('fail-stage-initramfs', 'fixture failure')
        result, report = self.run_new('enable')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertTrue(report.get('restored_originals'), report)
        self.assertEqual(self.originals(), self.old_active)
        self.assertEqual(self.old_state_path.read_bytes(), self.old_state)

    def test_newcam_current_tamper_blocks_rollback(self):
        self.install_old()
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        current = self.root/old.PLYMOUTH_CONF
        current.write_bytes(current.read_bytes()+b'# independently changed\n')
        before = self.originals()
        result, report = self.run_new('rollback')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertEqual(self.originals(), before)

    def assert_active_predecessor_tamper_is_rejected(self, tampered):
        before = self.originals()
        for action in ('verify', 'enable', 'rollback'):
            with self.subTest(material=tampered, action=action):
                result, report = self.run_new(action)
                self.assertNotEqual(result.returncode, 0, report)
                self.assertFalse(report['ok'])
                self.assertEqual(self.originals(), before)

    def test_active_old_theme_tamper_blocks_verify_repeat_enable_and_rollback(self):
        self.install_old()
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        theme = self.root/'usr/share/plymouth/themes/junshu-camera-test/page-1.png'
        theme.write_bytes(theme.read_bytes()+b'tampered theme')
        self.assert_active_predecessor_tamper_is_rejected('theme')

    def test_active_old_backup_tamper_blocks_verify_repeat_enable_and_rollback(self):
        self.install_old()
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        backup_id = json.loads(self.old_state)['backup_id']
        saved = self.root/'var/lib/junshu-camera-boot-logo-test/backups'/backup_id/old.CONFIG
        saved.write_bytes(saved.read_bytes()+b'tampered backup')
        self.assert_active_predecessor_tamper_is_rejected('backup')

    def test_active_old_rescue_tamper_blocks_verify_repeat_enable_and_rollback(self):
        self.install_old()
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        backup_id = json.loads(self.old_state)['backup_id']
        rescued = self.root/'boot/firmware/junshu-boot-logo-backups'/backup_id/Path(old.CONFIG).name
        rescued.write_bytes(rescued.read_bytes()+b'tampered rescue')
        self.assert_active_predecessor_tamper_is_rejected('rescue')

    def test_corrupt_predecessor_record_refuses_with_json_report(self):
        self.install_old()
        result, report = self.run_new('enable')
        self.assertEqual(result.returncode, 0, report)
        state_path = self.root/'var/lib/junshu-camera-boot-1.0.0/state.json'
        state = json.loads(state_path.read_text())
        state['predecessor'] = 'corrupt'
        state_path.write_text(json.dumps(state)+'\n')
        result, report = self.run_new('verify')
        self.assertNotEqual(result.returncode, 0, report)
        self.assertFalse(report['ok'])

    def test_old_tool_lock_blocks_new_upgrade_without_writes(self):
        self.install_old()
        old_lock = self.root/'run/lock/junshu-camera-boot-logo-test.lock'
        fd = os.open(old_lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, report = self.run_new('enable')
            self.assertNotEqual(result.returncode, 0, report)
            self.assertEqual(self.originals(), self.old_active)
            self.assertFalse((self.root/'var/lib/junshu-camera-boot-1.0.0/state.json').exists())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_rollback_without_new_state_is_no_op(self):
        before = self.originals()
        result, report = self.run_new('rollback')
        self.assertEqual(result.returncode, 0, report)
        self.assertFalse(report['changed'])
        self.assertEqual(self.originals(), before)


if __name__ == '__main__': unittest.main()
