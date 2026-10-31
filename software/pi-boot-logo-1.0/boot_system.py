#!/usr/bin/env python3
"""Conservative Pi 3B+ New Cam 1.0 Plymouth installer.

The tool never installs packages, contacts a Pi, reboots, or deletes files.
All originals and generated images are retained. A non-root ``--root`` is a
deliberately gated offline fixture, never an alternate installation target.
"""

import argparse
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid


KERNEL = "6.18.50+rpt-rpi-v8"
THEME = "newcam"
VERSION = "1.0.0"
BOOT_FLAGS = ("quiet", "splash", "logo.nologo", "vt.global_cursor_default=0")
CONFIG_BLOCK = b"\n# BEGIN JUNSHU NEW CAM 1.0\n[all]\ndisable_splash=1\n# END JUNSHU NEW CAM 1.0\n"
CONFIG_BEGIN = b"# BEGIN JUNSHU NEW CAM 1.0"
THEME_FILES = (f"{THEME}.plymouth", f"{THEME}.script", "logo.png")
MANIFEST_FILES = tuple(sorted(("VERSION", "boot_system.py") + tuple("theme/" + n for n in THEME_FILES)))
FILES = {
    "config": "boot/firmware/config.txt",
    "cmdline": "boot/firmware/cmdline.txt",
    "initramfs": "boot/firmware/initramfs8",
    "plymouth": "etc/plymouth/plymouthd.conf",
}
STATE_REL = "var/lib/junshu-camera-boot-1.0.0/state.json"
BACKUPS_REL = "var/lib/junshu-camera-boot-1.0.0/backups"
RESCUE_REL = "boot/firmware/junshu-newcam-1.0-backups"
THEME_REL = f"usr/share/plymouth/themes/{THEME}"
LOCK_REL = "run/lock/junshu-camera-boot-1.0.0.lock"
OLD_LOCK_REL = "run/lock/junshu-camera-boot-logo-test.lock"
OLD_STATE_REL = "var/lib/junshu-camera-boot-logo-test/state.json"
OLD_BACKUPS_REL = "var/lib/junshu-camera-boot-logo-test/backups"
OLD_RESCUE_REL = "boot/firmware/junshu-boot-logo-backups"
OLD_THEME_REL = "usr/share/plymouth/themes/junshu-camera-test"
OLD_THEME_FILES = ("junshu-camera-test.plymouth", "junshu-camera-test.script", "page-1.png", "page-2.png")
OLD_CONFIG_BLOCK = b"\n# BEGIN JUNSHU BOOT LOGO TEST\n[all]\ndisable_splash=1\n# END JUNSHU BOOT LOGO TEST\n"
AUTOSTART_REL = "home/junshu/.config/autostart/junshu-camera-r4-integrated.desktop"
START_SCRIPT_REL = "home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh"
AUTOSTART_BYTES = (
    "[Desktop Entry]\nType=Application\nName=Junshu Camera\n"
    "Exec=/bin/sh /home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh\n"
    "Terminal=false\nStartupNotify=false\nX-Junshu-Managed=0.4.0\n"
).encode()
HIDDEN_AUTOSTART_BYTES = AUTOSTART_BYTES + b"Hidden=true\n"
SESSION_HOOK_REL = "home/junshu/.config/labwc/autostart"
SESSION_HOOK_BYTES = (
    b"#!/bin/sh\n/usr/bin/kanshi &\n/usr/bin/lxsession-xdg-autostart &\n"
    b"/bin/sh /home/junshu/junshu-camera-pi-1.0.0/start-session.sh &\n"
)
SESSION_STATE_REL = "home/junshu/.local/state/junshu-camera/newcam-session/state.json"
SESSION_RELEASE_REL = "home/junshu/junshu-camera-pi-1.0.0"
DEPENDENCIES = (
    ("usr/sbin/plymouthd", True),
    ("usr/bin/plymouth", True),
    ("usr/sbin/plymouth-set-default-theme", True),
    ("usr/sbin/mkinitramfs", True),
    ("usr/bin/lsinitramfs", True),
    ("usr/share/initramfs-tools/hooks/plymouth", True),
    ("usr/share/initramfs-tools/scripts/init-premount/plymouth", True),
    ("usr/lib/aarch64-linux-gnu/plymouth/script.so", False),
    ("usr/lib/aarch64-linux-gnu/plymouth/renderers/drm.so", False),
)


class Refusal(Exception):
    """A precondition or conflict that must not be repaired automatically."""


