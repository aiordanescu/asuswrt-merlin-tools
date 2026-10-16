"""Randomized serialization stress + health-transition invariants.

The stress keeps both WANs healthy so no failover is expected, then asserts the
sticky invariant directly: a source's live connections never use two WANs, and
an association is never expired while one of its live connections exists.
"""

import random
import unittest

from base import (
    Clock,
    Event,
    INTERNET,
    INTERNET2,
    HealthView,
    dump,
    make_engine,
    make_packet,
    make_state,
    ok_facts,
    read_dump,
)
from wan_affinity.health import WanFacts


class FirstEligible:
    """Deterministic selector: lowest-indexed eligible WAN."""

    name = "first-eligible"

    def choose(self, choices, ratio, src):
        return sorted(choices)[0]

    def preferred_wan(self, ratio, src):
        return None


class StressTests(unittest.TestCase):
    def _run(self, seed):
        rng = random.Random(seed)
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0, max_reconcile_age=1000.0)
        kernel = {}
        counter = 0
        for _ in range(400):
            roll = rng.random()
            if roll < 0.35:
                counter += 1
                ctid = "k%d" % counter
                src = "192.168.1.%d" % rng.randint(2, 60)
                dst = rng.choice([INTERNET, INTERNET2])
                proto = rng.choice(["tcp", "udp"])
                result = engine.admission_decision(
                    make_packet(ctid, src=src, dst=dst, proto=proto)
                )
                self.assertEqual(result.action, "mark")
                kernel[ctid] = dict(src=src, dst=dst, proto=proto, wan=result.wan)
                if rng.random() < 0.7:
                    engine.on_event(
                        Event("NEW", ctid, make_state(ctid, src, dst, proto=proto, wan=result.wan))
                    )
            elif roll < 0.6 and kernel:
                ctid = rng.choice(list(kernel))
                del kernel[ctid]
                if rng.random() < 0.7:
                    engine.on_event(Event("DESTROY", ctid))
                # otherwise the event is lost; reconcile catches up
            elif roll < 0.85:
                states = [
                    make_state(ctid, s["src"], s["dst"], proto=s["proto"], wan=s["wan"])
                    for ctid, s in kernel.items()
                ]
                engine.apply_reconcile(read_dump(True, dump(states), clock()))
                clock.advance(0.5)
            else:
                engine.tick()
                clock.advance(0.2)

            live = {}
            for s in kernel.values():
                live.setdefault(s["src"], set()).add(s["wan"])
            for src, wans in live.items():
                self.assertEqual(len(wans), 1, "source %s split across WANs %s" % (src, wans))
                self.assertIn(src, engine.assoc, "association expired while a live flow exists")
            for src, assoc in engine.assoc.items():
                if src in live:
                    self.assertIn(assoc.wan, live[src])

    def test_serialization_stress_seed_1(self):
        self._run(1)

    def test_serialization_stress_seed_2(self):
        self._run(2)

    def test_serialization_stress_seed_3(self):
        self._run(3)


class HealthTransitionTests(unittest.TestCase):
    def test_active_association_is_not_forced_back_on_recovery(self):
        facts = ok_facts()
        facts[1] = WanFacts(enable=1, state_t=3, ipaddr="198.51.100.2",
                            gateway="198.51.100.1", ifname="eth2", route_ready=True)
        engine = make_engine(health=HealthView(facts), selector=FirstEligible())
        first = engine.admission_decision(make_packet("c1", src="192.168.1.120"))
        self.assertEqual(first.wan, 0)

        facts[0] = WanFacts(enable=1, state_t=3, ipaddr="203.0.113.2",
                            gateway="203.0.113.1", ifname="eth0", route_ready=True)
        facts[1] = ok_facts()[1]
        failover = engine.admission_decision(make_packet("c2", src="192.168.1.120"))
        self.assertEqual(failover.wan, 1)

        facts[0] = ok_facts()[0]
        facts[1] = ok_facts()[1]
        retained = engine.admission_decision(make_packet("c3", src="192.168.1.120"))
        self.assertEqual(retained.wan, 1)  # active association stays on the backup

        fresh = engine.admission_decision(make_packet("c4", src="192.168.1.121"))
        self.assertEqual(fresh.wan, 0)  # new sources may use the recovered WAN


if __name__ == "__main__":
    unittest.main()
