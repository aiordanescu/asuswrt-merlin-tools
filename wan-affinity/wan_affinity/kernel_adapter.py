"""Kernel adapters - DOCUMENTATION ONLY, never executed in gate 1.

Nothing here is run against the router.  These classes record the intended
real-world binding so the gate-1 model can be reviewed against it.  Every
method raises ``NotImplementedError`` if called.

Original proposed packet path (not a validated production recipe):

    # PREROUTING, inserted before the firmware `-j balance` jump:
    -A PREROUTING -i br0 -m state --state NEW -j WAN_AFFINITY_ADMIT
    -A WAN_AFFINITY_ADMIT ... (eligibility exclusions)
    -A WAN_AFFINITY_ADMIT -j NFQUEUE --queue-num <N> --queue-bypass

    # the helper's verdict carries the WAN mark; a following rule persists it:
    -A PREROUTING -m mark ! --mark 0x0/0xf0000000 -j CONNMARK --save-mark \
         --nfmask 0xf0000000 --ctmask 0xf0000000
    -A PREROUTING -i br0 -m connmark --mark 0x80000000/0x80000000 \
         -j CONNMARK --restore-mark --nfmask 0xf0000000 --ctmask 0xf0000000

Router loopback follow-up (see docs/ROUTER-TESTS-2026-10-14.md): NF_ACCEPT
skipped the following CONNMARK rule, so the original schematic above is wrong.
A test-only NF_REPEAT path with a packet-WAN-mark guard successfully re-entered
the table and saved marks. Queue binding and NFQA_MARK work without Entware.
Production re-entry, exclusions, lifecycle, forwarding and performance are
still unimplemented here; the separate router-tests bridge is diagnostic only.

Historical unproved dependencies (see docs/GATE1.md):

* Whether this kernel's NFQUEUE verdict can carry a packet mark
  (``nfq_set_verdict2`` / NFQA_MARK) so the helper can set it directly,
  or whether the helper must only maintain an ipset and let a rule apply it.
* Whether ``nfnetlink_queue`` is available/bindable on the router, and
  whether the missing Entware userspace (``libnetfilter_queue``) can be
  provided without a package install.
* Per-packet confirmation of ``CONNMARK --save-mark`` immediacy.
"""

from __future__ import annotations

from typing import List, Optional


def _unimplemented(name: str):
    raise NotImplementedError(
        "%s is documentation-only in gate 1; it has not been bound to any kernel" % name
    )


class NfqueueAdmission:
    """Bind a single serialized NFQUEUE for eligible first packets."""

    def __init__(self, queue_num: int, queue_bypass: bool = True) -> None:
        self.queue_num = queue_num
        self.queue_bypass = queue_bypass

    def bind(self):
        _unimplemented("NfqueueAdmission.bind")

    def verdict_with_mark(self, packet_id: int, mark: int):
        _unimplemented("NfqueueAdmission.verdict_with_mark")


class ConntrackEventSource:
    """``conntrack -E -e NEW,DESTROY,UPDATE`` event stream."""

    def __init__(self) -> None:
        self.verified_read_only = True  # observed working on the router

    def events(self):
        _unimplemented("ConntrackEventSource.events")


class ConntrackDumpReader:
    """Read one complete dump; return ``(ok, text)`` for validated parsing."""

    def read(self):
        _unimplemented("ConntrackDumpReader.read")


class IpSetManager:
    """Optional per-source sets (``hash:ip`` at the kernel's protocol rev 6)."""

    def __init__(self, set_names: Optional[List[str]] = None) -> None:
        self.set_names = set_names or ["wa_wan0", "wa_wan1"]

    def ensure(self):
        _unimplemented("IpSetManager.ensure")


class MangleInstaller:
    """Idempotent install/ownership/cleanup of the owned chain."""

    def install(self):
        _unimplemented("MangleInstaller.install")

    def remove(self):
        _unimplemented("MangleInstaller.remove")
