import unittest

from base import make_engine, make_packet, ok_facts
from wan_affinity.health import (
    ELIGIBLE,
    UNKNOWN,
    UNUSABLE,
    HealthView,
    WanFacts,
    firmware_eligibility,
    parse_snapshot,
)


def snap_text(**overrides):
    values = dict(
        seq=1,
        published=1000.0,
        w0_state=ELIGIBLE,
        w0_elig=1,
        w0_rec=0,
        w0_acc="1",
        w1_state=ELIGIBLE,
        w1_elig=1,
        w1_rec=0,
        w1_acc="1",
    )
    values.update(overrides)
    return "\n".join(
        [
            "version=1",
            "publisher=wan-watchdog",
            "publisher_boot_id=boot-a",
            "seq=%d" % values["seq"],
            "published_mono=%.1f" % values["published"],
            "wan0.state=%s" % values["w0_state"],
            "wan0.eligible=%d" % values["w0_elig"],
            "wan0.recovered=%d" % values["w0_rec"],
            "wan0.acceptable=%s" % values["w0_acc"],
            "wan1.state=%s" % values["w1_state"],
            "wan1.eligible=%d" % values["w1_elig"],
            "wan1.recovered=%d" % values["w1_rec"],
            "wan1.acceptable=%s" % values["w1_acc"],
        ]
    )


class FirmwareHealthTests(unittest.TestCase):
    def test_ready_is_eligible(self):
        facts = WanFacts(enable=1, state_t=2, ipaddr="203.0.113.2", gateway="203.0.113.1",
                         ifname="eth0", route_ready=True)
        self.assertEqual(firmware_eligibility(facts), ELIGIBLE)

    def test_disabled_is_unusable(self):
        facts = WanFacts(enable=0, state_t=2, ipaddr="203.0.113.2", gateway="203.0.113.1",
                         ifname="eth0", route_ready=True)
        self.assertEqual(firmware_eligibility(facts), UNUSABLE)

    def test_not_connected_is_unusable(self):
        facts = WanFacts(enable=1, state_t=3, ipaddr="203.0.113.2", gateway="203.0.113.1",
                         ifname="eth0", route_ready=True)
        self.assertEqual(firmware_eligibility(facts), UNUSABLE)

    def test_no_lease_is_unusable(self):
        facts = WanFacts(enable=1, state_t=2, ipaddr="0.0.0.0", gateway="203.0.113.1",
                         ifname="eth0", route_ready=True)
        self.assertEqual(firmware_eligibility(facts), UNUSABLE)

    def test_unknown_route_readiness_is_unknown(self):
        facts = WanFacts(enable=1, state_t=2, ipaddr="203.0.113.2", gateway="203.0.113.1",
                         ifname="eth0", route_ready=None)
        self.assertEqual(firmware_eligibility(facts), UNKNOWN)

    def test_missing_facts_is_unknown(self):
        self.assertEqual(firmware_eligibility(None), UNKNOWN)


class SnapshotTests(unittest.TestCase):
    def test_parse_and_consume(self):
        snap = parse_snapshot(snap_text())
        self.assertIsNotNone(snap)
        view = HealthView(ok_facts())
        self.assertTrue(view.apply_snapshot(snap, 100.0))
        view.refresh(100.0)
        self.assertEqual(view.eligibility(0), ELIGIBLE)

    def test_recovery_hold_is_not_recovery(self):
        snap = parse_snapshot(snap_text(w0_state=UNUSABLE, w0_elig=0, w0_rec=0, w0_acc="1"))
        view = HealthView(ok_facts())
        view.apply_snapshot(snap, 100.0)
        view.refresh(100.0)
        self.assertEqual(snap.wan_raw[0]["acceptable"], "1")  # raw flag
        self.assertFalse(snap.wan_eligible[0])                # explicit eligibility
        self.assertFalse(snap.wan_recovered[0])
        self.assertEqual(view.eligibility(0), UNUSABLE)       # not read as recovery

    def test_missing_publisher_identity_is_invalid(self):
        self.assertIsNone(parse_snapshot(snap_text().replace("publisher_boot_id=boot-a\n", "")))

    def test_wrong_version_is_invalid(self):
        self.assertIsNone(parse_snapshot(snap_text().replace("version=1", "version=2")))

    def test_bad_state_token_is_invalid(self):
        self.assertIsNone(parse_snapshot(snap_text(w0_state="weird")))

    def test_out_of_order_publication_rejected(self):
        view = HealthView(ok_facts())
        self.assertTrue(view.apply_snapshot(parse_snapshot(snap_text(seq=5)), 100.0))
        self.assertFalse(view.apply_snapshot(parse_snapshot(snap_text(seq=3)), 100.0))

    def test_enhanced_disappearance_preserves_exclusion(self):
        view = HealthView(ok_facts())
        view.apply_snapshot(parse_snapshot(snap_text(w0_state=UNUSABLE, w0_elig=0)), 100.0)
        view.refresh(100.0)
        self.assertEqual(view.eligibility(0), UNUSABLE)
        view.apply_snapshot(None, 100.0)
        view.refresh(100.0)
        self.assertFalse(view.enhanced_available)
        self.assertEqual(view.eligibility(0), UNKNOWN)   # absence is not recovery
        self.assertEqual(view.eligibility(1), ELIGIBLE)  # firmware covers the rest
        view.switch_to_firmware_policy()
        self.assertEqual(view.eligibility(0), ELIGIBLE)  # explicit operator switch

    def test_stale_snapshot_falls_back_but_keeps_exclusion(self):
        view = HealthView(ok_facts(), max_age=15.0)
        view.apply_snapshot(parse_snapshot(snap_text(w0_state=UNUSABLE, w0_elig=0)), 100.0)
        view.refresh(100.0)
        self.assertTrue(view.enhanced_available)
        view.refresh(200.0)
        self.assertFalse(view.enhanced_available)
        self.assertEqual(view.eligibility(0), UNKNOWN)