class Context:
    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise Refusal("--root must be an absolute path without traversal")
        self.fixture = self.root != Path("/")
        if self.fixture:
            if os.environ.get("JUNSHU_BOOT_LOGO_FIXTURE") != "1":
                raise Refusal("Non-/ --root requires explicit offline fixture mode")
            fixture_base = Path(__file__).resolve().parents[1] / "qa/r4-boot-logo-20261028/test-fixtures"
            try:
                self.root.relative_to(fixture_base)
            except ValueError as exc:
                raise Refusal("Offline fixture root must stay inside the project QA test-fixtures directory") from exc
            self._check_root_components()
            marker = self.root / ".junshu-boot-logo-fixture"
            marker_bytes = self.read(marker, max_size=1024, owner=os.geteuid())
            try:
                self.fixture_info = json.loads(marker_bytes)
            except (ValueError, UnicodeError) as exc:
                raise Refusal("Invalid offline fixture marker") from exc
            self.system_owner = os.geteuid()
            self.user_owner = os.geteuid()
        else:
            if platform.system() != "Linux" or os.geteuid() != 0:
                raise Refusal("Real system operations require Linux root")
            self.system_owner = 0
            try:
                self.user_owner = pwd.getpwnam("junshu").pw_uid
            except KeyError as exc:
                raise Refusal("Expected junshu account is missing") from exc

    def path(self, relative):
        return self.root / relative

    def _check_root_components(self):
        for component in reversed((self.root,) + tuple(self.root.parents)):
            info = component.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise Refusal(f"Fixture root component is not a real directory: {component}")
            if component == self.root and info.st_mode & 0o022:
                raise Refusal(f"Fixture root is group/world writable: {component}")

    def check_parents(self, path, secure=True):
        try:
            rel = path.relative_to(self.root)
        except ValueError as exc:
            raise Refusal(f"Path escapes root: {path}") from exc
        current = self.root
        for part in rel.parts[:-1]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError as exc:
                raise Refusal(f"Required directory is missing: {current}") from exc
            if not stat.S_ISDIR(info.st_mode):
                raise Refusal(f"Directory is a symlink or not a directory: {current}")
            if secure and info.st_mode & 0o022:
                raise Refusal(f"Directory is group/world writable: {current}")

    def check_dir(self, path, owner=None, secure=True):
        self.check_parents(path, secure=secure)
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise Refusal(f"Required directory is missing: {path}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise Refusal(f"Directory is a symlink or not a directory: {path}")
        if owner is not None and info.st_uid != owner:
            raise Refusal(f"Unexpected directory owner: {path}")
        if secure and info.st_mode & 0o022:
            raise Refusal(f"Directory is group/world writable: {path}")
        return info

    def check_file(self, path, owner=None, executable=False, max_size=None, secure=True):
        self.check_parents(path, secure=secure)
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise Refusal(f"Required file is missing: {path}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise Refusal(f"File is a symlink or not regular: {path}")
        if owner is not None and info.st_uid != owner:
            raise Refusal(f"Unexpected file owner: {path}")
        if secure and info.st_mode & 0o022:
            raise Refusal(f"File is group/world writable: {path}")
        if executable and not info.st_mode & 0o111:
            raise Refusal(f"Required executable is not executable: {path}")
        if max_size is not None and info.st_size > max_size:
            raise Refusal(f"File is unexpectedly large: {path}")
        return info

    def read(self, path, max_size=None, owner=None, secure=True):
        info = self.check_file(path, owner=owner, max_size=max_size, secure=secure)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise Refusal(f"File changed during read: {path}")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read() if max_size is None else handle.read(max_size + 1)
            if max_size is not None and len(data) > max_size:
                raise Refusal(f"File is unexpectedly large: {path}")
            return data
        finally:
            os.close(fd)

    def digest(self, path, owner=None, secure=True):
        info = self.check_file(path, owner=owner, secure=secure)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise Refusal(f"File changed during hash: {path}")
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return {"sha256": digest.hexdigest(), "size": info.st_size,
                    "mode": stat.S_IMODE(info.st_mode)}
        finally:
            os.close(fd)

    def mkdir(self, path, mode=0o700):
        """Create only missing directories; never replace an existing entry."""
        rel = path.relative_to(self.root)
        current = self.root
        for part in rel.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                self.check_dir(current, secure=True)
                continue
            os.mkdir(current, mode)
            self.check_dir(current, owner=self.system_owner, secure=True)


@contextmanager
def mutation_lock(ctx):
    """Serialize with both 0.1.1 and 1.0 boot writers."""
    run_dir = ctx.path("run")
    lock_dir = ctx.path("run/lock")
    if ctx.fixture:
        ctx.mkdir(lock_dir)
    else:
        ctx.check_dir(run_dir, owner=ctx.system_owner)
        info = ctx.check_dir(lock_dir, owner=ctx.system_owner, secure=False)
        # Debian normally uses a root-owned sticky /run/lock directory.
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise Refusal("/run/lock is writable without a sticky bit")
    held = []
    try:
        for relative in (OLD_LOCK_REL, LOCK_REL):
            lock_path = ctx.path(relative)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                info = os.fstat(fd)
                disk_info = lock_path.lstat()
                if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (disk_info.st_dev, disk_info.st_ino):
                    raise Refusal("Boot-logo lock is not a stable regular file")
                if info.st_uid != ctx.system_owner or stat.S_IMODE(info.st_mode) & 0o077:
                    raise Refusal("Boot-logo lock has unexpected owner or permissions")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Refusal("Another boot-logo enable or rollback holds the lock") from exc
                held.append(fd)
            except Exception:
                os.close(fd)
                raise
        yield
    finally:
        for fd in reversed(held):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _decode(data, path):
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise Refusal(f"Expected UTF-8 text: {path}") from exc


def validate_package(bundle, expected_sha):
    if not expected_sha or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise Refusal("enable requires --expected-manifest-sha256 from the trusted Mac package")
    bundle = Path(bundle)
    if not bundle.is_absolute():
        raise Refusal("--bundle must be absolute")
    if not bundle.is_dir() or bundle.is_symlink():
        raise Refusal(f"Bundle is missing or a symlink: {bundle}")
    for child in (bundle / "theme",):
        if not child.is_dir() or child.is_symlink():
            raise Refusal(f"Theme bundle directory is missing or a symlink: {child}")
    manifest_path = bundle / "MANIFEST.sha256"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise Refusal("Manifest is missing or a symlink")
    manifest_bytes = manifest_path.read_bytes()
    if len(manifest_bytes) > 4096 or sha(manifest_bytes) != expected_sha:
        raise Refusal("Bundle manifest SHA-256 differs from trusted Mac value")
    lines = _decode(manifest_bytes, manifest_path).splitlines(keepends=True)
    if len(lines) != len(MANIFEST_FILES):
        raise Refusal("Bundle manifest must list exactly five files")
    assets = {}
    for line, name in zip(lines, MANIFEST_FILES):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)\n", line)
        if not match or match.group(2) != name:
            raise Refusal("Bundle manifest entries are not in the fixed sorted format")
        path = bundle / name
        if path.is_symlink() or not path.is_file():
            raise Refusal(f"Bundle file is missing or a symlink: {path}")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise Refusal(f"Bundle file is unexpectedly large: {path}")
        contents = path.read_bytes()
        if sha(contents) != match.group(1):
            raise Refusal(f"Bundle file hash differs from manifest: {name}")
        assets[name] = contents
    if assets["VERSION"] != (VERSION + "\n").encode():
        raise Refusal("Unexpected bundle VERSION")
    return assets


def check_platform(ctx):
    if ctx.fixture:
        model = ctx.fixture_info.get("model")
        kernel = ctx.fixture_info.get("kernel")
        arch = ctx.fixture_info.get("arch")
    else:
        model = _decode(Path("/proc/device-tree/model").read_bytes().rstrip(b"\0"),
                        "/proc/device-tree/model")
        kernel = platform.release()
        arch = platform.machine()
    if model != "Raspberry Pi 3 Model B Plus Rev 1.3":
        raise Refusal(f"Unknown board model: {model!r}")
    if kernel != KERNEL or arch != "aarch64":
        raise Refusal(f"Unknown 64-bit boot chain: kernel={kernel!r}, arch={arch!r}")
    # Trixie has merged /usr; /lib is normally a symlink to /usr/lib.
    ctx.check_dir(ctx.path("usr/lib/modules/" + KERNEL), owner=ctx.system_owner)
    return {"model": model, "kernel": kernel, "arch": arch}


def check_boot_mapping(ctx, check_initrd=True):
    """Confirm both firmware boot files match the running packaged kernel.

    Pi 3B+ with arm_64bit=1 and no kernel/os_prefix override uses kernel8.img,
    and auto_initramfs=1 selects adjacent initramfs8.  A matching /boot pair
    ties these firmware files to the known kernel release before any write.
    """
    if not ctx.fixture and not os.path.ismount("/boot/firmware"):
        raise Refusal("/boot/firmware is not a mounted boot partition")
    pairs = [("boot/firmware/kernel8.img", "boot/vmlinuz-" + KERNEL)]
    if check_initrd:
        pairs.append(("boot/firmware/initramfs8", "boot/initrd.img-" + KERNEL))
    evidence = {}
    for firmware, packaged in pairs:
        left = ctx.digest(ctx.path(firmware), owner=ctx.system_owner)
        right = ctx.digest(ctx.path(packaged), owner=ctx.system_owner)
        if left["size"] == 0 or left["sha256"] != right["sha256"]:
            raise Refusal(f"Firmware boot mapping differs: {firmware} != {packaged}")
        evidence[firmware] = left["sha256"]
    return evidence


def check_dependencies(ctx):
    for relative, executable in DEPENDENCIES:
        ctx.check_file(ctx.path(relative), owner=ctx.system_owner, executable=executable)


def check_autostart(ctx):
    data = ctx.read(ctx.path(AUTOSTART_REL), owner=ctx.user_owner, max_size=16384)
    ctx.check_file(ctx.path(START_SCRIPT_REL), owner=ctx.user_owner)
    if data == AUTOSTART_BYTES:
        return "0.4.0-xdg"
    if data != HIDDEN_AUTOSTART_BYTES:
        raise Refusal("Known 0.4.0 or 1.0 camera startup entry is missing or changed")
    if ctx.read(ctx.path(SESSION_HOOK_REL), owner=ctx.user_owner, max_size=16384) != SESSION_HOOK_BYTES:
        raise Refusal("New Cam 1.0 session hook differs")
    try:
        state = json.loads(ctx.read(ctx.path(SESSION_STATE_REL), owner=ctx.user_owner, max_size=32768))
    except (ValueError, UnicodeError) as exc:
        raise Refusal("New Cam 1.0 session state is unreadable") from exc
    if (state.get("status") != "active" or
            not re.fullmatch(r"[0-9a-f]{32}", str(state.get("backup_id"))) or
            not re.fullmatch(r"[0-9a-f]{64}", str(state.get("manifest_sha256")))):
        raise Refusal("New Cam 1.0 session state is not active or recognized")
    manifest = ctx.read(ctx.path(SESSION_RELEASE_REL + "/MANIFEST.sha256"),
                        owner=ctx.user_owner, max_size=65536)
    if sha(manifest) != state["manifest_sha256"]:
        raise Refusal("New Cam 1.0 app manifest changed after session enable")
    match = [re.fullmatch(rb"([0-9a-f]{64})  start-session\.sh\n", line)
             for line in manifest.splitlines(keepends=True)]
    expected = [item.group(1).decode() for item in match if item is not None]
    if len(expected) != 1:
        raise Refusal("New Cam 1.0 session script is absent from app manifest")
    script = ctx.path(SESSION_RELEASE_REL + "/start-session.sh")
    if ctx.digest(script, owner=ctx.user_owner)["sha256"] != expected[0]:
        raise Refusal("New Cam 1.0 session script differs from app manifest")
    return "1.0-labwc"


def edited_config(data):
    text = _decode(data, FILES["config"])
    if "\r" in text or not text.endswith("\n") or CONFIG_BEGIN.decode() in text:
        raise Refusal("config.txt has unsupported newline or existing unmanaged logo block")
    if text.count("# BEGIN JUNSHU GPIO22 0.4.0") != 1 or text.count("# END JUNSHU GPIO22 0.4.0") != 1:
        raise Refusal("Expected 0.4.0 GPIO22 block is missing or ambiguous")
    section = "all"
    active = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if line not in ("[all]", "[cm4]", "[cm5]", "[pi5]"):
                raise Refusal(f"Unknown config.txt conditional branch: {line}")
            section = line[1:-1]
            continue
        key = re.split(r"[=\s]", line, maxsplit=1)[0].strip().lower()
        if key in ("include", "initramfs", "kernel", "os_prefix", "cmdline",
                   "ramfsfile", "ramfsaddr", "disable_splash"):
            raise Refusal(f"Conflicting boot directive in config.txt: {key}")
        if section == "all":
            active.append(line)
    for required in (
        "auto_initramfs=1", "arm_64bit=1", "dtoverlay=vc4-kms-v3d",
        "dtoverlay=vc4-kms-dsi-waveshare-panel-v2,4_3_inch_a",
        "dtoverlay=gpio-led,gpio=22,label=junshu-white,active_low=0,trigger=default-on",
    ):
        if active.count(required) != 1:
            raise Refusal(f"Known boot/screen/GPIO configuration differs: {required}")
    if any(line.startswith("auto_initramfs=") and line != "auto_initramfs=1" or
           line.startswith("arm_64bit=") and line != "arm_64bit=1" for line in active):
        raise Refusal("Conflicting initramfs or 64-bit directive")
    return data + CONFIG_BLOCK


def edited_cmdline(data):
    text = _decode(data, FILES["cmdline"])
    ending = "\n" if text.endswith("\n") else ""
    body = text[:-1] if ending else text
    if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in body):
        raise Refusal("cmdline.txt must contain one argument line (optional final LF)")
    tokens = body.split()
    if not any(t.startswith("root=") for t in tokens) or "rootwait" not in tokens:
        raise Refusal("Unknown cmdline root boot arguments")
    if any(t in ("nosplash", "plymouth.enable=0", "systemd.show_status=1") for t in tokens):
        raise Refusal("Conflicting splash settings in cmdline.txt")
    for prefix, wanted in (("logo.", "logo.nologo"),
                           ("vt.global_cursor_default=", "vt.global_cursor_default=0")):
        found = [t for t in tokens if t.startswith(prefix)]
        if any(t != wanted for t in found) or len(found) > 1:
            raise Refusal(f"Conflicting cmdline argument: {prefix}")
    for token in BOOT_FLAGS:
        if tokens.count(token) > 1:
            raise Refusal(f"Repeated cmdline argument: {token}")
    added = [flag for flag in BOOT_FLAGS if flag not in tokens]
    return (body + (" " if added else "") + " ".join(added) + ending).encode()


