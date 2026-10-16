"""Core data model: WAN marks, policy domain, connection state, associations.

Gate-1 prototype.  Nothing in this package touches a live router.  The kernel
adapter (``kernel_adapter.py``) is documentation-only and is never executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

# Firmware Merlin Load-Balance marks, confirmed read-only on the RT-AX88U:
#   WAN0 (eth0) -> 0x80000000, WAN1 (eth2) -> 0x90000000, mask 0xf0000000
WAN0_MARK = 0x80000000
WAN1_MARK = 0x90000000
WAN_MARK_MASK = 0xF0000000
NON_WAN_MASK = 0x0FFFFFFF

WAN_UNITS: Tuple[int, ...] = (0, 1)


def wan_mark(unit: int) -> int:
    if unit == 0:
        return WAN0_MARK
    if unit == 1:
        return WAN1_MARK
    raise ValueError(f"unknown WAN unit {unit!r}")


def mark_to_wan(mark: int) -> Optional[int]:
    """Return the WAN unit encoded in a mark, or None if unmarked/unknown."""
    top = mark & WAN_MARK_MASK
    if top == WAN0_MARK:
        return 0
    if top == WAN1_MARK:
        return 1
    return None


def apply_wan_mark(old_mark: int, unit: int) -> int:
    """Set only the WAN bits, preserving every unrelated mark bit."""
    return (old_mark & NON_WAN_MASK) | wan_mark(unit)


# --------------------------------------------------------------------------- #
# Policy domain
# --------------------------------------------------------------------------- #

RELEVANT_PROTOS: FrozenSet[str] = frozenset({"tcp", "udp"})

DEFAULT_LOCAL_CIDRS: Tuple[str, ...] = (
    "192.168.0.0/16",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "224.0.0.0/4",
)


def _ip(addr: str):
    import ipaddress

    return ipaddress.ip_address(addr)


def _net(cidr: str):
    import ipaddress

    return ipaddress.ip_network(cidr, strict=False)


def in_any(addr: str, cidrs: Iterable[str]) -> bool:
    try:
        ip = _ip(addr)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if ip in _net(cidr):
                return True
        except ValueError:
            continue
    return False


@dataclass(frozen=True)
class PinnedRule:
    """A GUI source/destination pin.  Matched traffic is left to firmware routing."""

    wan: int
    src_cidr: Optional[str] = None
    dst_cidr: Optional[str] = None

    def matches(self, src: str, dst: str) -> bool:
        if self.src_cidr and not in_any(src, (self.src_cidr,)):
            return False
        if self.dst_cidr and not in_any(dst, (self.dst_cidr,)):
            return False
        return True


@dataclass
class Domain:
    """Which connections participate in this policy."""

    lan_cidrs: Tuple[str, ...] = ("192.168.1.0/24",)
    local_cidrs: Tuple[str, ...] = DEFAULT_LOCAL_CIDRS
    vpn_cidrs: Tuple[str, ...] = ()
    pins: List[PinnedRule] = field(default_factory=list)

    def is_lan_source(self, src: str) -> bool:
        return in_any(src, self.lan_cidrs)

    def is_pinned(self, src: str, dst: str) -> bool:
        return any(pin.matches(src, dst) for pin in self.pins)

    def is_local_dest(self, dst: str) -> bool:
        return in_any(dst, self.local_cidrs) or in_any(dst, self.lan_cidrs)

    def is_vpn_dest(self, dst: str) -> bool:
        return in_any(dst, self.vpn_cidrs)


def packet_is_eligible(
    *,
    inbound: bool,
    proto: str,
    direction: str,
    src: str,
    dst: str,
    mark: int,
    domain: Domain,
) -> Tuple[bool, str]:
    """Should this packet be classified by the component?

    Returns ``(eligible, reason)``.  Ineligible packets are left untouched for
    firmware routing (GUI pins, local/VPN, incoming, already-marked).
    """
    if inbound:
        return False, "incoming"
    if direction != "orig":
        return False, "reply-direction"
    if proto not in RELEVANT_PROTOS:
        return False, f"proto-{proto}"
    if not domain.is_lan_source(src):
        return False, "non-lan-source"
    if domain.is_pinned(src, dst):
        return False, "gui-pin"
    if domain.is_local_dest(dst):
        return False, "local-or-lan-dest"
    if domain.is_vpn_dest(dst):
        return False, "vpn-dest"
    if mark_to_wan(mark) is not None:
        return False, "already-wan-marked"
    return True, "eligible"


# --------------------------------------------------------------------------- #
# Connection state
# --------------------------------------------------------------------------- #


def state_is_relevant(state: "State", domain: Domain) -> bool:
    """Is this connection one of the source's tracked relevant states?

    Unlike admission eligibility this deliberately ignores the mark: a tracked
    connection already carries its WAN mark.
    """
    if state.inbound or state.direction != "orig":
        return False
    if state.proto not in RELEVANT_PROTOS:
        return False
    if not domain.is_lan_source(state.src):
        return False
    if domain.is_pinned(state.src, state.dst):
        return False
    if domain.is_local_dest(state.dst):
        return False
    if domain.is_vpn_dest(state.dst):
        return False
    return True


@dataclass
class State:
    """One tracked connection identity (a conntrack entry)."""

    ctid: str
    src: str
    dst: str
    proto: str
    direction: str = "orig"
    inbound: bool = False
    wan: Optional[int] = None
    assured: bool = False
    provisional: bool = False
    created_mono: float = 0.0
    updated_mono: float = 0.0
    binding_id: int = 0  # -1: pre-existing; positive: source mapping membership

    def relevant_in(self, domain: Domain) -> bool:
        return state_is_relevant(self, domain)


@dataclass
class Association:
    src: str
    wan: int
    epoch: int
    created_mono: float
    binding_id: int = 0
    preferred_wan: Optional[int] = None  # affinity: persistent preference
    adopted: bool = False
    failovers: int = 0
    reason: str = "selected"
    candidate_since: Optional[float] = None
    confirmed_empty_mono: Optional[float] = None
    last_wan_change_mono: Optional[float] = None


# --------------------------------------------------------------------------- #
# Message types crossing the platform boundary
# --------------------------------------------------------------------------- #


@dataclass
class Packet:
    """An NFQUEUE-admitted packet handed to the engine (see DESIGN.md)."""

    ctid: str
    src: str
    dst: str
    proto: str
    mark: int = 0
    inbound: bool = False
    direction: str = "orig"


@dataclass
class Event:
    kind: str  # NEW | UPDATE | DESTROY
    ctid: str
    state: Optional[State] = None


@dataclass
class AdmissionResult:
    action: str  # mark | pass | drop
    wan: Optional[int]
    reason: str
    mark: Optional[int] = None
