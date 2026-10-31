"""GPIO3 safety tests: hardware boundaries substituted; all fixtures retained."""
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

try:
    power = importlib.import_module('camera_r4.power_gpio')
except ModuleNotFoundError:
    power = None

QA = Path(__file__).resolve().parents[2] / 'qa/r4-power-20261025'


def fixture(prefix):
    QA.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=QA))


class PowerGPIOTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(power, 'missing standalone GPIO3 safety implementation')
        self.fd = patch.object(power.os, 'fstat', return_value=NS(st_mode=stat.S_IFCHR, st_rdev=os.makedev(42, 0)))
        self.fd.start()
        self.addCleanup(self.fd.stop)

    def module(self, used=False, direction='INPUT', name='GPIO3', label='pinctrl-bcm2835'):
        chip = Mock()
        chip.fd = 77
        chip.get_info.return_value = NS(name='gpiochip0', label=label, num_lines=54)
        chip.get_line_info.return_value = NS(name=name, used=used, consumer='other' if used else None, direction=NS(name=direction))
        mod = Mock()
        mod.Chip.return_value = chip
        mod.line = NS(Direction=NS(INPUT='INPUT'), Bias=NS(PULL_UP='UP'), Value=NS(ACTIVE=1, INACTIVE=0))
        mod.request_lines.return_value.get_value.return_value = 1
        return mod, chip

    def test_aliases_identified_by_opened_character_device_number(self):
        mod, chip = self.module()
        chosen = power.select_chip(power.inspect_chips(mod, ['/dev/gpiochip0', '/dev/gpiochip4']))
        self.assertEqual(chosen['aliases'], ['/dev/gpiochip0', '/dev/gpiochip4'])
        self.assertEqual(chosen['device_major'], 42)
        chip.get_line_info.assert_called_with(3)
        mod.request_lines.assert_not_called()

    def test_identical_labels_on_distinct_devices_are_ambiguous(self):
        mod, _ = self.module()
        with patch.object(power.os, 'fstat', side_effect=[NS(st_mode=stat.S_IFCHR, st_rdev=os.makedev(42, n)) for n in (0, 4)]):
            rows = power.inspect_chips(mod, ['/dev/gpiochip0', '/dev/gpiochip4'])
        with self.assertRaises(RuntimeError):
            power.select_chip(rows)

    def test_alias_inconsistent_mapping_and_state_are_rejected(self):
        for name, used, direction in [('GPIO3', True, 'INPUT'), ('LED', False, 'INPUT'), ('GPIO3', False, 'OUTPUT')]:
            mod, chip = self.module()
            chip.get_line_info.side_effect = [NS(name='GPIO3', used=False, consumer=None, direction=NS(name='INPUT')),
                                             NS(name=name, used=used, consumer='other' if used else None, direction=NS(name=direction))]
            with self.assertRaises(RuntimeError):
                power.select_chip(power.inspect_chips(mod, ['/dev/gpiochip0', '/dev/gpiochip4']))

    def test_used_output_wrong_mapping_or_unknown_device_fail_closed(self):
        for kwargs in [{'used': True}, {'direction': 'OUTPUT'}, {'direction': 'UNKNOWN'}, {'name': 'SCL1'}, {'label': 'other'}]:
            mod, _ = self.module(**kwargs)
            with self.assertRaises(RuntimeError):
                power.select_chip(power.inspect_chips(mod, ['/dev/gpiochip0']))
        mod, _ = self.module()
        with patch.object(power.os, 'fstat', return_value=NS(st_mode=stat.S_IFREG, st_rdev=0)):
            rows = power.inspect_chips(mod, ['/dev/gpiochip0'])
        with self.assertRaises(RuntimeError):
            power.select_chip(rows)

    def test_unknown_used_flag_and_inconsistent_consumer_are_rejected(self):
        for used, consumer in [(None, None), (False, 'stray-owner')]:
            mod, chip = self.module()
            chip.get_line_info.return_value = NS(name='GPIO3', used=used, consumer=consumer, direction=NS(name='INPUT'))
            with self.assertRaises(RuntimeError):
                power.select_chip(power.inspect_chips(mod, ['/dev/gpiochip0']))

    def test_unreadable_chip_cannot_be_discarded_as_unrelated(self):
        mod, _ = self.module()
        mod.Chip.side_effect = OSError('denied')
        with self.assertRaises(RuntimeError):
            power.select_chip(power.inspect_chips(mod, ['/dev/gpiochip0']))

    def test_pinmux_needs_explicit_gpio3_input(self):
        for text in ['3: ip pu | hi // GPIO3 = input', 'GPIO 3: level=1 fsel=0 func=INPUT pull=UP']:
            self.assertTrue(power.pin_is_input(text))
        for text in ['', '3: op dh | hi', '3: a0 pn | hi // SCL1', 'GPIO 3: level=1 fsel=4 func=SCL1 pull=UP', '17: ip pu | hi']:
            self.assertFalse(power.pin_is_input(text))

    def tree(self, i2c_status='disabled', alias=True):
        root = fixture('gpio-dt-')
        (root / 'model').write_bytes(b'Raspberry Pi 3 Model B Plus Rev 1.3\0')
        (root / 'aliases').mkdir()
        soc = root / 'soc'
        soc.mkdir()
        (soc / '#address-cells').write_bytes(b'\0\0\0\1')
        (soc / '#size-cells').write_bytes(b'\0\0\0\1')
        controller = soc / 'i2c@7e804000'
        controller.mkdir()
        (controller / 'status').write_text(i2c_status)
        (controller / 'compatible').write_bytes(b'brcm,bcm2835-i2c\0')
        (controller / 'reg').write_bytes(bytes.fromhex('7e80400000001000'))
        if alias:
            (root / 'aliases/i2c1').write_bytes(b'/soc/i2c@7e804000\0')
        return root

    def test_runtime_dt_detects_active_poweroff_and_ignores_disabled_node(self):
        root = self.tree()
        node = root / 'poweroff'
        node.mkdir()
        (node / 'compatible').write_bytes(b'gpio-poweroff\0')
        self.assertTrue(power.device_tree_evidence(root)['conflicts'])
        (node / 'status').write_bytes(b'disabled\0')
        self.assertEqual(power.device_tree_evidence(root)['conflicts'], [])

    def test_active_i2c1_is_blocked_even_if_current_pinmux_looks_input(self):
        evidence = power.device_tree_evidence(self.tree(i2c_status='okay'))
        self.assertTrue(evidence['i2c']['i2c1']['active'])
        self.assertTrue(evidence['conflicts'])
        self.assertFalse(self.report(dt=evidence)['runtime_check_passed'])

    def test_missing_alias_cannot_hide_active_real_i2c1(self):
        evidence = power.device_tree_evidence(self.tree(i2c_status='okay', alias=False))
        self.assertTrue(evidence['errors'])
        self.assertFalse(self.report(dt=evidence)['runtime_check_passed'])

    def test_missing_alias_with_disabled_real_i2c1_still_fails_unknown(self):
        evidence = power.device_tree_evidence(self.tree(alias=False))
        self.assertTrue(evidence['errors'])
        self.assertFalse(self.report(dt=evidence)['runtime_check_passed'])

    def test_alias_to_unrelated_disabled_node_cannot_mask_active_real_i2c1(self):
        root = self.tree(i2c_status='okay')
        unrelated = root / 'soc/unrelated'
        unrelated.mkdir()
        (unrelated / 'status').write_text('disabled')
        (root / 'aliases/i2c1').write_bytes(b'/soc/unrelated\0')
        evidence = power.device_tree_evidence(root)
        self.assertTrue(evidence['errors'])
        self.assertFalse(self.report(dt=evidence)['runtime_check_passed'])

    def test_disabled_i2c1_identity_with_active_csi_dsi_bus_is_allowed(self):
        root = self.tree()
        # The Waveshare overlay enables i2c0if + i2c0mux + i2c_csi_dsi.
        controller = root / 'soc/i2c@7e205000'
        controller.mkdir()
        (controller / 'compatible').write_bytes(b'brcm,bcm2835-i2c\0')
        (controller / 'status').write_text('okay')
        mux = root / 'soc/i2c0mux'
        mux.mkdir()
        (mux / 'compatible').write_bytes(b'i2c-mux-pinctrl\0')
        (mux / 'status').write_text('okay')
        dsi = mux / 'i2c@1'
        dsi.mkdir()
        (dsi / 'status').write_text('okay')
        (root / 'aliases/i2c10').write_bytes(b'/soc/i2c0mux/i2c@1\0')
        evidence = power.device_tree_evidence(root)
        self.assertEqual(evidence['errors'], [])
        self.assertEqual(evidence['conflicts'], [])
        self.assertTrue(self.report(dt=evidence)['runtime_check_passed'])

    def test_expected_i2c1_path_with_wrong_compatible_or_reg_is_unknown(self):
        for prop, value in [('compatible', b'other,i2c\0'), ('reg', bytes.fromhex('7e20500000001000')),
                            ('reg', bytes.fromhex('7e80400000000001'))]:
            root = self.tree()
            (root / 'soc/i2c@7e804000' / prop).write_bytes(value)
            evidence = power.device_tree_evidence(root)
            self.assertTrue(evidence['errors'], (prop, value))
            self.assertFalse(self.report(dt=evidence)['runtime_check_passed'])

    def test_soc_cell_layout_and_related_alias_disagreement_fail_unknown(self):
        root = self.tree()
        (root / 'soc/#address-cells').write_bytes(b'\0\0\0\2')
        self.assertTrue(power.device_tree_evidence(root)['errors'])
        root = self.tree()
        (root / 'aliases/i2c').write_bytes(b'/soc/i2c@7e205000\0')
        self.assertTrue(power.device_tree_evidence(root)['errors'])

    def test_dt_unknown_status_and_unreadable_root_are_not_clearance(self):
        missing = fixture('gpio-empty-') / 'missing'
        self.assertTrue(power.device_tree_evidence(missing)['errors'])
        root = self.tree()
        node = root / 'poweroff'
        node.mkdir()
        (node / 'compatible').write_bytes(b'gpio-poweroff\0')
        (node / 'status').write_text('mystery')
        self.assertTrue(power.device_tree_evidence(root)['errors'])

    def test_active_poweroff_under_disabled_parent_is_inactive(self):
        root = self.tree()
        node = root / 'disabled-bus/poweroff'
        node.mkdir(parents=True)
        (node.parent / 'status').write_text('disabled')
        (node / 'compatible').write_bytes(b'gpio-poweroff\0')
        self.assertFalse(power.device_tree_evidence(root)['conflicts'])

    def test_i2c_alias_missing_target_and_path_escape_fail_closed(self):
        for target in ['/soc/missing', '/../other']:
            root = self.tree()
            (root / 'aliases/i2c1').write_text(target)
            self.assertTrue(power.device_tree_evidence(root)['errors'])

    def test_dt_gpio_shutdown_on_gpio3_is_conflict(self):
        root = self.tree()
        node = root / 'gpio-shutdown/button'
        node.mkdir(parents=True)
        (node.parent / 'compatible').write_bytes(b'gpio-keys\0')
        (node / 'gpios').write_bytes(b'\0\0\0\1\0\0\0\3\0\0\0\1')
        self.assertTrue(power.device_tree_evidence(root)['conflicts'])

    def report(self, mod=None, model='Raspberry Pi 3 Model B Plus Rev 1.3', pin='3: ip pu | hi', dt=None):
        mod = mod or self.module()[0]
        chosen = {'path': '/dev/gpiochip0'}
        dt = dt or {'errors': [], 'conflicts': [], 'i2c': {}, 'active_overlays': []}
        with patch.object(power, 'read_model', return_value=model), patch.object(power.importlib, 'import_module', return_value=mod), \
                patch.object(power, 'inspect_chips', return_value=[]), patch.object(power, 'select_chip', return_value=chosen), \
                patch.object(power.os, 'access', return_value=True), patch.object(power.shutil, 'which', return_value='/usr/bin/pinctrl'), \
                patch.object(power, 'command', return_value={'code': 0, 'stdout': pin, 'stderr': ''}), \
                patch.object(power, 'device_tree_evidence', return_value=dt):
            return power.inspect_power_gpio(details=False)

    def test_readonly_report_passes_known_state_without_claiming_line(self):
        mod, _ = self.module()
        report = self.report(mod)
        self.assertTrue(report['runtime_check_passed'], report['errors'])
        self.assertEqual(report['bcm'], 3)
        self.assertEqual(report['physical_signal'], 5)
        self.assertTrue(report['read_only'])
        json.dumps(report)
        mod.request_lines.assert_not_called()

    def test_report_rejects_nonpi_altfunc_and_active_conflicts(self):
        self.assertFalse(self.report(model='Mac')['runtime_check_passed'])
        self.assertFalse(self.report(pin='3: a0 pn | hi // SCL1')['runtime_check_passed'])
        for dt in [{'errors': ['unknown'], 'conflicts': [], 'i2c': {}}, {'errors': [], 'conflicts': ['gpio-poweroff'], 'i2c': {}}]:
            self.assertFalse(self.report(dt=dt)['runtime_check_passed'])

    def test_missing_library_and_unsuccessful_pinmux_fail(self):
        with patch.object(power.importlib, 'import_module', side_effect=ImportError('no gpiod')), \
                patch.object(power, 'read_model', return_value='Raspberry Pi 3 Model B Plus'), \
                patch.object(power.shutil, 'which', return_value=None), \
                patch.object(power, 'device_tree_evidence', return_value={'errors': [], 'conflicts': [], 'i2c': {}}):
            report = power.inspect_power_gpio(False)
        self.assertFalse(report['runtime_check_passed'])
        self.assertGreaterEqual(len(report['errors']), 2)

    def test_runtime_claim_only_bcm3_input_pullup_active_low(self):
        mod, _ = self.module()
        line = power.GPIO3Input(module=mod, probe=lambda: {'runtime_check_passed': True, 'errors': [], 'chip': '/dev/gpiochip0'})
        self.assertEqual(mod.LineSettings.call_args.kwargs, {'direction': 'INPUT', 'bias': 'UP', 'active_low': True})
        self.assertEqual(set(mod.request_lines.call_args.kwargs['config']), {3})
        self.assertTrue(line.read())
        mod.request_lines.return_value.get_value.return_value = 0
        self.assertFalse(line.read())
        mod.request_lines.return_value.get_value.return_value = 9
        with self.assertRaises(RuntimeError):
            line.read()
        line.close()
        line.close()
        mod.request_lines.return_value.release.assert_called_once()
        with self.assertRaises(RuntimeError):
            line.read()

    def test_runtime_unknown_clearance_and_occupied_race_rejected(self):
        mod, _ = self.module()
        for report in [{'errors': []}, {'runtime_check_passed': False, 'errors': []}, {'runtime_check_passed': True, 'errors': ['occupied']}]:
            with self.assertRaises(RuntimeError):
                power.GPIO3Input(module=mod, probe=lambda: report)
        mod.request_lines.assert_not_called()
        mod.request_lines.side_effect = OSError('busy')
        with self.assertRaises(OSError):
            power.GPIO3Input(module=mod, probe=lambda: {'runtime_check_passed': True, 'errors': [], 'chip': '/dev/gpiochip0'})