def edited_plymouth(data):
    text = _decode(data, FILES["plymouth"])
    if "\r" in text or not text.endswith("\n"):
        raise Refusal("plymouthd.conf has unsupported line endings")
    lines = text.splitlines(keepends=True)
    section = None
    daemon_count = 0
    daemon_index = None
    theme_index = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("["):
            section = line
            if line == "[Daemon]":
                daemon_count += 1
                daemon_index = index
            continue
        if line.startswith("Theme="):
            if section != "[Daemon]":
                raise Refusal("Theme directive outside [Daemon]")
            theme_index.append(index)
        if line.startswith("ShowDelay=") and line != "ShowDelay=0":
            raise Refusal("Nonzero Plymouth ShowDelay would hide the New Cam logo")
    if daemon_count > 1 or len(theme_index) > 1:
        raise Refusal("plymouthd.conf has ambiguous [Daemon] or Theme directives")
    if theme_index:
        if lines[theme_index[0]].strip() == "Theme=" + THEME:
            raise Refusal("Plymouth theme is already selected without managed state")
        lines[theme_index[0]] = "Theme=" + THEME + "\n"
    elif daemon_index is not None:
        lines.insert(daemon_index + 1, "Theme=" + THEME + "\n")
    else:
        lines.append("\n[Daemon]\nTheme=" + THEME + "\n")
    return "".join(lines).encode()


