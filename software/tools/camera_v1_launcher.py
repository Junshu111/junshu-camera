"""Trusted Mac-streamed user launcher writer for New Cam 1.0."""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def desktop_entry():
    package = '/home/junshu/junshu-camera-pi-1.0.0'
    return ('#!/usr/bin/gio launch\n'
            '[Desktop Entry]\n'
            'Type=Application\n'
            'Name=New Cam 1.0\n'
            'Comment=Junshu camera\n'
            'Exec=/bin/sh '+package+'/start-camera.sh\n'
            'Path='+package+'\n'
            'Icon='+package+'/assets/boot-logo.png\n'
            'Terminal=false\n'
            'StartupNotify=false\n'
            'Categories=Graphics;Photography;\n')


def write_icon(path, data):
    path = Path(path)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data or stat.S_IMODE(path.stat().st_mode) != 0o755:
            raise ValueError('existing New Cam icon differs; preserved')
        return False
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o755)
    return True


def validate_desktop(home, selected):
    home = Path(home)
    desktop = Path(selected)
    if not desktop.is_absolute() or desktop == home or not desktop.is_relative_to(home):
        raise ValueError('xdg desktop path must be inside user home')
    current = desktop
    while current != home:
        if current.is_symlink():
            raise ValueError('symlink desktop path refused')
        current = current.parent
    if not desktop.is_dir():
        raise ValueError('existing Desktop directory required')
    return desktop


def install_launcher(home, expected_manifest_sha256):
    home = Path(home)
    if home != Path('/home/junshu') or os.geteuid() == 0:
        raise ValueError('launcher requires junshu user home')
    if not re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256):
        raise ValueError('invalid trusted app manifest')
    bundle = home/'junshu-camera-pi-1.0.0'
    verify_directory(bundle)
    verify_modes(bundle)
    if hashlib.sha256((bundle/'MANIFEST.sha256').read_bytes()).hexdigest() != expected_manifest_sha256:
        raise ValueError('installed app manifest differs from trusted Mac release')
    selected = subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True, timeout=5).strip()
    desktop = validate_desktop(home, selected)
    path = desktop/'New Cam 1.0.desktop'
    data = desktop_entry().encode('utf-8')
    changed = write_icon(path, data)
    return {'changed': changed, 'launcher': str(path), 'old_icons_changed': False}
