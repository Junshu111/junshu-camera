#!/usr/bin/env python3
"""Bounded, allowlisted New Cam 1.0 boot package transfer; no cleanup."""
import hashlib
import fcntl
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import tarfile
import zlib
import uuid

VERSION = '1.0.0'
PREFIX = 'junshu-camera-boot-' + VERSION
FILES = ('VERSION', 'boot_system.py', 'theme/newcam.plymouth',
         'theme/newcam.script', 'theme/logo.png')
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024
APPROVED_LOGO_SHA256 = '8a8249c28098749e0b7a780a0d25a8e0120b3fe8a1a0fef39c6cd0928dcbfb2c'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path, limit=MAX_ARCHIVE):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Not a regular file: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('File size limit: ' + str(path))
    return data


def check_contents(contents):
    if set(contents) != set(FILES) | {'MANIFEST.sha256'}:
        raise ValueError('Unexpected package file list')
    expected = ''.join(sha(contents[name])+'  '+name+'\n' for name in sorted(FILES)).encode('ascii')
    if contents['MANIFEST.sha256'] != expected:
        raise ValueError('Manifest hash mismatch')
    if contents['VERSION'] != (VERSION+'\n').encode('ascii'):
        raise ValueError('Unexpected New Cam package version')
    check_png(contents['theme/logo.png'])
    if sha(contents['theme/logo.png']) != APPROVED_LOGO_SHA256:
        raise ValueError('Approved New Cam logo mismatch')
    return contents


def check_png(data):
    """Validate the exact opaque, non-interlaced PNG contract without Pillow."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Test screen must be PNG')
    offset = 8; chunks = []; compressed = bytearray()
    while offset + 12 <= len(data):
        size = struct.unpack('!I', data[offset:offset+4])[0]
        tag = data[offset+4:offset+8]; end = offset+12+size
        if end > len(data):
            raise ValueError('Truncated PNG')
        value = data[offset+8:offset+8+size]
        if zlib.crc32(tag+value)&0xffffffff != struct.unpack('!I',data[end-4:end])[0]:
            raise ValueError('PNG CRC mismatch')
        if not chunks and (tag != b'IHDR' or value != struct.pack('!IIBBBBB',480,800,8,2,0,0,0)):
            raise ValueError('Test PNG must be 480x800, 8-bit RGB, non-interlaced')
        if tag == b'IDAT':
            compressed.extend(value)
        chunks.append(tag); offset = end
        if tag == b'IEND':
            if value or offset != len(data):
                raise ValueError('PNG has trailing data')
            break
    if not chunks or chunks[-1] != b'IEND' or chunks.count(b'IHDR') != 1:
        raise ValueError('Incomplete PNG')
    expected = 800*(480*3+1)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected+1)
    except zlib.error as error:
        raise ValueError('Invalid PNG pixel data') from error
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid PNG pixel length')
    if any(raw[y*(480*3+1)]>4 for y in range(800)):
        raise ValueError('Invalid PNG filter')


def read_archive(path, expected_sha=None):
    data = regular_bytes(path)
    if expected_sha is not None and (not re.fullmatch('[0-9a-f]{64}', expected_sha) or sha(data) != expected_sha):
        raise ValueError('Trusted archive SHA256 mismatch')
    contents = {}; total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive:
            if not member.name.startswith(PREFIX+'/'):
                raise ValueError('Unexpected archive root')
            name = member.name[len(PREFIX)+1:]
            if name not in (*FILES, 'MANIFEST.sha256') or name in contents:
                raise ValueError('Unknown or duplicate archive entry')
            if not member.isfile() or member.mode != 0o644 or not 0 <= member.size <= MAX_ARCHIVE:
                raise ValueError('Unsafe archive file type/mode/size')
            total += member.size
            if total > MAX_EXPANDED:
                raise ValueError('Expanded archive limit')
            contents[name] = archive.extractfile(member).read(member.size + 1)
    return check_contents(contents)


def check_dir(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError('Unsafe receiver directory: ' + str(path))


def receive(archive, expected_sha, home):
    contents = read_archive(archive, expected_sha)  # validate everything before making directories
    home = Path(home).absolute(); check_dir(home)
    destination = home/PREFIX
    lock_path = home/('.'+PREFIX+'.import.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+b') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe import lock')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return receive_verified(contents, home, destination)


def receive_verified(contents, home, destination):
    if destination.exists() or destination.is_symlink():
        check_dir(destination); check_dir(destination/'theme')
        entries = {p.relative_to(destination).as_posix() for p in destination.rglob('*')}
        if entries != set(contents) | {'theme'}:
            raise ValueError('Existing receiver differs; preserved without overwrite')
        for name, data in contents.items():
            path = destination/name
            if regular_bytes(path) != data or path.stat().st_mode & 0o022 or path.stat().st_uid != os.geteuid():
                raise ValueError('Existing receiver differs; preserved: ' + name)
        changed = False
    else:
        stage = home/('.'+PREFIX+'.staging-'+uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        (stage/'theme').mkdir(mode=0o755)
        for name, data in sorted(contents.items()):
            with (stage/name).open('xb') as output:
                output.write(data)
                output.flush(); os.fsync(output.fileno())
            (stage/name).chmod(0o644)
            if regular_bytes(stage/name) != data:
                raise ValueError('Staged receiver verification failed')
        for directory in (stage/'theme', stage):
            fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        if destination.exists() or destination.is_symlink():
            raise ValueError('Receiver appeared during staging; preserved both directories')
        os.rename(stage, destination)
        changed = True
    return {'changed': changed, 'package': str(destination), 'files_verified': len(contents)-1,
            'manifest_sha256': sha(contents['MANIFEST.sha256']), 'system_config_changed': False}