def check_space(ctx, image_size):
    root_free = shutil.disk_usage(ctx.root).free
    boot_free = shutil.disk_usage(ctx.path("boot/firmware")).free
    # Backup plus generated image on root; room to build and to restore.
    if root_free < max(64 * 1024 * 1024, image_size * 3 + 32 * 1024 * 1024):
        raise Refusal("Insufficient root filesystem space for retained backup and initramfs build")
    if boot_free < max(16 * 1024 * 1024, image_size + 16 * 1024 * 1024):
        raise Refusal("Insufficient boot filesystem space for replacing initramfs8")


def load_old_state(ctx):
    path = ctx.path(OLD_STATE_REL)
    if not path.exists() and not path.is_symlink():
        return None
    if stat.S_IMODE(ctx.check_file(path, owner=ctx.system_owner).st_mode) != 0o600:
        raise Refusal("Existing 0.1.1 state permissions changed")
    data = ctx.read(path, owner=ctx.system_owner, max_size=32768)
    try:
        state = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise Refusal("Existing 0.1.1 state is unreadable") from exc
    if state.get("schema") != 1 or state.get("status") not in ("enabled", "rolled_back", "failed_restored"):
        raise Refusal("Existing 0.1.1 state is incomplete or unknown")
    return state, sha(data)


def verify_old_recovery_materials(ctx, state):
    """Validate 0.1.1 recovery copies without reading the four live boot files."""
    backup_id = state.get("backup_id")
    if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9]{8}-[0-9a-f]{32}", backup_id):
        raise Refusal("Existing 0.1.1 backup identifier is invalid")
    original, enabled = state.get("original"), state.get("enabled")
    if not isinstance(original, dict) or not isinstance(enabled, dict):
        raise Refusal("Existing 0.1.1 state lacks file hashes")
    for record in (original, enabled):
        if set(record) != set(FILES):
            raise Refusal("Existing 0.1.1 state has unknown file inventory")
        for entry in record.values():
            if (not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256")))
                    or not isinstance(entry.get("size"), int) or entry["size"] < 0
                    or not isinstance(entry.get("mode"), int)):
                raise Refusal("Existing 0.1.1 file record is invalid")
    old_backup = ctx.path(OLD_BACKUPS_REL) / backup_id
    ctx.check_dir(old_backup, owner=ctx.system_owner)
    for key, relative in FILES.items():
        saved = ctx.digest(old_backup / relative, owner=ctx.system_owner)
        if saved != original[key]:
            raise Refusal(f"Existing 0.1.1 backup differs: {relative}")
    old_rescue = ctx.path(OLD_RESCUE_REL) / backup_id
    ctx.check_dir(old_rescue, owner=ctx.system_owner)
    for key in ("config", "cmdline", "initramfs"):
        saved = ctx.digest(old_rescue / Path(FILES[key]).name, owner=ctx.system_owner)
        if saved["sha256"] != original[key]["sha256"]:
            raise Refusal(f"Existing 0.1.1 boot rescue differs: {FILES[key]}")
    theme = state.get("theme_sha256")
    if not isinstance(theme, dict) or set(theme) != set(OLD_THEME_FILES):
        raise Refusal("Existing 0.1.1 theme inventory is invalid")
    theme_dir = ctx.path(OLD_THEME_REL)
    ctx.check_dir(theme_dir, owner=ctx.system_owner)
    for name in OLD_THEME_FILES:
        if ctx.digest(theme_dir / name, owner=ctx.system_owner)["sha256"] != theme[name]:
            raise Refusal(f"Existing 0.1.1 theme differs: {name}")
    base_config = ctx.read(old_backup / FILES["config"], owner=ctx.system_owner, max_size=1024 * 1024)
    generated = ctx.digest(old_backup / "initramfs8.generated", owner=ctx.system_owner)
    if generated["sha256"] != enabled["initramfs"]["sha256"]:
        raise Refusal("Existing 0.1.1 generated initramfs differs")
    packaged = ctx.digest(ctx.path("boot/initrd.img-" + KERNEL), owner=ctx.system_owner)
    if packaged["sha256"] != original["initramfs"]["sha256"]:
        raise Refusal("Existing 0.1.1 original initramfs differs from packaged kernel")
    return base_config


