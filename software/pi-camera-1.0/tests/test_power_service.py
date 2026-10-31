"""Safety behavior, with retained fixtures and no real power commands."""
import fcntl
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class PowerServiceTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('camera_r4.power_service'),
                             'standalone power service is missing')
        from camera_r4 import power_service
        return power_service

    def test_hold_requires_release_at_start_and_continuous_three_seconds(self):
        detector = self.api().HoldDetector()
        def samples(pressed, start, count):
            return [detector.sample(pressed, (start + n) / 100) for n in range(count)]
        self.assertFalse(any(samples(True, 0, 500)))
        self.assertFalse(any(samples(False, 500, 10)))
        self.assertFalse(any(samples(True, 510, 300)))
        self.assertEqual(sum(samples(True, 810, 20)), 1)
        self.assertFalse(any(samples(True, 830, 500)))
        self.assertFalse(any(samples(False, 1330, 10)))
        self.assertEqual(sum(samples(True, 1340, 320)), 1)

    def test_bounce_short_press_and_sampling_failure_never_count_as_hold(self):
        detector = self.api().HoldDetector()
        for n in range(10): detector.sample(False, n / 100)
        for n in range(10, 100): self.assertFalse(detector.sample(True, n / 100))
        detector.sample(False, 1.0)
        for n in range(101, 400): self.assertFalse(detector.sample(True, n / 100))
        detector.invalidate()
        for n in range(400, 800): self.assertFalse(detector.sample(True, n / 100))
        for n in range(800, 810): detector.sample(False, n / 100)
        detector.sample(True, 8.1)
        self.assertFalse(detector.sample(True, 20.0))
        for n in range(2001, 2400): self.assertFalse(detector.sample(True, n / 100))
        with self.assertRaises(ValueError): detector.sample(0, 24.)

    def test_safe_legacy_lock_refuses_held_symlink_fifo_and_missing(self):
        api = self.api()
        base = Path(tempfile.mkdtemp(prefix='junshu-power-lock-', dir='/private/tmp' if sys.platform == 'darwin' else '/tmp')).resolve()
        lock = base / 'instance.lock'; lock.write_text('retained fixture')
        with lock.open('r') as old_app:
            fcntl.flock(old_app, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises((OSError, RuntimeError)): api.InstanceLease(lock, os.getuid())
        lease = api.InstanceLease(lock, os.getuid())
        with lock.open('r') as contender:
            with self.assertRaises(BlockingIOError): fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lease.close()
        link = base / 'link'; link.symlink_to(lock)
        fifo = base / 'fifo'; os.mkfifo(fifo)
        for bad in (link, fifo, base / 'missing'):
            with self.subTest(bad=bad):
                with self.assertRaises((OSError, RuntimeError)): api.InstanceLease(bad, os.getuid())
        self.assertEqual(lock.read_text(), 'retained fixture')

    def test_legacy_lock_replacement_and_ancestor_symlink_are_refused(self):
        api = self.api()
        base = Path(tempfile.mkdtemp(prefix='junshu-power-race-', dir='/private/tmp' if sys.platform == 'darwin' else '/tmp')).resolve()
        lock = base / 'instance.lock'; lock.touch()
        lease = api.InstanceLease(lock, os.getuid()); self.addCleanup(lease.close)
        lock.rename(base / 'retained-original.lock'); lock.touch()
        with self.assertRaises(RuntimeError): lease.validate()
        sibling = base / 'linked-directory'; sibling.symlink_to(base)
        with self.assertRaises((OSError, RuntimeError)): api.InstanceLease(sibling / 'instance.lock', os.getuid())

    def test_legacy_lock_does_not_open_untrusted_fifo_even_nonblocking(self):
        api = self.api()
        base = Path(tempfile.mkdtemp(prefix='junshu-power-fifo-', dir='/private/tmp' if sys.platform == 'darwin' else '/tmp')).resolve()
        fifo = base / 'instance.lock'; os.mkfifo(fifo)
        real_open = os.open
        opened_special = []
        def checked_open(path, flags, **kwargs):
            if path == 'instance.lock': opened_special.append(path)
            return real_open(path, flags, **kwargs)
        with patch.object(api.os, 'open', checked_open):
            with self.assertRaises((OSError, RuntimeError)): api.InstanceLease(fifo, os.getuid())
        self.assertEqual(opened_special, [])

    def test_disabled_indicator_never_calls_driver_and_failure_is_nonfatal(self):
        self.api()
        from camera_r4.power_indicator import OptionalIndicator
        class Driver:
            def stage(self, name): raise AssertionError('driver fixture')
        off = OptionalIndicator(driver=Driver()); self.assertFalse(off.stage('saving'))
        absent = OptionalIndicator(enabled=True); self.assertFalse(absent.stage('saving'))
        broken = OptionalIndicator(enabled=True, driver=Driver())
        self.assertFalse(broken.stage('saving')); self.assertFalse(broken.available)
        self.assertIn('driver fixture', broken.reason)

    def test_requires_root(self):
        api = self.api()
        with patch.object(api.os, 'geteuid', return_value=501):
            with self.assertRaises(PermissionError): api.PowerService(gpio=object(), allowed_uid=501)

    def test_entry_refuses_uninstalled_user_code_before_gpio_or_commands(self):
        entry = Path(__file__).resolve().parents[1] / 'power_service.py'
        self.assertTrue(entry.is_file(), 'protected standalone entry is missing')
        result = subprocess.run([sys.executable, '-I', '-B', str(entry), '--user', 'junshu'],
                                capture_output=True, text=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refused', result.stderr)


class ServiceProtocolTests(unittest.TestCase):
    api = PowerServiceTests.api
    def setup_service(self, command=None):
        api = self.api()
        self.now = 0.; self.calls = []
        class GPIO:
            pressed = False
            failed = False
            closed = False
            def read(self):
                if self.failed: raise OSError('read failed')
                return self.pressed
            def close(self): self.closed = True
        self.gpio = GPIO()
        base = Path(tempfile.mkdtemp(prefix='junshu-power-service-', dir='/private/tmp' if sys.platform == 'darwin' else '/tmp')).resolve()
        self.lock = base / 'instance.lock'; self.lock.touch()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(base / 'ipc.sock')); listener.listen(4); listener.setblocking(False)
        self.address = str(base / 'ipc.sock')
        with patch.object(api.os, 'geteuid', return_value=0):
            self.service = api.PowerService(gpio=self.gpio, allowed_uid=os.getuid(),
                listener=listener, lock_path=self.lock, clock=lambda: self.now,
                peer_uid=lambda sock: os.getuid(),
                command=command or (lambda: self.calls.append('poweroff')))
        self.addCleanup(self.service.close)
        return api

    def tick(self, count=1):
        for _ in range(count):
            self.now += .01; self.service.step()

    def hold(self):
        self.gpio.pressed = False; self.tick(10)
        self.gpio.pressed = True; self.tick(320)

    def client(self):
        from camera_r4.power_client import PowerClient
        client = PowerClient(address=self.address, peer_uid=lambda sock: 0, clock=lambda: self.now)
        self.addCleanup(client.close)
        client.poll(); self.tick(); client.poll()
        return client

    def test_absent_gui_lock_held_through_command_and_one_shot_success(self):
        self.setup_service()
        def command():
            with self.lock.open('r') as other:
                with self.assertRaises(BlockingIOError): fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.calls.append('poweroff')
        self.service.command = command
        self.hold(); self.assertEqual(self.calls, ['poweroff'])
        self.assertEqual(self.service.state, 'committed')
        self.hold(); self.assertEqual(self.calls, ['poweroff'])

    def test_old_app_lock_refuses_shutdown(self):
        self.setup_service()
        with self.lock.open('r') as old_app:
            fcntl.flock(old_app, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.hold()
        self.assertEqual(self.calls, []); self.assertEqual(self.service.state, 'idle')
        self.tick(500); self.assertEqual(self.calls, [])

    def test_gui_ready_is_required_and_commit_is_sticky(self):
        self.setup_service(); client = self.client(); self.hold()
        events = client.poll(); prepare = next(e for e in events if e['type'] == 'prepare')
        self.assertEqual(self.calls, [])
        client.ready(prepare['id']); self.tick(); events = client.poll()
        self.assertEqual(self.calls, ['poweroff'])
        self.assertTrue(any(e['type'] == 'committed' for e in events))
        self.service.close()
        self.assertFalse(any(e['type'] == 'cancel' for e in client.poll()))

    def test_reject_disconnect_timeout_read_failure_and_command_error_cancel(self):
        for cause in ('reject', 'disconnect', 'timeout', 'read', 'command'):
            with self.subTest(cause=cause):
                self.setup_service(); client = self.client(); self.hold()
                request = next(e for e in client.poll() if e['type'] == 'prepare')
                if cause == 'reject': client.reject(request['id'], 'disk failed')
                elif cause == 'disconnect': client.close()
                elif cause == 'timeout': self.tick(6001)
                elif cause == 'read': self.gpio.failed = True
                else:
                    def fail(): raise OSError('systemctl failed')
                    self.service.command = fail; client.ready(request['id'])
                self.tick()
                self.assertEqual(self.calls, []); self.assertEqual(self.service.state, 'idle')
                if cause != 'disconnect': self.assertTrue(any(e['type'] == 'cancel' for e in client.poll()))
                client.close(); self.service.close()

    def test_unsolicited_ready_mismatched_id_and_second_peer_cannot_poweroff(self):
        self.setup_service(); first = self.client(); second = self.client()
        self.assertTrue(first.connected); self.assertFalse(second.connected)
        first._peer.queue({'type': 'ready', 'id': 'unsolicited'}); first.poll(); self.tick()
        self.assertEqual(self.calls, [])
        self.service.close()
        self.setup_service(); first = self.client(); self.hold(); first.poll()
        first._peer.queue({'type': 'ready', 'id': 'wrong'}); first.poll(); self.tick()
        self.assertEqual(self.calls, []); self.assertEqual(self.service.state, 'idle')

    def test_command_timeout_has_unknown_outcome_and_must_not_resume_gui(self):
        self.setup_service(); client = self.client(); self.hold()
        request = next(e for e in client.poll() if e['type'] == 'prepare')
        def timeout(): raise subprocess.TimeoutExpired('injected systemctl', 15)
        self.service.command = timeout; client.ready(request['id']); self.tick()
        self.assertEqual(self.service.state, 'uncertain')
        self.assertFalse(any(e['type'] == 'cancel' for e in client.poll()))
        self.hold(); self.assertEqual(self.service.state, 'uncertain')

    def test_stop_during_prepare_does_not_request_poweroff(self):
        self.setup_service(); client = self.client(); self.hold()
        request = next(e for e in client.poll() if e['type'] == 'prepare')
        client.ready(request['id']); self.service.stop_requested = lambda: True
        self.tick()
        self.assertEqual(self.calls, [])
        self.assertTrue(any(e['type'] == 'cancel' for e in client.poll()))

    def test_no_pressed_hold_queues_across_busy_cancellation(self):
        self.setup_service(); client = self.client(); self.hold()
        request = next(e for e in client.poll() if e['type'] == 'prepare')
        self.hold(); self.assertEqual(self.calls, [])
        client.reject(request['id'], 'fixture busy'); self.tick(); client.poll()
        self.tick(500)
        self.assertEqual(self.service.state, 'idle'); self.assertEqual(self.calls, [])
        self.hold()
        self.assertTrue(any(e['type'] == 'prepare' for e in client.poll()))

    def test_failed_absent_camera_command_releases_lease(self):
        self.setup_service()
        def fail(): raise OSError('injected command failure')
        self.service.command = fail; self.hold()
        self.assertEqual(self.service.state, 'idle')
        with self.lock.open('r') as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_unauthorized_peer_and_malformed_data_cannot_trigger(self):
        self.setup_service(); self.service.peer_uid = lambda sock: os.getuid() + 1
        client = self.client(); self.assertFalse(client.connected)
        self.assertEqual(self.calls, [])
        self.service.peer_uid = lambda sock: os.getuid()
        client.close(); client = self.client(); self.hold(); client.poll()
        client._peer.sock.send(b'x' * 4097); self.tick()
        self.assertEqual(self.service.state, 'idle'); self.assertEqual(self.calls, [])

    def test_late_ready_after_deadline_is_rejected(self):
        self.setup_service(); client = self.client(); self.hold()
        request = next(e for e in client.poll() if e['type'] == 'prepare')
        self.now += 61.; client.ready(request['id']); self.tick()
        self.assertEqual(self.calls, []); self.assertEqual(self.service.state, 'idle')

    def test_stalled_gpio_read_cannot_complete_pre_stall_hold(self):
        self.setup_service(); self.tick(10); self.gpio.pressed = True
        self.tick(306); self.assertEqual(self.calls, [])
        original = self.gpio.read
        def stalled_read():
            self.now += 10.
            return original()
        self.gpio.read = stalled_read
        self.tick(2)
        self.assertEqual(self.calls, [])

    def test_same_daemon_reconnect_delivers_exact_canceled_ready(self):
        self.setup_service(); client = self.client(); self.hold()
        request = next(e for e in client.poll() if e['type'] == 'prepare')
        client.ready(request['id']); client._peer.sock.shutdown(socket.SHUT_RDWR)
        events = client.poll(); self.tick()
        self.assertEqual(self.calls, []); self.assertFalse(any(e['type'] == 'cancel' for e in events))
        self.tick(110); client.poll(); self.tick(); events = client.poll()
        self.assertTrue(any(e['type'] == 'cancel' and e['id'] == request['id'] for e in events))

    def test_real_systemctl_wrapper_has_fixed_command_and_no_shell(self):
        api = self.api()
        # The real executable boundary alone is replaced. Never invoke shutdown.
        with patch.object(api.subprocess, 'run') as run:
            api.request_system_poweroff()
        self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', '--no-ask-password', 'poweroff'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertTrue(run.call_args.kwargs['check'])



if __name__ == '__main__': unittest.main()
