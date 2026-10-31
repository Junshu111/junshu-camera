"""Run the Mac report wrapper with a local SSH substitute; retain all files."""
import os,shutil,subprocess,tempfile,unittest
from pathlib import Path

class ProbeCommandTests(unittest.TestCase):
    def test_repeated_checks_keep_distinct_reports_without_overwriting(self):
        root=Path(__file__).resolve().parents[2]
        fixture=Path(tempfile.mkdtemp(prefix='l1-probe-command-'))
        shutil.copy2(root/'核查L1GPIO.command',fixture/'probe.command')
        source=fixture/'pi-r4-l1/camera_r4/gpio_probe.py';source.parent.mkdir(parents=True)
        source.write_text('# fixture sent to stdin; no GPIO\n')
        binary=fixture/'bin';binary.mkdir()
        ssh=binary/'ssh';ssh.write_text('#!/bin/sh\ncat > /dev/null\nprintf \'%s\\n\' \'{"fixture": true}\'\n');ssh.chmod(0o755)
        env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ['PATH'])
        for _ in range(2):
            result=subprocess.run(['bash',str(fixture/'probe.command')],input='junshu@fixture.invalid\n',env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        reports=list((fixture/'qa/r4-l1-20261024/device-reports').iterdir())
        self.assertEqual(len(reports),2)
        self.assertTrue(all(p.read_text()=='{"fixture": true}\n' for p in reports))
