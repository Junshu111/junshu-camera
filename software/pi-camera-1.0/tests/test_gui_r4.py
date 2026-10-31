"""R4 behavior contracts. Fixtures are retained: no file cleanup/deletion."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import tempfile,threading,time,unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from PyQt5.QtCore import Qt,QPoint,QPointF,QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
try:
    from camera_r4.gui import CameraWindow
    from camera_r4.state import AppSettings,load_settings
    from camera_r4.storage import JPEGStore
    from camera_r4.camera import PreviewFrame
    IMPORT_ERROR=''
except ImportError as error:
    CameraWindow=None
    IMPORT_ERROR=str(error)

app=QApplication.instance() or QApplication([])

class CameraDouble:
    def __init__(self):
        self.calls=0;self.gate=threading.Event();self.gate.set()
        self.error=None;self.stale=False;self.closed=False;self.configured=[]
        self.image=Image.new('RGB',(820,616),(40,110,160))
        self.last_still_metadata={'ExposureTime':8000,'AnalogueGain':2.0,'ColourTemperature':5200}
        self.capabilities={'ExposureTime':(1,66666,20000),'AnalogueGain':(1.,16.,1.),'ExposureValue':(-8.,8.,0.)}
    def configure(self,settings):self.configured.append(settings)
    def snapshot(self):
        return PreviewFrame(image=self.image,sequence=1,captured_at=0 if self.stale else time.monotonic(),state='live',metadata=self.last_still_metadata)
    def capture_image(self,settings):
        self.calls+=1
        if not self.gate.wait(5):raise TimeoutError('fixture gate')
        if self.error:raise self.error
        return Image.new('RGB',(1640,1232),(160,90,40))
    def close(self,timeout=12):self.closed=True

class DeviceDouble:
    def __init__(self):self.shutdown_calls=0;self.sounds=[]
    def capabilities(self):return {'brightness':False,'sound':False,'battery':False,'shutdown':True}
    def read_battery(self):return {'percent':None,'charging':False}
    def set_brightness(self,value):raise OSError('no fixture backlight')
    def play_sound(self,success):self.sounds.append(success)
    def shutdown(self):self.shutdown_calls+=1;return {'acknowledged':True}
    def close(self):pass

class R4UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if CameraWindow is None:raise AssertionError('R4 GUI implementation missing: '+IMPORT_ERROR)
    def setUp(self):
        retained=Path(__file__).resolve().parents[2]/'qa/r4-implementation/test-data'
        retained.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='gui-',dir=retained))
        self.camera=CameraDouble();self.device=DeviceDouble();self.store=JPEGStore(self.root/'photos')
        self.window=CameraWindow(self.camera,self.store,self.root/'settings.json',device=self.device)
        self.window.resize(480,800);self.window.show();app.processEvents();self.window.tick()
    def until(self,predicate,timeout=6):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents();self.window.tick()
            if predicate():return
            time.sleep(.01)
        self.fail('R4 UI did not reach state; '+str(self.window.page)+' '+str(self.window.message))
    def tearDown(self):
        self.camera.gate.set();self.camera.error=None
        self.window.cancel_countdown();self.window.capture.recover()
        if self.window._settings_failure:
            self.window._write(self.window.settings,self.window.settings,'setting')
            self.until(lambda:not self.window._settings_jobs and not self.window._settings_failure)
        self.window.request_exit(confirmed=True)
        self.until(lambda:self.window.exit_complete)
        self.window.deleteLater();app.processEvents()
    def test_draft_preview_dismiss_reverts_without_writing(self):
        self.window.open_panel('ev');self.window.change_draft(ev=1.0)
        self.assertEqual(self.window.settings.ev,0)
        self.assertEqual(self.window.effective_settings.ev,1)
        self.window.dismiss_panel()
        self.assertEqual(self.window.effective_settings.ev,0)
        self.assertEqual(self.camera.configured[-1].ev,0)
        self.assertFalse((self.root/'settings.json').exists())
    def test_checkmark_is_the_commit_boundary_and_survives_reload(self):
        self.window.open_panel('ev');self.window.change_draft(ev=-1.0);self.window.confirm_panel()
        self.until(lambda:self.window.panel=='menu')
        self.assertEqual(self.window.panel,'menu')
        self.assertEqual(load_settings(self.root/'settings.json').ev,-1)
    def test_failed_settings_write_keeps_committed_values_and_draft(self):
        self.window.open_panel('ev');self.window.change_draft(ev=2.)
        with patch('camera_r4.gui.save_settings',side_effect=OSError('fixture settings disk failure')):
            self.window.confirm_panel()
            self.until(lambda:'fixture settings disk failure' in self.window.message)
        self.assertEqual(self.window.settings.ev,0)
        self.assertEqual(self.window.panel,'ev')
        self.assertIn('fixture settings disk failure',self.window.message)
        self.window.confirm_panel()
        self.until(lambda:self.window.panel=='menu' and not self.window._settings_failure)
        self.assertEqual(load_settings(self.root/'settings.json').ev,2)
    def test_checkmark_waits_for_applied_camera_settings(self):
        self.window.open_panel('ev');self.window.change_draft(ev=1.)
        self.camera.stale=True;self.window.confirm_panel();QTest.qWait(80);self.window.tick()
        self.assertEqual(self.window.settings.ev,0)
        self.assertFalse((self.root/'settings.json').exists())
        self.camera.stale=False
        self.until(lambda:self.window.panel=='menu')
        self.assertEqual(load_settings(self.root/'settings.json').ev,1.)
    def test_latest_photo_thumbnail_is_loaded_from_saved_jpeg(self):
        self.window.trigger_shutter()
        self.until(lambda:self.window.page=='review' and self.window.displayed_path is not None)
        self.until(lambda:self.window.thumbnail is not None)
        self.assertEqual(self.window.thumbnail.pixelColor(30,30).red()>100,True)
    def test_lazy_grid_thumbnail_load_does_not_disappear_in_file_dispatch(self):
        record=self.store.commit(Image.new('RGB',(90,160),'blue'))
        self.window.open_gallery(grid=True);self.until(lambda:len(self.window.photos)==1)
        self.window.thumbnails.clear();self.window._ensure_visible_thumbnails()
        self.until(lambda:str(record.path) in self.window.thumbnails)
    def test_parameter_slider_does_not_dismiss_panel_or_fire_shutter(self):
        self.window.open_panel('ev');self.until(lambda:self.window.panel_progress==1)
        QTest.mousePress(self.window,Qt.LeftButton,pos=QPoint(80,400))
        QTest.mouseMove(self.window,QPoint(400,400))
        QTest.mouseRelease(self.window,Qt.LeftButton,pos=QPoint(400,400));app.processEvents()
        self.assertEqual(self.window.panel,'ev');self.assertGreater(self.window.effective_settings.ev,0)
        self.assertEqual(self.window.settings.ev,0);self.assertEqual(self.camera.calls,0)
    def test_new_switches_remember_last_selection(self):
        self.window.toggle_setting('fullscreen');self.window.toggle_setting('sound');self.window.toggle_setting('hdr')
        self.until(lambda:not self.window._settings_jobs)
        settings=load_settings(self.root/'settings.json')
        self.assertFalse(settings.fullscreen);self.assertFalse(settings.sound);self.assertTrue(settings.hdr)
    def test_header_tap_and_down_swipe_do_not_trigger_a_photo(self):
        QTest.mouseClick(self.window,Qt.LeftButton,pos=QPoint(90,20));app.processEvents()
        self.assertFalse(self.window.info_visible)
        QTest.mousePress(self.window,Qt.LeftButton,pos=QPoint(60,4))
        QTest.mouseMove(self.window,QPoint(60,80))
        QTest.mouseRelease(self.window,Qt.LeftButton,pos=QPoint(60,80));app.processEvents()
        self.assertTrue(self.window.info_visible);self.assertEqual(self.camera.calls,0)
    def test_open_panel_consumes_underlying_shutter_tap(self):
        self.window.open_panel('menu');self.until(lambda:self.window.panel_progress==1)
        self.assertAlmostEqual(self.window.panel_left,16,places=1)
        self.assertAlmostEqual(self.window.grip_x,8,places=1)
        QTest.mouseClick(self.window,Qt.LeftButton,pos=QPoint(240,734));app.processEvents()
        self.assertEqual(self.camera.calls,0)
        self.until(lambda:self.window.panel is None)
    def test_busy_double_trigger_does_not_queue(self):
        self.camera.gate.clear();self.window.trigger_shutter();self.window.trigger_shutter()
        self.assertIn(self.window.capture.snapshot().phase,('capturing','saving'))
        self.camera.gate.set();self.until(lambda:self.window.page=='review' and self.window.displayed_path is not None)
        self.assertEqual(self.camera.calls,1);self.assertEqual(len(self.store.list_photos()),1)
    def test_review_shutter_starts_next_photograph(self):
        self.window.trigger_shutter();self.until(lambda:self.window.page=='review' and self.window.displayed_path is not None)
        self.window.trigger_shutter();self.until(lambda:self.camera.calls==2 and self.window.page=='review' and self.window.displayed_path is not None)
        self.assertEqual(len(self.store.list_photos()),2)
    def test_countdown_cancel_and_repeated_trigger_make_no_file(self):
        self.window.set_setting(self_timer=3);self.window.trigger_shutter();self.window.trigger_shutter()
        self.assertIsNotNone(self.window.countdown_deadline);self.assertEqual(self.camera.calls,0)
        self.window.cancel_countdown();self.window.tick()
        self.assertIsNone(self.window.countdown_deadline);self.assertEqual(self.store.list_photos(),[])
    def test_countdown_display_never_exceeds_selected_delay(self):
        self.window.set_setting(self_timer=5);self.window.now-=.2;self.window.trigger_shutter()
        self.assertEqual(self.window.countdown_remaining,5)
    def test_countdown_deadline_captures_exactly_once(self):
        self.window.set_setting(self_timer=3);self.window.trigger_shutter()
        self.window.countdown_deadline=time.monotonic()-.1;self.window.tick();self.window.tick()
        self.until(lambda:self.window.page=='review' and self.window.displayed_path is not None)
        self.assertEqual(self.camera.calls,1)
    def test_stale_frame_is_not_displayed_as_current_live_scene(self):
        self.camera.stale=True;self.window.tick();app.processEvents()
        self.assertLess(self.window.grab().toImage().pixelColor(150,500).blue(),100)
    def test_stale_camera_frame_cannot_capture(self):
        self.camera.stale=True;self.window.tick();self.window.trigger_shutter()
        self.assertEqual(self.camera.calls,0);self.assertEqual(self.store.list_photos(),[])
    def test_gallery_selection_delete_cancel_preserves_actual_files(self):
        one=self.store.commit(Image.new('RGB',(90,160),'blue'));two=self.store.commit(Image.new('RGB',(90,160),'red'))
        self.window.open_gallery(grid=True);self.until(lambda:len(self.window.photos)==2)
        self.window.select_mode=True;self.window.toggle_selection(one.path);self.window.begin_delete()
        self.until(lambda:self.window.delete_plan is not None)
        self.assertIsNotNone(self.window.delete_plan)
        self.assertEqual(set(self.window.delete_plan.paths),{one.path})
        self.window.cancel_modal()
        self.assertTrue(one.path.exists());self.assertTrue(two.path.exists())
    def test_failed_save_cancels_shutdown_and_never_announces_success(self):
        self.camera.gate.clear();self.camera.error=OSError('fixture SD full')
        self.window.trigger_shutter();self.window.request_shutdown(confirmed=True)
        self.camera.gate.set();self.until(lambda:self.window.page=='error' and self.window.exit_job is None)
        self.assertEqual(self.device.shutdown_calls,0);self.assertFalse(self.window.exit_complete)
        self.assertEqual(self.store.list_photos(),[])
    def test_first_touch_after_two_finger_zoom_is_not_lost(self):
        from types import SimpleNamespace
        record=self.store.commit(Image.new('RGB',(90,160),'blue'))
        self.window.open_gallery();self.until(lambda:self.window.gallery_image is not None)
        def touch(kind,coords):
            event=SimpleNamespace(type=lambda:kind,touchPoints=lambda:[SimpleNamespace(pos=lambda p=p:QPointF(*p)) for p in coords],accept=lambda:None)
            self.window.event(event);app.processEvents()
        touch(QEvent.TouchBegin,[(160,350),(320,350)])
        touch(QEvent.TouchUpdate,[(80,350),(400,350)])
        self.assertAlmostEqual(self.window.view_zoom,2.)
        touch(QEvent.TouchEnd,[(80,350),(400,350)])
        touch(QEvent.TouchBegin,[(240,684)])
        touch(QEvent.TouchEnd,[(240,684)])
        self.assertEqual(self.window.view_zoom,1.)
        self.assertEqual(self.camera.calls,0)
    def test_two_failed_quick_switches_restore_last_durable_settings(self):
        with patch('camera_r4.gui.save_settings',side_effect=OSError('fixture read-only settings')):
            self.window.toggle_setting('fullscreen');self.window.toggle_setting('sound')
            self.until(lambda:not self.window._settings_jobs)
        self.assertTrue(self.window.settings.fullscreen)
        self.assertTrue(self.window.settings.sound)
    def test_delayed_review_success_does_not_interrupt_more_menu(self):
        pending=Future();token=self.window._view_token
        self.window._file_jobs.append((pending,'review',token,self.root/'old.JPG'))
        self.window._action('more');pending.set_result(Image.new('RGB',(90,160),'blue'))
        self.window._handle_files()
        self.assertEqual(self.window.page,'more')
    def test_stale_review_failure_does_not_interrupt_gallery(self):
        pending=Future();token=self.window._view_token
        self.window._file_jobs.append((pending,'review',token,self.root/'old.JPG'))
        self.window.open_gallery(grid=True);pending.set_exception(OSError('stale review read failure'))
        self.window._handle_files()
        self.assertEqual(self.window.page,'gallery')
        self.assertNotIn('stale review',self.window.message)
    def test_zoomed_gallery_controls_tolerate_finger_jitter(self):
        self.store.commit(Image.new('RGB',(90,160),'blue'))
        self.window.open_gallery();self.until(lambda:self.window.gallery_image is not None)
        self.window.view_zoom=2.;self.window.update();app.processEvents()
        self.window._pointer_begin(QPointF(240,684));self.window._pointer_move(QPointF(242,685));self.window._pointer_end(QPointF(242,685))
        self.assertEqual(self.window.view_zoom,1.)
    def test_review_timer_does_not_dismiss_exit_confirmation(self):
        self.window.page='review';self.window.review_until=time.monotonic()-1
        self.window.request_exit();self.window.tick()
        self.assertEqual(self.window.modal,'exit')
        self.window.cancel_modal()
    def test_old_thumbnail_completion_does_not_replace_latest_photo(self):
        from camera_r4.gui import qt_image
        old=self.store.commit(Image.new('RGB',(90,160),'red'));new=self.store.commit(Image.new('RGB',(90,160),'blue'))
        self.window.photos=[new,old];self.window.thumbnail=qt_image(Image.new('RGB',(90,160),'blue'))
        pending=Future();pending.set_result(Image.new('RGB',(90,160),'red'))
        self.window._file_jobs.append((pending,'thumbnail',0,old.path));self.window._handle_files()
        self.assertGreater(self.window.thumbnail.pixelColor(30,30).blue(),100)
    def test_native_render_contains_real_scene_and_no_fake_battery_percent(self):
        self.until(lambda:self.window._context is not None);app.processEvents()
        im=self.window.grab().toImage()
        self.assertEqual((im.width(),im.height()),(480,800))
        pixel=im.pixelColor(150,500)
        self.assertGreater(pixel.blue(),100)
        self.assertIsNone(self.window.battery['percent'])
        im.save(str(self.root/'live-native.png'))

if __name__=='__main__':unittest.main()
