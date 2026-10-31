#!/usr/bin/env python3
"""Configure the known Pi labwc session for New Cam 1.0.0, without live changes.

Only user-owned startup files are written. Backups remain on disk forever;
rollback uses a delegation shim when the original user autostart was absent.
"""
import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import sys
import uuid
from pathlib import Path, PurePosixPath

VERSION = '1.0.0'
RELEASE = '/home/junshu/junshu-camera-pi-1.0.0'
SYSTEM = (b'/usr/bin/lwrespawn /usr/bin/pcmanfm-pi &\n'
          b'/usr/bin/lwrespawn /usr/bin/wf-panel-pi &\n'
          b'/usr/bin/kanshi &\n/usr/bin/lxsession-xdg-autostart\n')
HOOK = (b'#!/bin/sh\n/usr/bin/kanshi &\n'
        b'/usr/bin/lxsession-xdg-autostart &\n'
        b'/bin/sh '+RELEASE.encode()+b'/start-session.sh &\n')
SHIM = b'#!/bin/sh\nexec /bin/sh /etc/xdg/labwc/autostart\n'
OLD = (b'[Desktop Entry]\nType=Application\nName=Junshu Camera\n'
       b'Exec=/bin/sh /home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh\n'
       b'Terminal=false\nStartupNotify=false\nX-Junshu-Managed=0.4.0\n')
OLD_HIDDEN = OLD+b'Hidden=true\n'
REQUIRED_RELEASE = {'run_camera.py', 'start-session.sh', 'session_setup.py',
                    'start-autostart.sh', 'assets/boot-logo.png'}
MAX_FILE = 4*1024*1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def path_at(root, absolute):
    return Path(root)/absolute.lstrip('/')


def trusted_file(path, root, missing=False):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('untrusted root')
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError('path outside root')
    cursor = root
    for part in relative.parts:
        cursor = cursor/part
        if cursor.is_symlink():
            raise ValueError('symlink refused: '+str(cursor))
        if cursor.exists() and str(cursor).startswith(str(path_at(root,'/home/junshu'))):
            info=cursor.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError('untrusted user ownership/mode: '+str(cursor))
    if path.exists():
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError('non-file or oversized input: '+str(path))
    elif not missing:
        raise ValueError('required file missing: '+str(path))
    return path


def read_file(path, root, missing=False):
    trusted_file(path, root, missing=missing)
    return path.read_bytes() if path.exists() else None


def verify_release(root, expected_manifest_sha256):
    if not expected_manifest_sha256 or not re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256):
        raise ValueError('trusted expected manifest SHA256 required')
    release = path_at(root, RELEASE)
    manifest_path = trusted_file(release/'MANIFEST.sha256', root)
    manifest = manifest_path.read_bytes()
    if digest(manifest) != expected_manifest_sha256:
        raise ValueError('installed manifest SHA256 mismatch')
    names = set()
    for line in manifest.decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_./-]+)', line)
        if not match:
            raise ValueError('invalid manifest entry')
        expected, name = match.groups()
        pure=PurePosixPath(name)
        parts=pure.parts
        if name in names or not parts or str(pure)!=name or pure.is_absolute() or any(p in ('.','..') for p in parts):
            raise ValueError('unsafe/duplicate manifest path')
        names.add(name)
        if digest(trusted_file(release/name, root).read_bytes()) != expected:
            raise ValueError('installed file digest mismatch: '+name)
    if not REQUIRED_RELEASE <= names:
        raise ValueError('manifest lacks runtime files')
    allowed=names|{'MANIFEST.sha256'}
    for entry in release.rglob('*'):
        relative=entry.relative_to(release).as_posix()
        if entry.is_symlink():
            raise ValueError('linked release member: '+relative)
        if entry.is_dir():
            if not any(n.startswith(relative+'/') for n in allowed):
                raise ValueError('unlisted release directory: '+relative)
        elif relative not in allowed or not entry.is_file():
            raise ValueError('unlisted release file: '+relative)
    return {'manifest_sha256':digest(manifest), 'files_verified':len(names)}


