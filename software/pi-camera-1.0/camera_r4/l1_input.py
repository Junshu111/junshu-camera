"""L1 input only. No Qt calls, camera ownership, output pins or power actions."""
from dataclasses import dataclass
import logging
import threading
import time

log=logging.getLogger(__name__)


class InputGate:
    def __init__(self):
        self._lock=threading.Lock();self._enabled=False;self._epoch=0
    def set(self,enabled):
        with self._lock:
            if bool(enabled)!=self._enabled:
                self._enabled=bool(enabled);self._epoch+=1
    def token(self):
        with self._lock:return self._enabled,self._epoch


@dataclass(frozen=True)
class LeverEvent:
    number:int
    at:float
    token:tuple
    eligible:bool


class DebouncedLever:
    def __init__(self):
        self.raw=None;self.stable=None;self.since=0.;self.armed=False
        self.count=0;self.press_token=None
    def sample(self,pressed,now,token):
        if type(pressed) is not bool:raise ValueError('L1读数必须明确为高/低电平')
        if pressed!=self.raw:
            self.raw=pressed;self.since=now
            if pressed and self.press_token is None:self.press_token=token
            log.info('L1 raw=%s monotonic=%.6f', 'LOW' if pressed else 'HIGH',now)
        if now-self.since<.05:return None
        if not pressed:self.press_token=None
        if self.stable is None:
            self.stable=pressed;self.armed=not pressed
            log.info('L1 initial=%s armed=%s; startup state never fires',pressed,self.armed)
            return None
        if self.stable==pressed:return None
        self.stable=pressed
        log.info('L1 stable=%s monotonic=%.6f','pressed' if pressed else 'released',now)
        if not pressed:self.armed=True;return None
        if not self.armed:return None
        self.armed=False;self.count+=1
        eligible=bool(self.press_token[0] and token==self.press_token)
        log.info('L1 press id=%d eligible=%s gate=%s',self.count,eligible,token)
        return LeverEvent(self.count,now,self.press_token,eligible)


class L1Input:
    def __init__(self,factory=None):
        self.gate=InputGate();self._factory=factory
        self._stop=threading.Event();self._lock=threading.Lock();self._thread=None
        self._release_error=None
        self._pending=None;self._state={'state':'starting','pressed':None,'count':0,'error':''}
    def start(self):
        if self._thread is not None:raise RuntimeError('L1已启动')
        self._thread=threading.Thread(target=self._run,name='junshu-l1-input',daemon=False)
        self._thread.start()
    def snapshot(self):
        with self._lock:return dict(self._state)
    def _publish(self,event):
        with self._lock:
            if not event.eligible:
                log.info('L1 ignored id=%d reason=busy_or_gate_changed_at_press',event.number)
            elif self._pending is not None:
                log.info('L1 ignored id=%d reason=input_slot_occupied; no queue',event.number)
            else:self._pending=event
    def take_event(self,now=None):
        now=time.monotonic() if now is None else now
        with self._lock:event,self._pending=self._pending,None
        if event is None:return None
        if event.token!=self.gate.token() or not self.gate.token()[0] or not 0<=now-event.at<=.25:
            log.info('L1 ignored id=%d reason=gate_changed_or_expired; no queue',event.number)
            return None
        return event
    def _run(self):
        line=None;lever=DebouncedLever()
        try:
            factory=self._factory
            if factory is None:
                from .gpio_probe import GPIO17Input
                factory=GPIO17Input
            line=factory()
            log.info('L1 acquired BCM17 input pull-up active-low; no other pins requested')
            while not self._stop.is_set():
                # Stamp BEFORE the hardware read: a paused input thread must not
                # relabel a busy-period LOW sample with a newly reopened gate.
                token=self.gate.token()
                event=lever.sample(line.read(),time.monotonic(),token)
                with self._lock:
                    self._state.update(state='ready',pressed=lever.stable,count=lever.count)
                if event is not None:self._publish(event)
                self._stop.wait(.005)
        except Exception as error:
            self.gate.set(False)
            with self._lock:
                self._pending=None;self._state.update(state='error',error=str(error))
            log.exception('L1 input failed; disabled')
        finally:
            try:
                if line is not None:line.close()
                log.info('L1 GPIO request released')
            except Exception as error:
                self._release_error=error
                with self._lock:self._state.update(state='error',error='GPIO释放失败：'+str(error))
                log.exception('L1 release failed')
            with self._lock:
                self._pending=None
                if self._state['state']!='error':self._state['state']='closed'
    def close(self):
        self.gate.set(False);self._stop.set()
        if self._thread is not None:
            self._thread.join(10)
            if self._thread.is_alive():raise TimeoutError('L1尚未释放，请保留日志')
        if self._release_error is not None:
            raise RuntimeError('L1 GPIO释放未确认，请保留日志') from self._release_error
