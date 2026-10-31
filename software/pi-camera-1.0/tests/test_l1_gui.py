import time,threading,unittest
from dataclasses import replace
import test_gui_r4 as ui
from camera_r4.l1_input import L1Input


class Line:
    def __init__(self,pressed=False):self.pressed=pressed;self.closed=False;self.failure=None
    def read(self):
        if self.failure:raise self.failure
        return self.pressed
    def close(self):self.closed=True


class L1GuiTests(unittest.TestCase):
    def setUp(self):
        ui.R4UITests.setUp(self)
        self.line=Line();self.l1=L1Input(factory=lambda:self.line)
        self.window.l1=self.l1;self.l1.start()
        self.window.settings=replace(self.window.settings,review_seconds=0,self_timer=0,sound=False)
        self.until(lambda:self.l1.snapshot()['pressed'] is False)
        self.window.tick()
    until=ui.R4UITests.until
    def tearDown(self):
        self.line.pressed=False;self.line.failure=None
        ui.R4UITests.tearDown(self)
        self.l1.close()
    def pump_for(self,duration):
        end=time.monotonic()+duration
        while time.monotonic()<end:ui.app.processEvents();self.window.tick();time.sleep(.005)
    def press(self):self.line.pressed=True;self.pump_for(.09)
    def release(self):self.line.pressed=False;self.pump_for(.09)
    def test_one_press_saves_once_hold_and_release_do_not_repeat(self):
        thread_ids=[];original=self.window.trigger_shutter
        def trigger(*args,**kwargs):thread_ids.append(threading.get_ident());return original(*args,**kwargs)
        self.window.trigger_shutter=trigger
        self.press();self.until(lambda:len(self.store.list_photos())==1 and not self.window.busy)
        self.pump_for(.18);self.release();self.pump_for(.15)
        self.assertEqual(self.camera.calls,1);self.assertEqual(self.window.l1_saved,1)
        self.assertEqual(thread_ids,[threading.get_ident()])
    def test_busy_press_is_discarded_and_never_replayed(self):
        self.camera.gate.clear();self.press();self.until(lambda:self.camera.calls==1)
        self.release();self.press();self.assertEqual(self.l1.snapshot()['count'],2)
        self.camera.gate.set();self.until(lambda:not self.window.busy)
        self.pump_for(.2);self.assertEqual(self.camera.calls,1)
        self.release();self.press();self.until(lambda:len(self.store.list_photos())==2)
    def test_start_held_does_not_fire(self):
        self.l1.close();self.line=Line(True);self.l1=L1Input(factory=lambda:self.line)
        self.window.l1=self.l1;self.l1.start();self.pump_for(.2)
        self.assertEqual(self.camera.calls,0)
        self.release();self.press();self.until(lambda:self.camera.calls==1)
    def test_press_while_durable_save_is_blocked_never_replays(self):
        saving=threading.Event();resume=threading.Event();original=self.store.commit
        def commit(*args,**kwargs):
            saving.set()
            if not resume.wait(3):raise TimeoutError('fixture save barrier')
            return original(*args,**kwargs)
        self.store.commit=commit
        try:
            self.press();self.until(lambda:saving.is_set())
            self.assertEqual(self.window.capture.snapshot().phase,'saving')
            self.release();self.press()
            self.assertEqual(self.l1.snapshot()['count'],2)
            resume.set();self.until(lambda:not self.window.busy)
            self.pump_for(.2)
            self.assertEqual(self.camera.calls,1)
            self.assertEqual(len(self.store.list_photos()),1)
        finally:resume.set()
    def test_menu_press_is_not_replayed_when_returning_live(self):
        self.window.open_panel();self.press();self.window.dismiss_panel(close=True)
        self.until(lambda:self.window.panel is None);self.pump_for(.2)
        self.assertEqual(self.camera.calls,0)
    def test_read_failure_is_reported_and_pin_released(self):
        self.line.failure=OSError('fixture GPIO permission lost')
        self.until(lambda:self.l1.snapshot()['state']=='error')
        self.until(lambda:self.line.closed)
        self.assertEqual(self.camera.calls,0)
        self.assertIn('fixture GPIO',self.l1.snapshot()['error'])
    def test_countdown_presses_are_discarded_and_do_not_start_later_shots(self):
        self.window.settings=replace(self.window.settings,self_timer=3)
        self.press();self.assertIsNotNone(self.window.countdown_deadline)
        self.release();self.press()
        self.window.countdown_deadline=time.monotonic()-.01
        self.until(lambda:self.window.l1_saved==1)
        self.release();self.pump_for(.15)
        self.assertEqual(self.camera.calls,1)
        self.assertIsNone(self.window.countdown_deadline)
    def test_save_failure_logs_stage_and_source_without_success_count(self):
        def fail(*args,**kwargs):raise OSError('fixture disk write failed')
        self.store.commit=fail
        with self.assertLogs('camera_r4',level='INFO') as logs:
            self.press();self.until(lambda:self.window.page=='error')
        output='\n'.join(logs.output)
        self.assertIn('photo_stage=save failed',output)
        self.assertIn('capture_result source=L1#1 failed=',output)
        self.assertEqual(self.window.l1_saved,0)
        self.assertEqual(len(self.store.list_photos()),0)

if __name__=='__main__':unittest.main()
