"""Retained-fixture packaging/install tests. Never sudo, delete, GPIO or shutdown."""
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parents[2]
QA = ROOT/'qa/r4-power-20261025/install-fixtures'
QA.mkdir(parents=True, exist_ok=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load('power_install', ROOT/'pi-r4-power/install_power_service.py')
check = load('power_check', ROOT/'pi-r4-power/check_package.py')
builder = load('power_build', ROOT/'tools/build_pi_power_release.py')
launcher = load('power_launcher', ROOT/'tools/create_pi_power_desktop_launcher.py')
remote = load('power_remote', ROOT/'tools/pi_power_remote.py')


def fixture():
    return Path(tempfile.mkdtemp(prefix='case-', dir=QA))


def source_fixture(parent):
    source = parent/'source'
    (source/'camera_r4').mkdir(parents=True)
    contents = {name: ('fixture '+name+'\n').encode() for name in check.RELEASE_FILES}
    for name,data in contents.items():
        (source/name).write_bytes(data)
    manifest = ''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n'
                       for name,data in sorted(contents.items())).encode()
    (source/'MANIFEST.sha256').write_bytes(manifest)
    return source, hashlib.sha256(manifest).hexdigest()


class InstallTests(unittest.TestCase):
    def test_allowlists_identical_and_independent_version(self):
        self.assertEqual(set(check.RELEASE_FILES),set(install.RELEASE_FILES))
        self.assertEqual(check.VERSION,'0.3.0-pwr.1')
        self.assertIn('camera_r4/power_bridge.py', check.RELEASE_FILES)
        self.assertIn('inspect_power_gpio.py',check.RELEASE_FILES)

    def test_snapshot_matches_manifest(self):
        source,digest = source_fixture(fixture())
        data = install.snapshot_release(source,digest)
        self.assertEqual(len(data),len(check.RELEASE_FILES)+1)

    def test_rejects_changed_source_and_untrusted_manifest(self):
        source,digest = source_fixture(fixture())
        with self.assertRaises(ValueError): install.snapshot_release(source,'0'*64)
        (source/'camera_r4/power_gpio.py').write_text('changed')
        with self.assertRaises(ValueError): install.snapshot_release(source,digest)

    def test_rejects_extra_file_and_symlink(self):
        parent=fixture(); source,digest=source_fixture(parent)
        (source/'extra').write_text('retained')
        with self.assertRaises(ValueError): install.snapshot_release(source,digest)
        source2,digest2=source_fixture(parent/'another')
        (source2/'linked').symlink_to(parent/'source')
        with self.assertRaises(ValueError): install.snapshot_release(source2,digest2)

    def test_exclusive_protected_copy_and_idempotent(self):
        parent=fixture(); source,digest=source_fixture(parent)
        data=install.snapshot_release(source,digest); target=parent/'protected'
        self.assertEqual(install.install_snapshot(data,target,owner=os.getuid(),group=os.getgid()),'installed')
        self.assertEqual(install.install_snapshot(data,target,owner=os.getuid(),group=os.getgid()),'already_installed')
        self.assertEqual((target/'power_service.py').stat().st_uid,os.getuid())
        self.assertEqual((target/'power_service.py').stat().st_mode & 0o777,0o644)
        self.assertEqual((target/'start-camera.sh').stat().st_mode & 0o777,0o755)
        # Source mutation after snapshot cannot influence protected bytes.
        (source/'power_service.py').write_text('new user bytes')
        self.assertNotEqual((source/'power_service.py').read_bytes(),(target/'power_service.py').read_bytes())

    def test_refuses_protected_unknown_content_and_insecure_mode(self):
        parent=fixture(); source,digest=source_fixture(parent); data=install.snapshot_release(source,digest)
        target=parent/'protected'; target.mkdir(); (target/'unknown').write_text('retained')
        with self.assertRaises(ValueError): install.install_snapshot(data,target,owner=os.getuid(),group=os.getgid())
        self.assertEqual((target/'unknown').read_text(),'retained')
        target2=parent/'protected-writable'; target2.mkdir(); target2.chmod(0o777)
        with self.assertRaises(ValueError): install.install_snapshot(data,target2,owner=os.getuid(),group=os.getgid())

    def test_refuses_symlink_install_destination(self):
        parent=fixture(); source,digest=source_fixture(parent); data=install.snapshot_release(source,digest)
        target=parent/'link'; target.symlink_to(source,target_is_directory=True)
        with self.assertRaises(ValueError): install.install_snapshot(data,target,owner=os.getuid(),group=os.getgid())

    def test_unit_is_fixed_trusted_root_isolated_runtime(self):
        unit=install.unit_text()
        self.assertIn('ExecStart=/usr/bin/python3 -I -B /opt/junshu-camera-power-0.3.0-pwr.1/power_service.py --user junshu',unit)
        self.assertIn('User=root',unit)
        self.assertIn('Type=notify\nNotifyAccess=main\nTimeoutStartSec=60\n',unit)
        self.assertNotIn('ExecStart=/home',unit)
        for forbidden in ('sudoers','polkit','dtoverlay','gpio-poweroff','gpio-shutdown'):
            self.assertNotIn(forbidden,unit)

    def test_unknown_unit_refused_without_overwrite(self):
        parent=fixture(); unit=parent/'junshu-camera-power.service'; unit.write_text('unknown retained')
        with self.assertRaises(ValueError): install.require_known_unit(unit,os.getuid())
        self.assertEqual(unit.read_text(),'unknown retained')

    def test_activation_refuses_without_wiring_confirmation(self):
        with self.assertRaises(ValueError): install.activate('/no-source','0'*64,False)

    def test_activation_unknown_unit_refused_before_copy(self):
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install.pwd,'getpwnam',return_value=types.SimpleNamespace(pw_dir='/home/junshu')),mock.patch.object(install,'require_known_unit',side_effect=ValueError('unknown')),mock.patch.object(install,'install_snapshot') as copied:
            with self.assertRaises(ValueError): install.activate('/source','0'*64,True)
            copied.assert_not_called()

    def test_activation_boot_enables_and_starts_only_fixed_service(self):
        calls=[]
        def run(command,**kwargs): calls.append(command)
        account=types.SimpleNamespace(pw_dir='/home/junshu')
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install.pwd,'getpwnam',return_value=account),mock.patch.object(install,'require_known_unit',side_effect=[False, True, True]),mock.patch.object(install,'require_effective_unit',return_value={}) as effective,mock.patch.object(install,'enablement_scope',return_value=[]),mock.patch.object(install,'snapshot_release',return_value={'MANIFEST.sha256':b'fixture'}),mock.patch.object(install,'ensure_state',return_value=999),mock.patch.object(install,'install_snapshot',return_value='installed'),mock.patch.object(install,'write_exclusive') as write,mock.patch.object(install.os,'close') as close:
            result=install.activate('/verified','a'*64,True,runner=run)
            close.assert_called_once_with(999)
            self.assertEqual(write.call_args[0][0],install.UNIT)
        self.assertEqual([call[1:] for call in calls],[['daemon-reload'],['enable',install.SERVICE],['start',install.SERVICE],['is-active','--quiet',install.SERVICE],['is-enabled','--quiet',install.SERVICE]])
        self.assertTrue(result['boot_enabled'])
        self.assertEqual(effective.call_count,2)
        self.assertFalse(any('poweroff' in call for call in calls))

    def test_system_unit_paths_fail_closed_and_actual_dependency_dirs_refused(self):
        parent=fixture(); unit=parent/install.SERVICE
        (parent/(install.SERVICE+'.upholds')).mkdir()
        output=types.SimpleNamespace(stdout=str(parent)+'\n')
        with mock.patch.object(install,'UNIT',unit),mock.patch.object(install,'system_unit_paths',return_value=(parent,)):
            with self.assertRaisesRegex(ValueError,'dependency directory'):
                install.require_known_unit(owner=os.getuid(),runner=mock.Mock(return_value=output))
        for raw in ('', '/etc/a\\x20b /usr/lib/systemd/system\n', '/etc/a\t/usr/lib\n', '/etc/a  /usr/lib\n', 'relative/path\n'):
            with self.assertRaises(ValueError):
                install.system_unit_paths(runner=mock.Mock(return_value=types.SimpleNamespace(stdout=raw)))
        valid=install.system_unit_paths(runner=mock.Mock(return_value=types.SimpleNamespace(stdout='/etc/systemd/system /nonexistent/compiled/path\n')))
        self.assertEqual(valid[1],Path('/nonexistent/compiled/path'))

    def test_effective_unit_rejects_inherited_dropins_wrong_fragment_and_reload(self):
        def response(**updates):
            values={'FragmentPath':str(install.UNIT),'DropInPaths':'','LoadState':'loaded','NeedDaemonReload':'no'}
            values.update(updates)
            return types.SimpleNamespace(stdout=''.join(key+'='+value+'\n' for key,value in values.items()))
        self.assertEqual(install.require_effective_unit(runner=mock.Mock(return_value=response()))['DropInPaths'],'')
        for updates in ({'DropInPaths':'/etc/systemd/system/service.d/90-home.conf'},
                        {'DropInPaths':'/etc/systemd/system.control/junshu-camera-power.service.d/50.conf'},
                        {'FragmentPath':'/home/junshu/other.service'},
                        {'LoadState':'not-found'},{'NeedDaemonReload':'yes'}):
            with self.assertRaises(ValueError):
                install.require_effective_unit(runner=mock.Mock(return_value=response(**updates)))

    def test_notify_start_wait_budget_and_failure_propagate(self):
        calls=[]
        def delayed_failure(command,**kwargs):
            calls.append(command)
            if command[1]=='start':
                self.assertGreaterEqual(kwargs['timeout'],75)
                # A failed/timed-out READY wait must propagate, never claim active.
                raise subprocess.TimeoutExpired(command,kwargs['timeout'])
        account=types.SimpleNamespace(pw_dir='/home/junshu')
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install.pwd,'getpwnam',return_value=account),mock.patch.object(install,'require_known_unit',side_effect=[False,True,True]),mock.patch.object(install,'require_effective_unit',return_value={}),mock.patch.object(install,'enablement_scope',return_value=[]),mock.patch.object(install,'snapshot_release',return_value={'MANIFEST.sha256':b'fixture'}),mock.patch.object(install,'ensure_state',return_value=999),mock.patch.object(install,'install_snapshot',return_value='installed'),mock.patch.object(install,'write_exclusive'),mock.patch.object(install.os,'close') as close:
            with self.assertRaises(subprocess.TimeoutExpired):
                install.activate('/verified','a'*64,True,runner=delayed_failure)
            close.assert_called_once_with(999)
        self.assertFalse(any('is-active' in call or 'is-enabled' in call for call in calls))

    def test_failed_gpio_initialization_start_propagates_without_status_claim(self):
        calls=[]
        def failed_start(command,**kwargs):
            calls.append(command)
            if command[1]=='start':
                raise subprocess.CalledProcessError(1,command)
        account=types.SimpleNamespace(pw_dir='/home/junshu')
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install.pwd,'getpwnam',return_value=account),mock.patch.object(install,'require_known_unit',side_effect=[False,True,True]),mock.patch.object(install,'require_effective_unit',return_value={}),mock.patch.object(install,'enablement_scope',return_value=[]),mock.patch.object(install,'snapshot_release',return_value={'MANIFEST.sha256':b'fixture'}),mock.patch.object(install,'ensure_state',return_value=999),mock.patch.object(install,'install_snapshot',return_value='installed'),mock.patch.object(install,'write_exclusive'),mock.patch.object(install.os,'close'):
            with self.assertRaises(subprocess.CalledProcessError):
                install.activate('/verified','a'*64,True,runner=failed_start)
        self.assertFalse(any('is-active' in call or 'is-enabled' in call for call in calls))

    def test_effective_dropin_refusal_precedes_enable_and_start(self):
        calls=[]
        account=types.SimpleNamespace(pw_dir='/home/junshu')
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install.pwd,'getpwnam',return_value=account),mock.patch.object(install,'require_known_unit',side_effect=[False,True]),mock.patch.object(install,'require_effective_unit',side_effect=ValueError('inherited dropin')),mock.patch.object(install,'enablement_scope',return_value=[]),mock.patch.object(install,'snapshot_release',return_value={'MANIFEST.sha256':b'fixture'}),mock.patch.object(install,'ensure_state',return_value=999),mock.patch.object(install,'install_snapshot',return_value='installed'),mock.patch.object(install,'write_exclusive'),mock.patch.object(install.os,'close'):
            with self.assertRaises(ValueError):
                install.activate('/verified','a'*64,True,runner=lambda command,**kw:calls.append(command))
        self.assertEqual(calls,[['/usr/bin/systemctl','daemon-reload']])

    def test_remote_failed_start_has_no_activation_success_claim(self):
        failure=subprocess.CalledProcessError(1,['ssh','start'])
        with mock.patch.object(remote,'checked_archive',return_value=(None,None,'a'*64,None)),mock.patch.object(remote,'ssh',side_effect=[None,failure]),mock.patch('builtins.input',return_value='activate'),mock.patch('sys.stdout',new=io.StringIO()) as output:
            with self.assertRaises(subprocess.CalledProcessError): remote.activate('junshu@camera-test.local')
        self.assertNotIn('服务激活步骤完成',output.getvalue())

    def test_user_state_creation_retained_and_busy_lock_refused(self):
        import fcntl
        parent=fixture(); home=parent/'home'; home.mkdir()
        account=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
        with mock.patch.object(install,'HOME',home):
            descriptor=install._ensure_state_as_user(account)
            self.assertTrue((home/'.local/state/junshu-camera/instance.lock').is_file())
            # A second independent descriptor must not steal the active lease.
            with self.assertRaises(ValueError): install._ensure_state_as_user(account)
            os.close(descriptor)
            descriptor2=install._ensure_state_as_user(account)
            os.close(descriptor2)
        self.assertEqual((home/'.local/state/junshu-camera/instance.lock').read_bytes(),b'')

    def test_enablement_scope_reports_only_expected_link_and_refuses_alias(self):
        parent=fixture(); unit=parent/install.SERVICE; unit.write_text(install.unit_text())
        wants=parent/'multi-user.target.wants'; wants.mkdir()
        link=wants/install.SERVICE; link.symlink_to('../'+install.SERVICE)
        with mock.patch.object(install,'UNIT',unit):
            scope=install.enablement_scope()
            self.assertEqual([item['path'] for item in scope],[str(link)])
            self.assertEqual(scope[0]['bytes'],len('../'+install.SERVICE))
            (parent/'alias.service').symlink_to(install.SERVICE)
            with self.assertRaises(ValueError): install.enablement_scope()
        self.assertTrue(link.is_symlink())

    def test_rollback_exact_stop_disable_and_preserves_code(self):
        calls=[]
        def run(command,**kwargs): calls.append(command)
        scope=[{'path':'/etc/systemd/system/multi-user.target.wants/junshu-camera-power.service','target':'../junshu-camera-power.service','bytes':30}]
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install,'require_known_unit',return_value=True),mock.patch.object(install,'require_effective_unit',return_value={}),mock.patch.object(install,'enablement_scope',return_value=scope),mock.patch('sys.stdout',new=io.StringIO()) as output:
            result=install.rollback(runner=run,confirm=lambda prompt:'yes')
            self.assertIn(scope[0]['path'],output.getvalue())
        self.assertEqual(calls,[['/usr/bin/systemctl','stop',install.SERVICE],['/usr/bin/systemctl','disable',install.SERVICE]])
        self.assertEqual(result['status'],'stopped_disabled')

    def test_rollback_confirmation_cannot_approve_changed_scope(self):
        run=mock.Mock(); original=[{'path':'known-link','bytes':30}]
        current=[original]
        def confirm(prompt):
            current[0]=[{'path':'new-link','bytes':40}]
            return 'yes'
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install,'require_known_unit',return_value=True),mock.patch.object(install,'enablement_scope',side_effect=lambda:current[0]),mock.patch('sys.stdout',new=io.StringIO()):
            with self.assertRaisesRegex(ValueError,'scope changed'):
                install.rollback(runner=run,confirm=confirm)
        run.assert_not_called()

    def test_rollback_scope_changed_during_stop_never_disables(self):
        calls=[]; original=[{'path':'known-link','bytes':30}]; current=[original]
        def run(command,**kwargs):
            calls.append(command)
            current[0]=[]
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install,'require_known_unit',return_value=True),mock.patch.object(install,'require_effective_unit',return_value={}),mock.patch.object(install,'enablement_scope',side_effect=lambda:current[0]),mock.patch('sys.stdout',new=io.StringIO()):
            with self.assertRaisesRegex(ValueError,'scope changed'):
                install.rollback(runner=run,confirm=lambda prompt:'yes')
        self.assertEqual(calls,[['/usr/bin/systemctl','stop',install.SERVICE]])

    def test_rollback_cancellation_does_not_stop_or_disable(self):
        run=mock.Mock()
        with mock.patch.object(install.sys,'platform','linux'),mock.patch.object(install.os,'geteuid',return_value=0),mock.patch.object(install,'require_known_unit',return_value=True),mock.patch.object(install,'enablement_scope',return_value=[{'path':'known-link','bytes':30}]),mock.patch('sys.stdout',new=io.StringIO()):
            with self.assertRaises(ValueError): install.rollback(runner=run,confirm=lambda prompt:'no')
        run.assert_not_called()

    def test_build_deterministic_and_refuses_existing_archive(self):
        parent=fixture(); source,digest=source_fixture(parent)
        first=builder.build(source,parent/'first'); second=builder.build(source,parent/'second')
        self.assertEqual(first.read_bytes(),second.read_bytes())
        self.assertEqual(check.verify_archive(first),len(check.RELEASE_FILES))
        with self.assertRaises(FileExistsError): builder.build(source,parent/'first')

    def test_checker_refuses_directory_extra_and_archive_traversal(self):
        parent=fixture(); source,digest=source_fixture(parent)
        self.assertEqual(check.verify_directory(source),len(check.RELEASE_FILES))
        (source/'unexpected').write_text('retained')
        with self.assertRaises(ValueError): check.verify_directory(source)
        bad=parent/'unsafe.tar.gz'
        with tarfile.open(bad,'w:gz') as archive:
            member=tarfile.TarInfo(check.PREFIX+'/../outside'); member.size=1
            archive.addfile(member,io.BytesIO(b'x'))
        with self.assertRaises(ValueError): check.verify_archive(bad)

    def test_gio_launcher_and_scoped_autostart_preserved_rename(self):
        parent=fixture(); directory=parent/'autostart'; directory.mkdir()
        old=parent/'original-l1.desktop'; old.write_text('L1 retained')
        launcher.autostart('enable',directory)
        self.assertTrue((directory/launcher.AUTOSTART_NAME).read_text().startswith('#!/usr/bin/gio launch\n'))
        result=launcher.autostart('disable',directory)
        self.assertFalse((directory/launcher.AUTOSTART_NAME).exists())
        self.assertEqual(Path(result['retained']).read_text(),launcher.entry_text())
        self.assertEqual(old.read_text(),'L1 retained')

    def test_unknown_autostart_retained(self):
        directory=fixture(); path=directory/launcher.AUTOSTART_NAME; path.write_text('other program')
        with self.assertRaises(ValueError): launcher.autostart('disable',directory)
        self.assertEqual(path.read_text(),'other program')

    def test_transport_strict_ssh_and_no_credentials(self):
        self.assertIn('StrictHostKeyChecking=yes',remote.SSH_FLAGS)
        with mock.patch('builtins.input',return_value='junshu@host;touch x'):
            with self.assertRaises(ValueError): remote.target_input()
        run=mock.Mock(return_value=types.SimpleNamespace(stdout=''))
        with mock.patch.object(remote.subprocess,'run',run): remote.ssh('junshu@camera-test.local','true')
        self.assertIn('StrictHostKeyChecking=yes',run.call_args[0][0])

    def test_import_cancel_requires_no_release_or_connection(self):
        with mock.patch.object(remote,'checked_archive') as archive,mock.patch.object(remote,'ssh') as ssh,mock.patch('builtins.input',return_value='no'),mock.patch('sys.stdout',new=io.StringIO()) as output:
            remote.import_release('junshu@camera-test.local')
            archive.assert_not_called(); ssh.assert_not_called()
            self.assertIn('已取消',output.getvalue())

    def test_import_stream_checker_is_valid_and_pins_mac_archive_digest(self):
        parent=fixture(); source,digest=source_fixture(parent)
        archive=builder.build(source,parent/'built')
        checker=(ROOT/'pi-r4-power/check_package.py').read_text().rsplit("\nif __name__ == '__main__':",1)[0]
        with mock.patch.object(remote,'checked_archive',return_value=(archive,archive.with_name(archive.name+'.sha256'),digest,checker)),mock.patch.object(remote,'ssh',side_effect=[types.SimpleNamespace(stdout='r4-power.12345678\n'),None,None]) as ssh,mock.patch.object(remote.subprocess,'run'),mock.patch('builtins.input',return_value='yes'),mock.patch('sys.stdout',new=io.StringIO()):
            remote.import_release('junshu@camera-test.local')
        payload=ssh.call_args_list[1].kwargs['data']
        ast.parse(payload)
        self.assertIn('sys.argv[2]',payload)
        self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(),ssh.call_args_list[1].args[1])
        self.assertNotIn('extractall',payload)

    def test_remote_activation_uses_trusted_inline_root_helper(self):
        with mock.patch.object(remote,'checked_archive',return_value=(None,None,'a'*64,None)),mock.patch.object(remote,'ssh') as ssh,mock.patch('builtins.input',return_value='activate'):
            remote.activate('junshu@camera-test.local')
        root_command=ssh.call_args_list[-1][0][1]
        self.assertIn('sudo /usr/bin/python3 -I -B -c',root_command)
        self.assertNotIn('sudo /usr/bin/python3 -I -B /home',root_command)
        self.assertIn('--wiring-confirmed',root_command)


if __name__=='__main__': unittest.main()
