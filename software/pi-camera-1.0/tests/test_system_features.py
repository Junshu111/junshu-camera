"""Behavioral system integration tests; fixture directories are deliberately retained."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('pi_next_system', ROOT/'tools/pi_next_system.py')
SYSTEM = None
if SPEC and SPEC.origin and Path(SPEC.origin).exists():
    SYSTEM = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(SYSTEM)
QA = ROOT/'qa/r4-integrated-20261026/system-fixtures'


class SystemFeatures(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SYSTEM, 'standalone system tool must exist')
        QA.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=QA))
        self.uid = os.getuid()

    def put(self, name, data, mode=0o644):
        p = self.root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        p.chmod(mode)
        return p

    def row(self, **kw):
        row = dict(path='/dev/gpiochip0', device_major=254, device_minor=0,
                   chip_name='gpiochip0', label='pinctrl-bcm2835', name='GPIO22',
                   used=False, consumer='', direction='INPUT', mapping_ok=True)
        row.update(kw)
        return row

    def boot(self):
        return self.put('boot/firmware/config.txt', b'# keep DSI\r\ndtoverlay=vc4-kms-v3d\r\ninclude display.txt\r\n[pi3]\r\ngpio=17=ip,pu\r\n')

    def good(self):
        return {'led_ready': True, 'errors': [], 'gpio22': {'ready': True}}

    def desktop(self, route='xdg'):
        return {'ready': True, 'route': route, 'errors': [], 'session': {'Name':'junshu', 'Active':'yes', 'Remote':'no', 'Type':'wayland'},
                'autologin': True}

    def test_gpio_free_input_and_same_device_aliases_pass(self):
        report = SYSTEM.inspect_gpio22([self.row(), self.row(path='/dev/gpiochip512')], '22: ip pu | hi // GPIO22 = input')
        self.assertTrue(report['ready'])
        self.assertEqual(report['selected']['aliases'], ['/dev/gpiochip0','/dev/gpiochip512'])

    def test_gpio_unsafe_unknown_alias_output_and_alt_refuse(self):
        cases = ([self.row(used=True, consumer='existing')], [self.row(used=None)],
                 [self.row(label='other', mapping_ok=False)],
                 [self.row(), self.row(path='/dev/gpiochip512', direction='OUTPUT')],
                 [self.row(direction='OUTPUT')], [self.row(error='permission denied')])
        for rows in cases:
            with self.subTest(rows=rows):
                self.assertFalse(SYSTEM.inspect_gpio22(rows, '22: ip pu | hi')['ready'])
        self.assertFalse(SYSTEM.inspect_gpio22([self.row()], '22: a0 // SD1_CLK')['ready'])

    def test_boot_nested_conditional_inputs_retained_and_conflict_blocks(self):
        self.boot()
        self.put('boot/firmware/display.txt', '[all]\ninclude nested.txt\n')
        p = self.put('boot/firmware/nested.txt', '[none]\ndtoverlay=gpio-led,gpio=22\n')
        report = SYSTEM.read_boot_tree(self.root)
        self.assertEqual(len(report['inputs']), 3)
        self.assertEqual(report['inputs'][-1]['sha256'], hashlib.sha256(p.read_bytes()).hexdigest())
        self.assertTrue(report['conflicts'])
        self.assertIn('[none]', report['inputs'][-1]['content'])

    def test_missing_escaping_and_cycle_include_refuse(self):
        for text in ('include absent.txt\n','include ../outside.txt\n','include config.txt\n'):
            self.put('boot/firmware/config.txt', text)
            self.assertTrue(SYSTEM.read_boot_tree(self.root)['errors'])

    def test_led_enable_preserves_bytes_backup_modes_idempotent_and_disable(self):
        p = self.boot(); self.put('boot/firmware/display.txt', 'display_auto_detect=1\n')
        before = p.read_bytes(); p.chmod(0o640)
        out = SYSTEM.led_config('enable', self.root, self.good(), True, self.uid)
        self.assertTrue(p.read_bytes().startswith(before))
        self.assertIn(b'[all]\ndtoverlay=gpio-led,gpio=22,label=junshu-white,active_low=0,trigger=default-on\n',p.read_bytes())
        backup = Path(out['backup'])/'config.txt'
        self.assertEqual(backup.read_bytes(),before)
        self.assertEqual(backup.stat().st_mode & 0o777,0o640)
        enabled = p.read_bytes()
        self.assertFalse(SYSTEM.led_config('enable',self.root,{'led_lifecycle':{'state':'staged','enable_noop':True}},False,self.uid)['changed'])
        disabled=SYSTEM.led_config('disable',self.root,None,False,self.uid)
        self.assertTrue(disabled['reboot_required'])
        self.assertEqual(p.read_bytes(),before)
        self.assertEqual((Path(disabled['backup'])/'config.txt').read_bytes(),enabled)

    def test_led_guards_leave_no_partial_config_edits(self):
        p=self.boot(); self.put('boot/firmware/display.txt','dtoverlay=gpio-led,gpio=22\n'); before=p.read_bytes()
        for evidence,reviewed in ((self.good(),False),({'led_ready':False},True),(self.good(),True)):
            with self.assertRaises(ValueError): SYSTEM.led_config('enable',self.root,evidence,reviewed,self.uid)
            self.assertEqual(p.read_bytes(),before)
        self.put('boot/firmware/display.txt','')
        p.write_bytes(before+b'# BEGIN JUNSHU GPIO22 unknown\n')
        with self.assertRaises(ValueError): SYSTEM.led_config('enable',self.root,self.good(),True,self.uid)


    def test_nested_managed_led_block_cannot_create_second_declaration(self):
        p=self.boot()
        self.put('boot/firmware/display.txt',b'display_auto_detect=1\n'+SYSTEM.LED_BLOCK)
        before=p.read_bytes()
        with self.assertRaises(ValueError): SYSTEM.led_config('enable',self.root,self.good(),True,self.uid)
        self.assertEqual(p.read_bytes(),before)

    def test_readonly_report_redacts_private_startup_values_and_gpio_lists_block(self):
        p=self.boot(); self.put('boot/firmware/display.txt','gpio=21,22=op,dh\n')
        self.assertTrue(SYSTEM.read_boot_tree(self.root)['conflicts'])
        self.put('home/junshu/.config/labwc/autostart','SSID=PRIVATE-NET\nTOKEN=PRIVATE-TOKEN\n/bin/sh /home/junshu/camera/start.sh --file PRIVATE-PHOTO.jpg\n')
        report=SYSTEM.startup_inputs(self.root,self.uid)
        output=str(report)
        self.assertNotIn('PRIVATE-NET',output)
        self.assertNotIn('PRIVATE-TOKEN',output)
        self.assertNotIn('PRIVATE-PHOTO',output)

    def test_root_and_group_writable_destination_refuse(self):
        p=self.boot(); self.put('boot/firmware/display.txt','')
        p.parent.chmod(0o775)
        with self.assertRaises(ValueError): SYSTEM.led_config('enable',self.root,self.good(),True,self.uid)

    def release(self):
        files={'check_package.py':b'raise RuntimeError("must not execute checker as root or during direct hash verification")\n',
               'start-autostart.sh':b'#!/bin/sh\nexec /bin/sh /home/junshu/junshu-camera-pi-0.4.0/start-camera.sh\n',
               'start-camera.sh':b'#!/bin/sh\nexit 0\n'}
        for name,data in files.items(): self.put('home/junshu/junshu-camera-pi-0.4.0/'+name,data)
        manifest=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in files.items())
        self.put('home/junshu/junshu-camera-pi-0.4.0/MANIFEST.sha256',manifest)
        return hashlib.sha256(manifest.encode()).hexdigest()

    def test_autostart_xdg_enable_disable_retains_backup_and_does_not_execute(self):
        digest=self.release()
        out=SYSTEM.autostart_config('enable',self.root,self.desktop(),digest,self.uid)
        p=self.root/'home/junshu/.config/autostart/junshu-camera-r4-integrated.desktop'
        self.assertIn(b'Terminal=false\n',p.read_bytes())
        self.assertIn(b'Exec=/bin/sh /home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh\n',p.read_bytes())
        self.assertFalse(out['started'])
        self.assertFalse(SYSTEM.autostart_config('enable',self.root,self.desktop(),digest,self.uid)['changed'])
        before=p.read_bytes()
        out=SYSTEM.autostart_config('disable',self.root,None,None,self.uid)
        self.assertIn(b'Hidden=true\n',p.read_bytes())
        self.assertEqual((Path(out['backup'])/'junshu-camera-r4-integrated.desktop').read_bytes(),before)
        self.assertFalse(out['started'])

    def test_autostart_unknown_desktop_manifest_or_entry_refuses_all_writes(self):
        digest=self.release()
        directory=self.root/'home/junshu/.config/autostart'
        cases=({'ready':False},self.desktop())
        for evidence in cases:
            with self.assertRaises(ValueError): SYSTEM.autostart_config('enable',self.root,evidence,'0'*64,self.uid)
        p=self.put('home/junshu/.config/autostart/old-camera.desktop','[Desktop Entry]\nExec=/home/junshu/camera/start.sh\n')
        with self.assertRaises(ValueError): SYSTEM.autostart_config('enable',self.root,self.desktop(),digest,self.uid)
        self.assertEqual(p.read_text(),'[Desktop Entry]\nExec=/home/junshu/camera/start.sh\n')
        self.assertFalse((directory/'junshu-camera-r4-integrated.desktop').exists())

    def test_known_power_entry_neutralized_with_backup_only(self):
        digest=self.release()
        p=self.put('home/junshu/.config/autostart/junshu-camera-r4-power.desktop',SYSTEM.LEGACY_POWER)
        before=p.read_bytes()
        out=SYSTEM.autostart_config('enable',self.root,self.desktop(),digest,self.uid)
        self.assertIn(b'Hidden=true\n',p.read_bytes())
        self.assertEqual((Path(out['backup'])/p.name).read_bytes(),before)

    def test_labwc_hook_preserves_system_defaults_and_exact_disable(self):
        digest=self.release()
        defaults=b'# default panel\nwf-panel-pi &\n'
        self.put('etc/xdg/labwc/autostart',defaults,0o755)
        out=SYSTEM.autostart_config('enable',self.root,self.desktop('labwc'),digest,self.uid)
        p=self.root/'home/junshu/.config/labwc/autostart'
        self.assertTrue(p.read_bytes().startswith(defaults))
        self.assertFalse((self.root/'home/junshu/.config/autostart/junshu-camera-r4-integrated.desktop').exists())
        SYSTEM.autostart_config('disable',self.root,None,None,self.uid)
        self.assertEqual(p.read_bytes(),defaults)

    def test_release_hash_mismatch_and_symlink_config_refuse(self):
        digest=self.release()
        self.put('home/junshu/junshu-camera-pi-0.4.0/start-autostart.sh','changed')
        with self.assertRaises(ValueError): SYSTEM.verify_release(self.root,digest,self.uid)
        p=self.put('actual.txt','[all]\n'); target=self.root/'boot/firmware/config.txt'
        target.parent.mkdir(parents=True); target.symlink_to(p)
        with self.assertRaises(ValueError): SYSTEM.led_config('disable',self.root,None,False,self.uid)


if __name__=='__main__': unittest.main()

class LinuxEvidence(unittest.TestCase):
    setUp = SystemFeatures.setUp
    put = SystemFeatures.put
    def linux(self, session_type='wayland', compositor='labwc', login='junshu'):
        self.put('proc/device-tree/model',b'Raspberry Pi 3 Model B Plus Rev 1.3\0')
        self.put('proc/device-tree/soc/gpio@7e200000/compatible',b'brcm,bcm2835-gpio\0')
        self.put('proc/device-tree/soc/gpio@7e200000/phandle',bytes.fromhex('00000007'))
        self.put('proc/device-tree/soc/gpio@7e200000/#gpio-cells',bytes.fromhex('00000002'))
        self.put('proc/device-tree/soc/gpio@7e200000/unused-pins/brcm,pins',bytes.fromhex('00000016'))
        self.put('etc/X11/default-display-manager','/usr/sbin/lightdm\n')
        self.put('etc/lightdm/lightdm.conf','[Seat:*]\nautologin-user='+login+'\nuser-session=LXDE-pi-labwc\n')
        self.put('etc/xdg/labwc/autostart','# panel\nwf-panel-pi &\n')
        self.put('boot/firmware/overlays/gpio-led.dtbo',b'\xd0\x0d\xfe\xed'+b'complete external FDT boundary fixture')
        self.put('boot/firmware/overlays/README','Name: gpio-led\nInfo: GPIO LED\nLoad: dtoverlay=gpio-led,<param>=<val>\nParams: gpio                 GPIO number\n        label                LED label\n        active_low           Active low\n        trigger              Trigger\n\nName: gpio-shutdown\n')
        self.put('boot/config-fixture-kernel','CONFIG_LEDS_GPIO=y\nCONFIG_LEDS_TRIGGER_DEFAULT_ON=m\n')
        def runner(args):
            if args==['loginctl','list-sessions','--no-legend','--no-pager']:
                out='2 1000 junshu seat0 1851 user tty1 no -'
            elif args[:2]==['loginctl','show-session']:
                out='Name=junshu\nActive=yes\nRemote=no\nType='+session_type+'\nClass=user\nDesktop=LXDE-pi-labwc\nSeat=seat0'
            elif args[:2]==['ps','-u']:
                out=compositor+'\nwf-panel-pi'
            else:
                return {'command':args,'code':127,'stdout':'','stderr':'not in named Linux evidence fixture'}
            return {'command':args,'code':0,'stdout':out,'stderr':''}
        return runner

    def test_supported_labwc_desktop_and_unknown_login_compositor_refuse(self):
        runner=self.linux()
        result=SYSTEM.desktop_probe(self.root,runner,self.uid)
        self.assertTrue(result['ready'],result)
        self.assertEqual(result['route'],'labwc')
        result=SYSTEM.desktop_probe(self.root,self.linux(compositor='sway'),self.uid)
        self.assertFalse(result['ready'])
        result=SYSTEM.desktop_probe(self.root,self.linux(login=''),self.uid)
        self.assertFalse(result['ready'])
        result=SYSTEM.desktop_probe(self.root,self.linux(session_type='tty'),self.uid)
        self.assertFalse(result['ready'])


    def test_autologin_must_match_actual_session_seat(self):
        runner=self.linux()
        for config in ('[Seat:seat1]\nautologin-user=junshu\n',
                       '[Seat:*]\nautologin-user=junshu\n[Seat:seat0]\nautologin-user=\n',
                       '[Seat:seat?]\nautologin-user=junshu\n'):
            with self.subTest(config=config):
                self.put('etc/lightdm/lightdm.conf',config)
                result=SYSTEM.desktop_probe(self.root,runner,self.uid)
                self.assertFalse(result['ready'],result)
                self.assertFalse(result['autologin'],result)
        self.put('etc/lightdm/lightdm.conf','[Seat:seat1]\nautologin-user=other\n[Seat:seat0]\nautologin-user=junshu\n')
        result=SYSTEM.desktop_probe(self.root,runner,self.uid)
        self.assertTrue(result['ready'],result)
        self.assertEqual(result['session']['Seat'],'seat0')

    def test_unreachable_labwc_hook_refuses_before_any_legacy_entry_change(self):
        digest=SystemFeatures.release(self)
        for location in ('home/junshu/.config/labwc/autostart','etc/xdg/labwc/autostart'):
            for control in ('exit 0','exec /usr/bin/wf-panel-pi'):
                with self.subTest(location=location,control=control):
                    # Each subcase uses a fresh retained root, so absent-user-hook
                    # and existing-user-hook paths remain independently exercised.
                    self.setUp()
                    digest=SystemFeatures.release(self)
                    runner=self.linux()
                    data=('#!/bin/sh\n'+control+'\n').encode()
                    p=self.put(location,data)
                    legacy=self.put('home/junshu/.config/autostart/junshu-camera-r4-power.desktop',SYSTEM.LEGACY_POWER)
                    result=SYSTEM.desktop_probe(self.root,runner,self.uid)
                    with self.subTest(boundary='probe'):
                        self.assertFalse(result['ready'],result)
                    # Direct helper also protects against stale probe evidence.
                    evidence={'ready':True,'route':'labwc'}
                    with self.assertRaises(ValueError):
                        SYSTEM.autostart_config('enable',self.root,evidence,digest,self.uid)
                    self.assertEqual(p.read_bytes(),data)
                    self.assertEqual(legacy.read_text(),SYSTEM.LEGACY_POWER)
                    self.assertFalse((self.root/'home/junshu/.config/autostart/junshu-camera-r4-integrated.desktop').exists())
                    self.assertFalse((self.root/'home/junshu/.local/state/junshu-camera/system-backups').exists())

    def test_standard_proc_device_tree_alias_is_supported(self):
        self.put('sys/firmware/devicetree/base/model',b'Raspberry Pi 3 Model B Plus Rev 1.3\0')
        p=self.root/'proc/device-tree'; p.parent.mkdir(parents=True,exist_ok=True)
        p.symlink_to(self.root/'sys/firmware/devicetree/base',target_is_directory=True)
        result=SYSTEM.live_dt(self.root)
        self.assertFalse(result['errors'],result)

    def test_kernel_local_readme_and_actual_support_required(self):
        self.linux()
        report=SYSTEM.kernel_led_support(self.root,'fixture-kernel')
        self.assertTrue(report['ready'],report)
        self.put('boot/config-fixture-kernel','# CONFIG_LEDS_TRIGGER_DEFAULT_ON is not set\nCONFIG_LEDS_GPIO=y\n')
        self.assertFalse(SYSTEM.kernel_led_support(self.root,'fixture-kernel')['ready'])
        self.put('boot/firmware/overlays/README','Name: gpio-led\nParams: gpio                 GPIO\n')
        self.assertFalse(SYSTEM.kernel_led_support(self.root,'fixture-kernel')['ready'])

    def test_dt_unused_pin_description_is_not_active_owner_but_selected_pins_block(self):
        self.linux()
        report=SYSTEM.live_dt(self.root)
        self.assertFalse(report['errors'],report)
        self.assertFalse(report['conflicts'],report)
        self.put('proc/device-tree/soc/gpio@7e200000/unused-pins/phandle',bytes.fromhex('0000000a'))
        self.put('proc/device-tree/soc/peripheral/status',b'okay\0')
        self.put('proc/device-tree/soc/peripheral/pinctrl-0',bytes.fromhex('0000000a'))
        self.assertTrue(SYSTEM.live_dt(self.root)['conflicts'])

    def test_dt_gpio22_owner_poweroff_and_malformed_cells_block(self):
        self.linux()
        self.put('proc/device-tree/leds/white/gpios',bytes.fromhex('000000070000001600000000'))
        self.assertTrue(SYSTEM.live_dt(self.root)['conflicts'])
        self.put('proc/device-tree/leds/status',b'disabled\0')
        self.assertFalse(SYSTEM.live_dt(self.root)['conflicts'])
        self.put('proc/device-tree/power/compatible',b'gpio-poweroff\0')
        self.assertTrue(SYSTEM.live_dt(self.root)['conflicts'])
        self.put('proc/device-tree/power/gpios',b'bad')
        self.assertTrue(SYSTEM.live_dt(self.root)['errors'])

    def test_verify_distinguishes_staged_from_running(self):
        self.linux()
        self.put('boot/firmware/config.txt',b'# boot\n'+SYSTEM.LED_BLOCK)
        result=SYSTEM.verify(self.root,lambda a:{'command':a,'code':127,'stdout':'','stderr':'no hardware'},self.uid)
        self.assertTrue(result['led_configured_for_boot'])
        self.assertFalse(result['led_runtime']['active'])
        self.assertIn('staged',result['led_runtime']['errors'][0])
        self.put('sys/class/leds/junshu-white/brightness','1\n')
        self.put('sys/class/leds/junshu-white/trigger','none [default-on]\n')
        result=SYSTEM.verify(self.root,lambda a:{'command':a,'code':127,'stdout':'','stderr':'no hardware'},self.uid)
        # Class alone cannot prove selected GPIO/DT ownership.
        self.assertFalse(result['led_runtime']['active'])


class ManagedLedLifecycle(unittest.TestCase):
    """Actual report assembly; only chip inventory and OS commands are boundaries."""
    setUp=SystemFeatures.setUp
    put=SystemFeatures.put
    row=SystemFeatures.row
    linux=LinuxEvidence.linux

    def fixture(self,configured=True,running=True,consumer='junshu-white'):
        base=self.linux()
        self.config=self.put('boot/firmware/config.txt',b'# retained boot\n'+(SYSTEM.LED_BLOCK if configured else b''))
        if running:
            self.put('proc/device-tree/leds/compatible',b'gpio-leds\0')
            self.put('proc/device-tree/leds/junshu-white/label',b'junshu-white\0')
            self.put('proc/device-tree/leds/junshu-white/gpios',bytes.fromhex('000000070000001600000000'))
            self.put('sys/class/leds/junshu-white/brightness','1\n')
            self.put('sys/class/leds/junshu-white/trigger','none [default-on]\n')
        self.rows=[self.row(used=running,consumer=consumer if running else '',direction='OUTPUT' if running else 'INPUT')]
        def runner(args):
            if args==['pinctrl','get','22']:
                return {'command':args,'code':0,'stdout':'22: op dh // GPIO22 = output' if running else '22: ip pu | hi // GPIO22 = input','stderr':''}
            return base(args)
        self.runner=runner
        return self.report()

    def report(self):
        from unittest.mock import patch
        with patch.object(SYSTEM,'gpio_inventory',return_value=(self.rows,[],'2.fixture')),patch.object(SYSTEM.platform,'release',return_value='fixture-kernel'):
            return SYSTEM.verify(self.root,self.runner,self.uid)

    def test_readonly_probe_also_reports_running_owner_without_free_pin_failure(self):
        from unittest.mock import patch
        self.fixture()
        with patch.object(SYSTEM,'gpio_inventory',return_value=(self.rows,[],'2.fixture')),patch.object(SYSTEM.platform,'release',return_value='fixture-kernel'):
            report=SYSTEM.probe(self.root,self.runner,owner=self.uid)
        self.assertEqual(report['errors'],[])
        self.assertEqual(report['led_lifecycle']['state'],'running_managed')
        self.assertFalse(report['led_ready'])
        self.assertTrue(report['first_enable_errors'])

    def test_running_exact_managed_owner_is_clear_and_repeat_enable_is_noop(self):
        report=self.fixture()
        self.assertTrue(report['led_runtime']['active'],report)
        self.assertEqual(report['errors'],[])
        self.assertFalse(report['led_ready'])  # Legacy field is still first-enable/free-input only.
        self.assertEqual(report['led_lifecycle']['state'],'running_managed')
        self.assertTrue(report['led_lifecycle']['enable_noop'])
        self.assertIn('GPIO22 is used or ownership unknown',report['first_enable_errors'])
        self.assertTrue(report['gpio22']['errors'])  # Raw inventory evidence preserved.
        before=self.config.read_bytes()
        result=SYSTEM.led_config('enable',self.root,report,False,self.uid)
        self.assertFalse(result['changed']);self.assertFalse(result['reboot_required']);self.assertFalse(result['pending_boot'])
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse((self.root/'var/lib/junshu-camera/system-backups').exists())

    def test_staged_is_not_lit_and_repeat_enable_does_not_change_config_or_require_review(self):
        report=self.fixture(running=False)
        self.assertEqual(report['led_lifecycle']['state'],'staged')
        self.assertTrue(report['led_lifecycle']['enable_noop']);self.assertFalse(report['led_runtime']['active'])
        before=self.config.read_bytes()
        result=SYSTEM.led_config('enable',self.root,report,False,self.uid)
        self.assertFalse(result['changed']);self.assertFalse(result['reboot_required']);self.assertTrue(result['pending_boot'])
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse((self.root/'var/lib/junshu-camera/system-backups').exists())

    def test_free_first_enable_still_needs_review_and_passed_fresh_evidence(self):
        report=self.fixture(configured=False,running=False)
        self.assertEqual(report['led_lifecycle']['state'],'free_candidate')
        self.assertTrue(report['led_ready']);self.assertFalse(report['led_lifecycle']['enable_noop'])
        before=self.config.read_bytes()
        with self.assertRaises(ValueError):SYSTEM.led_config('enable',self.root,report,False,self.uid)
        self.assertEqual(self.config.read_bytes(),before)
        report['led_ready']=False
        with self.assertRaises(ValueError):SYSTEM.led_config('enable',self.root,report,True,self.uid)
        self.assertEqual(self.config.read_bytes(),before)
        result=SYSTEM.led_config('enable',self.root,self.report(),True,self.uid)
        self.assertTrue(result['changed']);self.assertTrue(result['reboot_required'])

    def test_other_or_additional_owner_and_unconfigured_led_never_get_known_owner_route(self):
        for case in ('consumer','extra_dt','wrong_dt_controller','wrong_dt_flags','wrong_parent','pinmux','kernel','unconfigured','duplicate_config','boot_conflict','alias_disagreement','dark_class'):
            with self.subTest(case=case):
                self.setUp();self.fixture(configured=case!='unconfigured',consumer='other-driver' if case=='consumer' else 'junshu-white')
                if case=='extra_dt':self.put('proc/device-tree/another/gpios',bytes.fromhex('000000070000001600000000'))
                if case=='wrong_dt_controller':self.put('proc/device-tree/leds/junshu-white/gpios',bytes.fromhex('000000090000001600000000'))
                if case=='wrong_dt_flags':self.put('proc/device-tree/leds/junshu-white/gpios',bytes.fromhex('000000070000001600000001'))
                if case=='wrong_parent':self.put('proc/device-tree/leds/compatible',b'other-driver\0')
                if case=='pinmux':self.runner=lambda a:{'command':a,'code':0,'stdout':'22: a0 // SD1_CLK','stderr':''}
                if case=='kernel':self.put('boot/config-fixture-kernel','# CONFIG_LEDS_GPIO is not set\n')
                if case=='duplicate_config':self.config.write_bytes(self.config.read_bytes()+SYSTEM.LED_BLOCK)
                if case=='boot_conflict':self.config.write_bytes(self.config.read_bytes()+b'gpio=22=op,dh\n')
                if case=='alias_disagreement':self.rows.append(self.row(path='/dev/gpiochip512',used=True,consumer='other-driver',direction='OUTPUT'))
                if case=='dark_class':self.put('sys/class/leds/junshu-white/brightness','0\n')
                report=self.report()
                self.assertFalse(report['led_lifecycle']['enable_noop'],report)
                self.assertEqual(report['led_lifecycle']['state'],'blocked',report)
                self.assertTrue(report['errors'],report)
                if case=='extra_dt':self.assertIn('active candidate GPIO22 property: /another/gpios',report['errors'])
                if case=='kernel':self.assertTrue(any('kernel' in x.lower() for x in report['errors']),report)
                before=self.config.read_bytes()
                with self.assertRaises(ValueError):SYSTEM.led_config('enable',self.root,report,True,self.uid)
                self.assertEqual(self.config.read_bytes(),before)
                self.assertFalse((self.root/'var/lib/junshu-camera/system-backups').exists())

    def test_mac_repeated_enable_uses_actual_verified_report_without_prompt_or_privileged_action(self):
        import contextlib
        import io
        import json
        import subprocess
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('managed_led_remote',ROOT/'tools/pi_next_remote.py')
        remote=importlib.util.module_from_spec(spec);spec.loader.exec_module(remote)
        for running in (False,True):
            with self.subTest(running=running):
                self.setUp();report=self.fixture(running=running);before=self.config.read_bytes();commands=[]
                def ssh(target,command,**kw):
                    commands.append(command)
                    self.assertEqual(command,'/usr/bin/python3 -I -B - verify')
                    self.assertEqual(kw['data'],(ROOT/'tools/pi_next_system.py').read_text())
                    return subprocess.CompletedProcess([],0,json.dumps(report),'')
                with patch.object(remote,'ssh',side_effect=ssh),patch.object(remote,'REPORTS',self.root/'mac-reports'),patch('builtins.input',side_effect=AssertionError('repeat enable must not ask for rewiring/review')),contextlib.redirect_stdout(io.StringIO()) as output:
                    result=remote.enable_led('junshu@example.invalid')
                self.assertEqual(commands,['/usr/bin/python3 -I -B - verify'])
                self.assertFalse(result['changed']);self.assertFalse(result['reboot_required'])
                self.assertIn('已配置',output.getvalue())
                self.assertEqual(self.config.read_bytes(),before)
                self.assertFalse((self.root/'var/lib/junshu-camera/system-backups').exists())
