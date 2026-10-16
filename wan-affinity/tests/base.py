"""Shared test helpers for the wan-affinity gate-1 suite."""

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from wan_affinity import Domain, Event, Packet, PinnedRule, StickySelector  # noqa: E402
from wan_affinity.engine import Config, Engine  # noqa: E402
from wan_affinity.health import HealthView, WanFacts  # noqa: E402
from wan_affinity.reconcile import read_dump  # noqa: E402
from wan_affinity.sim import (  # noqa: E402
    Clock,
    dump,
    make_state,
    miscounted,
    partial_line,
    truncated,
)

LAN = "192.168.1.0/24"
INTERNET = "93.184.216.34"
INTERNET2 = "1.1.1.1"


def make_domain(pins=(), vpn=()):
    return Domain(lan_cidrs=(LAN,), vpn_cidrs=tuple(vpn), pins=list(pins))


def ok_facts():
    return {
        0: WanFacts(
            enable=1, state_t=2, ipaddr="203.0.113.2", gateway="203.0.113.1",
            ifname="eth0", route_ready=True,
        ),
        1: WanFacts(
            enable=1, state_t=2, ipaddr="198.51.100.2", gateway="198.51.100.1",
            ifname="eth2", route_ready=True,
        ),
    }


def make_health(max_age=15.0, facts=None):
    return HealthView(facts or ok_facts(), max_age=max_age)


def make_engine(domain=None, health=None, selector=None, clock=None,
                idle_seconds=0.0, max_reconcile_age=15.0, ratio=(2, 1)):
    domain = domain or make_domain()
    health = health or make_health()
    clock = clock or Clock(1000.0)
    selector = selector or StickySelector(random.Random(1234))
    engine = Engine(
        domain,
        health,
        selector,
        clock,
        Config(
            idle_seconds=idle_seconds,
            max_reconcile_age=max_reconcile_age,
            ratio=ratio,
        ),
    )
    return engine


def make_packet(ctid, src="192.168.1.50", dst=INTERNET, proto="tcp", mark=0, inbound=False):
    return Packet(ctid=ctid, src=src, dst=dst, proto=proto, mark=mark, inbound=inbound)


def reconcile(engine, states, clock, ok=True, text=None):
    if text is None:
        text = dump(states)
    result = read_dump(ok, text, clock())
    engine.apply_reconcile(result)
    return result
