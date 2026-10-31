"""Standalone, read-only BCM3 preflight and explicit exclusive input adapter.

This file can be streamed to system Python over SSH without installing a package.
Only constructing GPIO3Input requests a line. No code writes boot configuration,
changes pinmux, disables I2C, releases another owner, or drives an output.
"""
import importlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=4)
        return {'command': args, 'code': result.returncode,
                'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'command': args, 'code': -1, 'stdout': '', 'stderr': str(error)}


def read_model():
    try:
        return Path('/proc/device-tree/model').read_text().rstrip('\0')
    except OSError:
        return 'unknown'


def pin_is_input(text):
    return bool(re.search(r'^\s*3:\s+ip\b', text, re.M) or
                re.search(r'^GPIO\s+3:.*\bfsel=0\s+func=INPUT\b', text, re.M))


def inspect_chips(module, paths):
    rows = []
    for path in paths:
        chip = None
        try:
            chip = module.Chip(str(path))
            info = chip.get_info()
            identity = os.fstat(chip.fd)
            if not stat.S_ISCHR(identity.st_mode):
                raise RuntimeError('GPIO控制器不是字符设备')
            if info.num_lines <= 3:
                raise RuntimeError('GPIO控制器未提供GPIO3偏移，无法排除设备歧义')
            line = chip.get_line_info(3)
            rows.append({'path': str(path), 'realpath': str(Path(path).resolve()),
                         'device_major': os.major(identity.st_rdev), 'device_minor': os.minor(identity.st_rdev),
                         'chip_name': info.name, 'label': info.label, 'name': line.name,
                         'used': line.used, 'consumer': line.consumer, 'direction': line.direction.name,
                         'mapping_ok': info.label == 'pinctrl-bcm2835' and line.name == 'GPIO3'})
        except Exception as error:
            rows.append({'path': str(path), 'error': str(error), 'mapping_ok': False})
        finally:
            if chip is not None:
                chip.close()
    return rows


def select_chip(rows):
    if any(row.get('error') for row in rows):
        raise RuntimeError('部分GPIO设备身份或状态未知，不能排除歧义')
    candidates = [row for row in rows if row.get('mapping_ok')]
    identities = {(row['device_major'], row['device_minor']) for row in candidates}
    if len(identities) != 1:
        raise RuntimeError('无法唯一确认GPIO3对应的pinctrl-bcm2835控制器（按真实设备号）')
    candidates = [row for row in rows if (row['device_major'], row['device_minor']) in identities]
    if any(not row['mapping_ok'] for row in candidates):
        raise RuntimeError('同一设备别名的GPIO3映射不一致')
    observations = {(row['chip_name'], row['label'], row['name'], row['used'], row['consumer'], row['direction'])
                    for row in candidates}
    if len(observations) != 1:
        raise RuntimeError('GPIO3别名状态变化或不一致，请重查')
    chosen = dict(candidates[0])
    chosen['aliases'] = [row['path'] for row in candidates]
    chosen['path'] = chosen['realpath']
    if type(chosen['used']) is not bool or (not chosen['used'] and chosen['consumer']):
        raise RuntimeError('GPIO3占用标记未知或与consumer不一致')
    if chosen['used']:
        raise RuntimeError('GPIO3已被占用：' + str(chosen['consumer']))
    if chosen['direction'] != 'INPUT':
        raise RuntimeError('GPIO3当前不是已确认的输入')
    return chosen


def device_tree_evidence(root=Path('/proc/device-tree')):
    """Inspect the *live* tree, not just possibly conditional boot-file text."""
    root = Path(root)
    evidence = {'root': str(root), 'errors': [], 'conflicts': [], 'i2c': {}, 'active_overlays': []}
    errors, conflicts = evidence['errors'], evidence['conflicts']
    if not root.is_dir():
        errors.append('无法读取运行中的设备树，不能确认I2C/overlay状态')
        return evidence

    def active(node):
        # A child beneath a disabled bus/overlay is also disabled.
        current = node
        while True:
            status = current / 'status'
            try:
                value = status.read_bytes().rstrip(b'\0').decode('ascii')
            except FileNotFoundError:
                value = 'okay'  # DT specification: missing status means okay.
            except (OSError, UnicodeError) as error:
                errors.append(str(status) + ': ' + str(error))
                return False
            if value in ('disabled', 'reserved', 'fail') or value.startswith('fail-'):
                return False
            if value not in ('ok', 'okay'):
                errors.append(str(status) + ': 未知status ' + value)
                return False
            if current == root:
                return True
            current = current.parent

    def walk_error(error):
        errors.append('设备树遍历失败：' + str(error))

    for directory, _, filenames in os.walk(root, onerror=walk_error):
        node = Path(directory)
        if 'compatible' in filenames:
            try:
                compatible = node.joinpath('compatible').read_bytes().rstrip(b'\0').decode('ascii').split('\0')
                enabled = active(node)
                if enabled and 'gpio-poweroff' in compatible:
                    conflicts.append('活动gpio-poweroff不兼容GPIO3原生唤醒：' + str(node.relative_to(root)))
                if enabled and 'gpio-keys' in compatible:
                    for child_directory, _, child_files in os.walk(node, onerror=walk_error):
                        child = Path(child_directory)
                        if 'gpios' not in child_files or not active(child):
                            continue
                        raw = child.joinpath('gpios').read_bytes()
                        if len(raw) < 12 or len(raw) % 4:
                            errors.append('无法解释gpio-keys的gpios属性：' + str(child))
                        elif int.from_bytes(raw[4:8], 'big') == 3:
                            conflicts.append('活动gpio-keys/gpio-shutdown使用GPIO3：' + str(child.relative_to(root)))
            except (OSError, UnicodeError) as error:
                errors.append('设备树属性读取失败：' + str(error))

    # Pi3B+ primary DT: bcm283x.dtsi /soc/i2c@7e804000, compatible
    # brcm,bcm2835-i2c, reg=<0x7e804000 0x1000>, one address/size cell.
    # bcm270x-rpi.dtsi supplies aliases/i2c1. Inspect the real controller
    # independently: an absent/redirected alias must never conceal its state.
    expected = '/soc/i2c@7e804000'
    node = root / expected.lstrip('/')
    i2c1 = {'path': expected, 'identity_verified': False, 'active': None, 'aliases': {}}
    evidence['i2c']['i2c1'] = i2c1
    try:
        if not node.is_dir():
            raise ValueError('Pi3B+ I2C1控制器节点不存在')
        for prop in ('#address-cells', '#size-cells'):
            if (node.parent / prop).read_bytes() != b'\0\0\0\1':
                raise ValueError('I2C1父总线' + prop + '不是已确认的单cell布局')
        compatible = (node / 'compatible').read_bytes().rstrip(b'\0').decode('ascii').split('\0')
        raw_reg = (node / 'reg').read_bytes()
        i2c1['compatible'] = compatible
        i2c1['reg_hex'] = raw_reg.hex()
        if 'brcm,bcm2835-i2c' not in compatible:
            raise ValueError('I2C1 compatible不是已确认的BCM2835 I2C控制器')
        if raw_reg != bytes.fromhex('7e80400000001000'):
            raise ValueError('I2C1 reg与Pi3B+固定控制器地址/范围不符')
        i2c1['identity_verified'] = True
        i2c1['active'] = active(node)
        if i2c1['active']:
            conflicts.append('活动I2C1可能使用GPIO2/3，请先核对现有用途')
    except (OSError, UnicodeError, ValueError) as error:
        errors.append('I2C1控制器身份/状态未知：' + str(error))

    # i2c1 is mandatory for this Pi3B+-only probe. Related aliases, if
    # supplied, must agree too. CSI/DSI aliases such as i2c10 are unrelated.
    for name in ('i2c1', 'i2c', 'i2c_arm'):
        try:
            target = (root / 'aliases' / name).read_bytes().rstrip(b'\0').decode('ascii')
            i2c1['aliases'][name] = target
            if name == 'i2c1':
                i2c1['alias_present'] = True
            if target != expected:
                raise ValueError(name + '别名未指向已确认的Pi3B+ I2C1控制器')
        except FileNotFoundError:
            if name == 'i2c1':
                i2c1['alias_present'] = False
                errors.append('缺少必需的i2c1别名，I2C1状态不能授权通过')
        except (OSError, UnicodeError, ValueError) as error:
            errors.append('I2C1别名状态未知：' + str(error))

    overlay_root = root / 'chosen/overlays'
    if overlay_root.is_dir():
        try:
            evidence['active_overlays'] = sorted(p.name for p in overlay_root.iterdir())
            for name in evidence['active_overlays']:
                if re.search(r'gpio-(poweroff|shutdown)', name, re.I):
                    conflicts.append('运行中overlay冲突：' + name)
        except OSError as error:
            errors.append('活动overlay列表不可读：' + str(error))
    return evidence


def inspect_power_gpio(details=True):
    report = {'probe_version': '0.3.0-pwr.1', 'bcm': 3, 'physical_signal': 5,
              'physical_gnd': 6, 'read_only': True, 'errors': [], 'chip': None, 'selected_chip': None,
              'model': read_model(), 'system': platform.platform(), 'python': platform.python_version(),
              'uid': os.getuid(), 'pinmux': []}
    errors = report['errors']
    if not report['model'].startswith('Raspberry Pi 3 Model B Plus'):
        errors.append('未确认Pi 3B+型号，停止GPIO3接入')
    try:
        module = importlib.import_module('gpiod')
        report['gpiod'] = getattr(module, '__version__', 'unknown')
        if not hasattr(module, 'LineSettings') or not hasattr(module, 'request_lines'):
            raise RuntimeError('需要系统Python的libgpiod 2.x接口')
        report['chips'] = inspect_chips(module, sorted(Path('/dev').glob('gpiochip*')))
        chosen = select_chip(report['chips'])
        report['chip'], report['selected_chip'] = chosen['path'], chosen
        if not os.access(chosen['path'], os.R_OK | os.W_OK):
            errors.append('当前用户没有GPIO设备读写权限；此预检不会申请GPIO')
    except Exception as error:
        errors.append('GPIO库/设备检查失败：' + str(error))
    confirmed_pinmux = False
    for tool in ('pinctrl', 'raspi-gpio'):
        executable = shutil.which(tool)
        if executable:
            result = command([executable, 'get', '3'])
            report['pinmux'].append(result)
            if result['code'] == 0:
                confirmed_pinmux = True
                if not pin_is_input(result['stdout']):
                    errors.append('GPIO3复用/方向不是已确认的普通输入（可能为I2C SCL1）')
                break
    if not confirmed_pinmux:
        errors.append('无法读取GPIO3实际复用状态，需要成功的pinctrl/raspi-gpio只读结果')
    evidence = device_tree_evidence()
    report['device_tree'] = evidence
    report['i2c'] = evidence.get('i2c', {})
    report['active_overlays'] = evidence.get('active_overlays', [])
    errors.extend(evidence['errors'])
    errors.extend(evidence['conflicts'])
    if details:
        report['groups'] = command(['id'])
        report['os_release'] = command(['cat', '/etc/os-release'])
        report['boot_gpio_config'] = {}
        for filename in ('/boot/firmware/config.txt', '/boot/config.txt'):
            try:
                report['boot_gpio_config'][filename] = [line for line in Path(filename).read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#') and
                    re.search(r'gpio|dtoverlay|dtparam|include|^\[', line, re.I)]
            except OSError as error:
                report['boot_gpio_config'][filename] = {'unreadable': str(error)}
        report['overlay_listing'] = command(['dtoverlay', '-l'])
        report['active_services'] = command(['systemctl', 'list-units', '--type=service', '--state=running', '--no-pager', '--no-legend'])
        report['process_names'] = command(['ps', '-eo', 'pid,comm'])
    report['manual_review'] = ('仅确认本次软件可见状态；启动配置可能含条件与include，不能据此改配置。'
        '仍需C审阅、D确认接点，正常关机并断开全部供电后接线。'
        '无法排除绕过内核的直接GPIO访问；原生短按唤醒与待机耗电待实机验证。')
    report['runtime_check_passed'] = not errors
    return report


class GPIO3Input:
    """Exclusive active-low BCM3 INPUT; construction is the only claim path."""
    def __init__(self, module=None, probe=None):
        self.request = None
        report = (probe or (lambda: inspect_power_gpio(details=False)))()
        if not report.get('runtime_check_passed') or report.get('errors') or not report.get('chip'):
            raise RuntimeError('GPIO3预检未通过：' + '；'.join(report.get('errors', ['状态未知'])))
        self.module = module or importlib.import_module('gpiod')
        self.request = self.module.request_lines(report['chip'], consumer='junshu-camera-power', config={
            3: self.module.LineSettings(direction=self.module.line.Direction.INPUT,
                bias=self.module.line.Bias.PULL_UP, active_low=True)})

    def read(self):
        if self.request is None:
            raise RuntimeError('GPIO3输入已关闭')
        value = self.request.get_value(3)
        if value not in (self.module.line.Value.ACTIVE, self.module.line.Value.INACTIVE):
            raise RuntimeError('GPIO3返回未知电平')
        return value == self.module.line.Value.ACTIVE

    def close(self):
        if self.request is not None:
            self.request.release()
            self.request = None


def main():
    report = inspect_power_gpio()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['runtime_check_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
