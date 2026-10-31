"""Release artifacts are source-only and retained; never remove fixtures."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / 'tools/build_pi_next_release.py'
QA = ROOT / 'qa/r4-integrated-20261026/delivery/release-fixtures'
FILES = (
    'README.md', 'TESTING.md', 'feature-status.md', 'check_package.py',
    'run_camera.py', 'start-camera.sh', 'start-autostart.sh', 'camera_r4/artwork.py',
    'camera_r4/camera.py', 'camera_r4/capture.py', 'camera_r4/framing.py',
    'camera_r4/gui.py', 'camera_r4/imaging.py', 'camera_r4/platform_io.py',
    'camera_r4/state.py', 'camera_r4/storage.py',
    'camera_r4/l1_input.py', 'camera_r4/gpio_probe.py',
    'camera_r4/power_client.py', 'camera_r4/power_indicator.py', 'camera_r4/power_bridge.py',
    'camera_r4/photo_share.py', 'camera_r4/share_page.py',
    'camera_r4/share_ui.py', 'camera_r4/share_network.py',
    'camera_r4/vendor/qrcodegen.py', 'camera_r4/vendor/LICENSE-qrcodegen.txt', 'camera_r4/vendor/README.md',
)
PREFIX = 'junshu-camera-pi-0.4.0'


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(BUILDER.is_file(), 'R4 builder does not exist yet')
        spec = importlib.util.spec_from_file_location('r4_builder', BUILDER)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        QA.mkdir(parents=True, exist_ok=True)
        self.fixture = Path(tempfile.mkdtemp(prefix='camera-r4-release-', dir=QA))
        self.source = self.fixture/'source'
        self.source.mkdir()
        for name in FILES:
            path = self.source/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture '+name+'\n', encoding='utf-8')
        self.output = self.fixture/'output'

    def test_archive_allowlist_manifest_and_determinism(self):
        (self.source/'camera_r4/private.JPG').write_bytes(b'not for release')
        (self.source/'settings-r4.json').write_text('{"private":true}')
        (self.source/'PyQt5').mkdir()
        first = self.builder.build(self.source, self.output)
        second = self.builder.build(self.source, self.fixture/'second')
        self.assertEqual(first.read_bytes(), second.read_bytes())
        digest = hashlib.sha256(first.read_bytes()).hexdigest()
        self.assertEqual(first.with_name(first.name+'.sha256').read_text(), digest+'  '+first.name+'\n')
        with tarfile.open(first, 'r:gz') as archive:
            members = archive.getmembers()
            self.assertEqual({m.name for m in members}, {PREFIX+'/'+n for n in (*FILES, 'MANIFEST.sha256')})
            self.assertTrue(all(m.isfile() and m.mtime == 0 and m.uid == 0 and m.gid == 0 for m in members))
            for member in members:
                self.assertEqual(member.mode, 0o755 if member.name.endswith('.sh') else 0o644)
            manifest = archive.extractfile(PREFIX+'/MANIFEST.sha256').read().decode()
            seen = set()
            for line in manifest.splitlines():
                expected, name = line.split('  ', 1)
                self.assertEqual(expected, hashlib.sha256(archive.extractfile(PREFIX+'/'+name).read()).hexdigest())
                seen.add(name)
            self.assertEqual(seen, set(FILES))

    def test_existing_archive_is_never_overwritten(self):
        archive = self.builder.build(self.source, self.output)
        before = archive.read_bytes()
        with self.assertRaises(FileExistsError):
            self.builder.build(self.source, self.output)
        self.assertEqual(archive.read_bytes(), before)

    def test_existing_checksum_is_never_overwritten(self):
        self.output.mkdir()
        checksum = self.output/(PREFIX+'.tar.gz.sha256')
        checksum.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.builder.build(self.source, self.output)
        self.assertEqual(checksum.read_text(), 'keep')
        self.assertFalse((self.output/(PREFIX+'.tar.gz')).exists())

    def test_missing_allowlisted_file_fails_before_output(self):
        incomplete = self.fixture/'incomplete'
        incomplete.mkdir()
        with self.assertRaises((FileNotFoundError, ValueError)):
            self.builder.build(incomplete, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_source_file_is_rejected(self):
        self.source.joinpath('README.md').rename(self.source/'README-retained.md')
        self.source.joinpath('README.md').symlink_to(self.source/'README-retained.md')
        with self.assertRaises(ValueError):
            self.builder.build(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_ancestor_is_rejected(self):
        self.source.joinpath('camera_r4').rename(self.source/'retained-camera_r4')
        self.source.joinpath('camera_r4').symlink_to(self.source/'retained-camera_r4', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.builder.build(self.source, self.output)

    def test_source_symlink_is_rejected(self):
        alias = self.fixture/'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.builder.build(alias, self.output)

    def test_untrusted_archive_rejects_traversal_links_and_duplicate_names(self):
        for bad_name, bad_type in (('../escape', tarfile.REGTYPE), (PREFIX+'/../escape', tarfile.REGTYPE),
                                  ('/'+PREFIX+'/absolute', tarfile.REGTYPE), (PREFIX+'/link', tarfile.SYMTYPE),
                                  (PREFIX+'/link', tarfile.LNKTYPE), (PREFIX+'/pipe', tarfile.FIFOTYPE)):
            with self.subTest(name=bad_name, type=bad_type):
                path = self.fixture/('bad-'+str(len(list(self.fixture.glob('bad-*'))))+'.tar.gz')
                with tarfile.open(path, 'w:gz') as archive:
                    info = tarfile.TarInfo(bad_name)
                    info.type = bad_type
                    info.linkname = '../escape'
                    archive.addfile(info, io.BytesIO())
                with self.assertRaises(ValueError):
                    self.builder.verify_archive(path)
        duplicate = self.fixture/'duplicates.tar.gz'
        with tarfile.open(duplicate, 'w:gz') as archive:
            for _ in range(2):
                archive.addfile(tarfile.TarInfo(PREFIX+'/README.md'), io.BytesIO())
        with self.assertRaises(ValueError):
            self.builder.verify_archive(duplicate)

    def test_archive_with_privileged_mode_is_rejected(self):
        good = self.builder.build(self.source, self.output)
        unsafe = self.fixture/'privileged.tar.gz'
        with tarfile.open(good, 'r:gz') as original, tarfile.open(unsafe, 'w:gz') as changed:
            for info in original.getmembers():
                data = original.extractfile(info).read()
                if info.name.endswith('/start-camera.sh'):
                    info.mode = 0o4755
                changed.addfile(info, io.BytesIO(data))
        with self.assertRaises(ValueError):
            self.builder.verify_archive(unsafe)

    def test_tampered_payload_is_rejected(self):
        good = self.builder.build(self.source, self.output)
        corrupt = self.fixture/'tampered.tar.gz'
        with tarfile.open(good, 'r:gz') as original, tarfile.open(corrupt, 'w:gz') as changed:
            for info in original.getmembers():
                data = original.extractfile(info).read()
                if info.name.endswith('/README.md'):
                    data += b'tampered'
                info.size = len(data)
                changed.addfile(info, io.BytesIO(data))
        with self.assertRaises(ValueError):
            self.builder.verify_archive(corrupt)

    def test_importer_rejects_shell_syntax_and_option_targets_without_connection(self):
        importer = ROOT/'导入R4整合版.command'
        self.assertTrue(importer.is_file(), 'R4 importer does not exist yet')
        for target in ('user@host;touch /tmp/r4-should-not-exist', '-oProxyCommand=evil',
                       'user name@host', 'user@host/path', 'user@host:22', 'user@$(hostname)'):
            with self.subTest(target=target):
                result = subprocess.run(['bash', str(importer)], input=target+'\n',
                                        capture_output=True, text=True, timeout=3)
                self.assertIn(result.returncode, (1, 2), result.stdout+result.stderr)
                self.assertIn('SSH', result.stderr)

    def test_importer_cancellation_needs_no_release_or_device(self):
        importer = ROOT/'导入R4整合版.command'
        self.assertTrue(importer.is_file(), 'R4 importer does not exist yet')
        result = subprocess.run(['bash', str(importer)], input='junshu@example.invalid\nno\n',
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('取消', result.stdout)

    def test_directory_checker_rejects_injected_source(self):
        archive = self.builder.build(self.source, self.output)
        with tarfile.open(archive, 'r:gz') as bundle:
            (self.source/'MANIFEST.sha256').write_bytes(bundle.extractfile(PREFIX+'/MANIFEST.sha256').read())
        spec = importlib.util.spec_from_file_location('release_checker', ROOT/'pi-r4-next/check_package.py')
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertEqual(checker.verify_directory(self.source), len(FILES))
        (self.source/'camera_r4/__init__.py').write_text('raise RuntimeError("injected")')
        with self.assertRaises(ValueError):
            checker.verify_directory(self.source)

    def test_real_package_verifier_detects_corruption_and_symlinks(self):
        archive = self.builder.build(ROOT/'pi-r4-next', self.output)
        destination = self.fixture/'extracted'
        destination.mkdir()
        # Validation precedes extraction even in the retained local fixture.
        self.builder.verify_archive(archive)
        with tarfile.open(archive, 'r:gz') as bundle:
            for member in bundle.getmembers():
                path = destination/member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as target:
                    target.write(bundle.extractfile(member).read())
        release = destination/PREFIX
        command = [sys.executable, '-B', str(release/'check_package.py')]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        (release/'README.md').write_text('corrupt')
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        (release/'README.md').rename(release/'README-retained.md')
        (release/'README.md').symlink_to(release/'README-retained.md')
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
