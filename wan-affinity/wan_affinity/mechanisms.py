"""Rejected mechanisms, kept as executable counter-examples.

These are *not* used.  They exist so the gate-1 failure modes are demonstrated
rather than asserted:

* ``FirmwareThenLearn`` - let the firmware ``balance`` chain choose per connection,
  then learn the source's WAN from the resulting mark.  Fails: two simultaneous
  first connections from one source can be chosen independently and land on
  different WANs before any learning happens.

* ``TimeoutIpset`` - a per-source set with a packet-idle timeout refreshed by a
  poller.  Fails: a quiet but established connection is expired once it stops
  sending packets, even though its conntrack state still exists, so a later
  connection from the same source can be placed on a different WAN.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from .health import ELIGIBLE
from .model import AdmissionResult, Association, Domain, Event, Packet, apply_wan_mark, packet_is_eligible


class FirmwareThenLearn:
    name = "firmware-then-learn"

    def __init__(self, domain: Domain, firmware_pick: Callable[[str], int]):
        self.domain = domain
        self.firmware_pick = firmware_pick
        self.learned: Dict[str, int] = {}

    def admission_decision(self, pkt: Packet) -> AdmissionResult:
        eligible, reason = packet_is_eligible(
            inbound=pkt.inbound,
            proto=pkt.proto,
            direction=pkt.direction,
            src=pkt.src,
            dst=pkt.dst,
            mark=pkt.mark,
            domain=self.domain,
        )
        if not eligible:
            return AdmissionResult("pass", None, reason, None)
        wan = self.firmware_pick(pkt.src)  # independent, per-connection decision
        return AdmissionResult("mark", wan, "firmware-choice", apply_wan_mark(pkt.mark, wan))

    def on_event(self, event: Event) -> None:
        if event.kind in ("NEW", "UPDATE") and event.state and event.state.wan is not None:
            self.learned[event.state.src] = event.state.wan


class TimeoutIpset:
    name = "timeout-ipset"

    def __init__(self, domain: Domain, selector, clock, idle_timeout: float):
        self.domain = domain
        self.selector = selector
        self.clock = clock
        self.idle_timeout = idle_timeout
        self.assoc: Dict[str, Association] = {}
        self.last_activity: Dict[str, float] = {}

    def admission_decision(self, pkt: Packet) -> AdmissionResult:
        eligible, reason = packet_is_eligible(
            inbound=pkt.inbound,
            proto=pkt.proto,
            direction=pkt.direction,
            src=pkt.src,
            dst=pkt.dst,
            mark=pkt.mark,
            domain=self.domain,
        )
        if not eligible:
            return AdmissionResult("pass", None, reason, None)
        now = self.clock()
        ent = self.assoc.get(pkt.src)
        if ent is not None and (now - self.last_activity[pkt.src]) <= self.idle_timeout:
            self.last_activity[pkt.src] = now
            return AdmissionResult("mark", ent.wan, "set-hit", apply_wan_mark(pkt.mark, ent.wan))
        wan = self.selector.choose([0, 1], (2, 1), pkt.src)
        self.assoc[pkt.src] = Association(pkt.src, wan, 1, now)
        self.last_activity[pkt.src] = now
        return AdmissionResult("mark", wan, "set-add", apply_wan_mark(pkt.mark, wan))

    def on_event(self, event: Event) -> None:
        if event.kind in ("NEW", "UPDATE") and event.state:
            self.last_activity[event.state.src] = self.clock()
            if event.state.wan is not None:
                ent = self.assoc.get(event.state.src)
                if ent is None:
                    self.assoc[event.state.src] = Association(event.state.src, event.state.wan, 1, self.clock())

    def tick(self):
        now = self.clock()
        expired = []
        for src, last in list(self.last_activity.items()):
            if now - last > self.idle_timeout:
                self.last_activity.pop(src, None)
                if self.assoc.pop(src, None) is not None:
                    expired.append(src)
        return expired
