import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest


class PowerClientTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('camera_r4.power_client'), 'authenticated power IPC is missing')
        from camera_r4.power_client import PowerClient
        self.now = 0.
        base = Path(tempfile.mkdtemp(prefix='junshu-power-client-', dir='/private/tmp' if sys.platform == 'darwin' else '/tmp')).resolve()
        self.address = str(base / 'ipc.sock')
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.address); self.listener.listen(4); self.listener.settimeout(.2)
        self.addCleanup(self.listener.close)
        self.client = PowerClient(address=self.address, clock=lambda: self.now, peer_uid=lambda sock: 0)
        self.addCleanup(self.client.close)

    def connect(self):
        self.client.poll(); server, _ = self.listener.accept(); self.addCleanup(server.close)
        server.sendall(b'{"type":"status","state":"connected","phase":"idle","reason":""}\n')
        self.client.poll(); self.assertTrue(self.client.connected)
        return server

    def test_rejects_nonroot_server(self):
        self.client._peer_uid = lambda sock: 501
        self.client.poll()
        self.assertFalse(self.client.connected)

    def test_prepare_ready_cancel_and_reconnect(self):
        server = self.connect()
        server.sendall(b'{"type":"prepare","id":"request1"}\n')
        self.assertEqual(self.client.poll(), [{'type': 'prepare', 'id': 'request1'}])
        self.client.ready('request1')
        self.assertEqual(json.loads(server.recv(4096)), {'type': 'ready', 'id': 'request1'})
        server.sendall(b'{"type":"cancel","id":"request1","reason":"failed"}\n')
        self.assertEqual(self.client.poll()[0]['reason'], 'failed')
        server.close(); self.assertFalse(any(e['type'] == 'cancel' for e in self.client.poll()))
        self.now = 2.; self.connect()

    def test_disconnect_before_ready_cancels_but_after_ready_requires_explicit_matching_cancel(self):
        server = self.connect(); server.sendall(b'{"type":"prepare","id":"first"}\n'); self.client.poll()
        server.close(); self.assertTrue(any(e['type'] == 'cancel' for e in self.client.poll()))
        self.now = 2.; server = self.connect()
        server.sendall(b'{"type":"prepare","id":"second"}\n'); self.client.poll(); self.client.ready('second')
        server.close(); events = self.client.poll()
        self.assertFalse(any(e['type'] == 'cancel' for e in events))
        self.assertTrue(any(e.get('reason') == 'ready_outcome_unknown' for e in events))
        self.now = 4.; self.client.poll(); server, _ = self.listener.accept(); self.addCleanup(server.close)
        server.sendall(b'{"type":"status","state":"connected","phase":"idle","reason":""}\n')
        self.assertFalse(any(e['type'] == 'cancel' for e in self.client.poll()))
        server.sendall(b'{"type":"cancel","id":"second","reason":"camera_disconnected"}\n')
        self.assertTrue(any(e['type'] == 'cancel' and e['id'] == 'second' for e in self.client.poll()))

    def test_malformed_oversized_and_wrong_direction_messages_disconnect(self):
        for payload in (b'not json\n', b'x' * 4097, b'{"type":"ready","id":"bad"}\n', b'[]\n'):
            with self.subTest(payload=payload[:30]):
                server = self.connect(); server.sendall(payload)
                self.client.poll(); self.assertFalse(self.client.connected)
                server.close(); self.now += 2.

    def test_local_ready_cannot_be_sent_for_wrong_request(self):
        server = self.connect()
        with self.assertRaises(ValueError): self.client.ready('not-prepared')
        server.settimeout(.02)
        with self.assertRaises(socket.timeout): server.recv(100)

    def test_commit_delivered_together_with_eof_still_stays_committed(self):
        server = self.connect()
        server.sendall(b'{"type":"prepare","id":"commit1"}\n'); self.client.poll(); self.client.ready('commit1')
        server.recv(4096)
        server.sendall(b'{"type":"committed","id":"commit1"}\n'); server.close()
        events = self.client.poll()
        self.assertTrue(any(e['type'] == 'committed' for e in events))
        self.assertFalse(any(e['type'] == 'cancel' for e in events))

    def test_partial_frames_and_flood_are_bounded(self):
        server = self.connect()
        server.sendall(b'{"type":"prep')
        self.assertEqual(self.client.poll(), [])
        server.sendall(b'are","id":"partial"}\n')
        self.assertEqual(self.client.poll(), [{'type': 'prepare', 'id': 'partial'}])
        server.sendall(b'{"type":"status","state":"connected","phase":"idle"}\n' * 40)
        events = self.client.poll()
        self.assertFalse(self.client.connected)
        self.assertTrue(any(e['type'] == 'cancel' for e in events))

    @unittest.skipUnless(sys.platform == 'linux', 'real Linux SO_PEERCRED and abstract namespace require target Linux')
    def test_linux_abstract_socket_and_real_uid(self):
        import os
        import uuid
        from camera_r4.power_client import peer_uid
        address = '\0junshu-power-test-' + uuid.uuid4().hex
        listener = socket.socket(socket.AF_UNIX); self.addCleanup(listener.close)
        listener.bind(address); listener.listen(1)
        client = socket.socket(socket.AF_UNIX); self.addCleanup(client.close)
        client.connect(address); server, _ = listener.accept(); self.addCleanup(server.close)
        self.assertEqual(peer_uid(server), os.getuid())


if __name__ == '__main__': unittest.main()
