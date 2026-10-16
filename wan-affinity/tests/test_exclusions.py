import unittest

from base import INTERNET, PinnedRule, make_domain, make_engine, make_packet
from wan_affinity.model import NON_WAN_MASK, mark_to_wan

SRC = "192.168.1.70"


class ExclusionTests(unittest.TestCase):
    def _result(self, **kwargs):
        engine = make_engine()
        return engine.admission_decision(make_packet("c1", **kwargs))

    def test_pinned_source_excluded(self):
        domain = make_domain(pins=[PinnedRule(wan=1, src_cidr=SRC + "/32")])
        engine = make_engine(domain=domain)
        result = engine.admission_decision(make_packet("c1", src=SRC))
        self.assertEqual(result.action, "pass")
        self.assertEqual(result.reason, "gui-pin")
        self.assertEqual(engine.assoc, {})

    def test_pinned_destination_subnet_excluded(self):
        domain = make_domain(pins=[PinnedRule(wan=0, dst_cidr="93.184.216.0/24")])
        result = make_engine(domain=domain).admission_decision(make_packet("c1", src=SRC))
        self.assertEqual(result.reason, "gui-pin")

    def test_local_destination_excluded(self):
        self.assertEqual(self._result(src=SRC, dst="192.168.1.1").reason, "local-or-lan-dest")

    def test_vpn_destination_excluded(self):
        domain = make_domain(vpn=("74.122.141.0/24",))
        result = make_engine(domain=domain).admission_decision(
            make_packet("c1", src=SRC, dst="74.122.141.5")
        )
        self.assertEqual(result.reason, "vpn-dest")

    def test_incoming_excluded(self):
        self.assertEqual(self._result(src=SRC, inbound=True).reason, "incoming")

    def test_non_lan_source_excluded(self):
        self.assertEqual(self._result(src="203.0.113.9").reason, "non-lan-source")

    def test_already_marked_excluded(self):
        self.assertEqual(self._result(src=SRC, mark=0x80000000).reason, "already-wan-marked")

    def test_icmp_excluded(self):
        self.assertEqual(self._result(src=SRC, proto="icmp").reason, "proto-icmp")

    def test_unrelated_mark_bits_preserved(self):
        engine = make_engine()
        result = engine.admission_decision(make_packet("c1", src=SRC, mark=0x00000005))
        self.assertEqual(result.action, "mark")
        self.assertEqual(mark_to_wan(result.mark), result.wan)
        self.assertEqual(result.mark & NON_WAN_MASK, 0x00000005)

    def test_web_traffic_is_in_scope(self):
        # HTTPS/QUIC are not special-cased: the native 443/8443/80 exemptions
        # only skip marking, so this policy claims them too.
        engine = make_engine()
        result = engine.admission_decision(
            make_packet("c1", src=SRC, proto="tcp", dst=INTERNET)
        )
        self.assertEqual(result.action, "mark")


if __name__ == "__main__":
    unittest.main()
