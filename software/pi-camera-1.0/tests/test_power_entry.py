"""Systemd readiness is sent only after actual input/IPC constructors succeed."""
import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PowerEntryTests(unittest.TestCase):
    def entry(self):
        spec = importlib.util.spec_from_file_location('power_entry_test', Path(__file__).resolve().parents[1]/'power_service.py')
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_ready_is_a_single_real_notify_datagram_and_no_environment_is_noop(self):
        entry = self.entry()
        retained = Path(tempfile.mkdtemp(prefix='pwr-notify-', dir='/private/tmp' if sys.platform=='darwin' else '/tmp'))
        address = str(retained/'notify.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(address);receiver.settimeout(.5)
            with patch.dict(os.environ, {'NOTIFY_SOCKET':address}):
                self.assertTrue(entry.notify_ready())
            self.assertIn(b'READY=1', receiver.recv(1024).split(b'\n'))
            with self.assertRaises(socket.timeout): receiver.recv(1024)
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(entry.notify_ready())

    def run_entry(self, failure=None):
        entry = self.entry();events=[];handlers={}
        from camera_r4 import power_gpio, power_service
        class Input:
            def __init__(self):
                events.append('gpio')
                if failure=='gpio': raise OSError('input unavailable')
                if failure=='stop': handlers[entry.signal.SIGTERM]()
            def close(self): events.append('gpio_closed')
        class Service:
            def __init__(self, gpio, allowed_uid, stop_requested):
                events.append('ipc')
                if failure=='ipc': raise OSError('endpoint occupied')
                self.stop = stop_requested.__self__
            def step(self): self.stop.set()
            def close(self): events.append('service_closed')
        def ready():
            self.assertEqual(events, ['gpio','ipc'])
            events.append('ready')
            if failure=='notify': raise OSError('notify failed')
        with patch.object(entry,'validate_install'), patch.object(entry.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1000,pw_dir='/home/junshu')), \
             patch.object(entry.signal,'signal',side_effect=lambda sig, handler:handlers.update({sig:handler})), patch.object(entry,'notify_ready',side_effect=ready), \
             patch.object(power_gpio,'GPIO3Input',Input), patch.object(power_service,'PowerService',Service), patch.dict(sys.modules):
            code = entry.main(['--user','junshu'])
        return code, events

    def test_ready_after_both_resources_construct_and_clean_exit(self):
        code, events = self.run_entry()
        self.assertEqual(code,0)
        self.assertEqual(events,['gpio','ipc','ready','service_closed'])

    def test_failed_initialization_or_notification_never_reports_start_success(self):
        for stage in ('gpio','ipc','notify'):
            with self.subTest(stage=stage):
                code, events = self.run_entry(stage)
                self.assertEqual(code,1)
                if stage in ('gpio','ipc'): self.assertNotIn('ready',events)
                if stage=='ipc': self.assertIn('gpio_closed',events)
                if stage=='notify': self.assertIn('service_closed',events)

    def test_stop_during_input_initialization_does_not_announce_ready(self):
        code, events = self.run_entry('stop')
        self.assertEqual(code,0)
        self.assertNotIn('ready',events)
        self.assertIn('service_closed',events)


if __name__=='__main__': unittest.main()
