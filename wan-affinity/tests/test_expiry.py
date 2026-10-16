import unittest

from base import (
    Clock,
    Event,
    INTERNET,
    INTERNET2,
    make_engine,
    make_packet,
    make_state,
    reconcile,
    read_dump,
    truncated,
)

SRC = "192.168.1.60"


class ExpiryTests(unittest.TestCase):
    def test_quiet_established_tcp_is_retained(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        state = make_state("c1", SRC, INTERNET, wan=admitted.wan)
        reconcile(engine, [state], clock)
        clock.advance(3600.0)  # hours of total silence
        reconcile(engine, [state], clock)  # state still exists
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)

    def test_long_lived_udp_is_retained(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC, proto="udp"))
        state = make_state("c1", SRC, INTERNET, proto="udp", wan=admitted.wan)
        reconcile(engine, [state], clock)
        clock.advance(600.0)
        reconcile(engine, [state], clock)
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)

    def test_expiry_only_after_last_state_disappears(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        engine.on_event(Event("DESTROY", "c1"))
        reconcile(engine, [], clock)
        expired = engine.tick()
        self.assertEqual([src for src, _, _ in expired], [SRC])
        self.assertNotIn(SRC, engine.assoc)

    def test_idle_retention_cancelled_by_new_connection(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=60)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        engine.on_event(Event("DESTROY", "c1"))
        reconcile(engine, [], clock)
        clock.advance(30.0)
        second = engine.admission_decision(make_packet("c2", src=SRC))
        self.assertEqual(second.wan, admitted.wan)
        state2 = make_state("c2", SRC, INTERNET2, wan=second.wan)
        clock.advance(120.0)
        reconcile(engine, [state2], clock)
        self.assertEqual(engine.tick(), [])
        # once it too is gone and retention elapses, the association expires
        engine.on_event(Event("DESTROY", "c2"))
        reconcile(engine, [], clock)
        clock.advance(61.0)
        reconcile(engine, [], clock)
        expired = engine.tick()
        self.assertEqual([src for src, _, _ in expired], [SRC])

    def test_no_expiry_on_truncated_read(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        engine.on_event(Event("DESTROY", "c1"))
        clock.advance(1.0)
        bad = read_dump(True, truncated([]), clock())
        self.assertFalse(bad.valid)
        engine.apply_reconcile(bad)
        clock.advance(1.0)
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)
        self.assertTrue(engine.uncertain)

    def test_no_expiry_without_fresh_reconcile(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0, max_reconcile_age=5.0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        engine.on_event(Event("DESTROY", "c1"))
        clock.advance(100.0)
        self.assertEqual(engine.tick(), [])
        self.assertTrue(engine.uncertain)
        self.assertIn(SRC, engine.assoc)

    def test_expiry_does_not_preempt_a_concurrent_admission(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        engine.on_event(Event("DESTROY", "c1"))
        reconcile(engine, [], clock)  # source looks empty
        # a new admission arrives before the expiry sweep
        second = engine.admission_decision(make_packet("c2", src=SRC))
        self.assertEqual(second.wan, admitted.wan)
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)


if __name__ == "__main__":
    unittest.main()