class WrapperTests(unittest.TestCase):
    def test_ssh_streams_standalone_probe_and_retains_reports(self):
        root = Path(__file__).resolve().parents[2]
        wrapper = root / '核查开关机按钮GPIO.command'
        self.assertTrue(wrapper.exists(), 'missing read-only wrapper')
        work = fixture('power-wrapper-')
        shutil.copy2(wrapper, work / 'probe.command')
        source = work / 'pi-r4-power/camera_r4/power_gpio.py'
        source.parent.mkdir(parents=True)
        source.write_text('# standalone fixture\n')
        binary = work / 'bin'
        binary.mkdir()
        ssh = binary / 'ssh'
        ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FIXTURE/arguments"\ncat > "$FIXTURE/stream"\nprintf \'{"fixture":true}\\n\'\nexit "${SSH_STATUS:-0}"\n')
        ssh.chmod(0o755)
        env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], FIXTURE=str(work))
        for status in ('0', '2'):
            env['SSH_STATUS'] = status
            result = subprocess.run(['bash', str(work / 'probe.command')], input='junshu@fixture.invalid\n', text=True, capture_output=True, env=env, timeout=5)
            self.assertEqual(result.returncode, int(status), result.stdout + result.stderr)
        self.assertIn('StrictHostKeyChecking=yes', (work / 'arguments').read_text())
        self.assertIn('/usr/bin/python3 -I -B -', (work / 'arguments').read_text())
        self.assertEqual((work / 'stream').read_text(), '# standalone fixture\n')
        self.assertEqual(len(list((work / 'qa/r4-power-20261025/device-reports').iterdir())), 2)
        result = subprocess.run(['bash', str(work / 'probe.command')], input='-oProxyCommand=bad\n', text=True, capture_output=True, env=env, timeout=5)
        self.assertEqual(result.returncode, 2)

    def test_single_file_exec_has_no_package_dependency_and_never_claims_on_mac(self):
        source = Path(__file__).resolve().parents[1] / 'camera_r4/power_gpio.py'
        self.assertTrue(source.exists(), 'missing standalone GPIO3 probe')
        result = subprocess.run(['python3', '-I', '-B', '-'], input=source.read_text(), capture_output=True, text=True, timeout=15)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report['runtime_check_passed'])
        self.assertTrue(report['read_only'])


if __name__ == '__main__':
    unittest.main()
