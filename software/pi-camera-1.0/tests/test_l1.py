import unittest, threading, time
from camera_r4.l1_input import DebouncedLever, InputGate, L1Input, LeverEvent


class LeverTests(unittest.TestCase):
    def setUp(self):
        self.gate=InputGate();self.gate.set(True)
        self.lever=DebouncedLever()
    def sample(self,low,t):
        return self.lever.sample(low,t,self.gate.token())
    def test_held_at_start_requires_stable_release_then_new_press(self):
        self.assertIsNone(self.sample(True,0))
        self.assertIsNone(self.sample(True,1))
        self.sample(False,2);self.sample(False,2.06)
        self.sample(True,3);event=self.sample(True,3.06)
        self.assertEqual(event.number,1)
        self.assertIsNone(self.sample(True,4))
        self.sample(False,5);self.assertIsNone(self.sample(False,5.06))
    def test_bounce_and_short_release_do_not_repeat(self):
        self.sample(False,0);self.sample(False,.06)
        self.sample(True,.1);self.sample(False,.12);self.sample(True,.13)
        self.assertIsNone(self.sample(True,.16))
        self.assertEqual(self.sample(True,.19).number,1)
        self.sample(False,.2);self.sample(True,.22)
        self.assertIsNone(self.sample(True,.3))
        self.assertEqual(self.lever.count,1)
    def test_press_starting_busy_is_not_released_into_later_capture(self):
        self.sample(False,0);self.sample(False,.06)
        self.gate.set(False);self.sample(True,.1)
        self.gate.set(True)
        event=self.sample(True,.16)
        self.assertFalse(event.eligible)
        self.assertIsNone(self.sample(True,2))
    def test_busy_transition_during_debounce_invalidates_press(self):
        self.sample(False,0);self.sample(False,.06);self.sample(True,.1)
        self.gate.set(False);self.gate.set(True)
        self.assertFalse(self.sample(True,.16).eligible)
    def test_initial_high_is_not_armed_until_stable(self):
        self.sample(False,0);self.sample(True,.01)
        self.assertIsNone(self.sample(True,.1))
        self.sample(False,.2);self.sample(False,.26);self.sample(True,.3)
        self.assertEqual(self.sample(True,.36).number,1)
    def test_bounce_cannot_turn_a_busy_press_into_a_later_eligible_one(self):
        self.sample(False,0);self.sample(False,.06)
        self.gate.set(False);self.sample(True,.1);self.sample(False,.12)
        self.gate.set(True);self.sample(True,.13)
        self.assertFalse(self.sample(True,.19).eligible)

class DeliveryTests(unittest.TestCase):
    def test_release_failure_is_not_reported_as_successful_close(self):
        failed=threading.Event()
        class Line:
            def read(self):return False
            def close(self):
                failed.set()
                raise OSError('fixture release failure')
        source=L1Input(factory=Line);source.start()
        with self.assertRaisesRegex(RuntimeError,'释放未确认'):source.close()
        self.assertTrue(failed.is_set())
        self.assertEqual(source.snapshot()['state'],'error')

    def test_read_started_busy_cannot_gain_a_new_enabled_gate_token(self):
        sampled=threading.Event();resume=threading.Event()
        class Line:
            pressed=False
            closed=False
            def read(self):
                value=self.pressed
                if value and not sampled.is_set():
                    sampled.set()
                    if not resume.wait(2):raise TimeoutError('fixture read barrier')
                return value
            def close(self):self.closed=True
        line=Line();source=L1Input(factory=lambda:line)
        def until(predicate):
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if predicate():return
                time.sleep(.005)
            self.fail('fixture worker did not reach expected state')
        try:
            source.start();until(lambda:source.snapshot()['pressed'] is False)
            source.gate.set(False);line.pressed=True
            self.assertTrue(sampled.wait(2))
            source.gate.set(True);resume.set()
            until(lambda:source.snapshot()['count']==1)
            self.assertIsNone(source.take_event())
        finally:resume.set();source.close()
        self.assertTrue(line.closed)

    def test_expired_and_gate_changed_events_never_replay(self):
        source=L1Input();source.gate.set(True)
        source._publish(LeverEvent(1,1.,source.gate.token(),True))
        self.assertIsNone(source.take_event(2.))
        source._publish(LeverEvent(2,3.,source.gate.token(),True))
        source.gate.set(False);source.gate.set(True)
        self.assertIsNone(source.take_event(3.1))
    def test_only_one_event_slot_no_later_burst(self):
        source=L1Input();source.gate.set(True)
        source._publish(LeverEvent(1,1.,source.gate.token(),True))
        source._publish(LeverEvent(2,1.1,source.gate.token(),True))
        self.assertEqual(source.take_event(1.15).number,1)
        self.assertIsNone(source.take_event(1.16))

if __name__=='__main__':unittest.main()