def known_inputs(root):
    home = path_at(root, '/home/junshu')
    system = path_at(root, '/etc/xdg/labwc/autostart')
    hook = home/'.config/labwc/autostart'
    old = home/'.config/autostart/junshu-camera-r4-integrated.desktop'
    if read_file(system, root) != SYSTEM:
        raise ValueError('system labwc startup differs from reviewed Pi baseline')
    hook_bytes = read_file(hook, root, missing=True)
    if hook_bytes not in (None, SYSTEM, SHIM, HOOK):
        raise ValueError('unknown customized user labwc startup retained')
    old_bytes = read_file(old, root)
    if old_bytes not in (OLD, OLD_HIDDEN):
        raise ValueError('unknown managed 0.4.0 XDG entry retained')
    # Preserve unrelated XDG services. Refuse a second active camera entry.
    for folder in (home/'.config/autostart', path_at(root, '/etc/xdg/autostart')):
        if not folder.exists():
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError('untrusted XDG directory')
        for entry in folder.glob('*.desktop'):
            if entry == old:
                continue
            data = read_file(entry, root)
            if (not re.search(rb'(?im)^Hidden\s*=\s*true\s*$',data) and
                re.search(rb'junshu-camera|run_camera\.py|camera_r4', data, re.I)):
                raise ValueError('unknown active camera XDG entry: '+str(entry))
    return {'hook':hook, 'hook_bytes':hook_bytes, 'old':old, 'old_bytes':old_bytes}


