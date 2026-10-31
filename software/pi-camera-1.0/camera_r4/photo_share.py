"""Scoped, revocable LAN JPEG sharing. No Qt, camera, GPIO or network configuration.

Call start in a GUI worker: validation hashes only the chosen files. The server
binds exactly the supplied RFC1918 IPv4 (or explicit loopback for local tests).
The URL is a bearer capability: callers must never log it.
"""
from dataclasses import dataclass
from io import BytesIO
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import threading
import time
from urllib.parse import quote, urlsplit
import zipfile

from PIL import Image

from .share_page import CSP, render_page

MAX_PHOTOS = 100
MAX_BYTES = 2 * 1024 ** 3
CHUNK = 65536
MAX_WORKERS = 4
SOCKET_TIMEOUT = 5
HEADER_TIMEOUT = 5
MAX_PIXELS = 32 * 1024 ** 2
_ID = re.compile(r'(?:0|[1-9][0-9]{0,2})\Z')
_PRIVATE = tuple(ipaddress.IPv4Network(value) for value in
                 ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class _SelectedPhoto:
    name: str
    byte_count: int
    width: int
    height: int
    sha256: str
    fingerprint: tuple


class _AcquisitionReader:
    """Read request line + headers against one absolute monotonic deadline.

    Buffered socket.readline applies its timeout separately to every underlying
    recv, so trickled bytes can keep it alive forever. Recompute the remaining
    budget before each bounded recv instead.
    """
    def __init__(self, connection):
        self.connection = connection
        self.deadline = time.monotonic() + HEADER_TIMEOUT
        self.pending = bytearray()

    def readline(self, limit=65536):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('请求读取超时')
            newline = self.pending.find(b'\n', 0, limit)
            count = newline + 1 if newline >= 0 else min(len(self.pending), limit)
            if newline >= 0 or len(self.pending) >= limit:
                result = bytes(self.pending[:count])
                del self.pending[:count]
                return result
            self.connection.settimeout(min(SOCKET_TIMEOUT, remaining))
            block = self.connection.recv(min(2048, limit - len(self.pending)))
            if not block:
                result = bytes(self.pending)
                self.pending.clear()
                return result
            self.pending.extend(block)


class _HeaderReader:
    """Bound the sum of header bytes before http.client parses them."""
    def __init__(self, source):
        self.source = source
        self.remaining = 8192

    def readline(self, limit=-1):
        maximum = min(2049, self.remaining + 1, limit if limit >= 0 else 2049)
        line = self.source.readline(maximum)
        self.remaining -= len(line)
        if self.remaining < 0 or len(line) > 2048:
            raise http.client.LineTooLong('request header')
        return line


class _ShareServer(HTTPServer):
    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, address, owner):
        self.owner = owner
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)
        super().__init__(address, _Handler)
        self.timeout = .1

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.owner._lock:
            if not self.owner._active_unlocked():
                self.slots.release()
                self.shutdown_request(request)
                return
            self.owner._connections.add(request)
        worker = threading.Thread(target=self._worker, args=(request, client_address),
                                  name='photo-share-request', daemon=True)
        try:
            worker.start()
        except BaseException:
            with self.owner._lock:
                self.owner._connections.discard(request)
            self.slots.release()
            self.shutdown_request(request)
            raise

    def _worker(self, request, address):
        try:
            request.settimeout(SOCKET_TIMEOUT)
            self.finish_request(request, address)
        except (OSError, ValueError):
            pass  # No request paths, capability tokens, filenames or URLs in logs.
        finally:
            self.shutdown_request(request)
            with self.owner._lock:
                self.owner._connections.discard(request)
            self.slots.release()

    def handle_error(self, request, client_address):
        pass


class _StreamSink:
    """Unseekable ZIP writer: zipfile emits data descriptors, never a disk archive."""
    def __init__(self, handler):
        self.handler = handler
        self.position = 0

    def write(self, data):
        self.handler.session._require_active()
        # Even central-directory and metadata output obey the chunk bound.
        for offset in range(0, len(data), CHUNK):
            self.handler.wfile.write(data[offset:offset + CHUNK])
        self.position += len(data)
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        self.handler.wfile.flush()


