#!/usr/bin/env python3
"""Explicit Pi-only activation/rollback. Readonly report is the default.

No importing user release code as root. Verified bytes are snapshotted before
exclusive creation under /opt. No deletion, overwriting unknown content, shell,
GPIO configuration, sudo policy, firmware or display edits.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

VERSION = '0.3.0-pwr.1'
PREFIX = 'junshu-camera-pi-' + VERSION
INSTALL = Path('/opt/junshu-camera-power-' + VERSION)
UNIT = Path('/etc/systemd/system/junshu-camera-power.service')
SERVICE = UNIT.name
ACCOUNT = 'junshu'
HOME = Path('/home/junshu')
RELEASE_FILES = (
    'README.md', 'TESTING.md', 'feature-status.md', 'check_package.py',
    'run_camera.py', 'start-camera.sh', 'camera_r4/artwork.py',
    'camera_r4/camera.py', 'camera_r4/capture.py', 'camera_r4/framing.py',
    'camera_r4/gui.py', 'camera_r4/imaging.py', 'camera_r4/platform_io.py',
    'camera_r4/state.py', 'camera_r4/storage.py',
    'camera_r4/l1_input.py', 'camera_r4/gpio_probe.py', 'inspect_gpio.py',
    'camera_r4/power_gpio.py', 'camera_r4/power_service.py',
    'camera_r4/power_client.py', 'camera_r4/power_indicator.py', 'camera_r4/power_bridge.py',
    'power_service.py', 'inspect_power_gpio.py', 'install_power_service.py',
)
MAX_BYTES = 4 * 1024 * 1024


def unit_text():
    return '''# Junshu Camera power 0.3.0-pwr.1; managed, preserve on rollback.
[Unit]
Description=Junshu Camera physical power button
After=local-fs.target
ConditionPathExists=/opt/junshu-camera-power-0.3.0-pwr.1/power_service.py

[Service]
Type=notify
NotifyAccess=main
TimeoutStartSec=60
User=root
Group=root
WorkingDirectory=/opt/junshu-camera-power-0.3.0-pwr.1
ExecStart=/usr/bin/python3 -I -B /opt/junshu-camera-power-0.3.0-pwr.1/power_service.py --user junshu
Environment=PYTHONNOUSERSITE=1
UMask=0077
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/junshu/.local/state/junshu-camera
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


def reject_links(path):
    for component in (Path(path), *Path(path).parents):
        if component.is_symlink():
            raise ValueError('symlink refused: ' + str(component))


def read_regular(path):
    reject_links(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('not a bounded regular file: ' + str(path))
        data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('oversized file: ' + str(path))
        return data


def snapshot_release(source, expected_manifest):
    """Check with our embedded allowlist, never execute the release checker."""
    source = Path(source)
    reject_links(source)
    if not re.fullmatch('[0-9a-f]{64}', expected_manifest or ''):
        raise ValueError('trusted expected manifest SHA256 required')
    allowed = set(RELEASE_FILES) | {'MANIFEST.sha256'}
    for entry in source.rglob('*'):
        name = entry.relative_to(source).as_posix()
        if entry.is_symlink() or (name != 'camera_r4' and name not in allowed):
            raise ValueError('unexpected release entry: ' + name)
    contents = {name: read_regular(source/name) for name in allowed}
    if hashlib.sha256(contents['MANIFEST.sha256']).hexdigest() != expected_manifest:
        raise ValueError('trusted manifest SHA256 mismatch')
    hashes = {}
    for line in contents['MANIFEST.sha256'].decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] not in RELEASE_FILES or match[2] in hashes:
            raise ValueError('invalid manifest')
        hashes[match[2]] = match[1]
    if set(hashes) != set(RELEASE_FILES):
        raise ValueError('manifest coverage mismatch')
    for name, digest in hashes.items():
        if hashlib.sha256(contents[name]).hexdigest() != digest:
            raise ValueError('release SHA256 mismatch: ' + name)
    return contents


def protected(path, *, uid=0):
    reject_links(path)
    info = Path(path).stat()
    if info.st_uid != uid or info.st_mode & 0o022:
        raise ValueError('untrusted owner/mode: ' + str(path))


def ensure_directory(path, uid, gid):
    reject_links(path)
    if path.exists():
        if not path.is_dir():
            raise ValueError('not a directory: ' + str(path))
        protected(path, uid=uid)
    else:
        path.mkdir(mode=0o755)
        os.chown(path, uid, gid)
    return path


def write_exclusive(path, data, mode, uid, gid):
    reject_links(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as target:
        os.fchown(target.fileno(), uid, gid)
        os.fchmod(target.fileno(), mode)
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def verify_installed(destination, contents, owner=0):
    reject_links(destination)
    protected(destination, uid=owner)
    for entry in destination.rglob('*'):
        relative = entry.relative_to(destination).as_posix()
        if entry.is_symlink() or relative not in set(contents) | {'camera_r4'}:
            raise ValueError('unknown protected installation entry: ' + relative)
        protected(entry, uid=owner)
    for name, data in contents.items():
        if read_regular(destination/name) != data:
            raise ValueError('protected installation differs: ' + name)


def install_snapshot(contents, destination=INSTALL, *, owner=0, group=0):
    destination = Path(destination)
    protected(destination.parent, uid=owner)
    if destination.exists() or destination.is_symlink():
        verify_installed(destination, contents, owner)
        return 'already_installed'
    ensure_directory(destination, owner, group)
    ensure_directory(destination/'camera_r4', owner, group)
    for name, data in sorted(contents.items()):
        write_exclusive(destination/name, data, 0o755 if name.endswith('.sh') else 0o644, owner, group)
    verify_installed(destination, contents, owner)
    # Sync parent entries before enabling a persistent boot unit.
    for directory in (destination/'camera_r4', destination, destination.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return 'installed'


def system_unit_paths(*, runner=subprocess.run):
    """Read the running manager's load paths, never guess compiled defaults."""
    output = runner(['/usr/bin/systemctl', 'show', '--property=UnitPath', '--value'],
                    check=True, capture_output=True, text=True, timeout=30,
                    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL':'C'})
    raw = output.stdout.removesuffix('\n')
    # systemd system paths have no whitespace/escape characters. Refuse an
    # unfamiliar representation instead of splitting an escaped path incorrectly.
    if not raw or any(ord(char) < 32 or char == '\\' for char in raw) or '  ' in raw:
        raise ValueError('unknown systemd UnitPath representation; retained')
    names = raw.split(' ')
    if any(not re.fullmatch(r'/[A-Za-z0-9_./:@+-]+', name) or
           '..' in Path(name).parts for name in names):
        raise ValueError('unsafe/ambiguous systemd UnitPath; retained')
    return tuple(dict.fromkeys(Path(name) for name in names))


def require_known_unit(unit=None, owner=0, *, runner=subprocess.run):
    unit = UNIT if unit is None else Path(unit)
    if unit == UNIT:
        # Inspect actual manager load paths before creating any root content.
        # Existing unknown units or dependency directories must never be hidden
        # by our /etc fragment or silently add other activation jobs.
        for parent in system_unit_paths(runner=runner):
            if not parent.exists():
                continue
            other = parent/SERVICE
            if other != unit and (other.exists() or other.is_symlink()):
                raise ValueError('unknown same-name systemd unit retained: '+str(other))
            dropin = parent/(SERVICE+'.d')
            if dropin.exists() or dropin.is_symlink():
                raise ValueError('unknown service drop-in retained: '+str(dropin))
            for suffix in ('.wants', '.requires', '.upholds'):
                dependency = parent/(SERVICE+suffix)
                if dependency.exists() or dependency.is_symlink():
                    raise ValueError('unknown service dependency directory retained: '+str(dependency))
    protected(unit.parent, uid=owner)
    reject_links(unit)
    if unit.exists():
        protected(unit, uid=owner)
        if read_regular(unit) != unit_text().encode('ascii'):
            raise ValueError('unknown same-name systemd unit; retained, not changed')
        return True
    return False


def require_effective_unit(*, runner=subprocess.run):
    """Reject inherited/global/.control drop-ins before any unit execution."""
    properties = ('FragmentPath', 'DropInPaths', 'LoadState', 'NeedDaemonReload')
    output = runner(['/usr/bin/systemctl', 'show',
                     *('--property='+name for name in properties), SERVICE],
                    check=True, capture_output=True, text=True, timeout=30,
                    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL':'C'})
    actual = {}
    for line in output.stdout.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in properties or key in actual:
            raise ValueError('unknown effective systemd unit report; retained')
        actual[key] = value
    expected = {'FragmentPath':str(UNIT), 'DropInPaths':'',
                'LoadState':'loaded', 'NeedDaemonReload':'no'}
    if actual != expected:
        raise ValueError('effective systemd unit differs; retained: '+json.dumps(actual, sort_keys=True))
    return actual


def _ensure_state_as_user(account):
    protected(HOME, uid=account.pw_uid)
    path = HOME
    for relative in ('.local', 'state', 'junshu-camera'):
        path = ensure_directory(path/relative, account.pw_uid, account.pw_gid)
    lock = path/'instance.lock'
    reject_links(lock)
    if not lock.exists():
        write_exclusive(lock, b'', 0o600, account.pw_uid, account.pw_gid)
    else:
        protected(lock, uid=account.pw_uid)
        read_regular(lock)
        if lock.stat().st_nlink != 1:
            raise ValueError('instance lock has multiple hard links; retained')
    descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise ValueError('camera instance is running; exit it and wait for saves before activation')
    return descriptor


def ensure_state(account):
    # User-state setup is done with the user's effective identity: a hostile
    # home-directory race must never redirect a privileged create/chown.
    if os.geteuid() != 0:
        raise ValueError('root installer required')
    previous_gid = os.getegid()
    try:
        os.setegid(account.pw_gid)
        os.seteuid(account.pw_uid)
        return _ensure_state_as_user(account)
    finally:
        os.seteuid(0)
        os.setegid(previous_gid)


def systemctl(*args, runner=subprocess.run):
    return runner(['/usr/bin/systemctl', *args], check=True,
                  timeout=90 if args and args[0] == 'start' else 30,
                  env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL':'C'})


def report(source=None, expected_manifest=None):
    result = {'version':VERSION, 'action':'report_only', 'install':str(INSTALL),
              'unit':str(UNIT), 'service':'not_queried', 'led':'disabled',
              'next':'C复核本次GPIO3报告，D确认全部断电后的NO接点接线，再由用户明确运行激活' }
    if source is not None and expected_manifest:
        contents = snapshot_release(source, expected_manifest)
        result['verified_release_files'] = len(contents)-1
    if UNIT.exists() or UNIT.is_symlink():
        require_known_unit()
        for verb in ('is-enabled', 'is-active'):
            output = subprocess.run(['/usr/bin/systemctl', verb, SERVICE], capture_output=True,
                                    text=True, timeout=10)
            result[verb] = output.stdout.strip()
    return result


def enablement_scope():
    """Bound systemctl disable to the one documented enablement symlink."""
    link = UNIT.parent/'multi-user.target.wants'/SERVICE
    scope = []
    for root in (UNIT.parent, Path('/run/systemd/system')):
        if not root.exists():
            continue
        for entry in root.rglob('*'):
            if not entry.is_symlink():
                continue
            target = os.readlink(entry)
            resolved = (entry.parent/target).resolve()
            if resolved == UNIT or entry.name == SERVICE:
                if entry != link or resolved != UNIT:
                    raise ValueError('unknown enablement/alias link retained: '+str(entry))
                scope.append({'path':str(entry), 'target':target, 'bytes':entry.lstat().st_size})
    if link.exists() and not link.is_symlink():
        raise ValueError('unknown regular enablement entry retained')
    return scope


def activate(source, expected_manifest, wiring_confirmed, *, runner=subprocess.run):
    if not wiring_confirmed:
        raise ValueError('activation requires C GPIO3 review and D NO wiring confirmation')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('activation is Pi Linux root only; use sudo in your terminal')
    account = pwd.getpwnam(ACCOUNT)
    if Path(account.pw_dir) != HOME:
        raise ValueError('unexpected junshu home; refuse')
    # Reject unknown units before creating any installation/state content.
    known = require_known_unit(runner=runner)
    enablement_scope()
    contents = snapshot_release(source, expected_manifest)
    lease = ensure_state(account)
    try:
        state = install_snapshot(contents)
        if not known:
            write_exclusive(UNIT, unit_text().encode('ascii'), 0o644, 0, 0)
        systemctl('daemon-reload', runner=runner)
        require_known_unit(runner=runner)
        require_effective_unit(runner=runner)
        # Boot enablement is explicit and confined to this service.
        systemctl('enable', SERVICE, runner=runner)
        require_known_unit(runner=runner)
        require_effective_unit(runner=runner)
        # Type=notify blocks until GPIO/socket initialization sends READY=1.
        systemctl('start', SERVICE, runner=runner)
        systemctl('is-active', '--quiet', SERVICE, runner=runner)
        systemctl('is-enabled', '--quiet', SERVICE, runner=runner)
    finally:
        os.close(lease)
    return {'status':state, 'boot_enabled':True, 'unit':SERVICE,
            'root_code':str(INSTALL), 'led':'disabled',
            'rollback':'sudo /usr/bin/python3 -I -B '+str(INSTALL/'install_power_service.py')+' rollback'}


def rollback(*, runner=subprocess.run, confirm=input):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('rollback is Pi Linux root only')
    if not require_known_unit(runner=runner):
        return {'status':'not_installed', 'files':'all retained'}
    scope = enablement_scope()
    if scope:
        print('本次将移除以下开机启用符号链接；容量按链接字节数计，代码和其他文件全部保留。', flush=True)
        print(json.dumps({'disable_removes_only_these_symlinks':scope,
                          'total_bytes':sum(item['bytes'] for item in scope),
                          'preserved':'all code, service unit, releases, photos, settings, logs and desktop entries'},
                         ensure_ascii=False, indent=2), flush=True)
        if confirm('确认上述具体链接清单，并停止/禁用本服务？输入 yes：') != 'yes':
            raise ValueError('已取消回退；服务状态和文件均未改变')
    # Approval binds this exact current list. A changed unit/link invalidates it.
    if not require_known_unit(runner=runner) or enablement_scope() != scope:
        raise ValueError('rollback scope changed after confirmation; retained, rerun to review')
    require_effective_unit(runner=runner)
    systemctl('stop', SERVICE, runner=runner)
    # Stop may take time; never let that widen the approved removal scope.
    if not require_known_unit(runner=runner) or enablement_scope() != scope:
        raise ValueError('rollback scope changed before disable; retained, service may be stopped')
    require_effective_unit(runner=runner)
    systemctl('disable', SERVICE, runner=runner)
    return {'status':'stopped_disabled', 'files':'all releases, unit, photos, settings and logs retained',
            'camera':'normal desktop exit first, then open original Junshu Camera R4 L1.2'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('report', 'activate', 'rollback'), default='report')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--expected-manifest-sha256')
    parser.add_argument('--wiring-confirmed', action='store_true',
                        help='C reviewed current GPIO3 report and D confirmed NO wiring while fully unpowered')
    args = parser.parse_args(argv)
    if args.action == 'activate':
        if not args.source or not args.expected_manifest_sha256:
            parser.error('activate requires --source and --expected-manifest-sha256')
        result = activate(args.source, args.expected_manifest_sha256, args.wiring_confirmed)
    elif args.action == 'rollback':
        result = rollback()
    else:
        result = report(args.source, args.expected_manifest_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit('Power service operation stopped; retain output for C: '+str(error))
