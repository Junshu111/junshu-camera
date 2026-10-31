"""Launcher guards run without camera, display or user-setting changes."""
import os,subprocess,sys,unittest
from pathlib import Path

class LauncherTests(unittest.TestCase):
    def test_version_command_is_available_without_device(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,'-B',str(root/'run_camera.py'),'--version'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('0.4.0',result.stdout)
    @unittest.skipUnless(sys.platform=='darwin','Mac guard contract')
    def test_mac_start_refuses_to_fake_a_camera(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,'-B',str(root/'run_camera.py')],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('树莓派',result.stderr)

if __name__=='__main__':unittest.main()
