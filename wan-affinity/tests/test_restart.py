import unittest

from base import (
    Clock,
    INTERNET,
    INTERNET2,
    PinnedRule,
    dump,
    make_domain,
    make_engine,
    make_packet,
    make_state,
    read_dump,
)


class RestartTests(unittest.TestCase):
    def _reconstruct(self, states, domain=None):
        clock = Clock(500.0)
        engine = make_engine(domain=domain, clock=clock)
        engine.reconstruct(read_dump(True, dump(states), clock()))
        return engine

    def test_single_wan_flows_are_reconstructed(self):
        engine = self._reconstruct(
            [
                make_state("c1", "192.168.1.90", INTERNET, wan=0),
                make_state("c2", "192.168.1.90", INTERNET2, proto="udp", wan=0),
                make_state("c3", "192.168.1.91", INTERNET, wan=1),
            ]
        )
        self.assertEqual(engine.assoc["192.168.1.90"].wan, 0)
        self.assertTrue(engine.assoc["192.168.1.90"].adopted)
        self.assertEqual(engine.assoc["192.168.1.91"].wan, 1)

    def test_mixed_wan_flows_admit_new_connections(self):
        engine = self._reconstruct(
            [
                make_state("c1", "192.168.1.92", INTERNET, wan=0),
                make_state("c2", "192.168.1.92", INTERNET2, wan=1),
            ]
        )
        self.assertIn("192.168.1.92", engine.transitional)
        self.assertNotIn("192.168.1.92", engine.assoc)
        result = engine.admission_decision(make_packet("c3", src="192.168.1.92"))
        self.assertEqual(result.action, "mark")
        self.assertEqual(result.reason, "selected-mixed")
        self.assertNotIn("192.168.1.92", engine.transitional)

    def test_unattributed_flows_are_deferred(self):
        engine = self._reconstruct([make_state("c1", "192.168.1.93", INTERNET, wan=None)])
        self.assertIn("192.168.1.93", engine.transitional)

    def test_invalid_dump_reconstructs_nothing(self):
        clock = Clock(500.0)
        engine = make_engine(clock=clock)
        engine.reconstruct(read_dump(False, None, clock()))
        self.assertEqual(engine.assoc, {})
        self.assertTrue(engine.uncertain)

    def test_pinned_flows_are_not_adopted(self):
        domain = make_domain(pins=[PinnedRule(wan=1, src_cidr="192.168.1.94/32")])
        engine = self._reconstruct(
            [make_state("c1", "192.168.1.94", INTERNET, wan=1)], domain=domain
        )
        self.assertNotIn("192.168.1.94", engine.assoc)


if __name__ == "__main__":
    unittest.main()