class PhotoShareSession:
    @classmethod
    def start(cls, photo_directory: Path, paths: list[Path], address: str,
              port: int = 0, ttl_seconds: int = 900):
        try:
            ip = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError as error:
            raise ValueError('分享地址必须为本机私有 IPv4') from error
        if str(ip) != '127.0.0.1' and not any(ip in network for network in _PRIVATE):
            raise ValueError('仅支持同一 Wi-Fi 的私有 IPv4')
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 900:
            raise ValueError('分享有效期须为 1 到 900 秒')
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError('无效端口')
        if not paths or len(paths) > MAX_PHOTOS:
            raise ValueError('每次须选择 1 到 100 张照片')
        root = Path(photo_directory).expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ValueError('照片目录必须为普通目录')
        root = root.resolve()
        self = cls()
        self._lock = threading.RLock()
        self._closed = False
        self._connections = set()
        self._downloads_started = self._downloads_finished = 0
        self._server = None
        self._thread = None
        self._initializing = True
        self._deadline = None
        self._root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = []
            for selected in paths:
                selected = Path(selected).expanduser()
                if not selected.is_absolute():
                    selected = root / selected
                if selected.parent.resolve() != root or selected.parent.is_symlink():
                    raise ValueError('只能分享相机照片目录内的照片')
                name = selected.name
                if (not name.startswith('JC_') or selected.suffix.upper() not in ('.JPG', '.JPEG')
                        or '\\' in name
                        or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise ValueError('只能分享相机 JPG 照片')
                names.append(name)
            if len(set(names)) != len(names):
                raise ValueError('不可重复选择照片')
            # Check count and aggregate size before JPEG decoding/hashing.
            infos = [os.stat(name, dir_fd=self._root_fd, follow_symlinks=False) for name in names]
            if any(not stat.S_ISREG(info.st_mode) for info in infos):
                raise ValueError('照片必须为普通文件，不可为符号链接')
            if sum(info.st_size for info in infos) > MAX_BYTES:
                raise ValueError('所选照片总大小不可超过 2 GiB')
            records = []
            for name, info in zip(names, infos):
                with self._open_name(name, _fingerprint(info)) as source:
                    with Image.open(source) as image:
                        if image.format != 'JPEG' or image.width * image.height > MAX_PIXELS:
                            raise ValueError('不是有效的相机 JPEG 或像素过大')
                        width, height = image.size
                        image.load()
                    source.seek(0)
                    digest = hashlib.sha256()
                    for block in iter(lambda: source.read(CHUNK), b''):
                        digest.update(block)
                    if _fingerprint(os.fstat(source.fileno())) != _fingerprint(info):
                        raise ValueError('所选照片已变动')
                records.append(_SelectedPhoto(name, info.st_size, width, height,
                                               digest.hexdigest(), _fingerprint(info)))
            self._records = tuple(records)
            self._token = secrets.token_urlsafe(32)
            self._server = _ShareServer((str(ip), port), self)
            endpoint = '{}:{}'.format(ip, self._server.server_address[1])
            self._host = endpoint
            self._origin = 'http://' + endpoint
            self.url = self._origin + '/s/' + self._token + '/'
            # Validation belongs to startup, not the user's 15 minute transfer window.
            with self._lock:
                self._deadline = time.monotonic() + ttl_seconds
                self._initializing = False
            self._thread = threading.Thread(target=self._serve, name='photo-share-server', daemon=True)
            self._thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def _active_unlocked(self):
        return (not self._closed and not self._initializing
                and time.monotonic() < self._deadline)

    def _require_active(self, *, allow_initializing=False):
        with self._lock:
            if self._closed or not (self._active_unlocked() or (allow_initializing and self._initializing)):
                raise ConnectionAbortedError('分享已结束')

    def status(self):
        with self._lock:
            active = self._active_unlocked()
            return {'active': active,
                    'seconds_left': max(0, math.ceil(self._deadline - time.monotonic())) if active else 0,
                    'count': len(self._records), 'downloads_started': self._downloads_started,
                    'downloads_finished': self._downloads_finished,
                    'active_requests': len(self._connections)}

    def _serve(self):
        try:
            while True:
                with self._lock:
                    if not self._active_unlocked():
                        break
                try:
                    self._server.handle_request()
                except (OSError, ValueError):
                    break
        finally:
            self.close()

    def close(self):
        # No joins or server.shutdown(): safe even from worker/server/GUI paths.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            connections = tuple(self._connections)
            self._connections.clear()
            if self._server is not None:
                self._server.server_close()
            fd, self._root_fd = self._root_fd, None
            if fd is not None:
                os.close(fd)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _open_name(self, name, expected):
        with self._lock:
            self._require_active(allow_initializing=True)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._root_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or _fingerprint(info) != expected:
                raise ValueError('照片已变动，请重新选择分享')
            return os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise

    def _open_selected(self, index):
        record = self._records[index]
        return self._open_name(record.name, record.fingerprint)

    def _download_count(self, finished=False):
        with self._lock:
            if finished:
                self._downloads_finished += 1
            else:
                self._downloads_started += 1


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'CameraShare'
    sys_version = ''

    @property
    def session(self):
        return self.server.owner

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        self.close_connection = True
        try:
            acquisition = _AcquisitionReader(self.connection)
            self.raw_requestline = acquisition.readline(2049)
            if not self.raw_requestline:
                return
            if len(self.raw_requestline) > 2048:
                self.requestline = ''
                self.request_version = 'HTTP/1.0'
                self.command = None
                self.send_error(414)
                return
            original = self.rfile
            self.rfile = _HeaderReader(acquisition)
            try:
                parsed = self.parse_request()
            finally:
                self.rfile = original
                self.connection.settimeout(SOCKET_TIMEOUT)
            if not parsed:
                return
            if self.command not in ('GET', 'HEAD'):
                self.send_error(405)
                return
            self.do_GET()
        except (OSError, ValueError):
            self.close_connection = True

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Connection', 'close')
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        labels = {400: '请求无效', 403: '此请求不属于当前分享', 404: '链接无效或分享已结束',
                  405: '仅支持下载', 409: '照片已变动，请在相机上重新分享',
                  410: '分享已结束，请在相机上重新开启', 414: '请求过长', 431: '请求头过大'}
        body = ('<!doctype html><meta charset="utf-8"><title>相机分享</title><p>'
                + labels.get(code, '无法完成请求，请重新分享') + '</p>').encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if getattr(self, 'command', None) != 'HEAD':
            self.wfile.write(body)

    def _bytes(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _ids(self, route, query):
        if route == 'download':
            parts = query.split('&')
            if not 1 <= len(parts) <= MAX_PHOTOS or any(not part.startswith('ids=') for part in parts):
                raise ValueError('Invalid selection')
            values = [part[4:] for part in parts]
        else:
            if query:
                raise ValueError('Invalid query')
            values = [route.split('/')[1]]
        if any(not _ID.fullmatch(value) for value in values):
            raise ValueError('Invalid ID')
        ids = [int(value) for value in values]
        if len(set(ids)) != len(ids) or any(index >= len(self.session._records) for index in ids):
            raise ValueError('Invalid selection')
        return ids

    def do_GET(self):
        session = self.session
        if self.headers.get_all('Host') != [session._host]:
            self.send_error(403)
            return
        origins = self.headers.get_all('Origin')
        if origins is not None and origins != [session._origin]:
            self.send_error(403)
            return
        lengths = self.headers.get_all('Content-Length')
        if self.headers.get('Transfer-Encoding') is not None or (lengths is not None and lengths != ['0']):
            self.send_error(400)
            return
        if not session.status()['active']:
            self.send_error(410)
            return
        parsed = urlsplit(self.path)
        prefix = '/s/' + session._token + '/'
        if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith(prefix):
            self.send_error(404)
            return
        route = parsed.path[len(prefix):]
        if route in ('', 'status'):
            if parsed.query:
                self.send_error(400)
                return
            if route == '':
                self._bytes(render_page(session._records, session.status()['seconds_left']), 'text/html; charset=utf-8')
            else:
                self._bytes(json.dumps(session.status()).encode(), 'application/json')
            return
        if route != 'download' and not re.fullmatch(r'(original|thumbnail)/[^/]+', route):
            self.send_error(404)
            return
        try:
            ids = self._ids(route, parsed.query)
        except ValueError:
            self.send_error(400)
            return
        opened = []
        try:
            try:
                for index in ids:
                    opened.append(session._open_selected(index))
            except (ValueError, OSError):
                self.send_error(409)
                return
            if route.startswith('thumbnail/'):
                with Image.open(opened[0]) as image:
                    image.draft('RGB', (320, 320))
                    image.thumbnail((320, 320))
                    buffer = BytesIO()
                    image.convert('RGB').save(buffer, format='JPEG', quality=75)
                session._require_active()
                self._bytes(buffer.getvalue(), 'image/jpeg')
                return
            session._require_active()
            self.send_response(200)
            if route == 'download':
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', 'attachment; filename="JunshuCamera-photos.zip"')
            else:
                record = session._records[ids[0]]
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(record.byte_count))
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(record.name, safe=''))
            self.end_headers()
            if self.command == 'HEAD':
                return
            session._download_count()
            if route == 'download':
                with zipfile.ZipFile(_StreamSink(self), 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for index, source in zip(ids, opened):
                        record = session._records[index]
                        info = zipfile.ZipInfo(record.name)
                        info.compress_type = zipfile.ZIP_STORED
                        info.file_size = record.byte_count
                        with archive.open(info, 'w', force_zip64=True) as destination:
                            self._copy(source, destination, record.byte_count)
            else:
                self._copy(opened[0], self.wfile, session._records[ids[0]].byte_count)
            self.wfile.flush()
            session._require_active()
            session._download_count(finished=True)
        finally:
            for source in opened:
                source.close()

    def _copy(self, source, destination, byte_count):
        remaining = byte_count
        while remaining:
            self.session._require_active()
            block = source.read(min(CHUNK, remaining))
            if not block:
                raise OSError('所选照片已变动')
            destination.write(block)
            remaining -= len(block)
