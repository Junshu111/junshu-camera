"""Real Qt gallery + real localhost session lifecycle; physical network discovery injected."""
import threading,time,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import test_gui_r4 as ui

class ShareGuiTests(unittest.TestCase):
    until=ui.R4UITests.until
    def setUp(self):
        ui.R4UITests.setUp(self)
        from camera_r4.share_network import LanAddress
        self.network=patch('camera_r4.gui.discover_lan_addresses',return_value=[LanAddress('wlan0','127.0.0.1')]);self.network.start()
        self.one=self.store.commit(Image.new('RGB',(90,160),'blue'));self.two=self.store.commit(Image.new('RGB',(90,160),'red'))
    def tearDown(self):
        self.network.stop()
        if hasattr(self.window,'share'):self.window.share.revoke_async().result(timeout=6)
        if self.window.exit_complete:
            self.window.deleteLater();ui.app.processEvents()
        else:ui.R4UITests.tearDown(self)
    def open(self,grid=False):
        self.window.open_gallery(grid=grid);self.until(lambda:len(self.window.photos)==2 and not self.window.file_job)
    def start(self):
        self.window.begin_send();self.until(lambda:self.window.share.session is not None)
        return self.window.share.session
    def test_send_during_pending_photo_load_restores_same_photo_on_return(self):
        from camera_r4 import gui
        entered=threading.Event();release=threading.Event();actual_load=gui.load_image
        def delayed(path,size=(1200,1200)):
            if size==(1200,1200):
                entered.set()
                if not release.wait(5):raise TimeoutError('retained gallery return fixture gate')
            return actual_load(path,size)
        try:
            with patch('camera_r4.gui.load_image',side_effect=delayed),patch('camera_r4.gui.discover_lan_addresses',return_value=[]):
                self.window.open_gallery();self.until(entered.is_set)
                # Filmstrip switch is also invalidated; preserve this current index.
                self.window._load_photo(1);expected=self.window.photos[1].path
                self.window.begin_send();self.until(lambda:self.window.share_network_job is None)
                release.set();self.until(lambda:not self.window._file_jobs)
                self.assertIsNone(self.window.gallery_image)
                self.window.return_share_gallery()
                self.until(lambda:self.window.gallery_image is not None and self.window.displayed_path==expected)
                self.assertEqual(self.window.photo_index,1)
                self.assertEqual(self.window.page,'gallery')
        finally:release.set()
    def test_return_from_send_preserves_grid_selection_and_scroll(self):
        for _ in range(10):self.store.commit(Image.new('RGB',(90,160),'green'))
        self.window.open_gallery(grid=True);self.until(lambda:len(self.window.photos)==12 and not self.window.file_job)
        self.window.select_mode=True;self.window.gallery_scroll=3
        self.window.toggle_selection(self.one.path);self.window.toggle_selection(self.two.path)
        with patch('camera_r4.gui.discover_lan_addresses',return_value=[]):
            self.window.begin_send();self.until(lambda:self.window.share_network_job is None)
        self.window.return_share_gallery()
        self.assertEqual(self.window.gallery_mode,'grid');self.assertTrue(self.window.select_mode)
        self.assertEqual(self.window.gallery_scroll,3);self.assertEqual(self.window.selected,{self.one.path,self.two.path})
    def test_return_from_send_keeps_loaded_current_photo_zoom(self):
        self.open();self.until(lambda:self.window.gallery_image is not None)
        expected=self.window.displayed_path;self.window.view_zoom=2
        with patch('camera_r4.gui.discover_lan_addresses',return_value=[]):
            self.window.begin_send();self.until(lambda:self.window.share_network_job is None)
        self.window.return_share_gallery()
        self.assertEqual(self.window.displayed_path,expected);self.assertIsNotNone(self.window.gallery_image)
        self.assertEqual(self.window.view_zoom,2)
    def test_current_photo_send_opens_genuine_session_and_back_revokes(self):
        self.open();session=self.start()
        self.assertEqual(session.status()['count'],1)
        self.assertEqual(self.window.page,'share')
        self.assertIsNotNone(self.window.share.qr)
        self.assertEqual(urllib.request.urlopen(session.url,timeout=2).status,200)
        self.window.return_live();self.until(lambda:not self.window.share.revoking)
        self.assertFalse(session.status()['active'])
        with self.assertRaises((urllib.error.URLError,ConnectionError,OSError)):urllib.request.urlopen(session.url,timeout=1)
    def test_grid_send_contains_only_selected_paths(self):
        third=self.store.commit(Image.new('RGB',(90,160),'green'))
        self.window.open_gallery(grid=True);self.until(lambda:len(self.window.photos)==3 and not self.window.file_job)
        self.window.select_mode=True;self.window.toggle_selection(self.one.path);self.window.toggle_selection(self.two.path)
        session=self.start()
        self.assertEqual(session.status()['count'],2)
        self.assertNotEqual(self.one.path,self.two.path)
        # Real service page must present only the allowlisted file.
        body=urllib.request.urlopen(session.url,timeout=2).read().decode()
        self.assertIn(self.one.path.name,body);self.assertIn(self.two.path.name,body);self.assertNotIn(third.path.name,body)
    def test_empty_and_busy_cannot_publish(self):
        self.open(True);self.window.select_mode=True;self.window.begin_send()
        self.assertIsNone(self.window.share.start_job)
        self.camera.gate.clear();self.window.return_live();self.window.trigger_shutter();self.window.begin_send()
        self.assertIsNone(self.window.share.start_job);self.camera.gate.set()
        self.until(lambda:self.window.capture_job is None)
    def test_cancel_during_start_never_reopens_or_leaks_server(self):
        from camera_r4.photo_share import PhotoShareSession
        entered=threading.Event();release=threading.Event();made=[]
        def delayed(*args,**kwargs):
            entered.set();release.wait(3);session=PhotoShareSession.start(*args,**kwargs);made.append(session);return session
        self.open()
        with patch('camera_r4.share_ui.start_session',side_effect=delayed):
            self.window.begin_send();self.until(lambda:entered.is_set());self.window.return_live();release.set()
            self.until(lambda:not self.window.share.revoking and bool(made))
        self.assertEqual(self.window.page,'live');self.assertIsNone(self.window.share.session)
        self.assertFalse(made[0].status()['active'])
    def test_revoke_is_idempotent_after_normal_exit(self):
        self.open();self.start();self.window.request_exit(confirmed=True)
        self.until(lambda:self.window.exit_complete)
        self.assertIsNone(self.window.share.revoke_async().result(timeout=1))
    def test_session_expiry_removes_qr_and_never_silently_restarts(self):
        from camera_r4.photo_share import PhotoShareSession
        def short(*args,**kwargs):
            kwargs['ttl_seconds']=1;return PhotoShareSession.start(*args,**kwargs)
        self.open()
        with patch('camera_r4.share_ui.start_session',side_effect=short):session=self.start()
        self.until(lambda:self.window.share.session is None)
        self.assertFalse(session.status()['active']);self.assertIsNone(self.window.share.qr)
        for _ in range(10):self.window.tick();time.sleep(.01)
        self.assertIsNone(self.window.share.session)
    def test_navigation_during_network_discovery_cannot_publish_late(self):
        entered=threading.Event();release=threading.Event()
        def delayed():
            from camera_r4.share_network import LanAddress
            entered.set();release.wait(3);return [LanAddress('lo','127.0.0.1')]
        self.open()
        with patch('camera_r4.gui.discover_lan_addresses',side_effect=delayed):
            self.window.begin_send();self.until(entered.is_set);self.window.return_live();release.set()
            for _ in range(20):self.window.tick();time.sleep(.01)
        self.assertEqual(self.window.page,'live');self.assertIsNone(self.window.share.session);self.assertIsNone(self.window.share.start_job)
    def test_start_work_is_off_ui_thread_and_controls_stay_responsive(self):
        from camera_r4.photo_share import PhotoShareSession
        from PyQt5.QtCore import Qt,QPoint
        from PyQt5.QtTest import QTest
        entered=threading.Event();release=threading.Event();threads=[];made=[]
        def delayed(*args,**kwargs):
            threads.append(threading.get_ident());entered.set();release.wait(3)
            session=PhotoShareSession.start(*args,**kwargs);made.append(session);return session
        self.open()
        with patch('camera_r4.share_ui.start_session',side_effect=delayed):
            self.window.begin_send();self.until(entered.is_set)
            QTest.mouseClick(self.window,Qt.LeftButton,pos=QPoint(240,748));ui.app.processEvents()
            self.assertEqual(self.window.page,'gallery');self.assertNotEqual(threads[0],threading.get_ident())
            release.set();self.until(lambda:not self.window.share.revoking and bool(made))
        self.assertFalse(made[0].status()['active'])
    def test_qr_encoding_failure_closes_created_session(self):
        from camera_r4.photo_share import PhotoShareSession
        made=[]
        def start(*args,**kwargs):
            session=PhotoShareSession.start(*args,**kwargs);made.append(session);return session
        self.open()
        with patch('camera_r4.share_ui.start_session',side_effect=start),patch('camera_r4.share_ui.qr_image',side_effect=ValueError('fixture encoding failure')):
            self.window.begin_send();self.until(lambda:bool(self.window.share_error))
        self.assertTrue(made);self.assertFalse(made[0].status()['active']);self.assertIsNone(self.window.share.session)
    def test_selection_limits_reject_101_images_and_over_two_gib(self):
        self.open(True);self.window.select_mode=True
        self.window.selected={Path('fixture-%d.JPG'%i) for i in range(101)}
        self.window.begin_send();self.assertIsNone(self.window.share_network_job)
        self.assertIn('100',self.window.message)
        large=self.store.directory/'JC_sparse_test.JPG'
        with large.open('wb') as output:output.truncate(2*1024**3+1)
        self.window.selected={large};self.window.begin_send();self.assertIsNone(self.window.share_network_job)
        self.assertIn('2 GB',self.window.message)
    def test_exit_revokes_before_camera_close(self):
        self.open();session=self.start();self.window.request_exit(confirmed=True)
        self.until(lambda:self.window.exit_complete)
        self.assertFalse(session.status()['active']);self.assertTrue(self.camera.closed)
        # teardown handles completed exit below
    def test_power_prepare_revokes_before_ready_and_does_not_reopen_on_cancel(self):
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        self.peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,self.peer)
        self.open();session=self.start()
        self.peer.events.append({'type':'prepare','id':'share-power'});self.window.tick()
        self.until(lambda:('ready','share-power') in self.peer.replies)
        self.assertFalse(session.status()['active'])
        self.peer.events.append({'type':'cancel','id':'share-power','reason':'fixture'});self.window.tick()
        self.until(lambda:not self.window.power_bridge.busy)
        for _ in range(10):self.window.tick();time.sleep(.01)
        self.assertIsNone(self.window.share.session)
    def test_power_prepare_waits_for_pending_start_and_revokes_before_ready(self):
        from camera_r4.photo_share import PhotoShareSession
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        entered=threading.Event();release=threading.Event();made=[]
        def delayed(*args,**kwargs):
            entered.set();release.wait(3);session=PhotoShareSession.start(*args,**kwargs);made.append(session);return session
        self.peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,self.peer)
        self.open()
        with patch('camera_r4.share_ui.start_session',side_effect=delayed):
            self.window.begin_send();self.until(entered.is_set)
            self.peer.events.append({'type':'prepare','id':'pending-start'});self.window.tick()
            self.assertFalse(any(reply[0]=='ready' for reply in self.peer.replies))
            release.set();self.until(lambda:('ready','pending-start') in self.peer.replies)
        self.assertFalse(made[0].status()['active']);self.assertIsNone(self.window.share.session)
        self.peer.events.append({'type':'cancel','id':'pending-start'});self.window.tick()
        self.until(lambda:not self.window.power_bridge.busy)
    def test_unfinished_start_has_bounded_power_rejection_and_late_server_revocation(self):
        from camera_r4.photo_share import PhotoShareSession
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        entered=threading.Event();release=threading.Event();made=[]
        def delayed(*args,**kwargs):
            entered.set();release.wait(8);session=PhotoShareSession.start(*args,**kwargs);made.append(session);return session
        self.peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,self.peer)
        self.open()
        with patch('camera_r4.share_ui.start_session',side_effect=delayed):
            self.window.begin_send();self.until(entered.is_set);started=time.monotonic()
            self.peer.events.append({'type':'prepare','id':'slow-start'});self.window.tick()
            try:
                self.until(lambda:any(reply[0]=='reject' for reply in self.peer.replies),timeout=5)
                self.assertLess(time.monotonic()-started,5)
                self.assertFalse(any(reply[0]=='ready' for reply in self.peer.replies));self.assertFalse(self.camera.closed)
            finally:release.set()
            self.until(lambda:bool(made) and not self.window.share.pending)
        self.assertFalse(made[0].status()['active']);self.assertIsNone(self.window.share.session)
    def test_failed_close_cannot_send_power_ready(self):
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        self.peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,self.peer)
        self.open();session=self.start()
        with patch.object(session,'close',side_effect=OSError('fixture close failure')):
            self.peer.events.append({'type':'prepare','id':'share-failed'});self.window.tick()
            self.until(lambda:any(reply[0]=='reject' for reply in self.peer.replies))
        self.assertFalse(any(reply[0]=='ready' for reply in self.peer.replies))
        self.assertTrue(session.status()['active'])
        self.window.share.revoke_async().result(timeout=6)
        self.until(lambda:not self.window.power_bridge.busy)
    def test_failed_navigation_close_is_visible_and_retry_revokes(self):
        self.open();session=self.start()
        with patch.object(session,'close',side_effect=OSError('fixture close failure')):
            self.window.return_share_gallery();self.until(lambda:not self.window.share.revoking)
            self.window.tick()
            self.assertIn('尚未安全关闭',self.window.message)
        self.window.close_share();self.until(lambda:not self.window.share.revoking)
        self.assertFalse(session.status()['active'])
    def test_received_uncertain_power_status_revokes_existing_session(self):
        from test_power_gui import PowerPeer
        from camera_r4.power_bridge import PowerBridge
        self.peer=PowerPeer();self.window.power_bridge=PowerBridge(self.window,self.peer)
        self.open();session=self.start()
        self.peer.events.append({'type':'status','state':'connected','phase':'uncertain','id':'old'});self.window.tick()
        self.until(lambda:not session.status()['active'])
        self.assertTrue(self.window.power_bridge.busy)
        self.window.power_bridge.close();self.until(lambda:not self.window.power_bridge.busy)
    def test_offline_and_multiple_interfaces_require_explicit_action(self):
        self.open()
        with patch('camera_r4.gui.discover_lan_addresses',return_value=[]):
            self.window.begin_send();self.until(lambda:self.window.share_network_job is None)
            self.assertIsNone(self.window.share.session);self.assertIn('Wi-Fi',self.window.share_error)
        from camera_r4.share_network import LanAddress
        with patch('camera_r4.gui.discover_lan_addresses',return_value=[LanAddress('wlan0','127.0.0.1'),LanAddress('eth0','192.168.4.7')]):
            self.window.begin_send();self.until(lambda:self.window.share_network_job is None)
        self.assertEqual(len(self.window.share_choices),2);self.assertIsNone(self.window.share.start_job)
        self.window.choose_share_address(0);self.until(lambda:self.window.share.session is not None)

if __name__=='__main__':unittest.main()