def verify_old_install(ctx, state):
    """Trust 0.1.1 only after its active bytes and recovery copies agree."""
    base_config = verify_old_recovery_materials(ctx, state)
    check_matches(current_hashes(ctx), state["enabled"], "Existing 0.1.1 installation")
    if ctx.read(ctx.path(FILES["config"]), owner=ctx.system_owner, max_size=1024 * 1024) != base_config + OLD_CONFIG_BLOCK:
        raise Refusal("Existing 0.1.1 config marker differs")
    old_backup = ctx.path(OLD_BACKUPS_REL) / state["backup_id"]
    base_cmdline = ctx.read(old_backup / FILES["cmdline"], owner=ctx.system_owner, max_size=4096)
    if ctx.read(ctx.path(FILES["cmdline"]), owner=ctx.system_owner, max_size=4096) != edited_cmdline(base_cmdline):
        raise Refusal("Existing 0.1.1 cmdline differs")
    if b"Theme=junshu-camera-test\n" not in ctx.read(ctx.path(FILES["plymouth"]), owner=ctx.system_owner, max_size=1024 * 1024):
        raise Refusal("Existing 0.1.1 Plymouth selection differs")
    return base_config


def check_predecessor(ctx, state):
    predecessor = state.get("predecessor")
    if predecessor is None:
        return
    if not isinstance(predecessor, dict) or predecessor.get("kind") != "boot-test-0.1.1":
        raise Refusal("Unknown predecessor in 1.0 state")
    old = load_old_state(ctx)
    if old is None or old[1] != predecessor.get("state_sha256") or old[0].get("status") != "enabled":
        raise Refusal("Existing 0.1.1 state changed since 1.0 installation")
    verify_old_recovery_materials(ctx, old[0])


def preflight(ctx):
    platform_info = check_platform(ctx)
    check_dependencies(ctx)
    check_autostart(ctx)
    original = {}
    for key, relative in FILES.items():
        path = ctx.path(relative)
        original[key] = ctx.digest(path, owner=ctx.system_owner)
    config = ctx.read(ctx.path(FILES["config"]), owner=ctx.system_owner, max_size=1024 * 1024)
    cmdline = ctx.read(ctx.path(FILES["cmdline"]), owner=ctx.system_owner, max_size=4096)
    plymouth = ctx.read(ctx.path(FILES["plymouth"]), owner=ctx.system_owner, max_size=1024 * 1024)
    old = load_old_state(ctx)
    predecessor = None
    if old and old[0]["status"] == "enabled":
        platform_info["firmware_boot_mapping"] = check_boot_mapping(ctx, check_initrd=False)
        base_config = verify_old_install(ctx, old[0])
        predecessor = {"kind": "boot-test-0.1.1", "state_sha256": old[1],
                       "backup_id": old[0]["backup_id"]}
    else:
        platform_info["firmware_boot_mapping"] = check_boot_mapping(ctx)
        base_config = config
    desired = {"config": edited_config(base_config), "cmdline": edited_cmdline(cmdline),
               "plymouth": edited_plymouth(plymouth)}
    theme_parent = ctx.path("usr/share/plymouth/themes")
    ctx.check_dir(theme_parent, owner=ctx.system_owner)
    check_space(ctx, original["initramfs"]["size"])
    return platform_info, original, desired, predecessor


def _write_new(path, data, mode=0o600, set_mode=True):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    try:
        if set_mode:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
            # Some FAT implementations cannot fsync a directory descriptor.
            os.sync()
    finally:
        os.close(fd)


def _overwrite(ctx, path, data=None, source=None):
    """Stage, verify, then atomically replace one existing regular file.

    A failed stage remains on disk for inspection. The old bytes also remain
    in both retained backup locations; this function never unlinks anything.
    """
    if (data is None) == (source is None):
        raise ValueError("Exactly one of data or source is required")
    before = ctx.check_file(path, owner=ctx.system_owner)
    mode = stat.S_IMODE(before.st_mode)
    if source is not None:
        expected = ctx.digest(source, owner=ctx.system_owner)["sha256"]
    else:
        expected = sha(data)
    stage = path.with_name("." + path.name + ".junshu-stage-" + uuid.uuid4().hex)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(stage, flags, mode)
    try:
        # FAT mount options determine mode. On ext4, retain the original mode.
        if path.parts[:len(ctx.root.parts) + 2] != ctx.root.parts + ("boot", "firmware"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            if ctx.fixture and path.name == "initramfs8" and ctx.path("fail-stage-initramfs").exists():
                handle.write(b"INTERRUPTED STAGE\n")
                handle.flush()
                os.fsync(fd)
                raise Refusal("Simulated initramfs staging failure")
            elif data is not None:
                handle.write(data)
            else:
                input_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    with os.fdopen(input_fd, "rb", closefd=False) as input_handle:
                        shutil.copyfileobj(input_handle, handle, 1024 * 1024)
                finally:
                    os.close(input_fd)
            handle.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    if ctx.digest(stage, owner=ctx.system_owner)["sha256"] != expected:
        raise Refusal(f"Staged file hash differs before replacement: {stage}")
    current = ctx.check_file(path, owner=ctx.system_owner)
    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
        raise Refusal(f"Target changed before atomic replacement: {path}")
    os.replace(stage, path)
    _fsync_directory(path.parent)
    if ctx.digest(path, owner=ctx.system_owner)["sha256"] != expected:
        raise Refusal(f"Atomic replacement verification failed: {path}")


def _copy_new(ctx, source, destination, mode, set_mode=True):
    ctx.mkdir(destination.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, mode)
    try:
        if set_mode:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as output:
            with open(source, "rb") as input_handle:
                shutil.copyfileobj(input_handle, output, 1024 * 1024)
            output.flush()
        os.fsync(fd)
    finally:
        os.close(fd)


def _state_path(ctx):
    return ctx.path(STATE_REL)


def load_state(ctx):
    path = _state_path(ctx)
    if not path.exists() and not path.is_symlink():
        return None
    data = ctx.read(path, owner=ctx.system_owner, max_size=32768)
    try:
        state = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise Refusal("Managed boot-logo state is unreadable; no automatic overwrite") from exc
    if state.get("schema") != 1 or state.get("backup_id") is None:
        raise Refusal("Unknown managed boot-logo state format")
    return state


def save_state(ctx, state):
    path = _state_path(ctx)
    ctx.mkdir(path.parent)
    data = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists() or path.is_symlink():
        ctx.check_file(path, owner=ctx.system_owner)
        _overwrite(ctx, path, data=data)
    else:
        _write_new(path, data, 0o600)


def backup_dir(ctx, backup_id):
    if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9]{8}-[0-9a-f]{32}", backup_id):
        raise Refusal("Invalid backup identifier in state")
    return ctx.path(BACKUPS_REL) / backup_id


