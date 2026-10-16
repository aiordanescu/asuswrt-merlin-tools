import random
import unittest

from base import (
    Clock,
    Event,
    INTERNET,
    INTERNET2,
    dump,
    make_engine,
    make_packet,
    make_state,
    reconcile,
    read_dump,
    StickySelector,
)


class CreationTests(unittest.TestCase):
    def test_simultaneous_tcp_first_packets_get_one_wan(self):
        engine = make_engine(selector=StickySelector(random.Random(3)))
        packets = [make_packet("c%d" % i, src="192.168.1.50") for i in range(6)]
        results = [engine.admission_decision(p) for p in packets]
        self.assertEqual(len({r.wan for r in results}), 1)
        self.assertTrue(all(r.action == "mark" for r in results))
        self.assertEqual(len(engine.assoc), 1)

    def test_simultaneous_tcp_and_quic_get_one_wan(self):
        engine = make_engine(selector=StickySelector(random.Random(9)))
        results = [
            engine.admission_decision(make_packet("c1", proto="tcp")),
            engine.admission_decision(make_packet("c2", proto="udp")),
            engine.admission_decision(make_packet("c3", proto="udp", dst=INTERNET2)),
        ]
        self.assertEqual(len({r.wan for r in results}), 1)

    def test_retransmitted_first_packet_keeps_wan(self):
        engine = make_engine()
        first = engine.admission_decision(make_packet("c1"))
        again = engine.admission_decision(make_packet("c1"))
        self.assertEqual(first.wan, again.wan)

    def test_pending_admission_survives_unconfirmed_reconcile(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock)
        admitted = engine.admission_decision(make_packet("c1", src="192.168.1.55"))
        # reconcile taken before conntrack confirmed the new entry
        reconcile(engine, [], clock)
        clock.advance(0.1)
        self.assertEqual(engine.tick(), [])
        self.assertIn("192.168.1.55", engine.assoc)
        # ...then it is confirmed
        reconcile(engine, [make_state("c1", "192.168.1.55", INTERNET, wan=admitted.wan)], clock)
        clock.advance(0.1)
        self.assertEqual(engine.tick(), [])

    def test_old_unconfirmed_admission_is_retained_without_terminal_evidence(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock)
        engine.admission_decision(make_packet("c1", src="192.168.1.55"))
        clock.advance(5.0)  # beyond the warning threshold, not an expiry timer
        reconcile(engine, [], clock)
        self.assertEqual(engine.tick(), [])
        self.assertIn("192.168.1.55", engine.assoc)
        self.assertTrue(engine.uncertain)
        self.assertEqual(engine.last_reconcile_error, "unconfirmed-admission")

    def test_delayed_confirmation_clears_uncertainty(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock)
        admitted = engine.admission_decision(make_packet("c1", src="192.168.1.55"))
        clock.advance(5.0)
        reconcile(engine, [], clock)
        self.assertTrue(engine.uncertain)
        reconcile(engine, [make_state("c1", "192.168.1.55", INTERNET, wan=admitted.wan)], clock)
        self.assertFalse(engine.uncertain)
        self.assertEqual(engine.tick(), [])

    def test_terminal_evidence_allows_unconfirmed_admission_cleanup(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock)
        engine.admission_decision(make_packet("c1", src="192.168.1.55"))
        clock.advance(5.0)
        reconcile(engine, [], clock)
        self.assertEqual(engine.tick(), [])
        engine.on_event(Event("DESTROY", "c1"))
        reconcile(engine, [], clock)
        self.assertEqual([src for src, _, _ in engine.tick()], ["192.168.1.55"])


if __name__ == "__main__":
    unittest.main()
