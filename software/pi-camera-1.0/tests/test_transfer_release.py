"""Real retained local release fixtures; no SSH/device/system configuration operations."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT/'qa/r4-integrated-20261026/delivery/packaging-fixtures'
PREFIX = 'junshu-camera-pi-0.4.0'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransferReleaseTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True, exist_ok=True)
        self.case = Path(tempfile.mkdtemp(prefix='case-', dir=QA))

    def components(self):
        for name in ('build_pi_next_release.py','pi_next_remote.py','create_pi_next_launcher.py'):
            self.assertTrue((ROOT/'tools'/name).is_file(), 'Share release component missing: '+name)
        return (load('share_build',ROOT/'tools/build_pi_next_release.py'),
                load('share_check',ROOT/'pi-r4-next/check_package.py'),
                load('share_remote',ROOT/'tools/pi_next_remote.py'),
                load('share_launcher',ROOT/'tools/create_pi_next_launcher.py'))

    def release(self):
        builder,check,remote,launcher = self.components()
        source=self.case/'source'; source.mkdir()
        for name in check.RELEASE_FILES:
            path=source/name; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((ROOT/'pi-r4-next/check_package.py').read_bytes() if name=='check_package.py' else b'# fixture\n')
        # Privileged and personal files must never enter the allowlisted archive.
        (source/'install_power_service.py').write_bytes(b'never run')
        (source/'private.JPG').write_bytes(b'private photo')
        archive=builder.build(source,self.case/'built')
        return builder,check,remote,launcher,archive

    def test_deterministic_share_archive_excludes_privileged_and_personal_payload(self):
        builder,check,_,_,archive=self.release()
        second=builder.build(self.case/'source',self.case/'second')
        self.assertEqual(archive.name,PREFIX+'.tar.gz')
        self.assertEqual(archive.read_bytes(),second.read_bytes())
        with tarfile.open(archive) as bundle:
            names={m.name for m in bundle}
        for required in ('camera_r4/photo_share.py','camera_r4/share_page.py','camera_r4/share_ui.py','camera_r4/share_network.py','camera_r4/vendor/qrcodegen.py','camera_r4/vendor/LICENSE-qrcodegen.txt','camera_r4/vendor/README.md'):
            self.assertIn(PREFIX+'/'+required,names)
        for excluded in ('power_service.py','install_power_service.py','camera_r4/power_service.py','camera_r4/power_gpio.py','private.JPG'):
            self.assertNotIn(PREFIX+'/'+excluded,names)
        self.assertEqual(check.verify_archive(archive),len(check.RELEASE_FILES))
        with self.assertRaises(FileExistsError): builder.build(self.case/'source',self.case/'built')

    def test_trusted_receive_verifies_digest_before_creating_fresh_release_and_accepts_only_exact_idempotent_retry(self):
        _,check,_,_,archive=self.release()
        home=self.case/'home';home.mkdir();checksum=archive.with_name(archive.name+'.sha256')
        with self.assertRaises(ValueError): check.receive_archive(archive,checksum,'0'*64,home)
        self.assertFalse((home/PREFIX).exists())
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        release=check.receive_archive(archive,checksum,digest,home)
        self.assertEqual(release,home/PREFIX)
        self.assertEqual(check.verify_directory(release),len(check.RELEASE_FILES))
        before=(release/'run_camera.py').read_bytes()
        self.assertEqual(check.receive_archive(archive,checksum,digest,home),release)
        (release/'untrusted').write_bytes(b'unknown')
        with self.assertRaises(ValueError):check.receive_archive(archive,checksum,digest,home)
        self.assertEqual((release/'run_camera.py').read_bytes(),before)

    def test_tampered_manifest_archive_and_receiver_symlink_fail_before_extraction(self):
        _,check,_,_,archive=self.release();home=self.case/'home';home.mkdir()
        bad=self.case/'tampered.tar.gz'
        with tarfile.open(archive) as original,tarfile.open(bad,'w:gz') as changed:
            for member in original:
                data=original.extractfile(member).read()
                if member.name.endswith('/run_camera.py'):data+=b'changed'
                member.size=len(data);changed.addfile(member,io.BytesIO(data))
        digest=hashlib.sha256(bad.read_bytes()).hexdigest();side=bad.with_name(bad.name+'.sha256');side.write_text(digest+'  '+bad.name+'\n')
        with self.assertRaises(ValueError):check.receive_archive(bad,side,digest,home)
        self.assertFalse((home/PREFIX).exists())
        linked=self.case/'linked';linked.symlink_to(home,target_is_directory=True)
        with self.assertRaises(ValueError):check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),linked)

    def test_launcher_checks_package_before_writing_and_creates_only_executable_desktop_menu(self):
        _,check,_,launcher,archive=self.release();home=self.case/'home';home.mkdir();desktop=home/'Desktop';desktop.mkdir()
        release=check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),home)
        manifest_digest=hashlib.sha256((release/'MANIFEST.sha256').read_bytes()).hexdigest()
        with self.assertRaises(ValueError):launcher.install_launchers(release,home,desktop,'0'*64)
        self.assertEqual(list(desktop.iterdir()),[])
        result=launcher.install_launchers(release,home,desktop,manifest_digest)
        for key in ('desktop','menu'):
            path=Path(result[key]);self.assertEqual(path.stat().st_mode&0o777,0o755)
            text=path.read_text();self.assertTrue(text.startswith('#!/usr/bin/gio launch\n'))
            self.assertIn('Name=Junshu Camera R4 Share\n',text)
            self.assertIn('Icon=/usr/share/icons/AdwaitaLegacy/48x48/legacy/camera-photo.png\n',text)
            self.assertIn('Terminal=true\n',text)
        self.assertFalse((home/'.config/autostart').exists());self.assertFalse(result['started'])
        # Refuse corruption before changing an existing known launcher.
        before=Path(result['desktop']).read_bytes();(release/'README.md').write_bytes(b'corrupt')
        actual_run=subprocess.run
        def capture_checker(*args,**kwargs):
            return actual_run(*args,**dict(kwargs,capture_output=True,text=True))
        with patch.object(launcher.subprocess,'run',side_effect=capture_checker):
            with self.assertRaises(subprocess.CalledProcessError) as refused:launcher.install_launchers(release,home,desktop,manifest_digest)
        self.assertIn('包校验失败',refused.exception.stderr)
        self.assertEqual(Path(result['desktop']).read_bytes(),before)

    def test_unknown_launcher_preflight_prevents_partial_write(self):
        _,check,_,launcher,archive=self.release();home=self.case/'home';home.mkdir();desktop=home/'Desktop';desktop.mkdir()
        release=check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),home)
        conflict=home/'.local/share/applications/junshu-camera-r4-share.desktop';conflict.parent.mkdir(parents=True);conflict.write_text('retained unknown')
        with self.assertRaises(ValueError):launcher.install_launchers(release,home,desktop,hashlib.sha256((release/'MANIFEST.sha256').read_bytes()).hexdigest())
        self.assertEqual(list(desktop.iterdir()),[]);self.assertEqual(conflict.read_text(),'retained unknown')

    def test_ssh_targets_are_validated_at_transport_boundary(self):
        _,_,remote,_=self.components()
        for target in ('-oProxyCommand=evil','junshu@host;touch /tmp/never','junshu@host/path','junshu@$(id)','root@host','junshu@host:22','junshu@host\n'):
            with self.subTest(target=target),patch.object(remote.subprocess,'run') as run:
                with self.assertRaises(ValueError):remote.ssh(target,'true')
                run.assert_not_called()
        with patch.object(remote.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            remote.ssh('junshu@camera-test.local','true')
        command=run.call_args.args[0];self.assertIn('StrictHostKeyChecking=yes',command);self.assertIn('--',command)

    def test_import_cancellation_and_wrapper_version_work_without_device(self):
        _,_,remote,_=self.components()
        with patch('builtins.input',return_value='no'),patch.object(remote,'ssh') as ssh,patch.object(remote,'checked_archive') as checked,patch('sys.stdout',new_callable=io.StringIO) as output:
            remote.import_release('junshu@example.invalid');ssh.assert_not_called();checked.assert_not_called()
        self.assertIn('取消',output.getvalue())
        result=subprocess.run([sys.executable,'-B',str(ROOT/'pi-r4-next/run_camera.py'),'--version'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0);self.assertIn('Junshu Camera R4 Integrated 0.4.0',result.stdout)
        wrapper=ROOT/'导入R4整合版.command';self.assertTrue(wrapper.is_file())
        result=subprocess.run(['bash',str(wrapper)],input='junshu@example.invalid\nno\n\n',capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('取消',result.stdout)

if __name__=='__main__':unittest.main()
