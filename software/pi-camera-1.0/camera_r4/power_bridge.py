"""Qt-thread bridge: drain durable work, report readiness, retain resources on failure."""
from concurrent.futures import TimeoutError as FutureTimeout
import logging
import threading
import time

log = logging.getLogger(__name__)


def drain(capture, pending, cancel, deadline):
    for job in pending:
        while True:
            if cancel.is_set():
                raise RuntimeError('关机准备已取消')
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('等待照片或设置保存超时，本次关机取消')
            try:
                job.result(timeout=min(.1, remaining))
                break
            except FutureTimeout:
                if job.done():
                    raise  # the job itself raised TimeoutError
    capture.prepare_shutdown(timeout=max(0, deadline-time.monotonic()), cancel_event=cancel)


class PowerBridge:
    def __init__(self, window, client):
        self.window = window
        self.client = client
        self.request_id = None
        self.phase = 'idle'
        self.job = None
        self.cancel = threading.Event()
        self.closed = False
        self._cancel_reason = ''
        self._was_connected = False

    @property
    def busy(self):
        return self.phase != 'idle' or self.job is not None

    @property
    def committed(self):
        return self.phase == 'committed'

    def _reject(self, request_id, reason):
        try:
            self.client.reject(request_id, str(reason))
        except Exception:
            log.exception('power reply failed')
        log.warning('power request=%s rejected=%s', request_id, reason)

    def _begin(self, request_id):
        if not isinstance(request_id, str) or not request_id:
            return
        if self.busy:
            if request_id != self.request_id:
                self._reject(request_id, '上一项电源请求尚未结束')
            return
        w = self.window
        if w.exit_job is not None or w.exit_complete or w._settings_failure:
            self._reject(request_id, w._settings_failure or '相机正在退出')
            w._notify('按钮关机未执行：'+(w._settings_failure or '相机正在退出'), 8)
            return
        self.request_id = request_id
        self.phase = 'preparing'
        self.cancel = threading.Event()
        self._cancel_reason = ''
        w.cancel_countdown()
        w._confirm_wait_until = 0.
        w.modal = None
        w.delete_plan = None
        w._view_token += 1
        w.review_until = None
        if w.l1 is not None:
            w.l1.gate.set(False)
        pending = [entry[0] for entry in w._settings_jobs]
        pending.append(w.close_share())
        preview=w._pause_preview()
        if preview is not None:pending.append(preview)
        if w.capture_job is not None:
            pending.append(w.capture_job)
        if w.delete_job is not None:
            pending.append(w.delete_job)
        self.job = w.pool.submit(drain, w.capture, pending, self.cancel, time.monotonic()+50)
        log.info('power request=%s phase=draining accepted_jobs=%d', request_id, len(pending))
        w.update()

    def _abort(self, reason):
        if self.committed:
            return
        self.cancel.set()
        self._cancel_reason = str(reason)
        if self.job is None:
            self._resume()
        else:
            self.phase = 'canceling'

    def _resume(self):
        self.window.capture.cancel_shutdown()
        self.phase = 'idle'
        self.request_id = None
        if self._cancel_reason and not self.closed:
            self.window._notify('按钮关机已取消：'+self._cancel_reason, 9)
        self._cancel_reason = ''
        self.window.update()

    def poll(self):
        if not self.closed:
            try:
                events = self.client.poll()
            except Exception as error:
                log.exception('power client polling failed')
                events = [{'type':'status','state':'disconnected','reason':str(error)}]
                if self.phase == 'preparing':
                    self._abort('电源服务连接中断')
            for event in events:
                kind = event.get('type')
                if kind == 'prepare':
                    self._begin(event.get('id'))
                elif kind == 'cancel' and event.get('id') == self.request_id:
                    self._abort(event.get('reason', '电源服务取消请求'))
                elif kind == 'committed' and event.get('id') == self.request_id and self.phase == 'ready':
                    self.phase = 'committed'
                    log.info('power request=%s phase=system_poweroff_accepted', self.request_id)
                elif kind == 'status':
                    state = event.get('state')
                    if event.get('phase') in ('committed', 'uncertain'):
                        # A fresh GUI may connect after another GUI has already
                        # acknowledged poweroff. Root's pending outcome applies
                        # even without a local transaction; never start new work.
                        self.cancel.set()
                        self.request_id = event.get('id')
                        self.phase = event['phase']
                        self.window.close_share()
                        self.window._pause_preview()
                        self.window.cancel_countdown()
                        self.window.modal = None
                        self.window.review_until = None
                        self.window._confirm_wait_until = 0.
                        if self.window.l1 is not None:
                            self.window.l1.gate.set(False)
                        self.window._notify('关机结果待确认；暂停新拍摄，请保持供电', 15)
                    if state == 'connected':
                        self._was_connected = True
                    elif state == 'disconnected':
                        if self.phase in ('ready', 'uncertain'):
                            # READY may already have caused poweroff; lost response is not cancellation.
                            self.window._notify('电源服务连接中断，关机结果待确认；暂停新拍摄', 15)
                        elif self.phase == 'preparing':
                            self._abort('电源服务连接中断')
                        elif self._was_connected and not self.committed:
                            self.window._notify('电源按钮服务已断开；请检查服务日志', 9)
                            self._was_connected = False
        if self.job is not None and self.job.done():
            job, self.job = self.job, None
            try:
                job.result()
                if self.phase in ('committed', 'uncertain') and not self.closed:
                    return
                if self.cancel.is_set() or self.closed:
                    self._resume()
                else:
                    # Set ready before sending: a transport error may follow partial delivery.
                    self.phase = 'ready'
                    try:
                        self.client.ready(self.request_id)
                    except Exception:
                        log.exception('power READY outcome uncertain; retaining freeze')
                        self.window._notify('关机确认发送结果未知，暂停新拍摄；请检查服务日志', 15)
                    log.info('power request=%s phase=saved_ready', self.request_id)
            except Exception as error:
                if self.phase in ('committed', 'uncertain') and not self.closed:
                    log.warning('power drain ended during pending system outcome: %s', error)
                    return
                if not self.cancel.is_set() and not self.closed:
                    self._reject(self.request_id, error)
                    self._cancel_reason = str(error)
                self._resume()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.request_id and not self.committed:
            self._reject(self.request_id, '相机正在退出')
        self.cancel.set()
        self.client.close()
        if self.job is None:
            self._resume()