def rescue_dir(ctx, backup_id):
    # This directory remains on the FAT boot partition for Mac-side recovery.
    backup_dir(ctx, backup_id)  # Validate identifier.
    return ctx.path(RESCUE_REL) / backup_id


def create_backup(ctx, original):
    backup_id = time.strftime("%Y%m%d", time.gmtime()) + "-" + uuid.uuid4().hex
    directory = backup_dir(ctx, backup_id)
    ctx.mkdir(directory)
    for key, relative in FILES.items():
        source = ctx.path(relative)
        destination = directory / relative
        ctx.check_file(source, owner=ctx.system_owner)
        _copy_new(ctx, source, destination, original[key]["mode"])
        if ctx.digest(destination, owner=ctx.system_owner)["sha256"] != original[key]["sha256"]:
            raise Refusal(f"Backup verification failed: {relative}")
    rescue = rescue_dir(ctx, backup_id)
    ctx.mkdir(rescue)
    for key in ("config", "cmdline", "initramfs"):
        source = ctx.path(FILES[key])
        destination = rescue / Path(FILES[key]).name
        # FAT mount options control mode; only the bytes need to match here.
        _copy_new(ctx, source, destination, original[key]["mode"], set_mode=False)
        if ctx.digest(destination, owner=ctx.system_owner)["sha256"] != original[key]["sha256"]:
            raise Refusal(f"Boot-partition recovery copy failed: {FILES[key]}")
    instructions = (
        "Junshu Camera New Cam 1.0 boot recovery copy.\n"
        "These files are the ORIGINAL bytes from before New Cam 1.0 enable.\n"
        "If the Pi fails to boot, mount the boot FAT partition on another computer,\n"
        "then restore config.txt, cmdline.txt and initramfs8 from this folder\n"
        "to the root of that SAME boot partition. Keep this folder.\n"
        "Original SHA-256 values for these three files:\n"
        + "".join(f"{original[key]['sha256']}  {Path(FILES[key]).name}\n"
                  for key in ("config", "cmdline", "initramfs"))
        + f"Backup ID: {backup_id}\n"
    ).encode()
    _write_new(rescue / "RECOVERY.txt", instructions, 0o644, set_mode=False)
    return backup_id, directory, rescue


def install_theme(ctx, assets):
    target = ctx.path(THEME_REL)
    if target.exists() or target.is_symlink():
        ctx.check_dir(target, owner=ctx.system_owner)
        extras = {p.name for p in target.iterdir()} - set(THEME_FILES)
        if extras:
            raise Refusal(f"Unexpected files in existing theme directory: {sorted(extras)}")
    else:
        os.mkdir(target, 0o755)
    for name in THEME_FILES:
        path = target / name
        expected = assets["theme/" + name]
        if path.exists() or path.is_symlink():
            current = ctx.read(path, owner=ctx.system_owner, max_size=16 * 1024 * 1024)
            if current != expected:
                raise Refusal(f"Existing theme file differs: {path}")
        else:
            _write_new(path, expected, 0o644)


def run_command(ctx, args, report, timeout=600):
    ctx.check_file(Path(args[0]), owner=ctx.system_owner, executable=True)
    env = os.environ.copy()
    if not ctx.fixture:
        env.pop("JUNSHU_BOOT_ROOT", None)
        env.pop("JUNSHU_BOOT_LOGO_FIXTURE", None)
        env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    command = {"argv": args}
    report["commands"].append(command)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=env, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        command.update({"returncode": process.returncode, "stdout": stdout[-4000:],
                        "stderr": stderr[-4000:], "timeout": True})
        raise Refusal(f"Command timed out: {args[0]}") from exc
    except KeyboardInterrupt as exc:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        command.update({"returncode": process.returncode, "stdout": stdout[-4000:],
                        "stderr": stderr[-4000:], "interrupted": True})
        raise Refusal(f"Command interrupted: {args[0]}") from exc
    command.update({"returncode": process.returncode, "stdout": stdout[-4000:],
                    "stderr": stderr[-4000:]})
    if process.returncode != 0:
        raise Refusal(f"Command failed ({process.returncode}): {args[0]}")
    return stdout


def check_initramfs_entries(output):
    entries = {line.strip().lstrip("./") for line in output.splitlines()}
    required = {THEME_REL + "/" + name for name in THEME_FILES}
    missing = required - entries
    if missing:
        raise Refusal(f"Generated initramfs lacks theme files: {sorted(missing)}")
    if "usr/sbin/plymouthd" not in entries:
        raise Refusal("Generated initramfs lacks plymouthd")
    if not any(item.endswith("/plymouth/script.so") for item in entries):
        raise Refusal("Generated initramfs lacks Plymouth script plugin")


