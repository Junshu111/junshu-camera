import unittest, os, stat
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from camera_r4 import gpio_probe
from camera_r4.gpio_probe import GPIO17Input, pin_is_input, inspect_chips


class ProbeTests(unittest.TestCase):
    def setUp(self):
        # Fake only the device descriptor boundary: no GPIO hardware on the Mac.
        self.fd_stat=patch('camera_r4.gpio_probe.os.fstat',return_value=NS(st_mode=stat.S_IFCHR,st_rdev=os.makedev(254,0)))
        self.fd_stat.start();self.addCleanup(self.fd_stat.stop)
    def test_pinmux_parser_rejects_output_alternate_or_missing_evidence(self):
        for text in ['17: ip pu | hi // GPIO17 = input','GPIO 17: level=1 fsel=0 func=INPUT pull=UP']:
            self.assertTrue(pin_is_input(text),text)
        for text in ['', '17: op dh | hi // GPIO17 = output','17: a3 pn | hi // SPI1_CE1_N',
                     'GPIO 17: level=1 fsel=1 func=OUTPUT pull=NONE','27: ip pu | hi']:
            self.assertFalse(pin_is_input(text),text)
    def fake(self,used=False,label='pinctrl-bcm2835',name='GPIO17'):
        chip=Mock();chip.fd=77;chip.get_info.return_value=NS(name='gpiochip0',label=label,num_lines=54)
        chip.get_line_info.return_value=NS(name=name,used=used,consumer='other' if used else None,direction=NS(name='INPUT'))
        module=Mock();module.Chip.return_value=chip
        module.line=NS(Direction=NS(INPUT='INPUT'),Bias=NS(PULL_UP='UP'),Value=NS(ACTIVE=1,INACTIVE=0))
        module.request_lines.return_value.get_value.return_value=1
        return module,chip
    def test_readonly_scan_never_requests_a_line_and_blocks_consumers(self):
        mod,chip=self.fake(used=True)
        rows=inspect_chips(mod,['/dev/gpiochip0'])
        self.assertTrue(rows[0]['used']);self.assertEqual(rows[0]['consumer'],'other')
        mod.request_lines.assert_not_called();chip.request_lines.assert_not_called();chip.close.assert_called_once()
    def test_mapping_must_match_known_bcm_chip_and_line_name(self):
        for label,name in [('other','GPIO17'),('pinctrl-bcm2835','LED')]:
            mod,_=self.fake(label=label,name=name)
            self.assertFalse(inspect_chips(mod,['/dev/gpiochip0'])[0]['mapping_ok'])
    def test_claim_is_input_pullup_active_low_and_released(self):
        mod,_=self.fake()
        line=GPIO17Input(module=mod,probe=lambda:{'errors':[],'chip':'/dev/gpiochip0'})
        settings=mod.LineSettings.call_args.kwargs
        self.assertEqual(settings,{'direction':'INPUT','bias':'UP','active_low':True})
        self.assertEqual(set(mod.request_lines.call_args.kwargs['config']),{17})
        self.assertTrue(line.read());line.close()
        mod.request_lines.return_value.release.assert_called_once()
    def test_unknown_or_conflicting_pin_fails_before_claim(self):
        mod,_=self.fake()
        with self.assertRaises(RuntimeError):GPIO17Input(module=mod,probe=lambda:{'errors':['occupied']})
        mod.request_lines.assert_not_called()
    def test_kernel_claim_race_is_not_ignored(self):
        mod,_=self.fake();mod.request_lines.side_effect=OSError('Device or resource busy')
        with self.assertRaises(OSError):GPIO17Input(module=mod,probe=lambda:{'errors':[],'chip':'/dev/gpiochip0'})

    def test_same_device_aliases_are_one_candidate(self):
        mod,_=self.fake()
        rows=inspect_chips(mod,['/dev/gpiochip0','/dev/gpiochip4'])
        select=getattr(gpio_probe,'select_chip',None)
        self.assertIsNotNone(select,'needs identity-based selection, not path count')
        chosen=select(rows)
        self.assertEqual(chosen['path'],'/dev/gpiochip0')
        self.assertEqual(chosen['aliases'],['/dev/gpiochip0','/dev/gpiochip4'])
        self.assertEqual(chosen['device_major'],254)
    def test_same_labels_on_distinct_devices_remain_ambiguous(self):
        mod,_=self.fake()
        with patch('camera_r4.gpio_probe.os.fstat',side_effect=[
            NS(st_mode=stat.S_IFCHR,st_rdev=os.makedev(254,0)),
            NS(st_mode=stat.S_IFCHR,st_rdev=os.makedev(254,4))]):
            rows=inspect_chips(mod,['/dev/gpiochip0','/dev/gpiochip4'])
        select=getattr(gpio_probe,'select_chip',None);self.assertIsNotNone(select)
        with self.assertRaisesRegex(RuntimeError,'唯一'):select(rows)
    def test_alias_observations_cannot_hide_a_consumer(self):
        mod,chip=self.fake()
        chip.get_line_info.side_effect=[NS(name='GPIO17',used=False,consumer=None,direction=NS(name='INPUT')),
            NS(name='GPIO17',used=True,consumer='another-app',direction=NS(name='INPUT'))]
        rows=inspect_chips(mod,['/dev/gpiochip0','/dev/gpiochip4'])
        select=getattr(gpio_probe,'select_chip',None);self.assertIsNotNone(select)
        with self.assertRaises(RuntimeError):select(rows)
    def test_alias_with_changed_mapping_cannot_be_filtered_out_as_unrelated(self):
        mod,chip=self.fake()
        chip.get_line_info.side_effect=[NS(name='GPIO17',used=False,consumer=None,direction=NS(name='INPUT')),
            NS(name='LED',used=True,consumer='another-app',direction=NS(name='OUTPUT'))]
        rows=inspect_chips(mod,['/dev/gpiochip0','/dev/gpiochip4'])
        with self.assertRaises(RuntimeError):gpio_probe.select_chip(rows)
    def test_non_character_device_or_unreadable_identity_is_not_accepted(self):
        mod,_=self.fake()
        for value in [NS(st_mode=stat.S_IFREG,st_rdev=0),OSError('cannot stat device')]:
            with patch('camera_r4.gpio_probe.os.fstat',side_effect=value if isinstance(value,Exception) else None,return_value=value):
                rows=inspect_chips(mod,['/dev/gpiochip0'])
            self.assertFalse(rows[0]['mapping_ok'])
            self.assertIn('error',rows[0])

if __name__=='__main__':unittest.main()
