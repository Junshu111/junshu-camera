"""Async QR/session ownership; revocation is an explicit shutdown dependency."""
from concurrent.futures import Future,ThreadPoolExecutor,TimeoutError as FutureTimeout
import threading,time
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage,QPainter,QColor
from .vendor.qrcodegen import QrCode

class ShareCloseError(RuntimeError):
    pass

def start_session(*args,**kwargs):
    from .photo_share import PhotoShareSession
    return PhotoShareSession.start(*args,**kwargs)

def qr_image(url):
    """Four-module quiet zone, integer scale and hard edges; callable off Qt thread."""
    code=QrCode.encode_text(url,QrCode.Ecc.MEDIUM)
    modules=code.get_size()+8;scale=max(1,360//modules);size=modules*scale
    image=QImage(size,size,QImage.Format_RGB32);image.fill(Qt.white)
    painter=QPainter(image);painter.setPen(Qt.NoPen);painter.setBrush(QColor('#000000'))
    for y in range(code.get_size()):
        for x in range(code.get_size()):
            if code.get_module(x,y):painter.drawRect((x+4)*scale,(y+4)*scale,scale,scale)
    painter.end();return image

class ShareController:
    """One generation at a time; stale workers revoke their own session before returning."""
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='junshu-share')
        self.lock=threading.RLock();self.epoch=0;self._sessions=set()
        self.start_job=None;self.close_job=None;self.session=None;self.qr=None;self.error=''
        self._closed=False
    @property
    def revoking(self):return self.close_job is not None and not self.close_job.done()
    @property
    def pending(self):return self.start_job is not None and not self.start_job.done()
    def start(self,directory,paths,address):
        if self._closed or self.pending or self.revoking or self.session is not None:
            raise RuntimeError('请先关闭当前发送')
        with self.lock:self.epoch+=1;epoch=self.epoch
        self.error='';self.qr=None
        self.start_job=self.pool.submit(self._start,directory,tuple(paths),address,epoch)
    def _close_one(self,session):
        try:session.close()
        except Exception as error:raise ShareCloseError('照片发送尚未安全关闭，请重试关闭') from error
        with self.lock:self._sessions.discard(session)
    def _start(self,directory,paths,address,epoch):
        session=start_session(directory,list(paths),address,ttl_seconds=900)
        with self.lock:self._sessions.add(session)
        try:
            image=qr_image(session.url)
            with self.lock:stale=epoch!=self.epoch
            if stale:self._close_one(session);return None
            return epoch,session,image
        except ShareCloseError:raise
        except Exception:
            self._close_one(session);raise
    def poll(self):
        if self.start_job is not None and self.start_job.done():
            job,self.start_job=self.start_job,None
            try:
                result=job.result()
                if result is not None:
                    epoch,session,image=result
                    with self.lock:current=epoch==self.epoch
                    if current:self.session=session;self.qr=image
                    else:self.close_job=self.pool.submit(self._close_one,session)
            except Exception as error:self.error=str(error)
        if self.close_job is not None and self.close_job.done():
            job,self.close_job=self.close_job,None
            try:job.result()
            except Exception as error:self.error=str(error)
    def revoke_async(self):
        if self._closed:
            done=Future();done.set_result(None);return done
        with self.lock:self.epoch+=1
        self.session=None;self.qr=None
        if self.revoking:return self.close_job
        startup=self.start_job
        self.close_job=self.pool.submit(self._revoke,startup)
        return self.close_job
    def _revoke(self,startup):
        if startup is not None:
            try:startup.result(timeout=4)
            except FutureTimeout:raise ShareCloseError('照片发送仍在关闭，请稍后重试')
            except ShareCloseError:raise
            except Exception:pass  # Startup failed without a published session.
        with self.lock:sessions=tuple(self._sessions)
        for session in sessions:self._close_one(session)
    def shutdown(self):
        """Called only after a successful revoke dependency, before GUI executor closes."""
        self._closed=True;self.pool.shutdown(wait=False)
