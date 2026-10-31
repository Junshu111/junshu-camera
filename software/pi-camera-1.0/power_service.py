#!/usr/bin/env python3
"""Privileged entry. Run only the protected installed copy with python3 -I -B."""
import logging
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import sys
import threading
import types

INSTALL_ROOT = Path('/opt/junshu-camera-power-0.3.0-pwr.1')


def notify_ready():
    """Notify systemd from its tracked main process after GPIO and IPC exist."""
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return False  # manual protected invocation has no service manager
    if address.startswith('@') and len(address) > 1:
        address = '\0' + address[1:]
    elif not address.startswith('/'):
        raise ValueError('invalid systemd notify endpoint')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.settimeout(2)
        channel.connect(address)
        channel.sendall(b'READY=1\nSTATUS=GPIO3 input and camera IPC initialized')
    return True


def validate_install():
    if os.geteuid() != 0:
        raise PermissionError('root service account required')
    if sys.platform != 'linux' or not sys.flags.isolated or not sys.dont_write_bytecode:
        raise PermissionError('Linux /usr/bin/python3 -I -B required')
    entry = Path(__file__).absolute()
    if entry != INSTALL_ROOT / 'power_service.py':
        raise PermissionError('execute the installed /opt copy only')
    paths = [Path('/'), Path('/opt'), INSTALL_ROOT]
    for root, dirs, files in os.walk(INSTALL_ROOT, followlinks=False):
        paths.extend(Path(root) / name for name in dirs + files)
    for path in paths:
        info = path.lstat()
        if (info.st_uid != 0 or info.st_mode & 0o022 or
                not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
            raise PermissionError('untrusted installed path: ' + str(path))
    for name in ('power_service.py', 'power_client.py', 'power_indicator.py', 'power_gpio.py', 'gpio_probe.py'):
        if not (INSTALL_ROOT / 'camera_r4' / name).is_file():
            raise PermissionError('missing protected service module: ' + name)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    gpio = None
    service = None
    try:
        validate_install()
        if argv not in ([], ['--user', 'junshu']):
            raise ValueError('only --user junshu is supported')
        account = pwd.getpwnam('junshu')
        if account.pw_uid <= 0 or account.pw_dir != '/home/junshu':
            raise PermissionError('junshu account UID/home mismatch')
        # -I excludes cwd/PYTHONPATH/user site. Bind the package explicitly so a
        # same-named regular system package cannot replace this namespace package.
        package = types.ModuleType('camera_r4')
        package.__path__ = [str(INSTALL_ROOT / 'camera_r4')]
        package.__package__ = 'camera_r4'
        sys.modules['camera_r4'] = package
        from camera_r4.power_gpio import GPIO3Input
        from camera_r4.power_service import PowerService
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(name)s %(message)s')
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        gpio = GPIO3Input()
        service = PowerService(gpio= gpio, allowed_uid=account.pw_uid, stop_requested=stop.is_set)
        if stop.is_set():
            return 0
        notify_ready()
        logging.info('power service started; GPIO3 input; optional LED disabled')
        while not stop.is_set():
            service.step()
            stop.wait(.005)
        return 0
    except Exception as error:
        print('Power service refused/failed: ' + str(error), file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()
        elif gpio is not None:
            gpio.close()


if __name__ == '__main__':
    sys.exit(main())
