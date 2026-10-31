"""Replay the user's Pi report; keep all fixture directories for audit."""
import json
from pathlib import Path
import tempfile
import unittest

from test_system_features import ROOT, SYSTEM
import test_system_features as fixtures

QA = ROOT/'qa/r4-probe-compat-20261026'
RECORDED = json.loads((QA/'fixtures/device-parser-inputs.json').read_text())


class ProbeDeviceCompatibility(unittest.TestCase):
    put = fixtures.SystemFeatures.put

    def setUp(self):
        fixtures = QA/'test-runs'
        fixtures.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=fixtures))

    def model(self, value=RECORDED['model']):
        self.put('proc/device-tree/model', value+'\0')

    def recorded_tree(self):
        self.model()
        for row in RECORDED['device_tree']['inputs']:
            for name, value in row['properties_hex'].items():
                self.put('proc/device-tree/'+row['node'].lstrip('/')+'/'+name,
                         bytes.fromhex(value))

    def test_recorded_pi3_config_excludes_pi5_only_overlay(self):
        self.model()
        for row in RECORDED['boot']['inputs']:
            path = self.put(row['path'].lstrip('/'), row['content'])
            self.assertEqual(SYSTEM.sha(path.read_bytes()), row['sha256'])
        result = SYSTEM.read_boot_tree(self.root)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(result['inputs'], RECORDED['boot']['inputs'])
        ignored = result['ignored_for_model']
        self.assertTrue(any(row['filter']=='[pi5]' and
                            row['directive']=='dtoverlay=nospi10' for row in ignored))

    def test_pi3_plus_all_unknown_filters_still_detect_conflicts(self):
        self.model()
        for section in ('[pi3]', '[pi3+]', '[all]', '[unknown-model]',
                        '[board-type=0x10]', '[none]', '[gpio4=1]'):
            with self.subTest(section=section):
                self.put('boot/firmware/config.txt', '[pi5]\ngpio=22=op,dh\n'+
                         section+'\ndtoverlay=gpio-led,gpio=22\n')
                result = SYSTEM.read_boot_tree(self.root)
                self.assertEqual(result['errors'], [])
                self.assertTrue(any('dtoverlay=gpio-led,gpio=22' in s
                                    for s in result['conflicts']))

    def test_missing_or_different_model_does_not_exclude_pi5(self):
        self.put('boot/firmware/config.txt', '[pi5]\ndtoverlay=nospi10\n')
        self.assertTrue(SYSTEM.read_boot_tree(self.root)['conflicts'])
        for model in ('unknown', 'Raspberry Pi 5 Model B Rev 1.0'):
            with self.subTest(model=model):
                self.model(model)
                self.assertTrue(SYSTEM.read_boot_tree(self.root)['conflicts'])

    def test_includes_are_checked_and_invalidate_model_exclusion(self):
        self.model()
        self.put('boot/firmware/config.txt', '[pi5]\ninclude extra.txt\ngpio=22=op,dh\n')
        self.put('boot/firmware/extra.txt', '[all]\ndtoverlay=gpio-led,gpio=22\n')
        result = SYSTEM.read_boot_tree(self.root)
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['inputs']), 2)
        self.assertTrue(any('gpio=22=op,dh' in s for s in result['conflicts']))
        self.assertTrue(any('dtoverlay=gpio-led,gpio=22' in s for s in result['conflicts']))

    def test_excluded_section_does_not_hide_bad_include(self):
        self.model()
        self.put('boot/firmware/config.txt', '[pi5]\ninclude ../escape.txt\n')
        self.assertTrue(SYSTEM.read_boot_tree(self.root)['errors'])

    def test_recorded_dt_path_metadata_is_not_gpio_cells(self):
        self.recorded_tree()
        result = SYSTEM.live_dt(self.root)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['conflicts'], [])
        original = {r['node']: r['properties_hex'] for r in RECORDED['device_tree']['inputs']}
        self.assertEqual({r['node']: r['properties_hex'] for r in result['inputs']}, original)
        for row in result['inputs']:
            if row['node'] in ('/aliases', '/__symbols__'):
                self.assertEqual(row['kind'], 'path_metadata')

    def test_real_gpio22_claim_still_blocks_with_path_metadata_present(self):
        self.recorded_tree()
        self.put('proc/device-tree/test-device/gpios', bytes.fromhex('000000070000001600000000'))
        result = SYSTEM.live_dt(self.root)
        self.assertEqual(result['errors'], [])
        self.assertIn('active candidate GPIO22 property: /test-device/gpios', result['conflicts'])

    def test_malformed_real_gpio_property_is_not_ignored(self):
        self.model()
        # Only the two root metadata nodes are special; a similarly named
        # hardware node must still fail closed for malformed GPIO cells.
        self.put('proc/device-tree/soc/aliases/gpio', b'/soc/gpio@7e200000\0')
        self.assertTrue(SYSTEM.live_dt(self.root)['errors'])

    def test_empty_pin_lists_are_valid_but_partial_cells_refuse(self):
        self.model()
        self.put('proc/device-tree/uart/pinctrl-0', b'')
        self.put('proc/device-tree/gpio/uart-pins/brcm,pins', b'')
        self.assertEqual(SYSTEM.live_dt(self.root)['errors'], [])
        self.put('proc/device-tree/uart/pinctrl-0', bytes.fromhex('00000013'))
        self.put('proc/device-tree/gpio/uart-pins/phandle', bytes.fromhex('00000013'))
        self.assertEqual(SYSTEM.live_dt(self.root)['errors'], [])
        self.put('proc/device-tree/gpio/uart-pins/brcm,pins', b'bad')
        self.assertTrue(SYSTEM.live_dt(self.root)['errors'])
        self.put('proc/device-tree/gpio/uart-pins/brcm,pins', bytes.fromhex('00000016'))
        self.assertTrue(SYSTEM.live_dt(self.root)['conflicts'])

    def test_empty_gpio_reference_still_refuses(self):
        self.model()
        self.put('proc/device-tree/led/gpios', b'')
        self.assertTrue(SYSTEM.live_dt(self.root)['errors'])


if __name__ == '__main__':
    unittest.main()
