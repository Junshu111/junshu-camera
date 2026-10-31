"""Integrated worker/lifecycle, bounded gallery and real touch-region contracts."""
from dataclasses import replace
import hashlib
from pathlib import Path
import threading,time,unittest
from unittest.mock import patch
from PIL import Image
from PyQt5.QtCore import Qt,QPoint
from PyQt5.QtTest import QTest
import test_gui_r4 as ui
from camera_r4 import gui

class NextGuiTests(unittest.TestCase):
    setUp=ui.R4UITests.setUp
    def tearDown(self):
        if self.window.exit_complete:
            self.window.deleteLater();ui.app.processEvents()
        else:
            if self.window.power_bridge is not None:self.window.power_bridge.close()
            self.until(lambda:not self.window._power_busy)
            ui.R4UITests.tearDown(self)
    until=ui.R4UITests.until

    def test_histogram_off_does_no_histogram_work_and_enabled_is_throttled(self):
        self.window.set_setting(histogram=False)
        self.until(lambda:self.window._context is not None)
        calls=[];actual=gui.luminance_histogram
        def tracked(image):calls.append(time.monotonic());return actual(image)
        snapshot=self.camera.snapshot
        with patch('camera_r4.gui.luminance_histogram',side_effect=tracked):
            self.window._histogram_at=0.
            for seq in range(10,16):
                self.camera.snapshot=lambda seq=seq:replace(snapshot(),sequence=seq)
                self.window.tick();time.sleep(.02)
            self.assertEqual(calls,[])
            self.window.set_setting(histogram=True)
            self.until(lambda:len(calls)==1)
            for seq in range(16,20):
                self.camera.snapshot=lambda seq=seq:replace(snapshot(),sequence=seq)
                self.window.tick();time.sleep(.02)
            self.assertEqual(len(calls),1)

    def test_invalidated_histogram_work_remains_throttled_and_current_config_eventually_renders(self):
        self.until(lambda:self.window._context is not None)
        self.window.timer.stop()
        entered=threading.Event();release=threading.Event();started=[]
        actual=gui.luminance_histogram
        def gated(image):
            started.append(image.size)
            if len(started)==1:
                entered.set()
                if not release.wait(3):raise TimeoutError('retained histogram invalidation gate')
            return actual(image)
        try:
            with patch('camera_r4.gui.luminance_histogram',side_effect=gated):
                # Drive the Qt-owned scheduler clock at exact boundaries while
                # retaining the real worker, image pipeline and histogram.
                scheduled_at=time.monotonic();self.window.now=scheduled_at
                self.window._histogram_at=0.;self.window._preview_key=None;self.window._poll_preview()
                self.assertTrue(entered.wait(1))
                first_job=self.window._preview_job[0]
                self.window.settings=replace(self.window.settings,ratio='1:1')
                self.window._apply_camera(self.window.settings)
                release.set();first_job.result(timeout=1)
                self.window.now=scheduled_at+.349
                self.window._poll_preview()
                self.window._preview_job[0].result(timeout=1)
                self.window._poll_preview()
                self.assertEqual(started,[(450,800)],'discarding stale output must not reset computation throttle')
                self.assertEqual(self.window.histogram_data,[])
                self.window.now=scheduled_at+.351
                self.window._poll_preview()
                self.window._preview_job[0].result(timeout=1)
                self.window._poll_preview()
                self.assertEqual(started,[(450,800),(480,480)])
                self.assertEqual(sum(self.window.histogram_data),480*480)
        finally:release.set()

    def test_enabled_histogram_recovers_after_configuration_on_constant_frame(self):
        self.until(lambda:bool(self.window.histogram_data))
        self.window.settings=replace(self.window.settings,ratio='1:1')
        self.window._apply_camera(self.window.settings)
        self.until(lambda:bool(self.window.histogram_data),timeout=1)
        self.assertEqual(sum(self.window.histogram_data),480*480)

    def slow_preview(self):
        entered=threading.Event();release=threading.Event();actual=gui.preview_context
        def slow(*args):
            entered.set()
            if not release.wait(4):raise TimeoutError('retained preview fixture gate')
            return actual(*args)
        return entered,release,slow

    def test_exit_waits_off_ui_for_preview_and_late_completion_cannot_revive(self):
        self.until(lambda:self.window._context is not None)
        entered,release,slow=self.slow_preview()
        try:
            with patch('camera_r4.gui.preview_context',side_effect=slow):
                self.window._preview_key=None;self.window.tick()
                self.assertTrue(entered.wait(1))
                start=time.monotonic();self.window.request_exit(confirmed=True)
                self.assertLess(time.monotonic()-start,.1)
                for _ in range(5):self.window.tick();ui.app.processEvents();time.sleep(.01)
                self.assertFalse(self.window.exit_complete,'exit must drain processing first')
                release.set();self.until(lambda:self.window.exit_complete)
                self.assertIsNone(self.window._context)
                self.window.tick();self.assertTrue(self.window.exit_complete)
        finally:release.set()

    def test_power_waits_for_preview_and_cancel_restores_only_fresh_frame(self):
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        self.until(lambda:self.window._context is not None)
        peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,peer)
        entered,release,slow=self.slow_preview()
        try:
            with patch('camera_r4.gui.preview_context',side_effect=slow):
                self.window._preview_key=None;self.window.tick();self.assertTrue(entered.wait(1))
                peer.events.append({'type':'prepare','id':'preview-power'});self.window.tick()
                for _ in range(6):self.window.tick();time.sleep(.01)
                self.assertFalse(any(r[0]=='ready' for r in peer.replies))
                peer.events.append({'type':'cancel','id':'preview-power'});self.window.tick()
                release.set();self.until(lambda:not self.window.power_bridge.busy)
            self.until(lambda:self.window._context is not None)
            self.assertFalse(any(r[0]=='ready' for r in peer.replies))
        finally:release.set()

    def test_preview_failure_rejects_power_without_ready_or_resource_close(self):
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        self.until(lambda:self.window._context is not None)
        peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,peer)
        entered=threading.Event();release=threading.Event()
        def fail(*args):
            entered.set();release.wait(3);raise OSError('retained failed processing fixture')
        try:
            with patch('camera_r4.gui.preview_context',side_effect=fail):
                self.window._preview_key=None;self.window.tick();self.assertTrue(entered.wait(1))
                peer.events.append({'type':'prepare','id':'failed-preview'});self.window.tick()
                with self.assertLogs('camera_r4.power_bridge',level='WARNING') as warnings:
                    release.set();self.until(lambda:any(r[0]=='reject' for r in peer.replies))
                self.assertEqual(warnings.output,['WARNING:camera_r4.power_bridge:power request=failed-preview rejected=retained failed processing fixture'])
            self.assertFalse(any(r[0]=='ready' for r in peer.replies))
            self.assertFalse(self.camera.closed)
            self.until(lambda:self.window._context is not None)
        finally:release.set()

    def test_live_restore_refuses_pre_navigation_frame_until_fresh_camera_frame(self):
        self.until(lambda:self.window._context is not None)
        old=self.camera.snapshot();self.camera.snapshot=lambda:old
        self.window.open_gallery();self.window.return_live()
        for _ in range(4):self.window.tick();ui.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.window._context)
        self.camera.snapshot=lambda:replace(old,sequence=2,captured_at=time.monotonic())
        self.until(lambda:self.window._preview_key is not None)
        self.assertEqual(self.window._preview_key[0],2)

    def test_obsolete_thumbnail_completion_cannot_resurrect_removed_path(self):
        from concurrent.futures import Future
        record=self.store.commit(Image.new('RGB',(90,160),'red'))
        self.window.photos=[];self.window._scan_token+=1
        job=Future();job.set_result(Image.new('RGB',(90,160),'red'))
        self.window._file_jobs.append((job,'thumbnail',self.window._scan_token-1,record.path))
        self.window._handle_files()
        self.assertNotIn(str(record.path),self.window.thumbnails)
        self.assertIsNone(self.window.thumbnail)

    def test_gallery_scan_does_not_decode_unseen_photos_and_first_photo_is_ready(self):
        for i in range(30):self.store.commit(Image.new('RGB',(90,160),(i*7,20,40)))
        reads=[];actual=gui.load_image
        def tracked(path,size=(1200,1200)):
            reads.append((path,size));return actual(path,size)
        with patch('camera_r4.gui.load_image',side_effect=tracked):
            self.window.open_gallery();self.until(lambda:self.window.gallery_image is not None)
            for _ in range(8):self.window.tick();time.sleep(.01)
        self.assertLessEqual(len([r for r in reads if r[1]==(160,160)]),8)
        self.assertEqual(self.window.displayed_path,self.window.photos[0].path)

    def test_rapid_scroll_bounds_jobs_and_deduplicates_requests(self):
        for i in range(30):self.store.commit(Image.new('RGB',(90,160),(i*7,20,40)))
        self.window.open_gallery(grid=True);self.until(lambda:len(self.window.photos)==30 and len(self.window.thumbnails)>=12 and not self.window._thumb_pending)
        entered=threading.Event();release=threading.Event();reads=[];actual=gui.load_image
        def slow(path,size=(1200,1200)):
            if size==(160,160):
                reads.append(path);entered.set();release.wait(3)
            return actual(path,size)
        try:
            with patch('camera_r4.gui.load_image',side_effect=slow):
                self.window.thumbnails.clear()
                for index in (0,3,6,9,12,15,18,21)*3:
                    self.window.gallery_scroll=index;self.window._ensure_visible_thumbnails()
                self.assertTrue(entered.wait(1))
                jobs=[e for e in self.window._file_jobs if e[1] in ('thumbnail','grid_thumb')]
                self.assertLessEqual(len(jobs),2,'scroll must keep a bounded set, not an obsolete queue')
                release.set();self.until(lambda:all(str(p.path) in self.window.thumbnails for p in self.window.photos[21:30]))
                self.assertEqual(len(reads),len(set(reads)))
        finally:release.set()

    def test_both_share_returns_use_real_touch_and_revoke_ready_error_pending(self):
        from camera_r4.share_network import LanAddress
        from camera_r4.photo_share import PhotoShareSession
        record=self.store.commit(Image.new('RGB',(90,160),'blue'))
        before=hashlib.sha256(record.path.read_bytes()).hexdigest()
        for state in ('ready','error','pending'):
            for point in (QPoint(65,42),QPoint(240,748)):
                with self.subTest(state=state,point=(point.x(),point.y())):
                    self.window.open_gallery();self.until(lambda:self.window.gallery_image is not None)
                    entered=threading.Event();release=threading.Event();made=[]
                    def delayed(*args,**kw):
                        entered.set();release.wait(3);s=PhotoShareSession.start(*args,**kw);made.append(s);return s
                    try:
                        with patch('camera_r4.gui.discover_lan_addresses',return_value=[LanAddress('lo','127.0.0.1')]),patch('camera_r4.share_ui.start_session',side_effect=delayed):
                            self.window.begin_send();self.until(entered.is_set)
                            if state!='pending':
                                release.set();self.until(lambda:self.window.share.session is not None)
                                if state=='error':self.window.share_error='fixture transport error'
                            self.window.repaint();ui.app.processEvents()
                            QTest.mouseClick(self.window,Qt.LeftButton,pos=point);ui.app.processEvents()
                            self.assertEqual(self.window.page,'gallery')
                            release.set();self.until(lambda:not self.window.share.pending and not self.window.share.revoking and bool(made))
                            self.assertFalse(made[0].status()['active'])
                            self.assertEqual(self.window.photo_index,0)
                    finally:release.set()
        self.assertEqual(hashlib.sha256(record.path.read_bytes()).hexdigest(),before)

if __name__=='__main__':unittest.main()
