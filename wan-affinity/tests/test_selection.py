import random
import unittest

from wan_affinity.selection import (
    AffinitySelector,
    StickySelector,
    derive_seed,
    parse_ratio,
)


class SelectionTests(unittest.TestCase):
    def test_parse_ratio(self):
        self.assertEqual(parse_ratio("2:1"), (2, 1))
        self.assertEqual(parse_ratio("bogus"), (1, 1))

    def test_sticky_weighting_matches_ratio(self):
        selector = StickySelector(random.Random(42))
        counts = {0: 0, 1: 0}
        for i in range(3000):
            counts[selector.choose([0, 1], (2, 1), "192.168.%d.%d" % (i // 254, i % 254))] += 1
        ratio = counts[0] / counts[1]
        self.assertGreater(ratio, 1.8)
        self.assertLess(ratio, 2.2)

    def test_affinity_is_deterministic(self):
        first = AffinitySelector("seed-x", (2, 1))
        second = AffinitySelector("seed-x", (2, 1))
        for i in range(500):
            src = "192.168.%d.%d" % (i // 254, i % 254)
            self.assertEqual(
                first.choose([0, 1], (2, 1), src),
                second.choose([0, 1], (2, 1), src),
            )

    def test_affinity_seed_changes_map(self):
        first = AffinitySelector("seed-x", (2, 1))
        second = AffinitySelector("seed-y", (2, 1))
        differences = 0
        for i in range(500):
            src = "192.168.%d.%d" % (i // 254, i % 254)
            if first.choose([0, 1], (2, 1), src) != second.choose([0, 1], (2, 1), src):
                differences += 1
        self.assertGreater(differences, 0)

    def test_affinity_weighting_matches_ratio(self):
        selector = AffinitySelector("seed-x", (2, 1))
        counts = {0: 0, 1: 0}
        for i in range(3000):
            counts[selector.choose([0, 1], (2, 1), "10.0.%d.%d" % (i // 254, i % 254))] += 1
        ratio = counts[0] / counts[1]
        self.assertGreater(ratio, 1.7)
        self.assertLess(ratio, 2.3)

    def test_affinity_with_one_eligible(self):
        selector = AffinitySelector("seed", (2, 1))
        self.assertEqual(selector.choose([1], (2, 1), "any"), 1)

    def test_derive_seed_is_stable_and_sensitive(self):
        self.assertEqual(derive_seed("AA:BB:CC:DD:EE:FF"), derive_seed("AA:BB:CC:DD:EE:FF"))
        self.assertNotEqual(derive_seed("AA:BB:CC:DD:EE:FF"), derive_seed("11:22:33:44:55:66"))

    def test_golden_vectors_match_c_engine(self):
        # These exact values are also asserted in tests/c/test_engine.c. If the
        # hash changes, both sides must change together.
        selector = AffinitySelector("seed-x", (2, 1))
        self.assertEqual(selector.preferred_wan((2, 1), "192.168.1.10"), 0)
        self.assertEqual(selector.preferred_wan((2, 1), "192.168.1.20"), 0)
        self.assertEqual(selector.preferred_wan((2, 1), "192.168.1.99"), 1)
        self.assertEqual(derive_seed("AA:BB:CC:DD:EE:FF"), "46aea50459418c75")


if __name__ == "__main__":
    unittest.main()
