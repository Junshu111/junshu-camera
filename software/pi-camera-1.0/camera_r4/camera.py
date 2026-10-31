"""Single-owner Picamera2 worker; raw preview and independent full-resolution still.

The stock Pi controls cannot express both a shutter ceiling and an analogue gain
ceiling for native AE. R4 therefore meters in software and sends bounded manual
exposure/gain. This is an explicitly uncalibrated, pending-real-tuning strategy.
"""
from concurrent.futures import Future
from dataclasses import dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import threading
import time

from PIL import ImageStat

from .state import AppSettings

log = logging.getLogger(__name__)
_FULL = (0, 0, 3280, 2464)


@dataclass(frozen=True)
class PreviewFrame:
    image: object = None
    sequence: int = 0
    captured_at: float = 0
    state: str = 'starting'
    error: str = ''
    metadata: dict = field(default_factory=dict)

    @property
    def ready(self):
        return (self.image is not None and self.state == 'live' and not self.error
                and 0 <= time.monotonic() - self.captured_at < 2.5)


class PiCamera:
    def __init__(self, factory=None, tuning_paths=None):
        self._factory = factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame = PreviewFrame()
        self._pending = None
        self._thread = None
        self._requested = AppSettings()
        self._generation = 0
        self._settings = AppSettings()
        self._configuration_error = ''
        self._cleanup_error = ''
        self._preview_crop = None
        self._preview_mode = True
        self._meter_at = float("-inf")
        self._ae_pair = (8000, 4.)
        self._curve = None
        self._tuning_paths = tuning_paths
        self._controls = {}
        self.last_still_metadata = {}
        self.capabilities = {
            'controls': {}, 'iso_reference': 'ISO100 = analogue gain 1 (reference scale; sensor ISO uncalibrated)',
            'exposure_strategy': 'software-bounded AE; pending real scene/metering tuning',
            'manual_wb': False, 'wb_tuning_source': '',
            'wb_limitation': 'Kelvin requires installed IMX219 ct_curve; tint is an uncalibrated relative adjustment',
            'hdr': 'selectable normal output; no HDR processing',
        }

    def configure(self, settings):
        """Queue latest immutable draft/commit without touching the device/UI thread."""
        if not isinstance(settings, AppSettings):
            raise TypeError('AppSettings is required')
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError('相机已关闭')
            self._requested = settings
            self._generation += 1
            if self._pending is None:
                self._frame = PreviewFrame(self._frame.image, self._frame.sequence,
                                           self._frame.captured_at, 'configuring', metadata=self._frame.metadata)

    def start(self):
        if self._thread is not None:
            raise RuntimeError('相机已启动，请退出后重开')
        if self._stop.is_set():
            raise RuntimeError('相机已关闭')
        self._thread = threading.Thread(target=self._run, name='junshu-r4-camera', daemon=False)
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._frame

    def capture_image(self, settings):
        if not isinstance(settings, AppSettings):
            raise TypeError('AppSettings is required')
        with self._lock:
            if self._stop.is_set() or not self._frame.ready:
                raise RuntimeError(self._frame.error or '实时取景未就绪，未拍摄')
            if self._pending is not None:
                raise RuntimeError('相机忙碌；本次未排队')
            future = Future()
            self._pending = (future, settings)
            self._frame = PreviewFrame(sequence=self._frame.sequence, state='capturing')
        return future.result()

    @staticmethod
    def _mode(camera, size):
        for mode in camera.sensor_modes:
            if (tuple(mode['size']) == size and mode.get('bit_depth') == 10
                    and tuple(mode.get('crop_limits', ())) == _FULL):
                return {'size': size, 'format': mode['format']}
        raise RuntimeError('未找到CM2全视野模式 {}'.format(size))

    def _load_curve(self):
        paths = self._tuning_paths
        if paths is None:
            selected = os.environ.get('LIBCAMERA_RPI_TUNING_FILE')
            paths = ([selected] if selected else [
                '/usr/share/libcamera/ipa/rpi/vc4/imx219.json',
                '/usr/local/share/libcamera/ipa/rpi/vc4/imx219.json'])
        for filename in paths:
            path = Path(filename)
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                awb = data.get('rpi.awb')
                for algorithm in data.get('algorithms', []):
                    if 'rpi.awb' in algorithm:
                        awb = algorithm['rpi.awb']
                        break
                values = awb['ct_curve']
                points = [tuple(map(float, values[i:i+3])) for i in range(0, len(values), 3)]
                if (len(points) < 2 or any(len(p) != 3 or min(p) <= 0 or
                    not all(math.isfinite(v) for v in p) for p in points)
                    or any(a[0] >= b[0] for a,b in zip(points,points[1:]))):
                    raise ValueError('invalid ct_curve')
                self._curve = points
                self.capabilities.update(wb_tuning_source=str(path),
                    wb_kelvin_range=(points[0][0],points[-1][0]))
                return
            except (OSError, ValueError, TypeError, KeyError):
                continue

    def _refresh_controls(self, camera):
        self._controls = dict(camera.camera_controls)
        self.capabilities['controls'] = dict(self._controls)
        self.capabilities['manual_wb'] = bool(self._curve and 'ColourGains' in self._controls)
        required = ('AeEnable','AwbEnable','ExposureTime','AnalogueGain','ScalerCrop')
        if any(name not in self._controls for name in required):
            raise RuntimeError('相机缺少手动曝光、增益、白平衡或全视野控制')

    def _configs(self, camera):
        properties = camera.camera_properties
        if properties.get('Model') != 'imx219' or tuple(properties.get('PixelArraySize', ())) != (3280,2464):
            raise RuntimeError('此测试包只接入已确认的CM2 / imx219')
        self._load_curve()
        self._refresh_controls(camera)
        base = {'ScalerCrop': _FULL, 'AeEnable': False, 'AwbEnable': True}
        preview = camera.create_preview_configuration(main={'size':(820,616),'format':'RGB888'},
            raw=self._mode(camera,(1640,1232)),buffer_count=3,queue=False,controls=base.copy(),display=None)
        still = camera.create_still_configuration(main={'size':(3280,2464),'format':'RGB888'},
            raw=self._mode(camera,(3280,2464)),buffer_count=1,queue=False,controls=base.copy(),display=None)
        return preview, still

    def _bound(self, name, value):
        low, high, _ = self._controls[name]
        return max(low, min(high, value))

    def _limits(self, settings):
        elo,ehi,_ = self._controls['ExposureTime']
        glo,ghi,_ = self._controls['AnalogueGain']
        if settings.exposure_mode == 'auto' and not settings.ael:
            ehi=min(ehi,settings.slowest_us)
            ghi=min(ghi,settings.iso_max/100.)
            if elo > ehi or glo > ghi:
                raise ValueError('当前模式无法满足最慢快门或ISO上限')
        return elo,ehi,glo,ghi

    def _wb(self, settings):
        if settings.wb_mode == 'auto':
            return {'AwbEnable': True}
        if not self.capabilities['manual_wb']:
            raise ValueError('手动色温不可用：未找到已安装的IMX219白平衡曲线/控制')
        kelvin = settings.wb_kelvin
        if not self._curve[0][0] <= kelvin <= self._curve[-1][0]:
            raise ValueError('色温超出已安装曲线范围 {}–{} K'.format(self._curve[0][0],self._curve[-1][0]))
        for a,b in zip(self._curve,self._curve[1:]):
            if a[0] <= kelvin <= b[0]:
                fraction=(kelvin-a[0])/(b[0]-a[0])
                r=a[1]+fraction*(b[1]-a[1]); blue=a[2]+fraction*(b[2]-a[2])
                # ct_curve stores R/G and B/G responses, hence reciprocal gains.
                tint=2**(settings.wb_tint/200.)
                gains=(tint/r,tint/blue)
                low,high,_=self._controls['ColourGains']
                if isinstance(low,(int,float)): low=(low,low); high=(high,high)
                gains=tuple(max(lo,min(hi,g)) for lo,hi,g in zip(low,high,gains))
                return {'AwbEnable':False,'ColourGains':gains}
        raise ValueError('无法解释已安装白平衡曲线')

    def _command(self, settings):
        elo,ehi,glo,ghi=self._limits(settings)
        if settings.ael:
            if settings.locked_exposure_us <= 0 or settings.locked_gain <= 0:
                raise ValueError('AEL缺少实际帧曝光数据')
            exp,gain=settings.locked_exposure_us,settings.locked_gain
            if not (elo <= exp <= ehi and glo <= gain <= ghi):
                raise ValueError('当前传感器模式无法保持AEL曝光/增益')
        elif settings.exposure_mode == 'manual':
            exp,gain=settings.shutter_us,settings.iso/100.
            if not (elo <= exp <= ehi and glo <= gain <= ghi):
                raise ValueError('手动曝光/ISO超出当前传感器模式范围')
        else:
            exp,gain=self._ae_pair
            exp=max(elo,min(ehi,exp)); gain=max(glo,min(ghi,gain))
        result={'AeEnable':False,'ExposureTime':int(exp),'AnalogueGain':float(gain),'ScalerCrop':_FULL}
        # New libcamera exposes independent modes; old Pi versions use AeEnable.
        for name in ('ExposureTimeMode','AnalogueGainMode'):
            if name in self._controls: result[name]=1
        if 'ExposureValue' in self._controls: result['ExposureValue']=0.0
        if 'FrameDurationLimits' in self._controls:
            low,high,_=self._controls['FrameDurationLimits']
            if isinstance(low,(tuple,list)): low=low[0]
            if isinstance(high,(tuple,list)): high=high[-1]
            duration=int(max(low,min(high,max(33333 if self._preview_mode else 66667,exp+1000))))
            result['FrameDurationLimits']=(duration,duration)
        result.update(self._wb(settings))
        return result

    def _set_mode(self, camera, config, settings):
        camera.configure(config)
        actual=camera.camera_configuration()
        if (tuple(actual['main']['size']) != tuple(config['main']['size']) or
                tuple(actual['raw']['size']) != tuple(config['raw']['size'])):
            raise RuntimeError('相机调整了输出尺寸，暂停拍摄以核对构图')
        self._refresh_controls(camera)
        self._preview_mode = tuple(actual['main']['size']) == (820,616)
        self._meter_at = float('-inf')
        camera.set_controls(self._command(settings))
        camera.start(show_preview=False)

    @staticmethod
    def _read(camera, discard=False):
        request=camera.capture_request(wait=6.0)
        try:
            if discard: return None,None
            return request.make_image('main').copy(),dict(request.get_metadata())
        finally:
            request.release()

    @staticmethod
    def _crop(metadata):
        crop=tuple(metadata.get('ScalerCrop',()))
        if len(crop)!=4 or crop[2]<=0 or crop[3]<=0 or any(abs(a-b)>4 for a,b in zip(crop,_FULL)):
            raise RuntimeError('帧元数据无法确认全视野ScalerCrop：{}'.format(crop))
        return crop

    def _verify_exposure(self, metadata, settings):
        if settings.wb_mode == 'manual':
            expected = self._wb(settings)['ColourGains']
            actual = metadata.get('ColourGains', ())
            if len(actual) != 2 or any(abs(a-b) > .03 for a,b in zip(actual,expected)):
                return False
        exp=metadata.get('ExposureTime'); gain=metadata.get('AnalogueGain')
        if not isinstance(exp,(int,float)) or not isinstance(gain,(int,float)) or min(exp,gain)<=0:
            raise RuntimeError('帧缺少有效曝光与增益元数据')
        elo,ehi,glo,ghi=self._limits(settings)
        if settings.exposure_mode=='auto' and not settings.ael:
            return exp <= ehi and gain <= ghi + .001
        command=self._command(settings)
        # Exposure is quantized to sensor line timing; permit one short line.
        return abs(exp-command['ExposureTime']) <= max(100,command['ExposureTime']*.01) and abs(gain-command['AnalogueGain']) <= .05

    def _settled_read(self, camera, settings):
        for _ in range(2): self._read(camera,discard=True)
        for _ in range(8):
            image,metadata=self._read(camera)
            self._crop(metadata)
            if self._verify_exposure(metadata,settings): return image,metadata
        raise RuntimeError('实际曝光/增益/白平衡未达到请求值或硬上限；未拍摄')

    def _meter(self, camera, image, metadata, settings):
        if settings.exposure_mode!='auto' or settings.ael or self._configuration_error: return
        now=time.monotonic()
        if now-self._meter_at < .1:return
        self._meter_at=now
        # Meter the actual capture rectangle, not unrelated landscape margins.
        from .framing import compose_frame
        sample=image.copy(); sample.thumbnail((160,120))
        sample=compose_frame(sample,settings).image.convert('L')
        mean=max(1.,ImageStat.Stat(sample).mean[0])
        target=max(16.,min(235.,105.*2**(settings.ev*.5)))
        factor=max(.65,min(1.5,(target/mean)**.65))
        elo,ehi,glo,ghi=self._limits(settings)
        product=metadata['ExposureTime']*metadata['AnalogueGain']*factor
        exp=max(elo,min(ehi,product/glo)); gain=max(glo,min(ghi,product/exp))
        self._ae_pair=(int(exp),gain)
        camera.set_controls(self._command(settings))

    def _take_still(self, camera, preview, still, settings):
        camera.stop()
        try:
            self._set_mode(camera,still,settings)
            image,metadata=self._settled_read(camera,settings)
            if self._preview_crop is None or self._crop(metadata)!=self._preview_crop:
                raise RuntimeError('取景与照片ScalerCrop不一致')
            self.last_still_metadata=metadata
            return image
        finally:
            camera.stop()
            self._set_mode(camera,preview,self._settings)

    def _run(self):
        camera=None; active=None
        try:
            factory=self._factory
            if factory is None:
                from picamera2 import Picamera2
                factory=Picamera2
            camera=factory()
            preview,still=self._configs(camera)
            # Start safe defaults; invalid user drafts are recoverable below.
            self._set_mode(camera,preview,self._settings)
            generation=-1
            stats_at=time.monotonic();delivered=0
            while not self._stop.is_set():
                with self._lock:
                    active=self._pending
                    requested,desired_generation=self._requested,self._generation
                if active is not None:
                    future,settings=active
                    try:
                        image=self._take_still(camera,preview,still,settings)
                    except Exception as error:
                        future.set_exception(error)
                    else:
                        future.set_result(image)
                    with self._lock:
                        self._pending=None
                        self._frame=PreviewFrame(sequence=self._frame.sequence,state='resuming')
                    active=None
                if generation!=desired_generation:
                    try:
                        camera.set_controls(self._command(requested))
                        image,metadata=self._settled_read(camera,requested)
                    except (ValueError,RuntimeError) as error:
                        self._configuration_error=str(error)
                        camera.set_controls(self._command(self._settings))
                    else:
                        self._settings=requested
                        self._configuration_error=''
                    generation=desired_generation
                image,metadata=self._read(camera)
                self._preview_crop=self._crop(metadata)
                valid=self._verify_exposure(metadata,self._settings)
                self._meter(camera,image,metadata,self._settings)
                with self._lock:
                    # Never overwrite the capturing/configuring state with an old read.
                    if self._pending is None and self._generation==generation:
                        self._frame=PreviewFrame(image,self._frame.sequence+1,time.monotonic(),
                            'live' if valid else 'settling',self._configuration_error,metadata)
                delivered+=1
                now=time.monotonic()
                if now-stats_at>=5:
                    log.info('preview camera delivered_fps=%.1f exposure_us=%s',delivered/(now-stats_at),metadata.get('ExposureTime'))
                    stats_at=now;delivered=0
        except Exception as error:
            log.exception('camera worker failed')
            with self._lock:
                self._frame=PreviewFrame(state='error',error='{}: {}'.format(type(error).__name__,error))
                pending,self._pending=self._pending,None
            for item in (active,pending):
                if item is not None and not item[0].done(): item[0].set_exception(error)
        finally:
            if camera is not None:
                try:
                    if hasattr(camera,'cancel_all_and_flush'): camera.cancel_all_and_flush()
                    camera.stop()
                except Exception as error:
                    self._cleanup_error='{}: {}'.format(type(error).__name__,error)
                try: camera.close()
                except Exception as error: self._cleanup_error='{}: {}'.format(type(error).__name__,error)
            with self._lock:
                pending,self._pending=self._pending,None
                if self._cleanup_error: self._frame=PreviewFrame(state='error',error=self._cleanup_error)
                elif not self._frame.error: self._frame=PreviewFrame(state='closed')
            if pending is not None and not pending[0].done():
                pending[0].set_exception(RuntimeError('相机已关闭，未拍摄'))

    def close(self, timeout=12):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive(): raise TimeoutError('相机尚未结束，请保持程序运行并保留日志')
            if self._cleanup_error: raise RuntimeError('相机释放未确认：'+self._cleanup_error)
