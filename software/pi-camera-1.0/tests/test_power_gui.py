"""Real GUI/capture drain behavior, with only physical power/IPC replaced."""
import threading,time,unittest
from concurrent.futures import Future
from dataclasses import replace
from camera_r4.storage import DeletePlan
import test_gui_r4 as ui

class PowerPeer:
    connected=True
    def __init__(self):self.events=[];self.replies=[]
    def poll(self):events,self.events=self.events,[];return events
    def ready(self,request):self.replies.append(('ready',request))
    def reject(self,request,reason):self.replies.append(('reject',request,reason))
    def close(self):self.connected=False

class PowerGuiTests(unittest.TestCase):
    until=ui.R4UITests.until
    def setUp(self):
        ui.R4UITests.setUp(self)
        self.peer=PowerPeer()
        from camera_r4.power_bridge import PowerBridge
        self.window.power_bridge=PowerBridge(self.window,self.peer)
        self.window.settings=replace(self.window.settings,review_seconds=0,sound=False)
    def tearDown(self):
        self.camera.gate.set();self.camera.error=None
        if hasattr(self.window,'power_bridge'):
            self.window.power_bridge.close()
            self.until(lambda:not self.window.power_bridge.busy)
        if self.window.exit_complete:
            self.window.deleteLater();ui.app.processEvents()
        else:
            ui.R4UITests.tearDown(self)
    def prepare(self,id='a1'):
        self.peer.events.append({'type':'prepare','id':id});self.window.tick()
    def test_power_request_waits_for_actual_jpeg_and_cancel_resumes_camera(self):
        self.camera.gate.clear();self.window.trigger_shutter()
        self.prepare()
        self.assertTrue(self.window.power_bridge.busy)
        self.window.trigger_shutter();self.window.confirm_panel()
        self.assertFalse(self.window.set_setting(fullscreen=False))
        self.assertEqual(self.peer.replies,[])
        self.camera.gate.set()
        self.until(lambda:('ready','a1') in self.peer.replies)
        self.assertEqual(len(self.store.list_photos()),1)
        self.assertFalse(self.camera.closed);self.assertEqual(self.device.shutdown_calls,0)
        self.window.trigger_shutter();self.assertEqual(self.camera.calls,1)
        self.peer.events.append({'type':'cancel','id':'a1','reason':'permission denied'});self.window.tick()
        self.until(lambda:not self.window.power_bridge.busy)
        self.window.trigger_shutter();self.until(lambda:len(self.store.list_photos())==2)
        self.assertFalse(self.camera.closed)
    def test_failed_save_rejects_shutdown_and_keeps_recoverable_error(self):
        self.camera.gate.clear();self.camera.error=OSError('fixture SD error')
        self.window.trigger_shutter();self.prepare();self.camera.gate.set()
        self.until(lambda:any(x[0]=='reject' for x in self.peer.replies))
        self.assertFalse(any(x[0]=='ready' for x in self.peer.replies))
        self.assertFalse(self.camera.closed);self.assertEqual(self.device.shutdown_calls,0)
        self.assertEqual(self.store.list_photos(),[])
        self.camera.error=None;self.window.acknowledge_error();self.window.trigger_shutter()
        self.until(lambda:len(self.store.list_photos())==1)
    def test_cancel_during_pending_settings_does_not_send_stale_ready(self):
        pending=Future();self.window._settings_jobs.append((pending,self.window.settings,self.window.settings,'setting',1))
        self.prepare();self.peer.events.append({'type':'cancel','id':'a1','reason':'connection lost'})
        self.window.tick();self.until(lambda:not self.window.power_bridge.busy)
        pending.set_result(None);self.window.tick()
        self.assertFalse(any(x[0]=='ready' for x in self.peer.replies))
        self.assertFalse(self.camera.closed)
    def test_confirm_and_delete_cannot_start_after_preparing(self):
        self.window.open_panel('ev');self.window.change_draft(ev=1)
        self.prepare();self.window.confirm_panel()
        self.assertEqual(self.window._settings_jobs,[])
        self.window.modal='delete';self.window.delete_plan=DeletePlan((),0,())
        self.window.confirm_modal()
        self.assertIsNone(self.window.delete_job)
        self.assertFalse((self.root/'settings.json').exists())
    def test_ready_transport_uncertain_and_committed_remain_frozen(self):
        self.prepare();self.until(lambda:('ready','a1') in self.peer.replies)
        self.peer.events.append({'type':'status','state':'disconnected','reason':'ready_outcome_unknown'});self.window.tick()
        self.assertTrue(self.window.power_bridge.busy)
        self.window.trigger_shutter();self.assertEqual(self.camera.calls,0)
        self.peer.events.append({'type':'committed','id':'a1'});self.window.tick()
        self.assertTrue(self.window.power_bridge.committed)
        self.window.trigger_shutter();self.assertEqual(self.camera.calls,0)
    def test_command_timeout_is_visible_without_resuming_capture(self):
        self.prepare();self.until(lambda:('ready','a1') in self.peer.replies)
        self.peer.events.append({'type':'status','state':'connected','phase':'uncertain',
                                 'id':'a1','reason':'ready_outcome_unknown'})
        self.window.tick()
        self.assertTrue(self.window.power_bridge.busy)
        self.assertIn('结果待确认', self.window.message)
        self.window.trigger_shutter();self.assertEqual(self.camera.calls,0)
    def test_fresh_window_freezes_on_existing_committed_or_uncertain_service(self):
        for phase in ('uncertain','committed'):
            with self.subTest(phase=phase):
                self.peer.events.append({'type':'status','state':'connected','phase':phase,
                                         'id':'existing','reason':''})
                self.window.tick()
                self.assertTrue(self.window.power_bridge.busy)
                self.window.trigger_shutter();self.assertEqual(self.camera.calls,0)
                self.assertFalse(self.window.set_setting(fullscreen=False))
                self.assertFalse(any(x[0]=='ready' for x in self.peer.replies))
    def test_duplicate_and_stale_response_do_not_change_current_request(self):
        self.prepare();self.prepare()
        self.until(lambda:('ready','a1') in self.peer.replies)
        self.assertEqual(self.peer.replies.count(('ready','a1')),1)
        self.peer.events.append({'type':'cancel','id':'old','reason':'stale'});self.window.tick()
        self.assertTrue(self.window.power_bridge.busy)
    def test_system_signal_closes_after_service_commit_without_second_poweroff(self):
        self.prepare();self.until(lambda:('ready','a1') in self.peer.replies)
        self.peer.events.append({'type':'committed','id':'a1'});self.window.tick()
        self.window.request_exit(confirmed=True,system_signal=True)
        self.until(lambda:self.window.exit_complete)
        self.assertTrue(self.camera.closed);self.assertEqual(self.device.shutdown_calls,0)
        # Existing shared teardown accepts a completed exit.

if __name__=='__main__':unittest.main()