def current_hashes(ctx):
    return {key: ctx.digest(ctx.path(relative), owner=ctx.system_owner)
            for key, relative in FILES.items()}


def check_matches(current, expected, label):
    for key in FILES:
        if (current[key]["sha256"] != expected[key]["sha256"] or
                current[key]["mode"] != expected[key]["mode"]):
            raise Refusal(f"{label} conflict: {FILES[key]} bytes or mode changed independently")


def check_installed_theme(ctx, state):
    target = ctx.path(THEME_REL)
    ctx.check_dir(target, owner=ctx.system_owner)
    for name, digest in state["theme_sha256"].items():
        if ctx.digest(target / name, owner=ctx.system_owner)["sha256"] != digest:
            raise Refusal(f"Installed theme changed: {name}")


def verify_backups(ctx, state):
    directory = backup_dir(ctx, state["backup_id"])
    ctx.check_dir(directory, owner=ctx.system_owner)
    for key, relative in FILES.items():
        path = directory / relative
        saved = ctx.digest(path, owner=ctx.system_owner)
        if saved["sha256"] != state["original"][key]["sha256"] or saved["mode"] != state["original"][key]["mode"]:
            raise Refusal(f"Retained backup differs from recorded original: {relative}")
    rescue = rescue_dir(ctx, state["backup_id"])
    ctx.check_dir(rescue, owner=ctx.system_owner)
    for key in ("config", "cmdline", "initramfs"):
        saved = ctx.digest(rescue / Path(FILES[key]).name, owner=ctx.system_owner)
        if saved["sha256"] != state["original"][key]["sha256"]:
            raise Refusal(f"Boot-partition recovery copy differs: {FILES[key]}")
    return directory


def restore_originals(ctx, state, tolerate_partial=False):
    directory = verify_backups(ctx, state)
    current = current_hashes(ctx)
    if not tolerate_partial:
        check_matches(current, state["enabled"], "Rollback")
    else:
        for key in FILES:
            allowed = {state["original"][key]["sha256"]}
            if key in state.get("enabled", {}):
                allowed.add(state["enabled"][key]["sha256"])
            if key == "plymouth" and "candidate_plymouth_sha256" in state:
                allowed.add(state["candidate_plymouth_sha256"])
            if (current[key]["sha256"] not in allowed or
                    current[key]["mode"] != state["original"][key]["mode"]):
                raise Refusal(f"Recovery conflict: {FILES[key]} changed independently")
    # Restore boot image first, so a partial rollback never points to a missing image.
    for key in ("initramfs", "plymouth", "cmdline", "config"):
        path = ctx.path(FILES[key])
        if ctx.digest(path, owner=ctx.system_owner)["sha256"] != state["original"][key]["sha256"]:
            _overwrite(ctx, path, source=directory / FILES[key])
    check_matches(current_hashes(ctx), state["original"], "Restoration")
    return directory


def action_probe(ctx, report):
    state = load_state(ctx)
    if state and state["status"] in ("enabled", "enabling", "rolling_back"):
        if state["status"] == "enabled":
            check_platform(ctx)
            check_boot_mapping(ctx, check_initrd=False)
            check_matches(current_hashes(ctx), state["enabled"], "Managed installation")
            check_installed_theme(ctx, state)
            verify_backups(ctx, state)
            check_predecessor(ctx, state)
            report.update({"ok": True, "status": "enabled", "backup_dir": str(backup_dir(ctx, state["backup_id"])),
                           "rescue_dir": str(rescue_dir(ctx, state["backup_id"]))})
            return
        raise Refusal(f"Managed operation is incomplete ({state['status']}); run rollback")
    info, _, _, predecessor = preflight(ctx)
    report.update({"ok": True, "status": "ready", "platform": info,
                   "predecessor": predecessor["kind"] if predecessor else None})


