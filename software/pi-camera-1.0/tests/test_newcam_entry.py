"""New Cam entry contracts; subprocess checks avoid real Pi camera hardware."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

APP=Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def test_version_identifies_formal_new_cam_release(self):
        result=subprocess.run([sys.executable,'-B',str(APP/'run_camera.py'),'--version'],
                              capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'New Cam 1.0.0')

    def test_no_graphical_session_still_refuses_hardware_start(self):
        env=dict(os.environ,DISPLAY='',WAYLAND_DISPLAY='')
        result=subprocess.run([sys.executable,'-B',str(APP/'run_camera.py')],env=env,
                              capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.assertIn('树莓派 Linux' if sys.platform!='linux' else '树莓派本地桌面',result.stderr)


if __name__=='__main__':unittest.main()
