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
)

SRC = "192.168.1.65"


class EventTests(unittest.TestCase):
    def _engine_with_state(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        return clock, engine, admitted

    def test_duplicate_destroy_is_idempotent(self):
        clock, engine, _ = self._engine_with_state()
        engine.on_event(Event("DESTROY", "c1"))
        engine.on_event(Event("DESTROY", "c1"))
        reconcile(engine, [], clock)
        self.assertEqual([s for s, _, _ in engine.tick()], [SRC])

    def test_unknown_destroy_is_ignored(self):
        clock, engine, admitted = self._engine_with_state()
        engine.on_event(Event("DESTROY", "does-not-exist"))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        self.assertEqual(engine.tick(), [])

    def test_destroy_then_reordered_new_keeps_association(self):
        clock, engine, admitted = self._engine_with_state()
        engine.on_event(Event("DESTROY", "c1"))
        engine.on_event(Event("NEW", "c2", make_state("c2", SRC, INTERNET2, wan=admitted.wan)))
        reconcile(engine, [make_state("c2", SRC, INTERNET2, wan=admitted.wan)], clock)
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)

    def test_lost_destroy_recovered_by_reconcile(self):
        clock, engine, _ = self._engine_with_state()
        # the DESTROY never arrives, but a valid empty dump clears + confirms
        reconcile(engine, [], clock)
        self.assertEqual([s for s, _, _ in engine.tick()], [SRC])

    def test_lost_new_recovered_by_reconcile(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        admitted = engine.admission_decision(make_packet("c1", src=SRC))
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        clock.advance(500.0)
        reconcile(engine, [make_state("c1", SRC, INTERNET, wan=admitted.wan)], clock)
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)


if __name__ == "__main__":
    unittest.main()
