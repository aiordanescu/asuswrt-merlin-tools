"""Counter-examples: the rejected mechanisms fail the gate-1 invariants."""

import random
import unittest

from base import (
    Clock,
    Event,
    INTERNET,
    StickySelector,
    make_domain,
    make_engine,
    make_packet,
    make_state,
    reconcile,
)
from wan_affinity.mechanisms import FirmwareThenLearn, TimeoutIpset

SRC = "192.168.1.100"


class RejectedMechanismTests(unittest.TestCase):
    def test_firmware_then_learn_splits_simultaneous_first_packets(self):
        domain = make_domain()
        picks = iter([0, 1])
        firmware_then_learn = FirmwareThenLearn(domain, firmware_pick=lambda src: next(picks))
        first = firmware_then_learn.admission_decision(make_packet("c1", src=SRC))
        second = firmware_then_learn.admission_decision(make_packet("c2", src=SRC))
        self.assertNotEqual(first.wan, second.wan)  # the defect

        # the serialized engine does not split them
        engine = make_engine()
        one = engine.admission_decision(make_packet("c1", src=SRC))
        two = engine.admission_decision(make_packet("c2", src=SRC))
        self.assertEqual(one.wan, two.wan)

    def test_timeout_ipset_expires_quiet_established_connection(self):
        domain = make_domain()
        clock = Clock(1000.0)
        timeout = TimeoutIpset(domain, StickySelector(random.Random(5)), clock, idle_timeout=30)
        admitted = timeout.admission_decision(make_packet("c1", src=SRC))
        # the connection is established, then goes quiet while still alive
        timeout.on_event(Event("NEW", "c1", make_state("c1", SRC, INTERNET, wan=admitted.wan)))
        clock.advance(60.0)
        self.assertIn(SRC, timeout.tick())  # the defect: state still exists

        # the serialized engine retains the same scenario
        clock2 = Clock(1000.0)
        engine = make_engine(clock=clock2, idle_seconds=0)
        result = engine.admission_decision(make_packet("c1", src=SRC))
        state = make_state("c1", SRC, INTERNET, wan=result.wan)
        reconcile(engine, [state], clock2)
        clock2.advance(60.0)
        reconcile(engine, [state], clock2)
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)


if __name__ == "__main__":
    unittest.main()