class EngineHealthTests(unittest.TestCase):
    def _engine(self, facts):
        return make_engine(health=HealthView(facts))

    def test_reassign_only_when_existing_wan_is_unusable(self):
        facts = ok_facts()
        facts[1] = WanFacts(enable=1, state_t=3, ipaddr="198.51.100.2",
                            gateway="198.51.100.1", ifname="eth2", route_ready=True)
        engine = self._engine(facts)
        first = engine.admission_decision(make_packet("c1", src="192.168.1.80"))
        self.assertEqual(first.wan, 0)
        facts[0] = WanFacts(enable=1, state_t=3, ipaddr="203.0.113.2",
                            gateway="203.0.113.1", ifname="eth0", route_ready=True)
        facts[1] = ok_facts()[1]
        second = engine.admission_decision(make_packet("c2", src="192.168.1.80"))
        self.assertEqual(second.wan, 1)
        self.assertEqual(engine.assoc["192.168.1.80"].failovers, 1)

    def test_unknown_existing_wan_is_retained(self):
        facts = ok_facts()
        facts[1] = WanFacts(enable=1, state_t=3, ipaddr="198.51.100.2",
                            gateway="198.51.100.1", ifname="eth2", route_ready=True)
        engine = self._engine(facts)
        first = engine.admission_decision(make_packet("c1", src="192.168.1.81"))
        self.assertEqual(first.wan, 0)
        facts[0] = WanFacts(enable=1, state_t=None, ipaddr="203.0.113.2",
                            gateway="203.0.113.1", ifname="eth0", route_ready=None)
        second = engine.admission_decision(make_packet("c2", src="192.168.1.81"))
        self.assertEqual(second.wan, 0)
        self.assertEqual(engine.assoc["192.168.1.81"].failovers, 0)

    def test_no_eligible_wan_reports_uncertain(self):
        facts = ok_facts()
        facts[0] = WanFacts(enable=1, state_t=3, ipaddr="203.0.113.2",
                            gateway="203.0.113.1", ifname="eth0", route_ready=True)
        facts[1] = WanFacts(enable=1, state_t=3, ipaddr="198.51.100.2",
                            gateway="198.51.100.1", ifname="eth2", route_ready=True)
        engine = self._engine(facts)
        result = engine.admission_decision(make_packet("c1", src="192.168.1.82"))
        self.assertEqual(result.action, "pass")
        self.assertEqual(result.reason, "no-eligible-wan")
        self.assertTrue(engine.uncertain)
        self.assertEqual(engine.assoc, {})

    def test_failover_with_no_other_wan_passes(self):
        facts = ok_facts()
        facts[1] = WanFacts(enable=1, state_t=3, ipaddr="198.51.100.2",
                            gateway="198.51.100.1", ifname="eth2", route_ready=True)
        engine = self._engine(facts)
        engine.admission_decision(make_packet("c1", src="192.168.1.83"))
        facts[0] = WanFacts(enable=1, state_t=3, ipaddr="203.0.113.2",
                            gateway="203.0.113.1", ifname="eth0", route_ready=True)
        result = engine.admission_decision(make_packet("c2", src="192.168.1.83"))
        self.assertEqual(result.action, "pass")
        self.assertEqual(result.reason, "no-eligible-wan-for-failover")
        self.assertTrue(engine.uncertain)


if __name__ == "__main__":
    unittest.main()