def action_enable(ctx, args, report):
    assets = validate_package(args.bundle, args.expected_manifest_sha256)
    state = load_state(ctx)
    if state and state["status"] == "enabled":
        check_platform(ctx)
        check_boot_mapping(ctx, check_initrd=False)
        if state.get("package_sha256") != args.expected_manifest_sha256:
            raise Refusal("Different bundle from active managed installation")
        check_matches(current_hashes(ctx), state["enabled"], "Managed installation")
        check_installed_theme(ctx, state)
        verify_backups(ctx, state)
        check_predecessor(ctx, state)
        report.update({"ok": True, "status": "enabled", "changed": False,
                       "backup_dir": str(backup_dir(ctx, state["backup_id"])),
                       "rescue_dir": str(rescue_dir(ctx, state["backup_id"]))})
        return
    if state and state["status"] in ("enabling", "rolling_back"):
        raise Refusal(f"Managed operation is incomplete ({state['status']}); run rollback")
    info, original, desired, predecessor = preflight(ctx)
    backup_id, directory, rescue = create_backup(ctx, original)
    report["backup_dir"] = str(directory)
    report["rescue_dir"] = str(rescue)
    state = {"schema": 1, "status": "enabling", "backup_id": backup_id,
             "original": original, "enabled": {}, "platform": info,
             "package_sha256": args.expected_manifest_sha256,
             "predecessor": predecessor,
             "candidate_plymouth_sha256": sha(desired["plymouth"]),
             "theme_sha256": {name: sha(assets["theme/" + name]) for name in THEME_FILES}}
    save_state(ctx, state)
    try:
        install_theme(ctx, assets)
        _overwrite(ctx, ctx.path(FILES["plymouth"]), data=desired["plymouth"])
        stage = directory / "initramfs8.generated"
        run_command(ctx, [str(ctx.path("usr/sbin/mkinitramfs")), "-o", str(stage), KERNEL], report)
        generated = ctx.digest(stage, owner=ctx.system_owner)
        if generated["size"] == 0:
            raise Refusal("mkinitramfs produced an empty image")
        listing = run_command(ctx, [str(ctx.path("usr/bin/lsinitramfs")), str(stage)], report)
        check_initramfs_entries(listing)
        if shutil.disk_usage(ctx.path("boot/firmware")).free < generated["size"] + 16 * 1024 * 1024:
            raise Refusal("Insufficient boot space for atomically staged generated initramfs")
        enabled = {
            "config": {"sha256": sha(desired["config"]), "size": len(desired["config"]),
                       "mode": original["config"]["mode"]},
            "cmdline": {"sha256": sha(desired["cmdline"]), "size": len(desired["cmdline"]),
                        "mode": original["cmdline"]["mode"]},
            "plymouth": {"sha256": sha(desired["plymouth"]), "size": len(desired["plymouth"]),
                         "mode": original["plymouth"]["mode"]},
            "initramfs": {"sha256": generated["sha256"], "size": generated["size"],
                          "mode": original["initramfs"]["mode"]},
        }
        state["enabled"] = enabled
        save_state(ctx, state)
        _overwrite(ctx, ctx.path(FILES["initramfs"]), source=stage)
        _overwrite(ctx, ctx.path(FILES["cmdline"]), data=desired["cmdline"])
        _overwrite(ctx, ctx.path(FILES["config"]), data=desired["config"])
        check_matches(current_hashes(ctx), enabled, "Install verification")
        state["status"] = "enabled"
        save_state(ctx, state)
        report.update({"ok": True, "status": "enabled", "changed": True,
                       "platform": info, "initramfs_sha256": generated["sha256"]})
    except Exception as exc:
        report["errors"].append(str(exc))
        try:
            # A failed builder leaves theme and backup available, with original
            # boot bytes restored. No file or fixture is removed.
            for key in ("initramfs", "plymouth", "cmdline", "config"):
                path = ctx.path(FILES[key])
                if ctx.digest(path, owner=ctx.system_owner)["sha256"] != original[key]["sha256"]:
                    _overwrite(ctx, path, source=directory / FILES[key])
            check_matches(current_hashes(ctx), original, "Failed-install restoration")
            state["status"] = "failed_restored"
            save_state(ctx, state)
            report["restored_originals"] = True
        except Exception as recovery_exc:
            report["errors"].append("Automatic restoration failed: " + str(recovery_exc))
            report["restored_originals"] = False
        raise Refusal("Enable failed; inspect retained backup and command report") from exc


def action_verify(ctx, report):
    state = load_state(ctx)
    if not state or state["status"] != "enabled":
        raise Refusal("No active managed installation to verify")
    check_platform(ctx)
    check_boot_mapping(ctx, check_initrd=False)
    check_matches(current_hashes(ctx), state["enabled"], "Managed installation")
    check_installed_theme(ctx, state)
    check_predecessor(ctx, state)
    directory = verify_backups(ctx, state)
    listing = run_command(ctx, [str(ctx.path("usr/bin/lsinitramfs")), str(ctx.path(FILES["initramfs"]))], report)
    check_initramfs_entries(listing)
    report.update({"ok": True, "status": "enabled", "backup_dir": str(directory),
                   "rescue_dir": str(rescue_dir(ctx, state["backup_id"])),
                   "initramfs_sha256": state["enabled"]["initramfs"]["sha256"]})


def action_rollback(ctx, report):
    state = load_state(ctx)
    if not state:
        report.update({"ok": True, "status": "no_op", "changed": False})
        return
    if state["status"] in ("rolled_back", "failed_restored"):
        check_matches(current_hashes(ctx), state["original"], "Already restored")
        report.update({"ok": True, "status": "rolled_back", "changed": False,
                       "backup_dir": str(backup_dir(ctx, state["backup_id"])),
                       "rescue_dir": str(rescue_dir(ctx, state["backup_id"]))})
        return
    if state["status"] not in ("enabled", "enabling", "rolling_back"):
        raise Refusal(f"Unknown managed state: {state['status']}")
    check_platform(ctx)
    check_boot_mapping(ctx, check_initrd=False)
    check_predecessor(ctx, state)
    # All four current files and retained originals are checked before writing.
    directory = verify_backups(ctx, state)
    current = current_hashes(ctx)
    for key in FILES:
        allowed = {state["original"][key]["sha256"]}
        if key in state.get("enabled", {}):
            allowed.add(state["enabled"][key]["sha256"])
        if key == "plymouth":
            allowed.add(state.get("candidate_plymouth_sha256"))
        if (current[key]["sha256"] not in allowed or
                current[key]["mode"] != state["original"][key]["mode"]):
            raise Refusal(f"Rollback conflict: {FILES[key]} changed independently")
    state["status"] = "rolling_back"
    save_state(ctx, state)
    restore_originals(ctx, state, tolerate_partial=True)
    state["status"] = "rolled_back"
    save_state(ctx, state)
    report.update({"ok": True, "status": "rolled_back", "changed": True,
                   "backup_dir": str(directory),
                   "rescue_dir": str(rescue_dir(ctx, state["backup_id"])),
                   "theme_retained": True})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "enable", "verify", "rollback"))
    parser.add_argument("--root", default="/", help="Offline fixture root; real operation only uses /")
    parser.add_argument("--bundle", default=str(Path(__file__).resolve().parent),
                        help="Local verified bundle for enable")
    parser.add_argument("--expected-manifest-sha256", help="Trusted Mac SHA-256 of MANIFEST.sha256")
    args = parser.parse_args(argv)
    report = {"action": args.action, "ok": False, "status": "blocked",
              "changed": False, "errors": [], "commands": []}
    try:
        ctx = Context(args.root)
        if args.action == "probe":
            action_probe(ctx, report)
        elif args.action == "enable":
            with mutation_lock(ctx):
                action_enable(ctx, args, report)
        elif args.action == "verify":
            action_verify(ctx, report)
        else:
            with mutation_lock(ctx):
                action_rollback(ctx, report)
    except Refusal as exc:
        if str(exc) not in report["errors"]:
            report["errors"].append(str(exc))
    except (OSError, KeyError, ValueError) as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc))
        report["status"] = "error"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
