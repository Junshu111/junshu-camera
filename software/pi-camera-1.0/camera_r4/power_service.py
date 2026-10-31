"""Root daemon: physical hold -> authenticated save -> ordinary systemd poweroff."""
import fcntl
import logging
import os
from pathlib import Path
import socket
import stat
import subprocess
import time
import uuid

from .power_client import JsonPeer, ProtocolError, SOCKET_ADDRESS, peer_uid, valid_id
from .power_indicator import OptionalIndicator

log = logging.getLogger(__name__)
LOCK_PATH = Path('/home/junshu/.local/state/junshu-camera/instance.lock')


class HoldDetector:
    """50 ms release/press debounce; 3 continuous seconds after stable press.

Unknown startup state, invalid reads, and >250 ms sample gaps require a fresh
stable release. Raw HIGH cancels the timer immediately (fail-safe bounce).
"""
    def __init__(self):
        self.invalidate()

    def invalidate(self):
        self.raw = None
        self.changed = None
        self.last = None
        self.armed = False
        self.held_since = None

    def sample(self, pressed, now):
        if type(pressed) is not bool:
            self.invalidate()
            raise ValueError('GPIO3 must return an explicit boolean')
        if self.last is not None and not 0 <= now - self.last <= .25:
            self.invalidate()
        self.last = now
        if pressed != self.raw:
            self.raw = pressed; self.changed = now
            self.held_since = None
        if now - self.changed < .05:
            return False
        if not pressed:
            self.armed = True
            return False
        if not self.armed:
            return False
        if self.held_since is None:
            self.held_since = now
        if now - self.held_since < 3.:
            return False
        self.armed = False
        return True


class InstanceLease:
    """Lock existing inode read-only, never follow links or open a FIFO blocking.

The directory chain is walked with openat/O_NOFOLLOW. Ancestor identity is
rechecked before poweroff. No root write, truncate, create, or unlink occurs.
"""
    def __init__(self, path, uid):
        self.path = Path(path)
        self.uid = uid
        self.fd = None
        self._dirs = []
        if not self.path.is_absolute():
            raise ValueError('absolute legacy lock path required')
        try:
            current = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            self._dirs.append((Path('/'), current))
            prefix = Path('/')
            for part in self.path.parts[1:-1]:
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                prefix = prefix / part
                self._dirs.append((prefix, current))
                info = os.fstat(current)
                # Root-owned sticky temporary ancestors are allowed for local
                # retained tests. Production /home has no writable ancestors.
                if info.st_uid not in (0, uid) or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                    raise PermissionError('untrusted legacy lock directory')
            before = os.stat(self.path.name, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise PermissionError('legacy lock is not a regular file')
            if hasattr(os, 'O_PATH'):
                # Linux O_PATH does not open device/FIFO endpoints, even if the
                # user's directory changed after lstat. Reopen only a checked
                # regular inode through our own fd, not through the user path.
                handle = os.open(self.path.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=current)
                try:
                    selected = os.fstat(handle)
                    if not stat.S_ISREG(selected.st_mode):
                        raise PermissionError('legacy lock changed to special file')
                    self.fd = os.open('/proc/self/fd/{}'.format(handle), os.O_RDONLY | os.O_NONBLOCK)
                finally:
                    os.close(handle)
            else:
                # Mac-only test fallback. Production entry requires Linux.
                self.fd = os.open(self.path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=current)
            info = os.fstat(self.fd)
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise RuntimeError('legacy lock changed while opening')
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or info.st_mode & 0o022:
                raise PermissionError('legacy lock must be single-link regular user-owned non-writable-by-others file')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.validate()
        except BaseException:
            self.close()
            raise

    def validate(self):
        for path, fd in self._dirs:
            actual, opened = os.stat(path, follow_symlinks=False), os.fstat(fd)
            if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
                raise RuntimeError('legacy lock directory changed')
        actual, opened = os.stat(self.path, follow_symlinks=False), os.fstat(self.fd)
        if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino) or actual.st_nlink != 1:
            raise RuntimeError('legacy lock changed')

    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None
        for _, fd in self._dirs:
            os.close(fd)
        self._dirs = []


def request_system_poweroff():
    subprocess.run(['/usr/bin/systemctl', '--no-ask-password', 'poweroff'],
                   check=True, timeout=15, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'},
                   close_fds=True)