def write_known(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.parent/('.'+path.name+'.newcam-'+uuid.uuid4().hex)
    with stage.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    stage.chmod(mode)
    os.replace(stage,path)
    directory = os.open(str(path.parent),os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    if path.read_bytes() != data or stat.S_IMODE(path.stat().st_mode) != mode:
        raise OSError('write verification failed: '+str(path))


def state_paths(root):
    base = path_at(root, '/home/junshu/.local/state/junshu-camera/newcam-session')
    return base, base/'state.json', base/'backups'


def load_state(root):
    _, state_path, _ = state_paths(root)
    data = read_file(state_path, root, missing=True)
    if data is None:
        return None
    state = json.loads(data)
    if not isinstance(state, dict):
        raise ValueError('unrecognized session state')
    if (state.get('status') not in ('pending','active','rolled_back') or
            not re.fullmatch(r'[0-9a-f]{32}',state.get('backup_id','')) or
            not re.fullmatch(r'[0-9a-f]{64}',state.get('manifest_sha256','')) or
            not re.fullmatch(r'[0-9a-f]{64}',state.get('backup_metadata_sha256',''))):
        raise ValueError('unrecognized session state')
    return state


def backup_inputs(root, inputs, manifest_sha):
    base, _, backups = state_paths(root)
    backups.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    folder = backups/ident
    folder.mkdir(mode=0o700)
    original_hook = inputs['hook_bytes']
    original_old = inputs['old_bytes']
    if original_hook is not None:
        write_known(folder/'hook.bin', original_hook, 0o600)
    write_known(folder/'old.bin', original_old, 0o600)
    write_known(folder/'candidate-hook.bin', HOOK, 0o600)
    write_known(folder/'candidate-old.bin', OLD_HIDDEN, 0o600)
    metadata = {'backup_id':ident, 'hook_existed':original_hook is not None,
                'hook_mode':stat.S_IMODE(inputs['hook'].stat().st_mode) if original_hook is not None else None,
                'old_mode':stat.S_IMODE(inputs['old'].stat().st_mode),
                'hook_sha256':digest(original_hook) if original_hook is not None else None,
                'old_sha256':digest(original_old), 'release_manifest_sha256':manifest_sha}
    metadata_bytes = (json.dumps(metadata,sort_keys=True)+'\n').encode()
    write_known(folder/'metadata.json', metadata_bytes, 0o600)
    metadata['metadata_sha256'] = digest(metadata_bytes)
    return metadata


def original_from_backup(root, ident, expected_metadata_sha256):
    _, _, backups = state_paths(root)
    folder = backups/ident
    metadata_bytes = read_file(folder/'metadata.json', root)
    if digest(metadata_bytes) != expected_metadata_sha256:
        raise ValueError('backup metadata changed')
    metadata = json.loads(metadata_bytes)
    if not isinstance(metadata, dict):
        raise ValueError('backup metadata invalid')
    if metadata.get('backup_id') != ident or type(metadata.get('hook_existed')) is not bool:
        raise ValueError('backup identity mismatch')
    if (type(metadata.get('old_mode')) is not int or not 0 <= metadata['old_mode'] <= 0o777 or
            (metadata['hook_existed'] and (type(metadata.get('hook_mode')) is not int or not 0 <= metadata['hook_mode'] <= 0o777)) or
            (not metadata['hook_existed'] and metadata.get('hook_mode') is not None)):
        raise ValueError('backup mode metadata invalid')
    old = read_file(folder/'old.bin', root)
    hook = read_file(folder/'hook.bin', root) if metadata['hook_existed'] else None
    if (old != OLD or digest(old) != metadata['old_sha256'] or
            (hook is not None and (hook not in (SYSTEM,SHIM) or digest(hook) != metadata['hook_sha256'])) or
            (hook is None and metadata.get('hook_sha256') is not None)):
        raise ValueError('backup content changed')
    if (read_file(folder/'candidate-hook.bin', root) != HOOK or
            read_file(folder/'candidate-old.bin', root) != OLD_HIDDEN):
        raise ValueError('backup candidate changed')
    return metadata, hook, old


def checked_recovery(root, state):
    metadata, hook, old = original_from_backup(root, state['backup_id'], state['backup_metadata_sha256'])
    if metadata.get('release_manifest_sha256') != state['manifest_sha256']:
        raise ValueError('backup release identity differs from state')
    return metadata, hook, old


def assert_rolled_back(inputs, metadata, original_hook, original_old):
    expected_hook = original_hook if original_hook is not None else SHIM
    expected_mode = metadata['hook_mode'] if original_hook is not None else 0o755
    if (inputs['hook_bytes'] != expected_hook or inputs['old_bytes'] != original_old or
            stat.S_IMODE(inputs['hook'].stat().st_mode) != expected_mode or
            stat.S_IMODE(inputs['old'].stat().st_mode) != metadata['old_mode']):
        raise ValueError('rolled-back session has drifted')


def configure(action, root=Path('/'), expected_manifest_sha256=None):
    root=Path(root)
    if action not in ('enable','rollback'):
        return _configure_unlocked(action,root,expected_manifest_sha256)
    base, _, _ = state_paths(root)
    lock_path=base/'mutation.lock'
    trusted_file(lock_path,root,missing=True)
    base.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a+b') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('session mutation already in progress')
        return _configure_unlocked(action,root,expected_manifest_sha256)


def _configure_unlocked(action, root, expected_manifest_sha256):
    root = Path(root)
    inputs = known_inputs(root)
    _, state_path, _ = state_paths(root)
    state = load_state(root)
    if action == 'probe':
        return {'ok':True, 'changed':False, 'configured':bool(state and state['status']=='active'),
                'pending':bool(state and state['status']=='pending'),
                'hook':('managed' if inputs['hook_bytes']==HOOK else 'system' if inputs['hook_bytes'] in (None,SYSTEM,SHIM) else 'unknown'),
                'old_camera_hidden':inputs['old_bytes']==OLD_HIDDEN, 'live_changed':False}
    if action == 'verify':
        if state and state['status']=='pending':
            raise ValueError('session mutation pending; run rollback before using session')
        active = bool(state and state['status']=='active')
        if active:
            metadata, _, _ = checked_recovery(root, state)
            if (inputs['hook_bytes']!=HOOK or inputs['old_bytes']!=OLD_HIDDEN or
                    stat.S_IMODE(inputs['hook'].stat().st_mode)!=(metadata['hook_mode'] if metadata['hook_existed'] else 0o755) or
                    stat.S_IMODE(inputs['old'].stat().st_mode)!=metadata['old_mode']):
                raise ValueError('enabled session has drifted')
            verification = verify_release(root, state['manifest_sha256'])
        else:
            verification = {}
        if state and state['status']=='rolled_back':
            metadata, original_hook, original_old = checked_recovery(root, state)
            assert_rolled_back(inputs, metadata, original_hook, original_old)
        return {'ok':True, 'changed':False, 'configured':active, 'live_changed':False, **verification}
    if action == 'enable':
        verification = verify_release(root, expected_manifest_sha256)
        if state and state['status']=='pending':
            raise ValueError('pending session mutation; rollback required before enable')
        if state and state['status']=='active':
            if inputs['hook_bytes']!=HOOK or inputs['old_bytes']!=OLD_HIDDEN or state['manifest_sha256']!=expected_manifest_sha256:
                raise ValueError('active session differs from requested release')
            metadata, _, _ = checked_recovery(root, state)
            if (stat.S_IMODE(inputs['hook'].stat().st_mode)!=(metadata['hook_mode'] if metadata['hook_existed'] else 0o755) or
                    stat.S_IMODE(inputs['old'].stat().st_mode)!=metadata['old_mode']):
                raise ValueError('active session modes differ from backup')
            return {'ok':True, 'changed':False, 'configured':True, 'live_changed':False, **verification}
        if inputs['hook_bytes']==HOOK:
            raise ValueError('managed hook exists without active state')
        if inputs['old_bytes']!=OLD:
            raise ValueError('old XDG camera must be enabled before first enable')
        metadata = backup_inputs(root, inputs, expected_manifest_sha256)
        hook_mode = metadata['hook_mode'] if metadata['hook_existed'] else 0o755
        record = {'status':'pending','backup_id':metadata['backup_id'],
                  'manifest_sha256':expected_manifest_sha256,
                  'backup_metadata_sha256':metadata['metadata_sha256']}
        write_known(state_path, (json.dumps(record,sort_keys=True)+'\n').encode(), 0o600)
        try:
            write_known(inputs['old'], OLD_HIDDEN, metadata['old_mode'])
            write_known(inputs['hook'], HOOK, hook_mode)
            record['status']='active'
            write_known(state_path, (json.dumps(record,sort_keys=True)+'\n').encode(), 0o600)
        except Exception:
            write_known(inputs['hook'], inputs['hook_bytes'] if inputs['hook_bytes'] is not None else SHIM, hook_mode)
            write_known(inputs['old'], inputs['old_bytes'], metadata['old_mode'])
            record['status']='rolled_back'
            write_known(state_path, (json.dumps(record,sort_keys=True)+'\n').encode(), 0o600)
            raise
        return {'ok':True, 'changed':True, 'configured':True, 'live_changed':False,
                'backup_id':metadata['backup_id'], **verification}
    if action == 'rollback':
        if not state:
            if inputs['hook_bytes']==HOOK or inputs['old_bytes']==OLD_HIDDEN:
                raise ValueError('managed startup files exist without recoverable session state')
            return {'ok':True, 'changed':False, 'configured':False, 'live_changed':False}
        metadata, original_hook, original_old = checked_recovery(root, state)
        if state['status']=='rolled_back':
            assert_rolled_back(inputs, metadata, original_hook, original_old)
            return {'ok':True, 'changed':False, 'configured':False, 'live_changed':False}
        if inputs['hook_bytes'] not in (HOOK, original_hook, SHIM) or inputs['old_bytes'] not in (OLD_HIDDEN,original_old):
            raise ValueError('session drifted; refusing rollback overwrite')
        write_known(inputs['hook'], original_hook if original_hook is not None else SHIM,
                    metadata['hook_mode'] if original_hook is not None else 0o755)
        write_known(inputs['old'], original_old, metadata['old_mode'])
        state['status']='rolled_back'
        write_known(state_path, (json.dumps(state,sort_keys=True)+'\n').encode(), 0o600)
        return {'ok':True, 'changed':True, 'configured':False, 'live_changed':False,
                'backup_id':state['backup_id']}
    raise ValueError('unknown action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('probe','enable','verify','rollback'))
    parser.add_argument('--expected-manifest-sha256')
    args = parser.parse_args(argv)
    try:
        account = pwd.getpwuid(os.geteuid())
        if os.geteuid()==0 or account.pw_name!='junshu' or str(Path.home())!='/home/junshu':
            raise ValueError('session setup must run as junshu, never root or another home')
        result = configure(args.action, expected_manifest_sha256=args.expected_manifest_sha256)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (OSError,ValueError,UnicodeError,KeyError,TypeError,json.JSONDecodeError) as error:
        print(json.dumps({'ok':False,'changed':False,'error':str(error)},sort_keys=True))
        return 1


if __name__=='__main__':
    sys.exit(main())
