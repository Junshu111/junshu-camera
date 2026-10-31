#!/usr/bin/env python3
"""Standard-library release integrity checker. Never changes files or devices."""
import hashlib
import re
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath

VERSION = '1.0.0'
PREFIX = 'junshu-camera-pi-' + VERSION
RELEASE_FILES = (
    'README.md', 'TESTING.md', 'feature-status.md', 'check_package.py',
    'run_camera.py', 'start-camera.sh', 'start-autostart.sh',
    'start-session.sh', 'session_setup.py', 'assets/boot-logo.png',
    'camera_r4/artwork.py',
    'camera_r4/camera.py', 'camera_r4/capture.py', 'camera_r4/framing.py',
    'camera_r4/gui.py', 'camera_r4/imaging.py', 'camera_r4/loading.py', 'camera_r4/platform_io.py',
    'camera_r4/state.py', 'camera_r4/storage.py',
    'camera_r4/l1_input.py', 'camera_r4/gpio_probe.py',
    'camera_r4/power_client.py', 'camera_r4/power_indicator.py', 'camera_r4/power_bridge.py',
    'camera_r4/photo_share.py', 'camera_r4/share_page.py',
    'camera_r4/share_ui.py', 'camera_r4/share_network.py',
    'camera_r4/vendor/qrcodegen.py', 'camera_r4/vendor/LICENSE-qrcodegen.txt', 'camera_r4/vendor/README.md',
)
MAX_FILE_BYTES = 4 * 1024 * 1024
APPROVED_LOGO_SHA256 = '8a8249c28098749e0b7a780a0d25a8e0120b3fe8a1a0fef39c6cd0928dcbfb2c'


def regular_file(root, relative):
    """Reject symlinks in the selected tree, including linked parent directories."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError('symlink release root: ' + str(root))
    name = PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or str(name) != relative:
        raise ValueError('unsafe release path: ' + relative)
    path = root
    for part in name.parts:
        path = path/part
        if path.is_symlink():
            raise ValueError('symlink release input: ' + str(path))
    if not path.is_file():
        raise FileNotFoundError('missing regular release file: ' + str(path))
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('oversized release file: ' + str(path))
    return path


def check_contents(contents):
    expected = set(RELEASE_FILES) | {'MANIFEST.sha256'}
    if set(contents) != expected:
        raise ValueError('release file allowlist does not match')
    manifest = contents['MANIFEST.sha256'].decode('utf-8')
    hashes = {}
    for line in manifest.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError('invalid manifest line')
        digest, name = match.groups()
        if name not in RELEASE_FILES or name in hashes:
            raise ValueError('unexpected/duplicate manifest path')
        hashes[name] = digest
    if set(hashes) != set(RELEASE_FILES):
        raise ValueError('manifest coverage does not match allowlist')
    for name, digest in hashes.items():
        if hashlib.sha256(contents[name]).hexdigest() != digest:
            raise ValueError('checksum mismatch: ' + name)
    if hashlib.sha256(contents['assets/boot-logo.png']).hexdigest() != APPROVED_LOGO_SHA256:
        raise ValueError('approved New Cam logo mismatch')
    return len(hashes)


def read_archive(archive):
    contents = {}
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if (not member.isfile() or member.name != str(path) or path.is_absolute()
                    or '..' in path.parts or not member.name.startswith(PREFIX+'/')
                    or member.size > MAX_FILE_BYTES or member.size < 0):
                raise ValueError('unsafe archive member: ' + member.name)
            relative = member.name[len(PREFIX)+1:]
            if relative not in (*RELEASE_FILES, 'MANIFEST.sha256') or relative in contents:
                raise ValueError('unexpected/duplicate archive member: ' + member.name)
            if member.mode != (0o755 if relative.endswith('.sh') else 0o644):
                raise ValueError('unexpected archive permissions: ' + member.name)
            contents[relative] = bundle.extractfile(member).read()
    check_contents(contents)
    return contents


def verify_archive(archive):
    return check_contents(read_archive(archive))


def verify_directory(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('symlink release root: ' + str(directory))
    expected = set(RELEASE_FILES) | {'MANIFEST.sha256'}
    for path in directory.rglob('*'):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise ValueError('symlink in release directory: ' + name)
        if path.is_dir() and name in ('camera_r4', 'camera_r4/vendor', 'assets'):
            continue
        if name not in expected or not path.is_file():
            raise ValueError('unexpected release entry: ' + name)
    contents = {name: regular_file(directory, name).read_bytes()
                for name in (*RELEASE_FILES, 'MANIFEST.sha256')}
    return check_contents(contents)


def verify_modes(directory):
    directory=Path(directory)
    for path in (directory,directory/'camera_r4',directory/'camera_r4/vendor',directory/'assets'):
        if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode)!=0o755:
            raise ValueError('unexpected release directory permissions: '+str(path))
    for name in (*RELEASE_FILES,'MANIFEST.sha256'):
        path=regular_file(directory,name)
        if stat.S_IMODE(path.stat().st_mode)!=(0o755 if name.endswith('.sh') else 0o644):
            raise ValueError('unexpected release file permissions: '+name)


def receive_archive(archive, checksum, expected_digest, home):
    """Trusted caller's digest + allowlist validate before fresh exclusive extraction."""
    archive, checksum, home = Path(archive), Path(checksum), Path(home)
    for path in (archive, checksum, home):
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError('symlink receiver path refused: '+str(parent))
    if not re.fullmatch(r'[0-9a-f]{64}', expected_digest):
        raise ValueError('invalid trusted archive SHA256')
    line = checksum.read_text(encoding='ascii')
    if not re.fullmatch('[0-9a-f]{64}  '+re.escape(archive.name)+'\n', line):
        raise ValueError('invalid received checksum format')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_digest or line[:64] != expected_digest:
        raise ValueError('received SHA256 mismatch against trusted Mac digest')
    contents = read_archive(archive)
    release = home/PREFIX
    if release.exists() or release.is_symlink():
        verify_directory(release)
        verify_modes(release)
        current = {name: regular_file(release, name).read_bytes() for name in contents}
        modes_match = all(stat.S_IMODE((release/name).stat().st_mode) == (0o755 if name.endswith('.sh') else 0o644) for name in contents)
        if current != contents or not modes_match:
            raise ValueError('existing release differs from trusted archive; all files retained')
        return release
    release.mkdir(mode=0o755)
    release.chmod(0o755)
    for name, data in sorted(contents.items()):
        path = release/name
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(data)
        path.chmod(0o755 if name.endswith('.sh') else 0o644)
    for path in (release/'camera_r4',release/'camera_r4/vendor',release/'assets'):path.chmod(0o755)
    verify_directory(release)
    verify_modes(release)
    return release


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print('用法：python3 -B check_package.py（仅校验本包，不访问相机）', file=sys.stderr)
        return 2
    try:
        count = verify_directory(Path(__file__).absolute().parent)
    except (OSError, ValueError, UnicodeError, tarfile.TarError) as error:
        print('包校验失败：'+str(error), file=sys.stderr)
        return 1
    print('New Cam '+VERSION+'：'+str(count)+' 个发布文件 SHA256 一致；这不代表实机拍照验收。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
