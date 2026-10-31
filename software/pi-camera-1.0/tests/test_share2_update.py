"""Share2 update fixtures retain backups and never touch a device or remove output."""
import fcntl
import hashlib
import io
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
QA=ROOT/'qa/r4-integrated-20261026/delivery/update-fixtures'
PREFIX='junshu-camera-pi-0.4.0'
OLD_TEXT=b'''#!/usr/bin/gio launch
[Desktop Entry]
Type=Application
Name=Junshu Camera R4 Share
Comment=Junshu Camera 0.3.0-share.1 same-Wi-Fi original JPG transfer
Exec=/bin/sh /home/junshu/junshu-camera-pi-0.3.0-share.1/start-camera.sh
Icon=/usr/share/icons/AdwaitaLegacy/48x48/legacy/camera-photo.png
Terminal=true
Categories=Graphics;
StartupNotify=false
'''


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class Share2UpdateTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True,exist_ok=True)
        self.case=Path(tempfile.mkdtemp(prefix='case-',dir=QA))
        self.home=self.case/'home';self.home.mkdir()
        self.desktop=self.home/'Desktop';self.desktop.mkdir()

    def components(self):
        launcher=ROOT/'tools/create_pi_next_launcher.py'
        self.assertTrue(launcher.is_file(),'Share2 launcher updater missing')
        return (load('share2launch',launcher),load('share2check',ROOT/'pi-r4-next/check_package.py'),load('share2build',ROOT/'tools/build_pi_next_release.py'))

    def fixture(self):
        launcher,check,build=self.components()
        source=self.case/'source';source.mkdir()
        for name in check.RELEASE_FILES:
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((ROOT/'pi-r4-next/check_package.py').read_bytes() if name=='check_package.py' else b'# fixture\n')
        archive=build.build(source,self.case/'built')
        release=check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),self.home)
        return launcher,check,release,hashlib.sha256((release/'MANIFEST.sha256').read_bytes()).hexdigest()

    def old_launchers(self):
        desktop=self.desktop/'Junshu-Camera-R4-Share.desktop'
        menu=self.home/'.local/share/applications/junshu-camera-r4-share.desktop'
        for path,mode in ((desktop,0o751),(menu,0o644)):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(OLD_TEXT);path.chmod(mode)
        return desktop,menu

    def test_actual_upgrade_backs_up_old_bytes_modes_keeps_power_photos_and_same_icons(self):
        launcher,_,release,digest=self.fixture();desktop,menu=self.old_launchers()
        power=self.desktop/'Power.desktop';power.write_bytes(b'power retained')
        photo=self.home/'Pictures/JunshuCamera/photos/original.JPG';photo.parent.mkdir(parents=True);photo.write_bytes(b'original retained')
        old=self.home/'junshu-camera-pi-0.3.0-share.1';old.mkdir();(old/'retained').write_bytes(b'old retained')
        result=launcher.install_launchers(release,self.home,self.desktop,digest)
        for path in (desktop,menu):
            self.assertIn(b'0.4.0/start-camera.sh',path.read_bytes())
            self.assertEqual(path.stat().st_mode&0o777,0o755)
        backup=Path(result['backup'])
        self.assertEqual((backup/'desktop.desktop').read_bytes(),OLD_TEXT)
        self.assertEqual((backup/'desktop.desktop').stat().st_mode&0o777,0o751)
        self.assertEqual((backup/'menu.desktop').read_bytes(),OLD_TEXT)
        self.assertEqual((backup/'menu.desktop').stat().st_mode&0o777,0o644)
        self.assertEqual({p.name for p in self.desktop.iterdir()},{'Junshu-Camera-R4-Share.desktop','Power.desktop'})
        self.assertEqual(power.read_bytes(),b'power retained');self.assertEqual(photo.read_bytes(),b'original retained');self.assertEqual((old/'retained').read_bytes(),b'old retained')
        self.assertFalse(result['started'])
        second=launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertEqual(second['backup'],None)
        self.assertEqual(len(list(backup.parent.iterdir())),1)

    def test_unknown_or_symlink_menu_blocks_both_changes_and_backups(self):
        launcher,_,release,digest=self.fixture();desktop,menu=self.old_launchers()
        menu.write_bytes(b'unknown retained')
        with self.assertRaises(ValueError):launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertEqual(desktop.read_bytes(),OLD_TEXT)
        self.assertEqual(menu.read_bytes(),b'unknown retained')
        self.assertFalse((self.home/'.local/state/junshu-camera/launcher-backups').exists())
        retained=menu.with_name('retained-menu');menu.rename(retained);menu.symlink_to(retained)
        with self.assertRaises(ValueError):launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertEqual(desktop.read_bytes(),OLD_TEXT)

    def test_active_shared_lock_refuses_without_changing_launchers(self):
        launcher,_,release,digest=self.fixture();desktop,menu=self.old_launchers()
        state=self.home/'.local/state/junshu-camera';state.mkdir(parents=True)
        with (state/'instance.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'退出|running|busy'):launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertEqual(desktop.read_bytes(),OLD_TEXT);self.assertEqual(menu.read_bytes(),OLD_TEXT)
        self.assertFalse((state/'launcher-backups').exists())

    def test_tampered_package_or_manifest_never_replaces_old_launchers(self):
        launcher,_,release,digest=self.fixture();desktop,menu=self.old_launchers()
        with self.assertRaises(ValueError):launcher.install_launchers(release,self.home,self.desktop,'0'*64)
        (release/'README.md').write_bytes(b'tampered')
        actual_run=subprocess.run
        def capture_checker(*args,**kwargs):
            return actual_run(*args,**dict(kwargs,capture_output=True,text=True))
        with patch.object(launcher.subprocess,'run',side_effect=capture_checker):
            with self.assertRaises(subprocess.CalledProcessError) as refused:launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertIn('包校验失败',refused.exception.stderr)
        self.assertEqual(desktop.read_bytes(),OLD_TEXT);self.assertEqual(menu.read_bytes(),OLD_TEXT)

    def test_real_extracted_version_and_dependency_imports_need_no_hardware(self):
        launcher,check,build=self.components()
        archive=build.build(ROOT/'pi-r4-next',self.case/'built')
        release=check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),self.home)
        result=subprocess.run([sys.executable,'-B',str(release/'run_camera.py'),'--version'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('0.4.0',result.stdout)
        env=dict(os.environ,PYTHONPATH=str(release)+os.pathsep+os.environ.get('PYTHONPATH',''))
        result=subprocess.run([sys.executable,'-B','-c','from camera_r4 import gui, share_ui, share_network, photo_share; from camera_r4.vendor import qrcodegen'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_idempotent_retry_refuses_altered_permissions_and_valid_but_different_manifest(self):
        _,check,release,_=self.fixture()
        archive=self.case/'built'/(PREFIX+'.tar.gz');side=archive.with_name(archive.name+'.sha256')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        (release/'run_camera.py').chmod(0o755)
        with self.assertRaises(ValueError):check.receive_archive(archive,side,digest,self.home)
        (release/'run_camera.py').chmod(0o644)
        (release/'README.md').write_bytes(b'replaced but internally consistent')
        manifest=''.join(hashlib.sha256((release/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in sorted(check.RELEASE_FILES))
        (release/'MANIFEST.sha256').write_text(manifest)
        self.assertEqual(check.verify_directory(release),28)
        with self.assertRaises(ValueError):check.receive_archive(archive,side,digest,self.home)
        self.assertEqual((release/'README.md').read_bytes(),b'replaced but internally consistent')

    def test_same_icon_rollback_to_verified_share2_and_upgrade_from_share2(self):
        launcher,check,release,digest=self.fixture();desktop,menu=self.old_launchers()
        share2=load('retained_share2_build',ROOT/'tools/build_pi_transfer_share2_release.py')
        old_check=load('retained_share2_check',ROOT/'pi-r4-transfer-share2/check_package.py')
        archive=share2.build(ROOT/'pi-r4-transfer-share2',self.case/'old-built')
        old=old_check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),self.home)
        launcher.install_launchers(release,self.home,self.desktop,digest)
        old_digest=hashlib.sha256((old/'MANIFEST.sha256').read_bytes()).hexdigest()
        launcher.install_launchers(old,self.home,self.desktop,old_digest,version='0.3.0-share.2')
        self.assertEqual(desktop.read_bytes(),launcher.entry_text('0.3.0-share.2').encode())
        self.assertEqual(menu.read_bytes(),desktop.read_bytes())
        launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertEqual(desktop.read_bytes(),launcher.entry_text().encode())
        self.assertEqual({p.name for p in self.desktop.iterdir()},{'Junshu-Camera-R4-Share.desktop'})

    def test_directory_mode_tamper_is_refused_before_launcher_or_retry(self):
        launcher,check,release,digest=self.fixture();desktop,menu=self.old_launchers()
        (release/'camera_r4').chmod(0o777)
        archive=self.case/'built'/(PREFIX+'.tar.gz')
        with self.assertRaises(ValueError):check.receive_archive(archive,archive.with_name(archive.name+'.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest(),self.home)
        with self.assertRaises(ValueError):launcher.install_launchers(release,self.home,self.desktop,digest)
        self.assertEqual(desktop.read_bytes(),OLD_TEXT);self.assertEqual(menu.read_bytes(),OLD_TEXT)

    def test_both_mac_wrappers_reject_injection_and_cancel_before_device(self):
        for name in ('导入R4整合版.command','导入R4整合版.command'):
            path=ROOT/name;self.assertTrue(path.is_file(),name)
            result=subprocess.run(['bash',str(path)],input='junshu@example.invalid\nno\n\n',capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('取消',result.stdout)
            result=subprocess.run(['bash',str(path)],input='junshu@host;echo BAD\n',capture_output=True,text=True,timeout=3)
            self.assertNotEqual(result.returncode,0);self.assertIn('SSH',result.stderr)

if __name__=='__main__':unittest.main()
