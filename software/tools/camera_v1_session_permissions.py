"""Repair only the empty session directory left by the 1.0 umask failure.

Streamed from the Mac. Does not change application archives or startup files.
The frozen session module is supplied by the caller as ``core``.
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid


def repair(core, expected_manifest_sha256, root=Path('/')):
    root = Path(root)
    core.verify_release(root, expected_manifest_sha256)
    base, _, _ = core.state_paths(root)
    core.trusted_file(base.parent/'permission-check-only', root, missing=True)
    if not base.exists() and not base.is_symlink():
        return {'ok': True, 'changed': False, 'reason': 'no failed session directory'}
    before = base.lstat()
    if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid():
        raise ValueError('session directory owner/type differs; no repair')
    mode = stat.S_IMODE(before.st_mode)
    if not mode & 0o022:
        core.trusted_file(base/'permission-check-only', root, missing=True)
        return {'ok': True, 'changed': False, 'reason': 'directory already has safe permissions'}
    if mode != 0o775:
        raise ValueError('session directory is not the known 0775 failure; no repair')
    inputs = core.known_inputs(root)
    if inputs['old_bytes'] != core.OLD or inputs['hook_bytes'] not in (None, core.SYSTEM, core.SHIM):
        raise ValueError('startup differs from the pre-enable failure; no repair')
    directory_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock_fd = None
    try:
        held = os.fstat(directory_fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
        if identity(held) != identity(before):
            raise ValueError('session directory changed during inspection')
        if os.listdir(directory_fd) != ['mutation.lock']:
            raise ValueError('session contains state, backups or unknown files; no repair')
        lock_fd = os.open('mutation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=directory_fd)
        lock = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock.st_mode) or lock.st_uid != os.geteuid() or
                lock.st_nlink != 1 or lock.st_size != 0 or
                stat.S_IMODE(lock.st_mode) not in (0o664, 0o644, 0o600)):
            raise ValueError('session lock differs from the empty installer lock; no repair')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('session operation is active; no repair')
        if (os.listdir(directory_fd) != ['mutation.lock'] or
                identity(base.lstat()) != identity(before) or
                (base/'mutation.lock').lstat().st_ino != lock.st_ino):
            raise ValueError('session files changed during inspection; no repair')
        record = {'directory': str(base), 'directory_mode': oct(mode),
                  'uid': before.st_uid, 'gid': before.st_gid,
                  'lock_mode': oct(stat.S_IMODE(lock.st_mode)), 'lock_size': lock.st_size,
                  'contents': ['mutation.lock'], 'startup_files_changed': False}
        backup = base.parent/('newcam-session-permissions-before-'+uuid.uuid4().hex+'.json')
        backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(backup_fd, 'w') as output:
            json.dump(record, output, sort_keys=True, indent=2)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.fchmod(lock_fd, 0o600)
        os.fchmod(directory_fd, 0o700)
        if (stat.S_IMODE(os.fstat(directory_fd).st_mode) != 0o700 or
                stat.S_IMODE(os.fstat(lock_fd).st_mode) != 0o600):
            raise ValueError('permission repair verification failed; backup retained')
        os.fsync(directory_fd)
        return {'ok': True, 'changed': True, 'permission_backup': str(backup),
                'before': record, 'directory_mode': '0o700', 'lock_mode': '0o600',
                'startup_files_changed': False}
    finally:
        if lock_fd is not None: os.close(lock_fd)
        os.close(directory_fd)
