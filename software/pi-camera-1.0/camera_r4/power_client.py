"""Bounded, nonblocking IPC. The GUI can acknowledge only physical requests.

The production endpoint is a Linux abstract AF_UNIX socket: no pathname may be
substituted or unlinked. SO_PEERCRED authenticates root before any GUI state is
changed. A READY whose outcome was lost stays frozen until that exact request
gets an explicit cancel/committed response; generic idle status is not evidence.
"""
import errno
import json
import socket
import struct
import time

SOCKET_ADDRESS = '\0junshu-camera-power-v1'
MAX_FRAME = 4096


def peer_uid(sock):
    if not hasattr(socket, 'SO_PEERCRED'):
        raise OSError('Linux SO_PEERCRED is required')
    return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


class ProtocolError(ValueError):
    pass


class JsonPeer:
    """One bounded receive/send slice per caller tick; never sleeps."""
    def __init__(self, sock):
        self.sock = sock
        sock.setblocking(False)
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.eof = False

    def queue(self, event):
        data = json.dumps(event, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        if len(data) > MAX_FRAME + 1 or len(self.outgoing) + len(data) > MAX_FRAME * 4:
            raise ProtocolError('outgoing frame/buffer limit')
        self.outgoing.extend(data)

    def flush(self):
        if self.outgoing:
            try:
                count = self.sock.send(self.outgoing)
            except BlockingIOError:
                return
            if not count:
                raise ConnectionError('socket closed')
            del self.outgoing[:count]

    def receive(self):
        events = []
        for _ in range(4):
            try:
                data = self.sock.recv(MAX_FRAME + 1)
            except BlockingIOError:
                break
            if not data:
                self.eof = True
                break
            self.incoming.extend(data)
            while b'\n' in self.incoming:
                line, _, rest = self.incoming.partition(b'\n')
                self.incoming = bytearray(rest)
                if not line or len(line) > MAX_FRAME:
                    raise ProtocolError('invalid frame length')
                try:
                    event = json.loads(line.decode('utf-8'))
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise ProtocolError('invalid JSON') from error
                if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                    raise ProtocolError('object with type required')
                events.append(event)
                if len(events) > 32:
                    raise ProtocolError('message rate limit')
            if len(self.incoming) > MAX_FRAME:
                raise ProtocolError('frame too large')
        return events

    def close(self):
        self.sock.close()


def valid_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(c.isalnum() or c in '-_' for c in value)


class PowerClient:
    def __init__(self, address=SOCKET_ADDRESS, peer_uid=peer_uid, clock=time.monotonic):
        self.address = address
        self._peer_uid = peer_uid
        self.clock = clock
        self._peer = None
        self._connecting = None
        self._retry_at = 0.
        self._active = None
        self._ready_sent = False
        self._committed = False
        self._closed = False
        self._events = []
        self.connected = False

    def _disconnect(self, reason):
        was_connected = self.connected
        if self._peer is not None:
            self._peer.close(); self._peer = None
        if self._connecting is not None:
            self._connecting.close(); self._connecting = None
        self.connected = False
        self._retry_at = self.clock() + 1.
        if self._active and not self._committed and not self._ready_sent:
            self._events.append({'type': 'cancel', 'id': self._active, 'reason': reason})
            self._active = None
        if was_connected:
            if self._ready_sent and not self._committed:
                reason = 'ready_outcome_unknown'
            self._events.append({'type': 'status', 'state': 'disconnected', 'reason': reason})

    def _connect(self):
        if self._closed or self.clock() < self._retry_at:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            result = sock.connect_ex(self.address)
            # Local AF_UNIX connect never needs a blocking wait; a saturated
            # backlog is retried on a later UI tick rather than kept half open.
            if result not in (0, errno.EISCONN):
                sock.close(); self._retry_at = self.clock() + 1.; return
            if self._peer_uid(sock) != 0:
                raise PermissionError('power service peer is not root')
            self._peer = JsonPeer(sock)
            self.connected = True
        except (OSError, ValueError):
            sock.close(); self._retry_at = self.clock() + 1.

    def _event(self, event):
        kind = event['type']
        if kind == 'status':
            if event.get('state') not in ('connected', 'committed') or event.get('phase') not in ('idle', 'preparing', 'committed', 'uncertain'):
                raise ProtocolError('invalid service status')
            # An idle daemon may be a fresh process after systemctl accepted
            # poweroff. Never infer cancellation of an unknown READY outcome.
            if event.get('phase') == 'committed' and self._active == event.get('id'):
                self._committed = True
                self._events.append({'type': 'committed', 'id': self._active})
        elif kind in ('prepare', 'cancel', 'committed'):
            ident = event.get('id')
            if not valid_id(ident):
                raise ProtocolError('invalid request id')
            if kind == 'prepare':
                if self._committed or (self._active is not None and self._active != ident):
                    raise ProtocolError('unexpected prepare')
                self._active = ident; self._ready_sent = False
            elif kind == 'cancel':
                if not isinstance(event.get('reason'), str):
                    raise ProtocolError('cancel reason required')
                if ident != self._active or self._committed:
                    return
                self._active = None; self._ready_sent = False
            else:
                if ident != self._active or not self._ready_sent:
                    raise ProtocolError('unsolicited committed')
                self._committed = True
        else:
            raise ProtocolError('unexpected service event')
        self._events.append(event)

    def poll(self):
        if self._closed:
            return []
        if self._peer is None:
            self._connect()
        if self._peer is not None:
            try:
                self._peer.flush()
                for event in self._peer.receive():
                    self._event(event)
                if self._peer.eof:
                    self._disconnect('service_disconnected')
            except (OSError, ValueError):
                self._disconnect('service_protocol_or_transport_error')
        events, self._events = self._events, []
        return events

    def _reply(self, ident, kind, reason=None):
        if ident != self._active or self._committed or self._peer is None or self._ready_sent:
            raise ValueError('no matching unacknowledged power request')
        event = {'type': kind, 'id': ident}
        if reason is not None:
            event['reason'] = str(reason)[:512]
        if kind == 'ready':
            # Even a partial write can have reached root; remain conservative.
            self._ready_sent = True
        try:
            self._peer.queue(event); self._peer.flush()
        except (OSError, ValueError):
            self._disconnect('reply_transport_error')

    def ready(self, ident):
        self._reply(ident, 'ready')

    def reject(self, ident, reason):
        self._reply(ident, 'reject', reason)

    def close(self):
        self._closed = True
        self._disconnect('client_closed')
