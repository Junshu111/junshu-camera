"""New Cam 1.0 package safety and installer stage behavior."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageTests(unittest.TestCase):
    def test_app_checker_has_independent_v1_allowlist(self):
        checker = load(ROOT/'pi-camera-1.0/check_package.py', 'v1_checker')
        self.assertEqual(checker.PREFIX, 'junshu-camera-pi-1.0.0')
        self.assertIn('session_setup.py', checker.RELEASE_FILES)
        self.assertIn('start-session.sh', checker.RELEASE_FILES)
        self.assertIn('assets/boot-logo.png', checker.RELEASE_FILES)
        self.assertIn('camera_r4/loading.py', checker.RELEASE_FILES)

    def test_both_builders_are_deterministic_and_reject_tampering(self):
        builder = load(ROOT/'tools/build_camera_v1_release.py', 'v1_builder')
        app_checker = load(ROOT/'pi-camera-1.0/check_package.py', 'v1_checker_build')
        boot_payload = load(ROOT/'tools/camera_v1_boot_payload.py', 'v1_boot_payload')
        qa = ROOT/'qa/newcam-1.0/package-tests'
        qa.mkdir(parents=True, exist_ok=True)
        first = Path(tempfile.mkdtemp(prefix='build-a-', dir=qa))
        second = Path(tempfile.mkdtemp(prefix='build-b-', dir=qa))
        app_a = builder.build_app(ROOT/'pi-camera-1.0', first)
        app_b = builder.build_app(ROOT/'pi-camera-1.0', second)
        boot_a = builder.build_boot(ROOT/'pi-boot-logo-1.0', first)
        boot_b = builder.build_boot(ROOT/'pi-boot-logo-1.0', second)
        self.assertEqual(app_a.read_bytes(), app_b.read_bytes())
        self.assertEqual(boot_a.read_bytes(), boot_b.read_bytes())
        self.assertEqual(app_checker.verify_archive(app_a), len(app_checker.RELEASE_FILES))
        self.assertEqual(len(boot_payload.read_archive(boot_a)), len(boot_payload.FILES)+1)
        corrupt = first/'corrupt-v1-app.tar.gz'
        corrupt.write_bytes(b'0'+app_a.read_bytes()[1:])
        with self.assertRaises(Exception):
            app_checker.verify_archive(corrupt)

        home = Path(tempfile.mkdtemp(prefix='receiver-', dir=qa))
        app_release = app_checker.receive_archive(app_a, app_a.with_name(app_a.name+'.sha256'),
                                                  __import__('hashlib').sha256(app_a.read_bytes()).hexdigest(), home)
        boot_release = boot_payload.receive(boot_a, __import__('hashlib').sha256(boot_a.read_bytes()).hexdigest(), home)
        self.assertEqual(app_checker.verify_directory(app_release), len(app_checker.RELEASE_FILES))
        self.assertEqual(app_checker.receive_archive(app_a, app_a.with_name(app_a.name+'.sha256'),
                                                     __import__('hashlib').sha256(app_a.read_bytes()).hexdigest(), home), app_release)
        self.assertEqual(boot_release['package'], str(home/boot_payload.PREFIX))
        self.assertFalse(boot_payload.receive(boot_a, __import__('hashlib').sha256(boot_a.read_bytes()).hexdigest(), home)['changed'])

    def test_boot_payload_identifies_new_theme_only(self):
        payload = load(ROOT/'tools/camera_v1_boot_payload.py', 'v1_boot_payload_names')
        self.assertEqual(payload.PREFIX, 'junshu-camera-boot-1.0.0')
        self.assertIn('theme/newcam.plymouth', payload.FILES)
        self.assertIn('theme/newcam.script', payload.FILES)
        self.assertIn('theme/logo.png', payload.FILES)
        self.assertNotIn('theme/page-1.png', payload.FILES)


class CoordinationTests(unittest.TestCase):
    def test_verify_refuses_boot_enabled_without_configured_session(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_verify_session_off')
        calls = []

        class Ops:
            def boot_verify(self):
                calls.append('boot')
                return {'ok':True, 'status':'enabled'}
            def session_verify(self):
                calls.append('session')
                return {'ok':True, 'configured':False, 'changed':False}

        report = remote.coordinate_readonly(Ops(), 'verify')
        self.assertEqual(calls, ['boot', 'session'])
        self.assertFalse(report['success'])
        self.assertEqual(report['stages']['session_verify']['status'], 'failed')

    def test_verify_refuses_inactive_boot_even_if_session_configured(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_verify_boot_off')

        class Ops:
            def boot_verify(self): return {'ok':True, 'status':'ready'}
            def session_verify(self): return {'ok':True, 'configured':True}

        report = remote.coordinate_readonly(Ops(), 'verify')
        self.assertFalse(report['success'])
        self.assertEqual(report['stages']['boot_verify']['status'], 'failed')

    def test_probe_can_succeed_while_both_components_inactive(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_probe_inactive')

        class Ops:
            def boot_probe(self): return {'ok':True, 'status':'ready'}
            def session_probe(self): return {'ok':True, 'configured':False, 'pending':False}

        report = remote.coordinate_readonly(Ops(), 'probe')
        self.assertTrue(report['success'])
        self.assertEqual(report['status'], 'probed')

    def test_verify_requires_both_active_components(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_verify_active')

        class Ops:
            def boot_verify(self): return {'ok':True, 'status':'enabled'}
            def session_verify(self): return {'ok':True, 'configured':True}

        report = remote.coordinate_readonly(Ops(), 'verify')
        self.assertTrue(report['success'])
        self.assertEqual(report['status'], 'verified')

    def test_readonly_checks_both_components_even_if_boot_refuses(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_probe')
        calls = []

        class Ops:
            def boot_probe(self):
                calls.append('boot')
                raise ValueError('boot baseline unknown')
            def session_probe(self): calls.append('session')

        report = remote.coordinate_readonly(Ops(), 'probe')
        self.assertEqual(calls, ['boot', 'session'])
        self.assertFalse(report['success'])
        self.assertEqual(report['status'], 'partial')

    def test_launcher_points_to_new_package_without_replacing_old_icon(self):
        launcher = load(ROOT/'tools/camera_v1_launcher.py', 'v1_launcher')
        entry = launcher.desktop_entry()
        self.assertTrue(entry.startswith('#!/usr/bin/gio launch\n[Desktop Entry]\n'))
        self.assertIn('Name=New Cam 1.0\n', entry)
        self.assertIn('Exec=/bin/sh /home/junshu/junshu-camera-pi-1.0.0/start-camera.sh\n', entry)
        self.assertIn('Icon=/home/junshu/junshu-camera-pi-1.0.0/assets/boot-logo.png\n', entry)
        self.assertIn('Terminal=false\n', entry)
        self.assertNotIn('R4 Share', entry)

    def test_launcher_is_idempotent_and_preserves_unknown_existing_file(self):
        launcher = load(ROOT/'tools/camera_v1_launcher.py', 'v1_launcher_write')
        qa = ROOT/'qa/newcam-1.0/launcher-tests'
        qa.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='desktop-', dir=qa))
        path = folder/'New Cam 1.0.desktop'
        data = launcher.desktop_entry().encode()
        self.assertTrue(launcher.write_icon(path, data))
        self.assertFalse(launcher.write_icon(path, data))
        unknown = folder/'unknown.desktop'
        unknown.write_bytes(b'custom icon\n')
        with self.assertRaises(ValueError):
            launcher.write_icon(unknown, data)
        self.assertEqual(unknown.read_bytes(), b'custom icon\n')

    def test_launcher_accepts_only_existing_desktop_inside_user_home(self):
        launcher = load(ROOT/'tools/camera_v1_launcher.py', 'v1_launcher_desktop')
        qa = ROOT/'qa/newcam-1.0/launcher-tests'
        qa.mkdir(parents=True, exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix='home-', dir=qa))
        desktop = home/'桌面'
        desktop.mkdir()
        self.assertEqual(launcher.validate_desktop(home, str(desktop)), desktop)
        with self.assertRaises(ValueError):
            launcher.validate_desktop(home, '/tmp')
        with self.assertRaises(ValueError):
            launcher.validate_desktop(home, str(home/'missing'))

    def test_failed_session_enable_attempts_boot_restore_and_reports_partial(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_failure')
        events = []

        class Ops:
            def boot_probe(self):
                events.append('boot_probe')
                return {'ok':True, 'status':'ready'}
            def session_probe(self):
                events.append('session_probe')
                return {'ok':True, 'configured':False, 'pending':False}
            def release(self): events.append('release')
            def transfer_app(self): events.append('transfer_app')
            def transfer_boot(self): events.append('transfer_boot')
            def install_launcher(self): events.append('launcher')
            def boot_enable(self):
                events.append('boot_enable')
                return {'ok':True, 'status':'enabled', 'changed':True}
            def session_enable(self):
                events.append('session_enable')
                raise RuntimeError('session refused')
            def session_rollback(self):
                events.append('session_rollback')
                return {'ok':True, 'configured':False, 'changed':True}
            def boot_rollback(self):
                events.append('boot_rollback')
                return {'ok':True, 'status':'rolled_back', 'changed':True}

        report = remote.coordinate_install(Ops())
        self.assertEqual(events, ['boot_probe', 'session_probe', 'release', 'transfer_app',
                                  'transfer_boot', 'launcher', 'boot_enable',
                                  'session_enable', 'session_rollback', 'boot_rollback'])
        self.assertEqual(report['status'], 'partial')
        self.assertFalse(report['success'])
        self.assertEqual(report['stages']['session_rollback']['status'], 'ok')
        self.assertEqual(report['stages']['boot_rollback']['status'], 'ok')

    def test_retry_with_existing_boot_never_rolls_it_back(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_existing_boot')
        events = []

        class Ops:
            def boot_probe(self): return {'ok':True, 'status':'enabled'}
            def session_probe(self): return {'ok':True, 'configured':True, 'pending':False}
            def release(self): pass
            def transfer_app(self): pass
            def transfer_boot(self): pass
            def install_launcher(self): pass
            def boot_enable(self): return {'ok':True, 'status':'enabled', 'changed':False}
            def session_enable(self): raise RuntimeError('different active session')
            def session_rollback(self): events.append('session_rollback')
            def boot_rollback(self):
                events.append('boot_rollback')
                return {'ok':True, 'status':'rolled_back', 'changed':True}

        report = remote.coordinate_install(Ops())
        self.assertEqual(events, [])
        self.assertFalse(report['success'])
        self.assertEqual(report['stages']['boot_rollback']['status'], 'skipped')
        self.assertEqual(report['stages']['session_rollback']['status'], 'skipped')

    def test_session_preflight_refusal_stops_before_release_or_device_write(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_preflight')
        events = []

        class Ops:
            def boot_probe(self):
                events.append('boot_probe')
                return {'ok':True, 'status':'ready'}
            def session_probe(self):
                events.append('session_probe')
                return {'ok':True, 'configured':False, 'pending':True}
            def release(self): events.append('release')
            def transfer_app(self): events.append('transfer_app')
            def transfer_boot(self): events.append('transfer_boot')
            def install_launcher(self): events.append('launcher')
            def boot_enable(self): events.append('boot_enable')
            def session_enable(self): events.append('session_enable')

        report = remote.coordinate_install(Ops())
        self.assertEqual(events, ['boot_probe', 'session_probe'])
        self.assertFalse(report['success'])
        self.assertEqual(report['stages']['session_probe']['status'], 'failed')

    def test_false_ok_report_cannot_be_counted_as_success(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_false_ok')
        events = []

        class Ops:
            def boot_probe(self):
                return {'ok':False, 'status':'blocked', 'errors':['unsupported setup']}
            def session_probe(self): events.append('session_probe')
            def release(self): events.append('release')
            def transfer_app(self): events.append('transfer_app')
            def transfer_boot(self): events.append('transfer_boot')
            def install_launcher(self): events.append('launcher')
            def boot_enable(self): events.append('boot_enable')
            def session_enable(self): events.append('session_enable')

        report = remote.coordinate_install(Ops())
        self.assertEqual(events, [])
        self.assertEqual(report['stages']['boot_probe']['status'], 'failed')
        self.assertFalse(report['success'])

    def test_rollback_attempts_both_components_after_first_failure(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_rollback')
        events = []

        class Ops:
            def session_rollback(self):
                events.append('session')
                raise RuntimeError('session backup unavailable')
            def boot_rollback(self):
                events.append('boot')
                return {'ok':True, 'status':'rolled_back', 'changed':True}

        report = remote.coordinate_rollback(Ops())
        self.assertEqual(events, ['session', 'boot'])
        self.assertEqual(report['status'], 'partial')
        self.assertFalse(report['success'])

    def test_recovery_report_must_confirm_restored_state(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_bad_restore')

        class Ops:
            def session_rollback(self): return {'ok':True, 'configured':True, 'changed':False}
            def boot_rollback(self): return {'ok':True, 'status':'enabled', 'changed':False}

        report = remote.coordinate_rollback(Ops())
        self.assertEqual(report['stages']['session_rollback']['status'], 'failed')
        self.assertEqual(report['stages']['boot_rollback']['status'], 'failed')
        self.assertFalse(report['success'])

    def test_failed_device_report_is_kept_in_summary(self):
        remote = load(ROOT/'tools/camera_v1_remote.py', 'v1_remote_detail')
        summary = {'stages':{}}
        device_report = {'ok':False, 'status':'blocked', 'restored_originals':True}

        def fail():
            raise remote.RemoteActionError('boot-enable failed', device_report)

        self.assertFalse(remote._stage(summary, 'boot_enable', fail))
        self.assertEqual(summary['stages']['boot_enable']['result'], device_report)


if __name__ == '__main__':
    unittest.main()
