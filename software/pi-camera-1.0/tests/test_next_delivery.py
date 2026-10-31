"""Integrated delivery boundaries: retained local fixtures, never a Pi connection."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
QA=ROOT/'qa/r4-integrated-20261026/delivery-fixtures'
WRAPPERS={'核查R4白灯和启动环境.command':'probe','导入R4整合版.command':'import','启用R4新版开机启动.command':'enable-autostart','停用R4新版开机启动.command':'disable-autostart','启用R4白灯.command':'enable-led','停用R4白灯.command':'disable-led','查看R4整合版日志.command':'logs'}

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class NextDeliveryTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True,exist_ok=True)
        self.case=Path(tempfile.mkdtemp(prefix='case-',dir=QA))

    def remote(self):
        path=ROOT/'tools/pi_next_remote.py';self.assertTrue(path.is_file(),'integrated remote missing');return load('next_remote',path)

    def test_startup_executes_system_python_once_and_retains_exit_status_without_restart(self):
        script=ROOT/'pi-r4-next/start-autostart.sh';self.assertTrue(script.is_file(),'startup script missing')
        fixture=self.case/'app';fixture.mkdir();(fixture/'start-autostart.sh').write_bytes(script.read_bytes())
        (fixture/'run_camera.py').write_text('import pathlib,sys\np=pathlib.Path.home()/"calls"\np.open("a").write(str(sys.dont_write_bytecode)+"\\n")\nprint("fixture GUI exit")\nsys.exit(int(sys.argv[1]))\n')
        home=self.case/'home';home.mkdir()
        for status in ('0','7'):
            result=subprocess.run(['/bin/sh',str(fixture/'start-autostart.sh'),status],env=dict(os.environ,HOME=str(home),DISPLAY=':fixture',WAYLAND_DISPLAY=''),capture_output=True,text=True)
            self.assertEqual(result.returncode,int(status),result.stderr)
        self.assertEqual((home/'calls').read_text().splitlines(),['True','True'])
        log=home/'.local/state/junshu-camera/autostart-r4.log'
        self.assertEqual(log.read_text().count('fixture GUI exit'),2)
        self.assertFalse((fixture/'__pycache__').exists())
        result=subprocess.run(['/bin/sh',str(fixture/'start-autostart.sh'),'0'],env=dict(os.environ,HOME=str(home),DISPLAY='',WAYLAND_DISPLAY=''),capture_output=True,text=True)
        self.assertEqual(result.returncode,2);self.assertEqual((home/'calls').read_text().count('True'),2)

    def test_wrappers_are_executable_dispatch_only_the_declared_action(self):
        for filename,action in WRAPPERS.items():
            path=ROOT/filename;self.assertTrue(path.is_file(),filename)
            self.assertEqual(path.stat().st_mode&0o777,0o755)
            text=path.read_text();self.assertIn('tools/pi_next_remote.py" '+action+'\n',text)
            result=subprocess.run(['bash','-n',str(path)],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)

    def test_system_actions_stream_trusted_source_without_root_home_execution(self):
        remote=self.remote();source=(ROOT/'tools/pi_next_system.py').read_text()
        with patch.object(remote,'ssh',return_value=subprocess.CompletedProcess([],0,'{"ok":true}\n','')) as ssh:
            remote.system_action('junshu@example.invalid','disable-led',report_dir=self.case)
        command=ssh.call_args.args[1];self.assertIn('sudo /usr/bin/python3 -I -B -c ',command);self.assertTrue(ssh.call_args.kwargs['terminal'])
        encoded=command.split('base64.b64decode("',1)[1].split('"',1)[0]
        self.assertEqual(base64.b64decode(encoded).decode(),source)
        self.assertNotIn('/home/junshu/',command)
        with patch.object(remote,'ssh',return_value=subprocess.CompletedProcess([],0,'{}\n','')) as ssh:
            remote.system_action('junshu@example.invalid','probe',report_dir=self.case)
        self.assertEqual(ssh.call_args.kwargs['data'],source);self.assertNotIn('sudo',ssh.call_args.args[1])

    def test_led_review_gate_cancels_without_root_write_and_fresh_probe_retained(self):
        remote=self.remote()
        with patch.object(remote,'system_action',return_value={'led_ready':True}) as action,patch('builtins.input',return_value='no'),patch('sys.stdout',new_callable=io.StringIO) as output:
            remote.enable_led('junshu@example.invalid')
        self.assertEqual([call.args[1] for call in action.call_args_list],['verify'])
        self.assertIn('取消',output.getvalue())
        with patch.object(remote,'system_action',return_value={'led_ready':False}) as action,patch('builtins.input') as prompt,patch('sys.stdout',new_callable=io.StringIO):
            with self.assertRaises(ValueError):remote.enable_led('junshu@example.invalid')
        prompt.assert_not_called()

    def test_sudo_pty_json_survives_private_prompt_and_refusal_stderr(self):
        remote=self.remote()
        self.assertEqual(remote.parse_result('[sudo] password for junshu: \r\n{"changed": false, "error": "refused"}\r\nrefused\r\n'),{'changed':False,'error':'refused'})
        with self.assertRaises(ValueError):remote.parse_result('not JSON')

    def test_reports_unique_bounded_and_logs_survive_missing_gui_without_private_names(self):
        remote=self.remote()
        with patch.object(remote,'ssh',return_value=subprocess.CompletedProcess([],0,'{"desktop": {"ready": false}}\n','')):
            remote.system_action('junshu@example.invalid','probe',report_dir=self.case)
            remote.system_action('junshu@example.invalid','probe',report_dir=self.case)
        reports=list(self.case.glob('*.json'));self.assertEqual(len(reports),2)
        for report in reports:self.assertIn('desktop',json.loads(report.read_text())['result'])
        home=self.case/'home';home.mkdir()
        def command(args,**kwargs):
            self.assertLessEqual(kwargs['timeout'],5)
            return subprocess.CompletedProcess(args,0,'ActiveState=active\nSubState=running\n','')
        namespace={'__name__':'fixture'}
        with patch('pathlib.Path.home',return_value=home),patch('subprocess.run',side_effect=command),patch('sys.stdout',new_callable=io.StringIO) as output:
            exec(compile(remote.LOGS_SOURCE,'<trusted log summary>','exec'),namespace)
        result=json.loads(output.getvalue());self.assertFalse(result['logs']['camera-r4-next.log']['exists']);self.assertIn('active',result['power']['stdout'])
        self.assertEqual(result['performance'],{'camera':[],'ui':[]})
        state=home/'.local/state/junshu-camera';state.mkdir(parents=True)
        (state/'camera-r4-next.log').write_text('Saved PRIVATE.JPG token=SECRET\nJunshu Camera R4 Integrated 0.4.0\n')
        with patch('pathlib.Path.home',return_value=home),patch('subprocess.run',side_effect=command),patch('sys.stdout',new_callable=io.StringIO) as output:
            exec(compile(remote.LOGS_SOURCE,'<trusted log summary>','exec'),{'__name__':'fixture'})
        self.assertNotIn('PRIVATE.JPG',output.getvalue());self.assertNotIn('SECRET',output.getvalue())

    def test_numeric_performance_samples_are_strict_private_and_bounded(self):
        remote=self.remote();home=self.case/'numeric-home';state=home/'.local/state/junshu-camera';state.mkdir(parents=True)
        path=state/'camera-r4-next.log'
        lines=[
            '2026-10-26 20:00:01,123 INFO camera_r4.camera preview camera delivered_fps=29.9 exposure_us=10000',
            '2026-10-26 20:00:02,456 INFO camera_r4.gui preview UI prepared_fps=28.8 processing_ms=4.1 frame_age_ms=12.3',
            'preview camera delivered_fps=0.0 exposure_us=1000000',
            'preview UI prepared_fps=12.3 processing_ms=15.4 frame_age_ms=22.5',
            'Saved PRIVATE.JPG token=SECRET https://192.168.1.2/QR-TOKEN',
            'preview camera delivered_fps=30.0 exposure_us=10000 PRIVATE.JPG',
            'preview UI prepared_fps=nan processing_ms=4.1 frame_age_ms=12.3',
            'preview UI prepared_fps=30.0 processing_ms=-1.0 frame_age_ms=12.3',
            'preview UI prepared_fps=999999999.0 processing_ms=1.0 frame_age_ms=2.0',
            'preview UI prepared_fps=30.0 processing_ms=1e4 frame_age_ms=12.3',
            'preview camera delivered_fps=30.0 exposure_us=None',
            'preview camera delivered_fps=30.0 exposure_us=0',
            'preview camera delivered_fps=30.0 exposure_us=99999999999999999',
            '2026-99-99 20:00:01,123 INFO camera_r4.camera preview camera delivered_fps=30.0 exposure_us=10000',
            '2026-10-26 20:00:01,123 INFO private.logger preview camera delivered_fps=30.0 exposure_us=10000',
        ]
        path.write_text('\n'.join(lines)+'\n')
        def summary():
            with patch('pathlib.Path.home',return_value=home),patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'ActiveState=active\n','')),patch('sys.stdout',new_callable=io.StringIO) as output:
                exec(compile(remote.LOGS_SOURCE,'<retained numeric logs>','exec'),{'__name__':'fixture'})
            text=output.getvalue();self.assertNotIn('PRIVATE',text);self.assertNotIn('SECRET',text);self.assertNotIn('QR-TOKEN',text)
            return json.loads(text)
        report=summary();samples=report['performance']
        self.assertEqual(samples['camera'],[{'delivered_fps':29.9,'exposure_us':10000,'timestamp_local':'2026-10-26T20:00:01.123'},{'delivered_fps':0.0,'exposure_us':1000000,'timestamp_local':None}])
        self.assertEqual(samples['ui'],[{'prepared_fps':28.8,'processing_ms':4.1,'frame_age_ms':12.3,'timestamp_local':'2026-10-26T20:00:02.456'},{'prepared_fps':12.3,'processing_ms':15.4,'frame_age_ms':22.5,'timestamp_local':None}])
        self.assertIn('active',report['power']['stdout'])
        # Old valid evidence outside64KiB and giant/private lines must not leak.
        path.write_text(lines[0]+'\n'+'PRIVATE-PADDING'*6000+'\n'+'\n'.join('preview camera delivered_fps=%.1f exposure_us=10000'%value for value in range(25))+'\n')
        samples=summary()['performance'];self.assertEqual(len(samples['camera']),20)
        self.assertEqual(samples['camera'][0]['delivered_fps'],5.0);self.assertEqual(samples['camera'][-1]['delivered_fps'],24.0)
        self.assertEqual(samples['ui'],[])

if __name__=='__main__':unittest.main()
