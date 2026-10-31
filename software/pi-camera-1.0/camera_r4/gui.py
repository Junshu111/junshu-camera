"""Native R4 touch application. Real camera frames, asynchronous files and power."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import logging,math,time
from pathlib import Path
from PIL import Image
from PyQt5.QtCore import Qt,QTimer,QRectF,QEvent
from PyQt5.QtGui import QImage,QPainter,QColor
from PyQt5.QtWidgets import QWidget
from .state import AppSettings,load_settings,save_settings
from .capture import CaptureService,CaptureBlocked
from .framing import preview_context,capture_rect
from .imaging import luminance_histogram
from .platform_io import DeviceIO
from .power_bridge import PowerBridge
from .share_ui import ShareController
from .share_network import discover_lan_addresses
from . import artwork

log=logging.getLogger(__name__)
SHUTTERS=(1000,2000,4000,8000,16667,33333,66666,125000,250000,500000,1000000)
ISOS=(100,200,400,800,1600,3200,6400)

def qt_image(image):
    image=image.convert('RGB')
    return QImage(image.tobytes(),image.width,image.height,image.width*3,QImage.Format_RGB888).copy()

def prepare_preview(image,settings,histogram):
    """CPU work and owned QImages only; never widgets/pixmaps on this worker."""
    started=time.monotonic();context=preview_context(image,settings)
    x,y,w,h=capture_rect(settings.ratio)
    crop=context.crop((round(x),round(y),round(x+w),round(y+h)))
    data=luminance_histogram(crop) if histogram else None
    return qt_image(context),qt_image(crop),data,(time.monotonic()-started)*1000

def load_image(path,size=(1200,1200)):
    with Image.open(path) as image:
        if image.format!='JPEG':raise ValueError('回看文件不是 JPEG')
        image.load();image.thumbnail(size,Image.Resampling.LANCZOS);return image.convert('RGB')

def scan_gallery(store):
    return store.list_photos()

class CameraWindow(QWidget):
    def __init__(self,camera,store,preferences_path,device=None,l1=None,power_client=None):
        super().__init__();self.camera=camera;self.store=store;self.preferences_path=Path(preferences_path)
        self.l1=l1;self.l1_saved=0;self._l1_error='';self._shot_source=''
        self.device=device or DeviceIO();self.capture=CaptureService(camera,store)
        self.power_bridge=PowerBridge(self,power_client) if power_client is not None else None
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='junshu-r4-io')
        self.share=ShareController();self.share_network_job=None;self.share_choices=[];self.share_error='';self.share_address=None;self._share_paths=();self._share_generation=0
        self.preview_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='junshu-r4-preview')
        self.settings_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='junshu-r4-settings')
        self.message='';self.toast_until=0.;self.now=time.monotonic()
        try:self.settings=load_settings(self.preferences_path,self.preferences_path.with_name('settings.json'))
        except Exception as error:
            self.settings=AppSettings();self.message='设置读取失败，临时使用默认：'+str(error);self.toast_until=self.now+8;log.exception('settings load failed')
        self._persisted_settings=self.settings
        self.draft=None;self.page='live';self.panel=None;self.panel_progress=0.;self._panel_target=0.
        self.info_visible=True;self.info_amount=1.;self._targets={};self.more_group=0
        self.modal=None;self.modal_body='';self.delete_plan=None;self.delete_offset=0
        self.selected=set();self.select_mode=False;self.gallery_mode='single';self.gallery_scroll=0
        self._thumb_pending={};self._thumb_failed=set()
        self.photos=[];self.thumbnails={};self.thumbnail=None;self.photo_index=0
        self.gallery_image=None;self.displayed_path=None;self.view_zoom=1.;self.pan_x=self.pan_y=0.
        self.countdown_deadline=None;self.review_until=None;self._frozen_shot=None
        self.capture_job=None;self.exit_job=None;self.exit_complete=False;self.delete_job=None
        self.file_job=None;self._file_jobs=[];self._view_token=0;self._scan_token=0
        self._settings_jobs=[];self._settings_failure='';self._settings_revision=0;self._confirmed_job=None;self._confirm_wait_until=0.
        self.hardware_caps={'brightness':False,'sound':False,'battery':False,'shutdown':False}
        self.battery={'percent':None,'charging':False};self._hardware_at=0.;self._hardware_job=self.pool.submit(self._read_hardware)
        self._context=None;self._capture_image=None;self._preview_key=None;self._histogram_at=0.;self.histogram_data=[]
        self.on_first_preview_painted=None;self._first_preview_painted=False
        self._preview_job=None;self._preview_revision=0;self._preview_after=0.;self._preview_closed=False
        self._preview_stats_at=self.now;self._preview_count=0;self._preview_ms=0.;self._preview_age_ms=0.
        self._gesture=None;self._pinch=None;self._suppress_mouse_until=0.;self._last_tick=self.now
        self.setWindowTitle('New Cam 1.0.0');self.resize(480,800);self.setMinimumSize(240,400)
        self.setAttribute(Qt.WA_AcceptTouchEvents,True);self.setMouseTracking(True)
        self.camera.configure(self.settings)
        self._queue_scan('startup')
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(33)

    @property
    def effective_settings(self):return self.draft if self.draft is not None else self.settings
    @property
    def panel_left(self):return 480-464*self.panel_progress
    @property
    def grip_x(self):return self.panel_left-8
    @property
    def _power_busy(self):return self.power_bridge is not None and self.power_bridge.busy
    @property
    def busy(self):return self._power_busy or self.capture_job is not None or self.capture.snapshot().phase in ('capturing','saving') or self.exit_job is not None or self.delete_job is not None
    @property
    def live_ready(self):return self.camera.snapshot().ready
    @property
    def first_preview_ready(self):return self.page=='live' and self._context is not None
    @property
    def countdown_remaining(self):
        return max(0,math.ceil(self.countdown_deadline-time.monotonic())) if self.countdown_deadline is not None else 0

    def _notify(self,message,seconds=3):
        self.message=str(message);self.toast_until=time.monotonic()+seconds;log.info('UI: %s',message);self.update()
    def _read_hardware(self):return self.device.capabilities(),self.device.read_battery()
    def _apply_camera(self,settings):
        try:self._invalidate_preview();self.camera.configure(settings);return True
        except Exception as error:self._notify('参数暂未应用：'+str(error),6);return False
    def _invalidate_preview(self):
        self._preview_revision+=1;self._preview_after=time.monotonic()
        self._preview_key=None;self._context=None;self._capture_image=None;self.histogram_data=[]
    def _pause_preview(self):
        self._invalidate_preview()
        return self._preview_job[0] if self._preview_job is not None else None
    def _preview_allowed(self):
        return (not self._preview_closed and not self.exit_complete and self.exit_job is None
                and not self._power_busy and self.capture_job is None
                and self.page not in ('gallery','share','review','error','system')
                and self.capture.snapshot().phase not in ('capturing','saving','closed','error'))
    def _preview_signature(self):
        return self._preview_revision,self._view_token,self.page,self.effective_settings
    def _poll_preview(self):
        allowed=self._preview_allowed();frame=self.camera.snapshot()
        if self._preview_job is not None and self._preview_job[0].done():
            job,key,signature,captured_at,histogram=self._preview_job;self._preview_job=None
            valid=(allowed and frame.ready and signature==self._preview_signature()
                   and captured_at>=self._preview_after and time.monotonic()-captured_at<2.5)
            try:
                context,crop,data,ms=job.result()
                if valid:
                    self._context=context;self._capture_image=crop;self._preview_key=key
                    if data is not None:self.histogram_data=data;self._histogram_at=self.now
                    self._preview_count+=1;self._preview_ms+=ms;self._preview_age_ms+=(self.now-captured_at)*1000
            except Exception as error:
                if valid:self._notify('取景处理失败：'+str(error),6)
        if not allowed:return
        if not frame.ready or frame.captured_at<self._preview_after:
            self._context=None;self._capture_image=None;self._preview_key=None
            if frame.error and frame.error!=self.message:self._notify('相机：'+frame.error,6)
            return
        key=(frame.sequence,self.effective_settings)
        histogram=self.effective_settings.histogram and self.now-self._histogram_at>=.35
        if self._preview_job is None and (key!=self._preview_key or (histogram and not self.histogram_data)):
            job=self.preview_pool.submit(prepare_preview,frame.image,self.effective_settings,histogram)
            # Reserve computation time even if navigation/config invalidates its output.
            if histogram:self._histogram_at=self.now
            self._preview_job=(job,key,self._preview_signature(),frame.captured_at,histogram)
        if self.now-self._preview_stats_at>=5:
            count=self._preview_count
            log.info('preview UI prepared_fps=%.1f processing_ms=%.1f frame_age_ms=%.1f',
                     count/(self.now-self._preview_stats_at),self._preview_ms/max(1,count),self._preview_age_ms/max(1,count))
            self._preview_stats_at=self.now;self._preview_count=0;self._preview_ms=0.;self._preview_age_ms=0.
    def _write(self,settings,old,kind):
        self._settings_revision+=1
        job=self.settings_pool.submit(save_settings,self.preferences_path,settings)
        self._settings_jobs.append((job,settings,old,kind,self._settings_revision));return job
    def _finish_settings(self):
        for entry in self._settings_jobs[:]:
            job,value,old,kind,revision=entry
            if not job.done():continue
            self._settings_jobs.remove(entry)
            try:
                job.result();self._settings_failure='';self._persisted_settings=value
                if kind=='panel':
                    self.settings=value;self.draft=None;self.panel='menu';self._confirmed_job=None;self._apply_camera(value)
                if value.brightness!=old.brightness and self.hardware_caps.get('brightness'):
                    self._queue_device('brightness',self.device.set_brightness,value.brightness)
            except Exception as error:
                self._settings_failure=str(error)
                if kind=='panel':self._confirmed_job=None
                if revision==self._settings_revision:
                    self.settings=self._persisted_settings;self._apply_camera(self.effective_settings)
                self._notify('设置保存失败：'+str(error),8)
    def set_setting(self,**changes):
        if self.busy or self.countdown_deadline is not None or self._confirmed_job is not None:return False
        old=self.settings
        try:value=replace(old,**changes)
        except (ValueError,TypeError) as error:self._notify(error);return False
        self.settings=value;self._apply_camera(value)
        if set(changes)<={'self_timer'}:self.update();return True
        self._write(value,old,'setting');self.update();return True
    def toggle_setting(self,name):
        if name not in ('fullscreen','sound','hdr','beauty','histogram','mirror'):return
        if self.set_setting(**{name:not getattr(self.settings,name)}) and name=='sound' and self.settings.sound and not self.hardware_caps.get('sound'):
            self._notify('提示音已开启，当前未发现声音输出')
    def open_panel(self,kind='menu'):
        if self.busy or self.countdown_deadline is not None or self.page not in ('live','review'):return
        if self._confirmed_job is not None:return
        if kind=='ev' and (self.settings.exposure_mode=='manual' or self.settings.ael):self._notify('先回到自动曝光并解除 AEL');return
        self._view_token+=1;self.review_until=None;self.page='live';self.modal=None
        self.draft=replace(self.settings) if kind!='menu' else None
        self.panel=kind;self._panel_target=1.;self.update()
    def change_draft(self,**changes):
        if self.draft is None or self._confirmed_job is not None or self._confirm_wait_until or self.busy:return
        if changes.get('exposure_mode') is not None:changes.update(ael=False,locked_exposure_us=0,locked_gain=0.)
        try:value=replace(self.draft,**changes)
        except (ValueError,TypeError) as error:self._notify(error);return
        self.draft=value;self._apply_camera(value);self.update()
    def confirm_panel(self):
        if self._power_busy or self.draft is None or self._confirmed_job is not None:return
        frame=self.camera.snapshot()
        if frame.error:self._notify('参数尚未生效：'+frame.error,7);return
        if not frame.ready:
            self._confirm_wait_until=time.monotonic()+6
            self._notify('等待相机应用参数后保存…',6);return
        self._confirm_wait_until=0.
        self._confirmed_job=self._write(self.draft,self.settings,'panel')
    def dismiss_panel(self,close=False):
        if self._confirmed_job is not None:return
        self._confirm_wait_until=0.
        if self.draft is not None:self.draft=None;self._apply_camera(self.settings)
        if close:self._panel_target=0.
        else:self.panel='menu';self._panel_target=1.
        self.update()
    def _change_ael(self):
        s=self.effective_settings
        if s.ael:self.change_draft(ael=False,locked_exposure_us=0,locked_gain=0.);return
        frame=self.camera.snapshot();metadata=frame.metadata or {}
        try:
            exposure=int(metadata['ExposureTime']);gain=float(metadata['AnalogueGain'])
            if not frame.ready or exposure<=0 or gain<=0:raise ValueError('没有可信曝光读数')
            self.change_draft(ael=True,locked_exposure_us=exposure,locked_gain=gain)
        except (KeyError,TypeError,ValueError) as error:self._notify('暂不能锁定曝光：'+str(error),6)
    def _camera_bounds(self,name,default):
        caps=getattr(self.camera,'capabilities',{}) or {};return caps.get('controls',caps).get(name,default)
    def _step(self,key,sign):
        s=self.effective_settings
        if s.ael and key in ('iso','iso_max','shutter_us','slowest_us','ev'):return
        if key in ('iso','iso_max'):
            bounds=self._camera_bounds('AnalogueGain',(1,16,1));values=[v for v in ISOS if bounds[0]*100<=v<=bounds[1]*100]
        elif key in ('shutter_us','slowest_us'):
            bounds=self._camera_bounds('ExposureTime',(1,66666,8000));values=[v for v in SHUTTERS if bounds[0]<=v<=bounds[1]]
        elif key=='ev':
            self.change_draft(ev=round(max(-2.,min(2.,s.ev+sign/3))*3)/3);return
        elif key=='wb':
            caps=getattr(self.camera,'capabilities',{}) or {};low,high=caps.get('wb_kelvin_range',(2800,8000));self.change_draft(wb_kelvin=max(round(low),min(round(high),s.wb_kelvin+sign*100)));return
        else:return
        if not values:self._notify('当前相机没有可用的参数范围');return
        current=getattr(s,key);index=min(range(len(values)),key=lambda n:abs(values[n]-current));value=values[max(0,min(len(values)-1,index+sign))]
        self.change_draft(**{key:value})
    def return_live(self):
        self.close_share()
        self._view_token+=1;self.page='live';self.displayed_path=None;self.gallery_image=None;self.review_until=None;self.select_mode=False;self.selected.clear();self.modal=None
        self.panel=None;self.panel_progress=0.;self._panel_target=0.;self.draft=None;self._preview_key=None;self._apply_camera(self.settings);self.update()
    def trigger_shutter(self,source='touch'):
        if self.modal or self.panel is not None:return
        if self.busy:self._notify('正在拍摄或保存，本次未排队');return
        if self.countdown_deadline is not None:self._notify('正在自拍倒计时');return
        if self._settings_jobs:self._notify('正在保存设置，请稍候');return
        if self.page not in ('live','review'):return
        if not self.camera.snapshot().ready:self._notify('实时取景未就绪，未拍摄');return
        if self.l1 is not None:self.l1.gate.set(False)
        self._invalidate_preview()
        self._shot_source=source
        self._view_token+=1;self.displayed_path=None;self.gallery_image=None;self.page='live';self.review_until=None
        self._frozen_shot=replace(self.settings)
        log.info('shutter source=%s settings=%s',source,self._frozen_shot)
        if self.settings.self_timer:
            self.countdown_deadline=time.monotonic()+self.settings.self_timer;self.update();return
        self._start_capture()
    def _start_capture(self):
        if self.l1 is not None:self.l1.gate.set(False)
        try:self.capture_job=self.capture.request(self._frozen_shot or self.settings);self.update()
        except Exception as error:
            log.warning('shutter rejected source=%s reason=%s',self._shot_source,error);self._notify(error,6)
    def cancel_countdown(self):
        if self.countdown_deadline is not None:log.info('shutter cancelled source=%s reason=countdown_cancelled',self._shot_source)
        self.countdown_deadline=None;self._frozen_shot=None;self.update()
    def acknowledge_error(self):
        try:self.capture.recover()
        except CaptureBlocked as error:self._notify(error);return
        self.message='';self.return_live()
    def _queue_device(self,kind,fn,*args):
        job=self.pool.submit(fn,*args);self._file_jobs.append((job,'device_'+kind,-1,None));return job
    def _queue_scan(self,kind='gallery'):
        self._scan_token+=1;self._thumb_failed.clear();job=self.pool.submit(scan_gallery,self.store)
        self._file_jobs.append((job,'scan_'+kind,self._scan_token,None));self.file_job=job
    def open_gallery(self,grid=False):
        if self.busy or self.countdown_deadline is not None:return
        self.close_share()
        self.dismiss_panel(close=True);self.panel=None;self.panel_progress=0.;self._panel_target=0.
        self._view_token+=1;self.page='gallery';self.gallery_mode='grid' if grid else 'single';self.select_mode=False;self.selected.clear();self.gallery_scroll=0
        self.gallery_image=None;self.displayed_path=None;self._queue_scan();self.update()
    def _load_photo(self,index,purpose='gallery'):
        if not self.photos:return
        self.photo_index=max(0,min(len(self.photos)-1,index));self._view_token+=1
        self.view_zoom=1.;self.pan_x=self.pan_y=0.;self.gallery_image=None;self.displayed_path=None
        record=self.photos[self.photo_index];job=self.pool.submit(load_image,record.path)
        self._file_jobs.append((job,purpose,self._view_token,record.path));self.file_job=job;self.update()
    def toggle_selection(self,path):
        path=Path(path)
        if path in self.selected:self.selected.remove(path)
        elif path in {record.path for record in self.photos}:self.selected.add(path)
        self.update()
    def begin_send(self):
        if self.busy or self.countdown_deadline is not None or self.page not in ('gallery','share'):return
        if self.share.pending:return
        paths=sorted(self.selected) if self.select_mode else ([self.photos[self.photo_index].path] if self.photos and self.gallery_mode=='single' else [])
        if not paths:self._notify('请先选择要发送的照片');return
        try:
            if len(paths)>100:raise ValueError('一次最多发送100张，请减少所选照片')
            if sum(path.stat().st_size for path in paths)>2*1024**3:raise ValueError('所选照片超过2 GB，请减少所选照片')
        except (ValueError,OSError) as error:self._notify(str(error),7);return
        self.close_share();self._share_paths=tuple(paths);self.share_choices=[];self.share_address=None;self.share_error=''
        self._view_token+=1;self.review_until=None;self.modal=None;self.page='share'
        generation=self._share_generation
        self.share_network_job=(self.pool.submit(discover_lan_addresses),generation)
        self.update()
    def choose_share_address(self,index):
        if self._power_busy or self.exit_job is not None or self.page!='share' or self.share.pending:return
        if not 0<=index<len(self.share_choices):return
        choice=self.share_choices[index];self.share_address=choice;self.share_error=''
        if self.share.revoking:return
        try:self.share.start(self.store.directory,self._share_paths,choice.address)
        except Exception as error:self.share_error=str(error)
        self.update()
    def close_share(self):
        self._share_generation+=1;self.share_network_job=None;self.share_address=None
        return self.share.revoke_async()
    def return_share_gallery(self):
        self.close_share();self.page='gallery'
        if self.gallery_mode=='single' and self.photos:
            index=max(0,min(self.photo_index,len(self.photos)-1))
            if self.gallery_image is None or self.displayed_path!=self.photos[index].path:
                self._load_photo(index)
        self.update()
    def _poll_share(self):
        self.share.poll()
        if self.share_network_job is not None:
            job,generation=self.share_network_job
            if job.done():
                self.share_network_job=None
                if generation==self._share_generation and self.page=='share' and not self._power_busy and self.exit_job is None:
                    try:self.share_choices=job.result()
                    except Exception:self.share_choices=[]
                    if not self.share_choices:self.share_error='请连接与手机相同的 Wi-Fi，再重试'
                    elif len(self.share_choices)==1:
                        # Revocation of the previous screen may still be finishing.
                        self.share_address=self.share_choices[0]
        if self.share.error:
            self.share_error=self.share.error;self.share.error=''
            if self.page!='share':self._notify(self.share_error,9)
        if (self.page=='share' and self.share_address is not None and not self.share_error
                and self.share.session is None and self.share.start_job is None and not self.share.revoking
                and not self._power_busy and self.exit_job is None):
            self.choose_share_address(self.share_choices.index(self.share_address))
        if self.share.session is not None and not self.share.session.status()['active']:
            self.close_share();self.share_error='本次发送已结束，请返回照片重新发送'
    def begin_delete(self):
        if self.busy:return
        paths=sorted(self.selected) if self.select_mode else ([self.photos[self.photo_index].path] if self.photos else [])
        if not paths:return
        job=self.pool.submit(self.store.plan_delete,paths);self._file_jobs.append((job,'delete_plan',self._view_token,None));self.file_job=job
    def cancel_modal(self):self.modal=None;self.delete_plan=None;self.update()
    def confirm_modal(self):
        if self._power_busy:return
        kind=self.modal
        if kind=='delete' and self.delete_plan:
            plan=self.delete_plan;self.modal=None;self.delete_plan=None
            self.delete_job=self.pool.submit(self.store.delete,plan,confirmed=True)
            self._file_jobs.append((self.delete_job,'delete',self._view_token,None));self._notify('正在删除已确认的照片…')
        elif kind=='shutdown':self.modal=None;self.request_shutdown(confirmed=True)
        elif kind=='exit':self.modal=None;self.request_exit(confirmed=True)
        else:self.cancel_modal()
    def _handle_files(self):
        for entry in self._file_jobs[:]:
            job,kind,token,path=entry
            if not job.done():continue
            self._file_jobs.remove(entry)
            if self.file_job is job:self.file_job=None
            if kind=='delete':self.delete_job=None
            if kind in ('thumbnail','grid_thumb'):self._thumb_pending.pop(str(path),None)
            obsolete=((kind in ('review','gallery') and token!=self._view_token)
                      or (kind=='review' and (self.page!='live' or self.panel is not None or self.modal is not None or self.exit_job is not None or self.capture_job is not None or self.countdown_deadline is not None))
                      or (kind=='gallery' and (self.page!='gallery' or self.gallery_mode!='single'))
                      or (kind.startswith('scan_') and token!=self._scan_token)
                      or (kind in ('thumbnail','grid_thumb') and (token!=self._scan_token or path not in {r.path for r in self.photos})))
            try:
                result=job.result()
                if obsolete:continue
                if kind.startswith('device_'):continue
                if kind.startswith('scan_'):
                    if token!=self._scan_token:continue
                    records=result;self.photos=records
                    paths={str(r.path) for r in records}
                    self.thumbnails={k:v for k,v in self.thumbnails.items() if k in paths}
                    self.thumbnail=self.thumbnails.get(str(records[0].path)) if records else None
                    if kind=='scan_gallery' and self.page=='gallery' and self.gallery_mode=='single' and records:self._load_photo(min(self.photo_index,len(records)-1))
                elif kind in ('gallery','review'):
                    if token!=self._view_token:continue
                    self.gallery_image=qt_image(result);self.displayed_path=path
                    if kind=='review':self.page='review';self.review_until=time.monotonic()+self.settings.review_seconds
                elif kind=='delete_plan':
                    if token!=self._view_token or self.page!='gallery':continue
                    self.delete_plan=result;self.delete_offset=0;self.modal='delete'
                    log.info('deletion confirmation paths=%s bytes=%s retained_count=%s',list(result.paths),result.total_bytes,len(self.photos)-len(result.paths))
                elif kind=='delete':
                    self.selected.clear();self.select_mode=False;self._queue_scan();self._notify('已删除 {} 张照片'.format(len(result)))
                elif kind in ('thumbnail','grid_thumb'):
                    image=qt_image(result);self.thumbnails[str(path)]=image
                    if kind=='thumbnail' and self.photos and self.photos[0].path==path:self.thumbnail=image
            except Exception as error:
                if obsolete:
                    log.info('ignored stale %s completion: %s',kind,error);continue
                log.exception('file/device operation failed: %s',kind)
                if kind in ('thumbnail','grid_thumb'):self._thumb_failed.add(str(path))
                if kind=='review':self.page='live';self._notify('照片已保存，回显读取失败：'+str(error),8)
                elif kind=='gallery':self._notify('照片读取失败：'+str(error),7)
                elif kind=='delete':self._queue_scan();self._notify('删除未全部完成：'+str(error),9)
                elif kind.startswith('device_'):self._notify('设备操作未完成：'+str(error),6)
                else:self._notify('读取失败：'+str(error),8)
    def request_exit(self,confirmed=False,system_signal=False):
        if self.exit_complete or self.exit_job is not None:return
        if self._power_busy and not system_signal:return
        if system_signal and self.power_bridge is not None:self.power_bridge.close()
        if not confirmed:self.modal='exit';self.update();return
        self._begin_exit(False)
    def request_shutdown(self,confirmed=False):
        if self._power_busy:return
        if self.exit_complete or self.exit_job is not None:return
        if not confirmed:self.modal='shutdown';self.update();return
        self._begin_exit(True)
    def _begin_exit(self,shutdown):
        if self.l1 is not None:self.l1.gate.set(False)
        self.cancel_countdown()
        if self._settings_failure:self._notify('设置尚未保存，请先重新保存：'+self._settings_failure,8);return
        self.modal=None
        self._view_token+=1;self.review_until=None
        if self.draft is not None:self.draft=None;self._apply_camera(self.settings)
        pending=[entry[0] for entry in self._settings_jobs]
        pending.append(self.close_share())
        preview=self._pause_preview()
        if preview is not None:pending.append(preview)
        if self.delete_job is not None:pending.append(self.delete_job)
        self._exit_shutdown=shutdown
        self.exit_job=self.pool.submit(self._close_resources,shutdown,pending)
        self.update()
    def _close_resources(self,shutdown,pending):
        for job in pending:job.result(timeout=30)
        if shutdown and not self.device.capabilities().get('shutdown'):
            raise RuntimeError('当前会话没有正常关机授权，请使用树莓派桌面关机')
        self.capture.close(timeout=30)
        try:self.camera.close()
        finally:
            if self.l1 is not None:self.l1.close()
        if shutdown:self.device.shutdown()
        self.device.close()
        self.share.shutdown()
        self._preview_closed=True;self.preview_pool.shutdown(wait=False)
    def handle_input(self,event):
        if event=='SHUTTER':self.trigger_shutter('lever')
        elif event=='POWER_CONFIRM':self.request_shutdown()
        elif event=='POWER_REQUEST':self.request_shutdown(confirmed=True)
    def _l1_allowed(self):
        return (not self.busy and self.countdown_deadline is None and not self._settings_jobs
                and not self.modal and self.panel is None and self.page in ('live','review')
                and self.camera.snapshot().ready and self.capture.snapshot().phase not in ('error','closed'))
    def _poll_l1(self):
        if self.l1 is None:return
        status=self.l1.snapshot()
        self.l1.gate.set(self._l1_allowed() and status['state']=='ready' and status['pressed'] is not None)
        if status['error'] and status['error']!=self._l1_error:
            self._l1_error=status['error'];self._notify('L1不可用：'+status['error'],8)
        event=self.l1.take_event()
        if event is not None:
            log.info('L1 shutter_request id=%d thread=Qt-main',event.number)
            self.trigger_shutter('L1#{}'.format(event.number))
            accepted=self.capture_job is not None or self.countdown_deadline is not None
            log.info('L1 shutter_result id=%d accepted=%s',event.number,accepted)
    def tick(self):
        if self.exit_complete:return
        self.now=time.monotonic();dt=min(.15,max(0,self.now-self._last_tick));self._last_tick=self.now
        if self.power_bridge is not None:self.power_bridge.poll()
        # Consume before processing capture completions: a busy-period edge must
        # never turn into a fresh shutter simply because saving just finished.
        self._poll_l1()
        changed=False
        target=1. if self.info_visible else 0.
        if abs(self.info_amount-target)>.001:
            self.info_amount+=math.copysign(min(abs(target-self.info_amount),dt/0.18),target-self.info_amount);changed=True
        if abs(self.panel_progress-self._panel_target)>.001:
            self.panel_progress+=math.copysign(min(abs(self._panel_target-self.panel_progress),dt/.22),self._panel_target-self.panel_progress);changed=True
            if self.panel_progress<.001 and self._panel_target==0:self.panel=None
        if abs(self.panel_progress-self._panel_target)<=.001:self.panel_progress=self._panel_target
        if abs(self.info_amount-target)<=.001:self.info_amount=target
        self._finish_settings();self._handle_files();self._poll_share()
        if self._confirm_wait_until:
            frame=self.camera.snapshot()
            if frame.error or self.now>self._confirm_wait_until:
                self._confirm_wait_until=0.;self._notify('参数未保存：'+(frame.error or '等待相机生效超时'),7)
            elif frame.ready:
                self._confirm_wait_until=0.;self.confirm_panel()
        if self._hardware_job is not None and self._hardware_job.done():
            try:
                previous=self.hardware_caps;self.hardware_caps,self.battery=self._hardware_job.result()
                if self.hardware_caps.get('brightness') and not previous.get('brightness'):self._queue_device('brightness',self.device.set_brightness,self.settings.brightness)
            except Exception:log.exception('hardware discovery failed')
            self._hardware_job=None;self._hardware_at=self.now;changed=True
        if self._hardware_job is None and self.now-self._hardware_at>10 and self.exit_job is None:self._hardware_job=self.pool.submit(self._read_hardware)
        if self.capture_job is not None and self.capture_job.done():
            job=self.capture_job;self.capture_job=None
            try:
                record=job.result();log.info('saved path=%s dimensions=%sx%s bytes=%s sha256=%s',record.path,record.width,record.height,record.byte_count,record.sha256)
                log.info('capture_result source=%s saved=%s sha256=%s',self._shot_source,record.path,record.sha256)
                if self._shot_source.startswith('L1#'):self.l1_saved+=1
                self._scan_token+=1
                self.photos=[record]+[p for p in self.photos if p.path!=record.path]
                if self.settings.sound and self.hardware_caps.get('sound'):self._queue_device('sound',self.device.play_sound,True)
                if self.exit_job is None and not self._power_busy:
                    if self.settings.review_seconds:
                        self._view_token+=1;read=self.pool.submit(load_image,record.path);self._file_jobs.append((read,'review',self._view_token,record.path));self.file_job=read
                    else:self.return_live();self._notify('已保存')
                    self._ensure_visible_thumbnails()
            except Exception as error:
                log.error('capture_result source=%s failed=%s',self._shot_source,error)
                self.page='error';self.message=str(error);self.toast_until=0;log.exception('capture failed')
                if self.settings.sound and self.hardware_caps.get('sound'):self._queue_device('sound',self.device.play_sound,False)
            changed=True
        if self.exit_job is not None and self.exit_job.done():
            job=self.exit_job;self.exit_job=None
            try:
                job.result()
                if self._exit_shutdown:
                    self.page='system';self.message='正常关机请求已接受\n等待系统结束；不据此判断可取卡';self.timer.stop();self.update();return
                if self.power_bridge is not None:self.power_bridge.close()
                self.exit_complete=True;self.timer.stop();self.settings_pool.shutdown(wait=False);self.pool.shutdown(wait=False);self.close();return
            except Exception as error:self.page='error';self.message='退出或关机未完成：\n'+str(error);self.toast_until=0;log.exception('close failed')
            changed=True
        if self.countdown_deadline is not None and self.now>=self.countdown_deadline:
            self.countdown_deadline=None
            if self.camera.snapshot().ready:self._start_capture()
            else:self._notify('取景已中断，自拍已取消',6);self._frozen_shot=None
        if self.page=='review' and self.modal is None and self.review_until is not None and self.now>=self.review_until:self.return_live();changed=True
        self._poll_preview()
        if self.exit_job is None and not self._power_busy and not self._preview_closed:self._ensure_visible_thumbnails()
        # File completions and settings changes may alter the page even for a
        # constant fixture sequence. Repainting does not recalculate image data.
        self.update()
    def _logical(self,pos):
        scale=min(self.width()/480,self.height()/800);ox=(self.width()-480*scale)/2;oy=(self.height()-800*scale)/2
        return ((pos.x()-ox)/scale,(pos.y()-oy)/scale)
    def _hit(self,x,y):
        for name,(x1,y1,x2,y2) in reversed(list(self._targets.items())):
            if x1<=x<=x2 and y1<=y<=y2:return name
        return None
    def _pointer_begin(self,pos):
        x,y=self._logical(pos);self._gesture={'start':(x,y),'last':(x,y),'target':self._hit(x,y),'time':time.monotonic(),'pan':(self.pan_x,self.pan_y)}
    def _pointer_move(self,pos):
        if not self._gesture:return
        x,y=self._logical(pos);g=self._gesture;g['last']=(x,y);target=g['target'];sx,sy=g['start']
        if self.modal:return
        if target in ('temperature','ev_slider','brightness_slider'):
            self._drag_value(target,x);g['dragged']=True
        elif self.page=='gallery' and self.gallery_mode=='single' and self.view_zoom>1 and target is None and 86<=sy<=618 and math.hypot(x-sx,y-sy)>=6:
            self.pan_x=g['pan'][0]+x-sx;self.pan_y=g['pan'][1]+y-sy;self._clamp_pan();g['dragged']=True;self.update()
        elif target and target.startswith('value_') and abs(x-sx)>30:
            self._step(target[6:],1 if x>sx else -1);g['start']=(x,sy);g['dragged']=True
    def _drag_value(self,target,x):
        if target=='ev_slider':self.change_draft(ev=round(max(-2,min(2,(x-48)/384*4-2))*3)/3)
        elif target=='temperature':
            caps=getattr(self.camera,'capabilities',{}) or {};low,high=caps.get('wb_kelvin_range',(2800,8000));value=round((low+max(0,min(1,(x-80)/320))*(high-low))/100)*100;self.change_draft(wb_kelvin=max(round(low),min(round(high),value)))
        elif target=='brightness_slider':
            value=round(20+max(0,min(1,(x-60)/360))*80)
            if self.hardware_caps.get('brightness'):self._brightness_drag=value;self.update()
            else:self._notify('当前没有可写的屏幕背光接口')
    def _pointer_end(self,pos):
        if not self._gesture:return
        x,y=self._logical(pos);g=self._gesture;self._gesture=None;sx,sy=g['start'];dx=x-sx;dy=y-sy;distance=math.hypot(dx,dy);target=g['target']
        if self.modal:
            if self.modal=='delete' and abs(dy)>30 and len(self.delete_plan.paths)>4:
                self.delete_offset=max(0,min(len(self.delete_plan.paths)-4,self.delete_offset+(4 if dy<0 else -4)));self.update();return
            if distance<20 and target=='modal_confirm':self.confirm_modal()
            elif distance<20 and target=='modal_cancel':self.cancel_modal()
            return
        if self.exit_job is not None or self.page=='system':return
        if target in ('temperature','ev_slider','brightness_slider'):
            self._drag_value(target,x)
            if target=='brightness_slider' and hasattr(self,'_brightness_drag'):self.set_setting(brightness=self._brightness_drag)
            return
        if g.get('dragged'):return
        if self.page=='live' and sy<=56 and abs(dy)>25 and abs(dy)>abs(dx)*1.2:
            self.info_visible=dy>0;self.update();return
        if self.panel is not None:
            if dx<-45 and abs(dx)>abs(dy):self.dismiss_panel(close=self.panel=='menu');return
            if distance<20:
                if not (16<=x<=464 and 266.5<=y<=533.5):self.dismiss_panel(close=self.panel=='menu');return
                if self.panel_progress>.98:self._action(target)
            return
        if self.page=='live' and sx>=440 and dx<-45 and abs(dx)>abs(dy):self.open_panel();return
        if self.page=='more' and abs(dy)>45 and abs(dy)>abs(dx):self.more_group=1 if dy<0 else 0;self.update();return
        if self.page=='gallery' and abs(dy)>45 and self.gallery_mode=='grid':
            self.gallery_scroll=max(0,min(max(0,((len(self.photos)-1)//3-2)*3),self.gallery_scroll+(3 if dy<0 else -3)));self._ensure_visible_thumbnails();self.update();return
        if self.page=='gallery' and self.gallery_mode=='single' and abs(dx)>45 and abs(dx)>abs(dy):self._load_photo(self.photo_index+(1 if dx<0 else -1));return
        if distance<20:
            if self.page=='live' and y<=56 and x<=310:self.info_visible=not self.info_visible;self.update();return
            self._action(target)
    def _ensure_visible_thumbnails(self):
        if self.exit_job is not None or self.exit_complete or self._power_busy or self._preview_closed:return
        if self.page=='gallery':
            start=self.gallery_scroll if self.gallery_mode=='grid' else max(0,self.photo_index-3)
            wanted=self.photos[start:start+(12 if self.gallery_mode=='grid' else 7)]
        else:wanted=self.photos[:1]
        keep={str(r.path) for r in wanted}
        while len(self.thumbnails)>48:
            key=next((k for k in self.thumbnails if k not in keep),None)
            if key is None:break
            self.thumbnails.pop(key)
        for record in wanted:
            name=str(record.path)
            if len(self._thumb_pending)>=2:break
            if name in self.thumbnails or name in self._thumb_pending or name in self._thumb_failed:continue
            job=self.pool.submit(load_image,record.path,(160,160));self._thumb_pending[name]=job
            self._file_jobs.append((job,'thumbnail',self._scan_token,record.path))
    def _clamp_pan(self):
        if self.gallery_image is None:return
        scale=min(480/self.gallery_image.width(),532/self.gallery_image.height())*self.view_zoom
        bx=max(0,(self.gallery_image.width()*scale-480)/2);by=max(0,(self.gallery_image.height()*scale-532)/2)
        self.pan_x=max(-bx,min(bx,self.pan_x));self.pan_y=max(-by,min(by,self.pan_y))
    def _action(self,key):
        if not key:return
        if self._power_busy:return
        if key=='ack_error':self.acknowledge_error();return
        if key=='exit':self.request_exit();return
        if key=='shutdown':self.request_shutdown();return
        if self.busy:
            if key=='shutter':self._notify('正在拍摄或保存，本次未排队')
            return
        if key=='cancel_timer':self.cancel_countdown();return
        if self.countdown_deadline is not None:return
        if key=='confirm_panel':self.confirm_panel()
        elif key=='ael':self._change_ael()
        elif key.startswith('cell_'):
            name=key[5:]
            if name in ('hdr','beauty','histogram'):self.toggle_setting(name)
            else:self.open_panel(name)
        elif key.startswith('mode_'):
            mode='auto' if key.endswith('0') else 'manual';self.change_draft(**{'exposure_mode' if self.panel=='exposure' else 'wb_mode':mode})
        elif key.endswith('_minus') or key.endswith('_plus'):
            base,sign=key.rsplit('_',1);self._step(base,-1 if sign=='minus' else 1)
        elif key=='zoom':self.set_setting(zoom=2 if self.settings.zoom==1 else 1)
        elif key=='shutter':self.trigger_shutter()
        elif key=='end_review':self.return_live()
        elif key=='gallery':self.open_gallery()
        elif key=='edge':self.open_panel()
        elif key=='more':self._view_token+=1;self.review_until=None;self.page='more';self.more_group=0;self.update()
        elif key=='back':
            if self.page=='more':self.return_live()
            else:self.page='more';self.update()
        elif key in ('fullscreen','sound','mirror'):self.toggle_setting(key)
        elif key in ('ratio','format','timer','review_setting','brightness','orientation','power'):self.page=key;self.update()
        elif key.startswith('ratio_'):self.set_setting(ratio=key[6:])
        elif key.startswith('rotation_'):self.set_setting(rotation=int(key[9:]))
        elif key.startswith('choice_'):
            value=key[7:]
            if self.page=='format':
                if value!='JPEG':self.modal='raw';self.update()
            elif self.page=='timer':self.set_setting(self_timer=int(value))
            else:self.set_setting(review_seconds=int(value))
        elif key=='send':self.begin_send()
        elif key in ('share_back','share_back_top'):self.return_share_gallery()
        elif key=='share_retry':self.begin_send()
        elif key.startswith('share_address_'):self.choose_share_address(int(key[14:]))
        elif key=='gallery_back':self.return_live()
        elif key=='select_mode':self.select_mode=not self.select_mode;self.selected.clear();self.update()
        elif key=='select_all':self.selected=set() if len(self.selected)==len(self.photos) else {record.path for record in self.photos};self.update()
        elif key.startswith('photo_'):
            index=int(key[6:])
            if self.select_mode:self.toggle_selection(self.photos[index].path)
            else:self.gallery_mode='single';self._load_photo(index)
        elif key.startswith('film_'):self._load_photo(int(key[5:]))
        elif key=='grid':self.gallery_mode='grid';self._ensure_visible_thumbnails();self.update()
        elif key in ('delete_one','delete_selected'):self.begin_delete()
        elif key=='fit_photo':self.view_zoom=1.;self.pan_x=self.pan_y=0.;self.update()
    def mousePressEvent(self,event):
        if time.monotonic()<self._suppress_mouse_until:return
        if event.button()==Qt.LeftButton:self._pointer_begin(event.localPos());event.accept()
    def mouseMoveEvent(self,event):
        if time.monotonic()<self._suppress_mouse_until:return
        self._pointer_move(event.localPos());event.accept()
    def mouseReleaseEvent(self,event):
        if time.monotonic()<self._suppress_mouse_until:return
        if event.button()==Qt.LeftButton:self._pointer_end(event.localPos());event.accept()
    def wheelEvent(self,event):
        if self.page=='gallery' and self.gallery_mode=='single' and self.gallery_image is not None:
            self.view_zoom=max(1.,min(8.,self.view_zoom*(1.2 if event.angleDelta().y()>0 else 1/1.2)));self._clamp_pan();self.update();event.accept()
        elif self.page=='gallery' and self.gallery_mode=='grid':
            self.gallery_scroll=max(0,min(max(0,((len(self.photos)-1)//3-2)*3),self.gallery_scroll+(3 if event.angleDelta().y()<0 else -3)));self._ensure_visible_thumbnails();self.update();event.accept()
        elif self.page=='more':self.more_group=1 if event.angleDelta().y()<0 else 0;self.update();event.accept()
        else:event.ignore()
    def event(self,event):
        if event.type() in (QEvent.TouchBegin,QEvent.TouchUpdate,QEvent.TouchEnd,QEvent.TouchCancel):
            points=event.touchPoints();self._suppress_mouse_until=time.monotonic()+.5
            if event.type()==QEvent.TouchCancel:self._gesture=None;self._pinch=None;event.accept();return True
            if event.type()==QEvent.TouchEnd:
                if self._pinch is None and self._gesture is not None and points:self._pointer_end(points[0].pos())
                self._gesture=None;self._pinch=None;event.accept();return True
            if len(points)>=2:
                self._gesture=None
                if self.page=='gallery' and self.gallery_mode=='single' and self.gallery_image is not None and not self.modal:
                    a,b=points[0].pos(),points[1].pos();distance=math.hypot(a.x()-b.x(),a.y()-b.y())
                    if self._pinch is None:self._pinch=(max(1,distance),self.view_zoom)
                    else:self.view_zoom=max(1.,min(8.,self._pinch[1]*distance/self._pinch[0]));self._clamp_pan();self.update()
            elif points:
                if self._pinch is not None:
                    if event.type()==QEvent.TouchEnd:self._pinch=None
                elif event.type()==QEvent.TouchBegin:self._pointer_begin(points[0].pos())
                elif event.type()==QEvent.TouchUpdate:self._pointer_move(points[0].pos())
                elif event.type()==QEvent.TouchEnd:self._pointer_end(points[0].pos())
            event.accept();return True
        return super().event(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:
            if self.modal:self.cancel_modal()
            elif self.panel:self.dismiss_panel(close=self.panel=='menu')
            elif self.page=='share':self.return_share_gallery()
            elif self.page!='live':self.return_live()
            else:self.request_exit()
        elif event.key() in (Qt.Key_Space,Qt.Key_Return):self.trigger_shutter('keyboard')
        else:super().keyPressEvent(event)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(),QColor('#000000'));scale=min(self.width()/480,self.height()/800);p.translate((self.width()-480*scale)/2,(self.height()-800*scale)/2);p.scale(scale,scale);p.setClipRect(QRectF(0,0,480,800))
        self._targets={};a=artwork.Artist(p,self._targets)
        s=self.effective_settings
        if self.page not in ('gallery','share','review','error','system'):
            if self._context is not None:
                if s.fullscreen:
                    p.drawImage(QRectF(0,0,480,800),self._context);x,y,w,h=capture_rect(s.ratio)
                    for rect in [(0,0,480,y),(0,y+h,480,800-y-h),(0,y,x,h),(x+w,y,480-x-w,h)]:
                        if rect[2]>0 and rect[3]>0:p.fillRect(QRectF(*rect),QColor(0,0,0,153))
                else:
                    rw,rh=map(int,s.ratio.split(':'));fw=min(480,628*rw/rh);fh=fw*rh/rw;a.image(self._capture_image,((480-fw)/2,44+(628-fh)/2,fw,fh),True)
            else:a.text(240,350,'等待实时画面…',22,artwork.MUTED,align='center')
        if self.page=='live':artwork.draw_live(a,self)
        elif self.page=='more':artwork.draw_more(a,self)
        elif self.page in ('ratio','format','timer','review_setting','brightness','orientation','power'):artwork.draw_setting_page(a,self)
        elif self.page=='gallery':artwork.draw_gallery(a,self)
        elif self.page=='share':artwork.draw_share(a,self)
        elif self.page=='review':artwork.draw_review(a,self)
        else:artwork.draw_status(a,self)
        if self._power_busy:
            bridge=self.power_bridge
            a.rect((0,0,480,800),QColor(0,0,0,175))
            label=('关机结果待确认，暂停拍摄' if bridge.phase=='uncertain' else
                   '等待照片与设置保存…' if bridge.phase in ('preparing','canceling') else '等待系统正常关机…')
            a.text(240,365,label,22,align='center')
            message='请保持电池连接' if self.now>=self.toast_until else self.message.replace('\n',' ')[:32]
            a.text(240,410,message,15,artwork.MUTED,align='center');self._targets={}
        elif self.exit_job is not None:
            a.rect((0,0,480,800),QColor(0,0,0,160));a.text(240,365,'等待拍摄与保存完成…',23,align='center');a.text(240,410,'随后正常关机' if getattr(self,'_exit_shutdown',False) else '随后返回桌面',18,artwork.MUTED,align='center');self._targets={}
        elif self.modal:artwork.draw_modal(a,self)
        elif self.message and self.now<self.toast_until and self.page not in ('error','system'):
            a.glass((22,126,458,186),225,15);text=self.message.replace('\n',' ');a.text(240,156,text[:34],15,align='center')
        if self.l1 is not None and self.page=='live' and self.exit_job is None:
            status=self.l1.snapshot()
            label=('故障，请看日志' if status['error'] else
                   ('等待初始化' if status['pressed'] is None else ('拨住' if status['pressed'] else '松开')))
            a.rect((0,774,480,800),QColor(0,0,0,190))
            a.text(240,787,'L1 {} · 输入 {} · 已保存 {}'.format(label,status['count'],self.l1_saved),12,align='center')
        p.end()
        if self.first_preview_ready and not self._first_preview_painted and self.on_first_preview_painted is not None:
            self._first_preview_painted=True
            callback=self.on_first_preview_painted
            QTimer.singleShot(0,callback)
    def closeEvent(self,event):
        if self.exit_complete:event.accept()
        else:event.ignore();self.request_exit()