class PowerService:
    def __init__(self, gpio, allowed_uid, listener=None, lock_path=LOCK_PATH,
                 clock=time.monotonic, peer_uid=peer_uid, command=request_system_poweroff,
                 indicator=None, stop_requested=lambda: False):
        if os.geteuid() != 0:
            raise PermissionError('power service requires root')
        if type(allowed_uid) is not int or allowed_uid <= 0:
            raise ValueError('configured camera UID must be a non-root account')
        self.gpio = gpio
        self.allowed_uid = allowed_uid
        self.clock = clock
        self.peer_uid = peer_uid
        self.command = command
        self.stop_requested = stop_requested
        self.lock_path = lock_path
        self.indicator = indicator or OptionalIndicator()
        self.detector = HoldDetector()
        self.state = 'idle'
        self.request_id = None
        self.deadline = None
        self._peer = None
        self._lease = None
        self._closed = False
        self._last_cancel = None
        self._gpio_error = False
        if listener is None:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(SOCKET_ADDRESS); listener.listen(4)
            except BaseException:
                listener.close(); raise
        self.listener = listener
        listener.setblocking(False)
        self.indicator.stage('ready')

    def _send(self, event):
        if self._peer is not None:
            self._peer.queue(event); self._peer.flush()

    def _cancel(self, reason):
        if self.state in ('committed', 'uncertain'):
            return
        ident = self.request_id
        self.state = 'idle'; self.request_id = None; self.deadline = None
        self.detector.invalidate()
        if self._lease is not None:
            self._lease.close(); self._lease = None
        if ident:
            log.warning('power request=%s canceled reason=%s', ident, reason)
            self._last_cancel = {'type': 'cancel', 'id': ident, 'reason': str(reason)[:512]}
            try:
                self._send(self._last_cancel)
            except (OSError, ValueError):
                if self._peer is not None:
                    self._peer.close(); self._peer = None
        self.indicator.stage('error')

    def _drop_peer(self, reason):
        if self._peer is not None:
            self._peer.close(); self._peer = None
        if self.state == 'preparing':
            self._cancel(reason)

    def _accept(self):
        for _ in range(4):
            try:
                sock, _ = self.listener.accept()
            except BlockingIOError:
                break
            try:
                if self._peer is not None or self.peer_uid(sock) != self.allowed_uid:
                    sock.close(); continue
                self._peer = JsonPeer(sock)
                self._send({'type': 'status', 'state': 'committed' if self.state == 'committed' else 'connected',
                            'phase': self.state, 'id': self.request_id, 'reason': ''})
                if self._last_cancel:
                    self._send(self._last_cancel)
            except (OSError, ValueError):
                sock.close(); self._drop_peer('peer_authentication_failed')

    def _receive(self):
        if self._peer is None:
            return
        try:
            self._peer.flush()
            events = self._peer.receive()
            # A peer that has already closed cannot authorize a new shutdown.
            if self._peer.eof:
                self._drop_peer('camera_disconnected'); return
            for event in events:
                if (event['type'] not in ('ready', 'reject') or not valid_id(event.get('id'))
                        or self.state != 'preparing' or event['id'] != self.request_id):
                    raise ProtocolError('unexpected camera reply')
                if event['type'] == 'reject':
                    if not isinstance(event.get('reason'), str):
                        raise ProtocolError('reject reason required')
                    self._cancel('camera_rejected: ' + event['reason'][:512])
                else:
                    self._poweroff()
        except (OSError, ValueError):
            self._drop_peer('camera_protocol_or_transport_error')

    def _prepare(self):
        if self.state != 'idle':
            return
        self.request_id = uuid.uuid4().hex
        self.state = 'preparing'
        self.deadline = self.clock() + 60.
        self.indicator.stage('saving')
        if self._peer is not None:
            try:
                self._send({'type': 'prepare', 'id': self.request_id})
            except (OSError, ValueError):
                self._drop_peer('prepare_delivery_failed')
        else:
            try:
                self._lease = InstanceLease(self.lock_path, self.allowed_uid)
            except (OSError, RuntimeError, ValueError) as error:
                self._cancel('camera_absence_unconfirmed: ' + str(error))
                return
            self._poweroff()

    def _poweroff(self):
        if self._closed or self.state != 'preparing':
            return
        if self.stop_requested():
            self._cancel('service_stopped')
            return
        try:
            if self._lease is not None:
                self._lease.validate()
            self.command()
        except subprocess.TimeoutExpired:
            # systemctl may have delivered poweroff before losing its reply.
            # Do not resume writes or release the absent-camera lease on doubt.
            self.state = 'uncertain'; self.deadline = None
            self.detector.invalidate()
            self.indicator.stage('error')
            log.error('poweroff outcome unknown request=%s; no retry', self.request_id)
            try:
                self._send({'type': 'status', 'state': 'connected', 'phase': 'uncertain',
                            'id': self.request_id, 'reason': 'ready_outcome_unknown'})
            except (OSError, ValueError):
                self._drop_peer('poweroff_outcome_unknown')
            return
        except Exception as error:
            self._cancel('poweroff_failed: ' + str(error))
            return
        self.state = 'committed'
        self.deadline = None
        self.detector.invalidate()
        self.indicator.stage('committed')
        log.info('ordinary systemd poweroff accepted request=%s', self.request_id)
        # Failure here cannot undo a successfully accepted systemd poweroff.
        # READY clients preserve the frozen state across a lost outcome.
        try:
            self._send({'type': 'committed', 'id': self.request_id})
        except (OSError, ValueError):
            self._drop_peer('committed_delivery_failed')

    def step(self):
        if self._closed:
            return
        now = self.clock()
        try:
            pressed = self.gpio.read()
            # Timestamp completion: a blocked read must not certify a hold
            # using the timestamp from before the missing sampling interval.
            now = self.clock()
            if self.state == 'idle':
                qualified = self.detector.sample(pressed, now)
            else:
                self.detector.invalidate(); qualified = False
            self._gpio_error = False
        except Exception as error:
            self.detector.invalidate(); qualified = False
            if not self._gpio_error:
                log.error('GPIO3 read failed: %s', error)
            self._gpio_error = True
            if self.state == 'preparing':
                self._cancel('gpio_read_failed')
        self._accept()
        if self.state == 'preparing' and now >= self.deadline:
            self._cancel('camera_prepare_timeout')
        self._receive()
        if qualified:
            self._prepare()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.state == 'preparing':
            self._cancel('service_stopped')
        if self._peer is not None:
            self._peer.close(); self._peer = None
        self.listener.close()
        if self._lease is not None:
            self._lease.close(); self._lease = None
        self.gpio.close()
