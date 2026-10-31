"""Contract tests: real pixel processing and a strict Picamera2 device boundary."""
from dataclasses import replace
import importlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from PIL import Image, ImageChops


class FramingTests(unittest.TestCase):
    def setUp(self):
        try:
            self.f = importlib.import_module('camera_r4.framing')
            self.imaging = importlib.import_module('camera_r4.imaging')
            self.S = importlib.import_module('camera_r4.state').AppSettings
        except ImportError as error:
            self.fail('Required R4 image pipeline is missing: '+str(error))

    def test_literal_rectangles_and_off_same_capture(self):
        for ratio, rect in [('9:16',(15,0,450,800)),('1:1',(0,160,480,480)),
                            ('3:4',(0,80,480,640)),('16:9',(0,265,480,270))]:
            with self.subTest(ratio=ratio):
                self.assertEqual(self.f.capture_rect(ratio), rect)
                self.assertEqual(self.f.capture_rect(ratio,False), rect)
                photo=self.f.compose_frame(Image.new('RGB',(4000,3000)),self.S(ratio=ratio))
                rw,rh=map(int,ratio.split(':'))
                self.assertEqual(photo.image.width*rh,photo.image.height*rw)
                self.assertEqual(photo.image.tobytes(), self.f.compose_frame(
                    Image.new('RGB',(4000,3000)),self.S(ratio=ratio,fullscreen=False)).image.tobytes())

    def test_maximal_context_and_two_times_zoom(self):
        self.assertEqual(self.f.context_box((4000,3000),self.S()),(1100,0,2900,3000))
        self.assertEqual(self.f.context_box((4000,3000),self.S(zoom=2)),(1550,750,2450,2250))
        self.assertEqual(self.f.compose_frame(Image.new('RGB',(4000,3000)),self.S()).box,
                         (1156.25,0.0,2843.75,3000.0))

    def test_rotation_mirror_and_preview_photo_centers(self):
        image=Image.new('RGB',(400,300))
        for x in range(400):
            for y in range(300):
                image.putpixel((x,y),(x//2,y//2,0))
        settings=self.S(rotation=90,mirror=True,ratio='1:1')
        preview=self.f.preview_context(image,settings)
        photo=self.f.compose_frame(image,settings)
        self.assertEqual(photo.source_size,(300,400))
        for source in [preview.getpixel((240,400)),photo.image.getpixel((photo.image.width//2,photo.image.height//2))]:
            self.assertLess(abs(source[0]-100),2)
            self.assertLess(abs(source[1]-75),2)

    def test_hdr_does_not_change_pixels_and_brightness_not_saved(self):
        image=Image.new('RGB',(400,300),(200,120,90))
        normal=self.f.compose_frame(image,self.S()).image
        for settings in [self.S(hdr=True),self.S(brightness=10)]:
            self.assertIsNone(ImageChops.difference(normal,self.f.compose_frame(image,settings).image).getbbox())

    def test_beauty_changes_skin_texture_and_preserves_non_skin(self):
        image=Image.new('RGB',(120,120),(0,100,240))
        for x in range(20,100):
            for y in range(20,100):
                image.putpixel((x,y),(190+(x+y)%2*40,120,90))
        smooth=self.imaging.beautify(image)
        self.assertNotEqual(image.getpixel((50,50)),smooth.getpixel((50,50)))
        self.assertEqual(image.getpixel((0,0)),smooth.getpixel((0,0)))
        self.assertEqual(sum(self.imaging.luminance_histogram(image)),14400)
        self.assertEqual(self.imaging.luminance_histogram(Image.new('RGB',(2,3),'white'))[-1],6)
        self.assertNotEqual(self.f.preview_context(image,self.S()).tobytes(),
                            self.f.preview_context(image,self.S(beauty=True)).tobytes())
        self.assertNotEqual(self.f.compose_frame(image,self.S()).image.tobytes(),
                            self.f.compose_frame(image,self.S(beauty=True)).image.tobytes())


class Request:
    def __init__(self,device): self.device=device; self.released=False
    def make_image(self,name):
        if self.device.fail_image: raise RuntimeError('sensor read failed')
        if self.device.config['kind']=='still' and getattr(self.device,'fail_still',False):
            raise RuntimeError('still sensor read failed')
        return self.device.buffer
    def get_metadata(self):
        return dict(ScalerCrop=(0,0,3280,2464), ExposureTime=self.device.controls['ExposureTime'],
                    AnalogueGain=self.device.controls['AnalogueGain'],ColourGains=(1.,1.) if getattr(self.device,'stale_wb',False) else self.device.controls.get('ColourGains',(1.5,1.3)))
    def release(self):
        self.released=True
        self.device.buffer.paste((255,0,255),(0,0,self.device.buffer.width,self.device.buffer.height))


class Device:
    camera_properties={'Model':'imx219','PixelArraySize':(3280,2464)}
    camera_controls={'AeEnable':(False,True,True),'AwbEnable':(False,True,True),
        'ExposureTime':(100,200000,8000),'AnalogueGain':(1.,10.,1.),
        'ExposureValue':(-8.,8.,0.),'ScalerCrop':((0,0,1,1),(0,0,3280,2464),None),
        'ColourGains':((.1,.1),(8.,8.),(1.,1.))}
    sensor_modes=[dict(size=size,bit_depth=10,crop_limits=(0,0,3280,2464),format='SRGGB10_CSI2P')
                  for size in [(1640,1232),(3280,2464)]]
    def __init__(self):
        self.calls=[]; self.controls={'ExposureTime':8000,'AnalogueGain':4.}; self.requests=[]
        self.running=False; self.config=None; self.fail_image=False; self.threads=set()
    def mark(self,name): self.calls.append(name); self.threads.add(threading.get_ident())
    def create_preview_configuration(self,**kw): self.mark('create_preview'); return dict(kw,kind='preview')
    def create_still_configuration(self,**kw): self.mark('create_still'); return dict(kw,kind='still')
    def configure(self,config):
        self.mark('configure_'+config['kind']); assert not self.running
        assert config['queue'] is False
        self.config=config; self.controls.update(config['controls'])
        self.camera_controls=dict(Device.camera_controls)
        if config['kind']=='still' and hasattr(self,'still_max_exposure'):
            self.camera_controls['ExposureTime']=(100,self.still_max_exposure,8000)
    def camera_configuration(self): self.mark('actual'); return self.config
    def start(self,show_preview=False):
        self.mark('start'); self.running=True
        if self.config['kind']=='still' and hasattr(self,'still_entered'):
            self.still_entered.set(); self.still_release.wait(3)
    def stop(self): self.mark('stop'); self.running=False
    def set_controls(self,controls): self.mark(('controls',dict(controls))); self.controls.update(controls)
    def capture_request(self,wait):
        self.mark('request'); assert self.running; assert wait==6.0
        time.sleep(.002)
        self.buffer=Image.new('RGB',self.config['main']['size'],getattr(self,'scene',(170,110,85)))
        request=Request(self); self.requests.append(request); return request
    def cancel_all_and_flush(self): self.mark('flush')
    def close(self): self.mark('close')


class CameraTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module=importlib.import_module('camera_r4.camera')
            self.S=importlib.import_module('camera_r4.state').AppSettings
        except ImportError as error: self.fail('Required R4 camera pipeline missing: '+str(error))
        self.device=Device()
        self.camera=self.module.PiCamera(factory=lambda:self.device)
    def wait_frame(self,predicate=lambda f:f.ready,timeout=4):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            frame=self.camera.snapshot()
            if predicate(frame): return frame
            time.sleep(.01)
        self.fail('camera frame deadline: '+repr(self.camera.snapshot()))
    def tearDown(self):
        if hasattr(self,'camera'): self.camera.close()

    def test_full_resolution_control_order_and_buffer_ownership(self):
        settings=self.S(exposure_mode='manual',shutter_us=12500,iso=200)
        self.camera.configure(settings); self.camera.start(); frame=self.wait_frame()
        self.assertEqual(frame.image.getpixel((0,0)),(170,110,85))
        self.assertEqual(frame.metadata['ExposureTime'],12500)
        image=self.camera.capture_image(settings)
        self.assertEqual(image.size,(3280,2464))
        self.assertEqual(image.getpixel((0,0)),(170,110,85))
        self.assertEqual(self.camera.last_still_metadata['ExposureTime'],12500)
        self.assertEqual(self.camera.last_still_metadata['AnalogueGain'],2.)
        self.assertIn('configure_still',self.device.calls)
        ix=self.device.calls.index('configure_still')
        self.assertEqual(self.device.calls[ix-1],'stop')
        self.assertEqual(self.device.calls[ix+1],'actual')
        self.assertEqual(self.device.calls[ix+2][0],'controls')
        self.assertEqual(self.device.calls[ix+2][1]['ExposureTime'],12500)
        self.assertEqual(self.device.calls[ix+3],'start')
        self.assertTrue(all(request.released for request in self.device.requests))
        self.assertEqual(len(self.device.threads),1)
        self.assertNotIn(threading.get_ident(),self.device.threads)

    def test_software_auto_caps_and_ael_exact_actual_pair_across_still(self):
        self.camera.configure(self.S(iso_max=200,slowest_us=4000))
        self.camera.start(); self.wait_frame()
        self.assertFalse(self.device.controls['AeEnable'])
        self.assertLessEqual(self.device.controls['ExposureTime'],4000)
        self.assertLessEqual(self.device.controls['AnalogueGain'],2.)
        locked=self.S(ael=True,locked_exposure_us=3300,locked_gain=1.75)
        self.camera.configure(locked)
        frame=self.wait_frame(lambda f:f.ready and f.metadata.get('ExposureTime')==3300)
        self.assertEqual(frame.metadata['AnalogueGain'],1.75)
        self.camera.capture_image(locked)
        self.assertEqual(self.camera.last_still_metadata['ExposureTime'],3300)
        self.assertEqual(self.camera.last_still_metadata['AnalogueGain'],1.75)

    def test_missing_tuning_is_recoverable_and_metadata_exposes_limitations(self):
        self.camera.start(); frame=self.wait_frame()
        self.assertIn('iso_reference',self.camera.capabilities)
        self.assertIn('exposure_strategy',self.camera.capabilities)
        self.camera.configure(self.S(wb_mode='manual',wb_kelvin=5200))
        self.wait_frame(lambda f:bool(f.error))
        self.camera.configure(self.S())
        self.wait_frame()

    def test_tuning_curve_interpolation_uses_reciprocal_sensor_responses(self):
        path=Path(tempfile.mkdtemp(prefix='r4-tuning-retained-'))/'imx219.json'
        path.write_text(json.dumps({'algorithms':[{'rpi.awb':{'ct_curve':[3000,.5,.25,6000,1.,.5]}}]}))
        self.camera=self.module.PiCamera(factory=lambda:self.device,tuning_paths=[path])
        self.camera.configure(self.S(wb_mode='manual',wb_kelvin=4500)); self.camera.start()
        self.wait_frame()
        self.assertAlmostEqual(self.device.controls['ColourGains'][0],1/.75)
        self.assertAlmostEqual(self.device.controls['ColourGains'][1],1/.375)

    def test_auto_commands_bound_dark_scene_at_selected_caps(self):
        self.device.scene=(5,5,5)
        self.camera.configure(self.S(iso_max=200,slowest_us=4000,ev=3))
        self.camera.start(); self.wait_frame()
        self.wait_frame(lambda f:f.sequence>=4)
        for call in self.device.calls:
            if isinstance(call,tuple) and call[0]=='controls':
                if call[1].get('ExposureTime',0) <= 4000:
                    self.assertLessEqual(call[1]['AnalogueGain'],2.)
        self.assertEqual(self.device.controls['ExposureTime'],4000)
        self.assertEqual(self.device.controls['AnalogueGain'],2.)

    def test_ev_changes_software_metering_target(self):
        pairs=[]
        for ev in (-2,2):
            device=Device(); camera=self.module.PiCamera(factory=lambda:device)
            camera.configure(self.S(ev=ev)); camera.start()
            deadline=time.monotonic()+3
            while not camera.snapshot().ready and time.monotonic()<deadline: time.sleep(.005)
            camera.close()
            # Startup and explicit configuration are followed by the first
            # meter-driven command, derived from the same real preview pixels.
            commands=[call[1] for call in device.calls if isinstance(call,tuple)]
            pairs.append(commands[2]['ExposureTime']*commands[2]['AnalogueGain'])
        self.assertLess(pairs[0],pairs[1])

    def test_busy_rejected_and_still_error_recovers_preview(self):
        self.camera.start(); self.wait_frame()
        self.device.still_entered=threading.Event(); self.device.still_release=threading.Event()
        result=[]
        def shoot():
            try: self.camera.capture_image(self.S())
            except Exception as error: result.append(error)
        thread=threading.Thread(target=shoot); thread.start()
        self.assertTrue(self.device.still_entered.wait(2))
        with self.assertRaises(RuntimeError): self.camera.capture_image(self.S())
        self.device.fail_still=True; self.device.still_release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result),1)
        self.assertIn('still sensor read failed',str(result[0]))
        self.wait_frame()
        self.assertEqual(self.device.config['kind'],'preview')
        self.assertTrue(all(r.released for r in self.device.requests))

    def test_still_mode_requeries_its_actual_exposure_bounds(self):
        self.device.still_max_exposure=20000
        settings=self.S(exposure_mode='manual',shutter_us=50000,iso=200)
        self.camera.configure(settings); self.camera.start(); self.wait_frame()
        with self.assertRaisesRegex(ValueError,'传感器模式'):
            self.camera.capture_image(settings)
        self.wait_frame()
        self.assertEqual(self.device.config['kind'],'preview')
        self.assertEqual(self.device.controls['ExposureTime'],50000)

    def test_configuration_metadata_must_match_manual_white_balance(self):
        path=Path(tempfile.mkdtemp(prefix='r4-wb-retained-'))/'imx219.json'
        path.write_text(json.dumps({'algorithms':[{'rpi.awb':{'ct_curve':[3000,.5,.25,6000,1.,.5]}}]}))
        self.camera=self.module.PiCamera(factory=lambda:self.device,tuning_paths=[path])
        self.camera.start(); self.wait_frame()
        self.device.stale_wb=True
        self.camera.configure(self.S(wb_mode='manual',wb_kelvin=4500))
        frame=self.wait_frame(lambda f:bool(f.error))
        self.assertIn('白平衡',frame.error)
        self.device.stale_wb=False; self.camera.configure(self.S()); self.wait_frame()

    def test_stale_frame_rejected_and_read_failure_releases_request(self):
        old=self.module.PreviewFrame(Image.new('RGB',(1,1)),1,time.monotonic()-3,'live')
        self.assertFalse(old.ready)
        with self.assertRaises(RuntimeError): self.camera.capture_image(self.S())
        self.device.fail_image=True
        with self.assertLogs('camera_r4.camera',level='ERROR'):
            self.camera.start()
            frame=self.wait_frame(lambda f:f.state=='error')
        self.assertIn('sensor read failed',frame.error)
        self.assertTrue(all(r.released for r in self.device.requests))


if __name__=='__main__': unittest.main()
