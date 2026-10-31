"""One accepted photograph at a time; real capture and durable save precede success."""
from concurrent.futures import Future
from dataclasses import asdict, dataclass
from pathlib import Path
import threading
import logging
import time
from typing import Optional

from .state import AppSettings
from .storage import PhotoRecord

log = logging.getLogger(__name__)


class CaptureBlocked(RuntimeError):
    pass


class CaptureBusy(CaptureBlocked):
    pass


@dataclass(frozen=True)
class CaptureStatus:
    phase: str
    photo: Optional[PhotoRecord] = None
    error: str = ''
    error_type: str = ''
    possible_path: Optional[Path] = None


class CaptureService:
    def __init__(self, camera, store):
        self.camera = camera
        self.store = store
        self._condition = threading.Condition()
        self._busy = False
        self._closing = False
        self._power_preparing = False
        self._status = CaptureStatus('live')
        self._failure_count = 0
        self._last_failure = ''

    def snapshot(self):
        with self._condition:
            return self._status

    def request(self, settings):
        if not isinstance(settings, AppSettings):
            raise TypeError('AppSettings is required')
        future = Future()
        with self._condition:
            if self._closing or self._power_preparing or self._status.phase == 'closed':
                raise CaptureBlocked('正在退出，不能开始下一张')
            if self._busy:
                raise CaptureBusy('正在拍摄或保存；本次未排队')
            if self._status.phase == 'error':
                raise CaptureBlocked('请先处理拍摄错误')
            self._busy = True
            self._status = CaptureStatus('capturing')
            future.set_running_or_notify_cancel()
        worker = threading.Thread(target=self._capture, args=(settings, future),
                                  name='junshu-r4-capture', daemon=False)
        try:
            worker.start()
        except Exception as error:
            self._finish(error=error)
            future.set_exception(error)
        return future

    def _capture(self, settings, future):
        stage = 'capture'
        try:
            # Deferred import keeps storage/settings usable on machines without a camera backend.
            from .framing import compose_frame
            log.info('photo_stage=capture started')
            source = self.camera.capture_image(settings)
            stage = 'compose'
            framed = compose_frame(source, settings)
            with self._condition:
                self._status = CaptureStatus('saving')
            metadata = {'settings': asdict(settings),
                        'camera': dict(getattr(self.camera, 'last_still_metadata', {}) or {}),
                        'crop_box': list(framed.box), 'source_size': list(framed.source_size),
                        'iso_scale': 'ISO100=AnalogueGain1 reference; not sensor-calibrated',
                        'hdr_processing': False}
            stage = 'save'
            log.info('photo_stage=save started')
            photo = self.store.commit(framed.image, metadata)
        except Exception as error:
            log.exception('photo_stage=%s failed', stage)
            self._finish(error=error)
            future.set_exception(error)
        else:
            self._finish(photo=photo)
            future.set_result(photo)

    def _finish(self, photo=None, error=None):
        with self._condition:
            if error is not None:
                self._failure_count += 1
                self._last_failure = str(error)
                self._status = CaptureStatus('error', error=str(error), error_type=type(error).__name__,
                                             possible_path=getattr(error, 'path', None))
                self._closing = False
            else:
                self._status = CaptureStatus('saved', photo=photo)
            self._busy = False
            self._condition.notify_all()

    def recover(self):
        """Acknowledge failure; camera readiness is still decided by its real frames."""
        with self._condition:
            if self._busy or self._closing or self._power_preparing or self._status.phase == 'closed':
                raise CaptureBlocked('当前状态不能恢复取景')
            if self._status.phase == 'error':
                self._status = CaptureStatus('live')

    def prepare_shutdown(self, timeout=50, cancel_event=None):
        """Drain accepted work while retaining resources for a failed power request."""
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._closing or self._status.phase == 'closed':
                raise CaptureBlocked('相机已在退出，不能准备另一项关机请求')
            self._power_preparing = True
            try:
                while self._busy:
                    if cancel_event is not None and cancel_event.is_set():
                        raise CaptureBlocked('关机准备已取消')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('拍摄或保存尚未结束，本次关机取消')
                    self._condition.wait(min(.1, remaining))
                if cancel_event is not None and cancel_event.is_set():
                    raise CaptureBlocked('关机准备已取消')
                if self._status.phase == 'error':
                    raise CaptureBlocked('保存未成功，本次关机取消：'+self._status.error)
            except Exception:
                self._power_preparing = False
                raise

    def cancel_shutdown(self):
        """Release only the power-preparation gate; never reopen a closed service."""
        with self._condition:
            self._power_preparing = False
            self._condition.notify_all()

    def close(self, timeout=30):
        """Wait in an exit worker; failure cancels exit and timeout retains accepted work."""
        with self._condition:
            failure_count = self._failure_count
            self._closing = True
            if not self._condition.wait_for(lambda: not self._busy or self._failure_count != failure_count,
                                            timeout=timeout):
                raise TimeoutError('拍摄或保存尚未结束，程序仍在运行')
            if self._failure_count != failure_count:
                raise CaptureBlocked('本次退出已取消：{}'.format(self._last_failure))
            if self._status.phase == 'error':
                self._closing = False
                raise CaptureBlocked('本次退出已取消：{}'.format(self._status.error))
            self._status = CaptureStatus('closed', photo=self._status.photo)
