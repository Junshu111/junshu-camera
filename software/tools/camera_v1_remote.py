#!/usr/bin/env python3
"""Mac-side New Cam 1.0 probe, staged install, verify and independent rollback."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]
TOOLS = Path(__file__).absolute().parent
APP_PREFIX = 'junshu-camera-pi-1.0.0'
BOOT_PREFIX = 'junshu-camera-boot-1.0.0'
REPORTS = ROOT/'qa/newcam-1.0/device-reports'


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


network = _load(TOOLS/'pi_next_remote.py', 'v1_network')
payload = _load(TOOLS/'camera_v1_boot_payload.py', 'v1_payload')
checker = _load(ROOT/'pi-camera-1.0/check_package.py', 'v1_checker_remote')


class RemoteActionError(ValueError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _validate_stage(name, result):
    if isinstance(result, dict):
        if result.get('ok') is False or result.get('status') in ('blocked', 'error', 'failed', 'partial'):
            raise ValueError(name+' report indicates failure')
    if name == 'boot_probe' and (not isinstance(result, dict) or result.get('ok') is not True
                                 or result.get('status') not in ('ready', 'enabled')):
        raise ValueError('boot probe has no verified ready/enabled state')
    if name == 'session_probe' and (not isinstance(result, dict) or result.get('ok') is not True
                                    or not isinstance(result.get('configured'), bool)
                                    or result.get('pending') is not False):
        raise ValueError('session probe is pending or incomplete')
    if name == 'boot_verify' and (not isinstance(result, dict) or result.get('ok') is not True
                                  or result.get('status') != 'enabled'):
        raise ValueError('boot verify did not confirm enabled New Cam 1.0')
    if name == 'session_verify' and (not isinstance(result, dict) or result.get('ok') is not True
                                     or result.get('configured') is not True
                                     or result.get('pending') is True):
        raise ValueError('session verify did not confirm configured New Cam 1.0')
    if name == 'boot_enable' and (not isinstance(result, dict) or result.get('ok') is not True
                                  or result.get('status') != 'enabled'
                                  or not isinstance(result.get('changed'), bool)):
        raise ValueError('boot enable has no verified enabled/changed result')
    if name == 'session_enable' and (not isinstance(result, dict) or result.get('ok') is not True
                                     or result.get('configured') is not True
                                     or not isinstance(result.get('changed'), bool)):
        raise ValueError('session enable has no verified configured/changed result')
    if name == 'boot_rollback' and (not isinstance(result, dict) or result.get('ok') is not True
                                    or result.get('status') not in ('rolled_back', 'no_op')
                                    or not isinstance(result.get('changed'), bool)):
        raise ValueError('boot rollback has no verified restored result')
    if name == 'session_rollback' and (not isinstance(result, dict) or result.get('ok') is not True
                                       or result.get('configured') is not False
                                       or not isinstance(result.get('changed'), bool)):
        raise ValueError('session rollback has no verified restored result')


def _stage(report, name, action):
    result = None
    try:
        result = action()
        _validate_stage(name, result)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report['stages'][name] = {'status': 'failed', 'error': type(error).__name__+': '+str(error)[:500],
                                  'result': (result if isinstance(result, dict) else
                                             error.report if isinstance(error, RemoteActionError) else None)}
        return False
    report['stages'][name] = {'status': 'ok', 'result': result if isinstance(result, dict) else None}
    return True


def coordinate_install(ops):
    report = {'action': 'install', 'success': False, 'status': 'failed', 'stages': {}}
    stages = (('boot_probe', ops.boot_probe), ('session_probe', ops.session_probe),
              ('release', ops.release),
              ('transfer_app', ops.transfer_app), ('transfer_boot', ops.transfer_boot),
              ('launcher', ops.install_launcher), ('boot_enable', ops.boot_enable),
              ('session_enable', ops.session_enable))
    session_was_active = False
    boot_changed = False
    for name, action in stages:
        if not _stage(report, name, action):
            if name == 'session_enable':
                if session_was_active:
                    report['stages']['session_rollback'] = {'status':'skipped', 'reason':'session was active before this install'}
                else:
                    _stage(report, 'session_rollback', ops.session_rollback)
            if boot_changed:
                _stage(report, 'boot_rollback', ops.boot_rollback)
            elif name in ('boot_enable', 'session_enable'):
                report['stages']['boot_rollback'] = {'status':'skipped', 'reason':'boot was not changed by this install'}
            report['status'] = 'partial' if name in ('transfer_app','transfer_boot','launcher','boot_enable','session_enable') else 'failed'
            return report
        if name == 'session_probe':
            session_was_active = report['stages'][name]['result']['configured']
        if name == 'boot_enable':
            boot_changed = report['stages'][name]['result']['changed']
    report['success'] = True
    report['status'] = 'staged'
    return report


def coordinate_rollback(ops):
    report = {'action': 'rollback', 'success': False, 'status': 'partial', 'stages': {}}
    session_ok = _stage(report, 'session_rollback', ops.session_rollback)
    boot_ok = _stage(report, 'boot_rollback', ops.boot_rollback)
    if session_ok and boot_ok:
        report['success'] = True
        report['status'] = 'restored'
    return report


def _sidecar(archive):
    side = archive.with_name(archive.name+'.sha256')
    data = payload.regular_bytes(archive)
    line = payload.regular_bytes(side, 512).decode('ascii')
    if line != sha(data)+'  '+archive.name+'\n':
        raise ValueError('archive checksum sidecar mismatch: '+archive.name)
    return side, sha(data)


def checked_release(folder):
    folder = Path(folder)
    if folder.is_symlink():
        raise ValueError('symlink release directory')
    app = folder/(APP_PREFIX+'.tar.gz')
    boot = folder/(BOOT_PREFIX+'.tar.gz')
    app_side, app_sha = _sidecar(app)
    boot_side, boot_sha = _sidecar(boot)
    app_files = checker.read_archive(app)
    boot_files = payload.read_archive(boot, boot_sha)
    for name, data in app_files.items():
        if name == 'MANIFEST.sha256':
            continue
        if checker.regular_file(ROOT/'pi-camera-1.0', name).read_bytes() != data:
            raise ValueError('app source differs from release: '+name)
    for name, data in boot_files.items():
        if name == 'MANIFEST.sha256':
            continue
        if payload.regular_bytes(_safe_source(ROOT/'pi-boot-logo-1.0', name)) != data:
            raise ValueError('boot source differs from release: '+name)
    return {'app': (app, app_side, app_sha, sha(app_files['MANIFEST.sha256'])),
            'boot': (boot, boot_side, boot_sha, sha(boot_files['MANIFEST.sha256']))}


def _safe_source(root, relative):
    path = Path(root)
    if path.is_symlink():
        raise ValueError('symlink source root')
    for component in Path(relative).parts:
        if component in ('', '.', '..'):
            raise ValueError('unsafe source path')
        path = path/component
        if path.is_symlink():
            raise ValueError('symlink source input')
    return path


def _parse_and_save(action, result):
    report = network.parse_result(result.stdout)
    network.save_report('v1-'+action, report, report_dir=REPORTS)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (result.returncode or report.get('ok') is False
            or report.get('status') in ('blocked', 'error', 'failed', 'partial')):
        raise RemoteActionError(action+' failed; device report retained', report)
    return report


def _ssh_action(target, command, *, data=None, sudo=False, action):
    try:
        result = network.ssh(target, command, data=data, capture=True, terminal=sudo)
    except subprocess.CalledProcessError as error:
        if error.stdout:
            _parse_and_save(action, error)
        raise
    return _parse_and_save(action, result)


def _trusted_action_source(path, package):
    source = payload.regular_bytes(path).decode('utf-8')
    encoded = base64.b64encode(source.encode('utf-8')).decode('ascii')
    # The frozen 1.0 session helper creates private directories with mkdir's
    # default mode. Pin this SSH process's umask, including backup directories.
    setup = 'import base64,os;os.umask(0o077);' if path.name == 'session_setup.py' else 'import base64;'
    return (setup+'__file__="/home/junshu/'+package+'/'+path.name+'";'
            'exec(compile(base64.b64decode("'+encoded+'"),"<trusted-mac-v1>","exec"))')


class RemoteOps:
    def __init__(self, target, release_dir):
        self.target = network.validate_target(target)
        self.release_dir = Path(release_dir)
        self.releases = None

    def release(self):
        self.releases = checked_release(self.release_dir)
        return {'app_sha256': self.releases['app'][2], 'boot_sha256': self.releases['boot'][2]}

    def _system(self, action, manifest=None):
        source = _trusted_action_source(ROOT/'pi-boot-logo-1.0/boot_system.py', BOOT_PREFIX)
        command = "sudo /usr/bin/python3 -I -B -c '"+source+"' "+action+' --bundle /home/junshu/'+BOOT_PREFIX
        if manifest:
            if not re.fullmatch('[0-9a-f]{64}', manifest):
                raise ValueError('invalid boot manifest')
            command += ' --expected-manifest-sha256 '+manifest
        return _ssh_action(self.target, command, sudo=True, action='boot-'+action)

    def _session(self, action, manifest=None):
        source = _trusted_action_source(ROOT/'pi-camera-1.0/session_setup.py', APP_PREFIX)
        command = "/usr/bin/python3 -I -B -c '"+source+"' "+action
        if manifest:
            if not re.fullmatch('[0-9a-f]{64}', manifest):
                raise ValueError('invalid app manifest')
            command += ' --expected-manifest-sha256 '+manifest
        return _ssh_action(self.target, command, action='session-'+action)

    def boot_probe(self): return self._system('probe')
    def boot_enable(self): return self._system('enable', self.releases['boot'][3])
    def boot_rollback(self): return self._system('rollback')
    def session_enable(self): return self._session('enable', self.releases['app'][3])
    def session_rollback(self): return self._session('rollback')
    def boot_verify(self): return self._system('verify')
    def session_verify(self): return self._session('verify')
    def session_probe(self): return self._session('probe')

    def repair_session_permissions(self):
        if self.releases is None:
            self.release()
        core = payload.regular_bytes(ROOT/'pi-camera-1.0/session_setup.py').decode('utf-8')
        helper = payload.regular_bytes(TOOLS/'camera_v1_session_permissions.py').decode('utf-8')
        source = 'import json,os,pwd,sys,types\nfrom pathlib import Path\n'
        source += 'namespace={"__name__":"trusted_frozen_session"}\n'
        source += 'exec(compile('+repr(core)+',"<trusted-frozen-session>","exec"),namespace)\n'
        source += helper + '''
try:
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != 'junshu' or Path.home() != Path('/home/junshu'):
        raise ValueError('Run as junshu without sudo')
    result = repair(types.SimpleNamespace(**namespace), sys.argv[1])
except (OSError,ValueError,KeyError,TypeError) as error:
    result = {'ok':False, 'error':str(error)}
print(json.dumps(result,sort_keys=True))
raise SystemExit(0 if result.get('ok') else 1)
'''
        command = "/usr/bin/python3 -I -B - '"+self.releases['app'][3]+"'"
        return _ssh_action(self.target, command, data=source, action='session-permission-repair')

    def _transfer(self, item, prefix):
        if self.releases is None:
            raise ValueError('release must be checked before transfer')
        archive, sidecar, digest, manifest = self.releases[item]
        prepare = '''set -eu
[ "$(id -un)" = junshu ] && [ "$HOME" = /home/junshu ]
parent="$HOME/.local/state/junshu-camera/imports"
mkdir -p -- "$parent"
incoming=$(mktemp -d "$parent/newcam1-'''+item+'''.XXXXXXXX")
basename -- "$incoming"
'''
        name = network.ssh(self.target, 'sh -s', data=prepare, capture=True).stdout.strip()
        if not re.fullmatch(r'newcam1-'+item+r'\.[A-Za-z0-9]{8}', name):
            raise ValueError('unexpected receiver directory')
        relative = '.local/state/junshu-camera/imports/'+name
        subprocess.run(['scp', *network.SSH_FLAGS, '--', str(archive), str(sidecar),
                        self.target+':'+relative+'/'], check=True)
        if item == 'app':
            source = payload.regular_bytes(ROOT/'pi-camera-1.0/check_package.py').decode('utf-8')
            source = source.rsplit("\nif __name__ == '__main__':", 1)[0]
            source += '''
import os
if Path.home() != Path('/home/junshu') or os.geteuid() == 0:
    raise SystemExit('Use junshu without sudo')
incoming = Path.home()/'.local/state/junshu-camera/imports'/sys.argv[1]
release = receive_archive(incoming/(PREFIX+'.tar.gz'), incoming/(PREFIX+'.tar.gz.sha256'), sys.argv[2], Path.home())
print(json.dumps({'package': str(release), 'manifest_sha256': sys.argv[3], 'changed_system': False}))
'''
            # checker does not import json itself.
            source = 'import json\n'+source
            command = "/usr/bin/python3 -I -B - '"+name+"' '"+digest+"' '"+manifest+"'"
        else:
            source = payload.regular_bytes(TOOLS/'camera_v1_boot_payload.py').decode('utf-8')
            source += '''
import sys
if Path.home() != Path('/home/junshu') or os.geteuid() == 0:
    raise SystemExit('Use junshu without sudo')
incoming = Path.home()/'.local/state/junshu-camera/imports'/sys.argv[1]/(PREFIX+'.tar.gz')
print(json.dumps(receive(incoming, sys.argv[2], Path.home())))
'''
            command = "/usr/bin/python3 -I -B - '"+name+"' '"+digest+"'"
        return _ssh_action(self.target, command, data=source, action=item+'-import')

    def transfer_app(self): return self._transfer('app', APP_PREFIX)
    def transfer_boot(self): return self._transfer('boot', BOOT_PREFIX)

    def install_launcher(self):
        if self.releases is None:
            raise ValueError('release must be checked before launcher')
        checker_source = payload.regular_bytes(ROOT/'pi-camera-1.0/check_package.py').decode('utf-8')
        checker_source = checker_source.rsplit("\nif __name__ == '__main__':", 1)[0]
        launcher_source = payload.regular_bytes(TOOLS/'camera_v1_launcher.py').decode('utf-8')
        source = checker_source+'\n'+launcher_source+'''
print(json.dumps(install_launcher(Path.home(), sys.argv[1])))
'''
        source = 'import json\n'+source
        command = "/usr/bin/python3 -I -B - '"+self.releases['app'][3]+"'"
        return _ssh_action(self.target, command, data=source, action='launcher-install')


def coordinate_readonly(ops, action):
    report = {'action': action, 'success': False, 'status': 'partial', 'stages': {}}
    calls = (('boot_'+action, getattr(ops, 'boot_'+action)),
             ('session_'+action, getattr(ops, 'session_'+action)))
    results = [_stage(report, name, call) for name, call in calls]
    if all(results):
        report['success'] = True
        report['status'] = 'verified' if action == 'verify' else 'probed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('probe', 'install', 'verify', 'rollback', 'repair-install'))
    parser.add_argument('--release-dir', type=Path, default=ROOT/'dist-1.0')
    args = parser.parse_args(argv)
    if args.action in ('install', 'repair-install'):
        checked_release(args.release_dir)
    target = network.target_input()
    ops = RemoteOps(target, args.release_dir)
    if args.action == 'repair-install':
        print('先核对已导入的1.0包和失败目录，仅修复已知的空目录权限，再继续安装；不删除或重启。')
        report = {'action':'repair-install', 'success':False, 'status':'partial', 'stages':{}}
        if _stage(report, 'session_permission_repair', ops.repair_session_permissions):
            installation = coordinate_install(ops)
            report.update(success=installation['success'], status=installation['status'], installation=installation)
    elif args.action == 'install':
        print('将核查并暂存 New Cam 1.0；不会重启、停止当前相机或删除旧包。')
        report = coordinate_install(ops)
    elif args.action == 'rollback':
        print('尝试独立恢复会话与开机画面；保留两个 1.0 包与历史备份。')
        report = coordinate_rollback(ops)
    else:
        report = coordinate_readonly(ops, args.action)
    network.save_report('v1-'+args.action+'-summary', report, report_dir=REPORTS)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as error:
        raise SystemExit('New Cam 1.0 工具停止；文件与备份保留：'+str(error))
