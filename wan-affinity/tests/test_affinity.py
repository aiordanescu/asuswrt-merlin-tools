"""Affinity vs sticky recovery semantics."""

import unittest

from base import HealthView, StickySelector, make_engine, make_packet, ok_facts
from wan_affinity.health import WanFacts
from wan_affinity.selection import AffinitySelector, derive_seed

RATIO = (2, 1)


class FirstEligible:
    name = "first-eligible"

    def choose(self, choices, ratio, src):
        return sorted(choices)[0]

    def preferred_wan(self, ratio, src):
        return None


def down_wan(unit):
    ip = "203.0.113.2" if unit == 0 else "198.51.100.2"
    gw = "203.0.113.1" if unit == 0 else "198.51.100.1"
    ifn = "eth0" if unit == 0 else "eth2"
    return WanFacts(enable=1, state_t=3, ipaddr=ip, gateway=gw, ifname=ifn, route_ready=True)


class AffinityRecoveryTests(unittest.TestCase):
    def _src_preferring(self, selector, wan):
        for i in range(2, 250):
            src = "192.168.1.%d" % i
            if selector.preferred_wan(RATIO, src) == wan:
                return src
        raise AssertionError("no source with preference %d" % wan)

    def test_affinity_returns_to_preference_after_recovery(self):
        affinity = AffinitySelector(derive_seed("AA:BB:CC:DD:EE:FF"), RATIO)
        src = self._src_preferring(affinity, 0)

        facts = ok_facts()
        facts[1] = down_wan(1)
        engine = make_engine(health=HealthView(facts), selector=affinity)

        first = engine.admission_decision(make_packet("c1", src=src))
        self.assertEqual(first.wan, 0)
        self.assertEqual(engine.assoc[src].preferred_wan, 0)

        facts[0] = down_wan(0)
        facts[1] = ok_facts()[1]
        second = engine.admission_decision(make_packet("c2", src=src))
        self.assertEqual(second.wan, 1)  # temporary failover
        self.assertEqual(engine.assoc[src].preferred_wan, 0)  # preference held

        facts[0] = ok_facts()[0]
        third = engine.admission_decision(make_packet("c3", src=src))
        self.assertEqual(third.wan, 0)  # returned to the persistent preference
        self.assertEqual(engine.assoc[src].reason, "affinity-return")

    def test_sticky_does_not_return_to_a_previous_wan(self):
        facts = ok_facts()
        facts[1] = down_wan(1)
        engine = make_engine(health=HealthView(facts), selector=FirstEligible())

        first = engine.admission_decision(make_packet("c1", src="192.168.1.40"))
        self.assertEqual(first.wan, 0)
        self.assertIsNone(engine.assoc["192.168.1.40"].preferred_wan)

        facts[0] = down_wan(0)
        facts[1] = ok_facts()[1]
        second = engine.admission_decision(make_packet("c2", src="192.168.1.40"))
        self.assertEqual(second.wan, 1)

        facts[0] = ok_facts()[0]
        third = engine.admission_decision(make_packet("c3", src="192.168.1.40"))
        self.assertEqual(third.wan, 1)  # retained on the backup

    def test_affinity_prefers_preference_when_eligible(self):
        affinity = AffinitySelector("seed", RATIO)
        src = self._src_preferring(affinity, 1)
        self.assertEqual(affinity.choose([0, 1], RATIO, src), 1)
        self.assertEqual(affinity.choose([1], RATIO, src), 1)
        # if the preference is not eligible, fall back deterministically
        self.assertEqual(affinity.choose([0], RATIO, src), 0)


if __name__ == "__main__":
    unittest.main()
