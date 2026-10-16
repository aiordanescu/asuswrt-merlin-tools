"""WAN health views.

Two layers, never mixed:

* **Standalone / native-only** - Merlin's per-WAN connection state and
  interface/routing readiness.  No probing.  Native "connected" is link/DHCP
  level only; it does not establish connection *quality*.
* **Enhanced** - an optional, versioned, atomically published snapshot from
  ``wan-watchdog``.  It carries raw flags, an explicit
  ``eligible_for_new_assignment`` decision (already accounting for the
  watchdog's recovery hold), a separate ``recovered`` flag, a sequence number,
  publisher/boot identity and a monotonic publication time.

If the enhanced snapshot is missing or stale the component keeps running on
native state.  It must not read the disappearance of enhanced health as
recovery: a WAN that enhanced monitoring had excluded stays excluded (UNKNOWN)
until fresh recovery evidence arrives or the operator explicitly switches to
native-only policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

ELIGIBLE = "eligible"
UNUSABLE = "unusable"
UNKNOWN = "unknown"

SNAPSHOT_VERSION = "1"
SNAPSHOT_PUBLISHER = "wan-watchdog"


@dataclass
class WanFacts:
    """Native per-WAN facts (from ``nvram`` + route readiness)."""

    enable: Optional[int] = None
    state_t: Optional[int] = None
    ipaddr: Optional[str] = None
    gateway: Optional[str] = None
    ifname: Optional[str] = None
    route_ready: Optional[bool] = None


def _blank(value: Optional[str]) -> bool:
    return value is None or value == "" or value == "0.0.0.0"


def native_eligibility(facts: Optional[WanFacts]) -> str:
    """Ready == usable.  This is *not* a quality claim."""
    if facts is None:
        return UNKNOWN
    if facts.enable == 0:
        return UNUSABLE
    if facts.enable != 1:
        return UNKNOWN
    if facts.state_t is None:
        return UNKNOWN
    if facts.state_t != 2:  # 2 == WAN_STATE_CONNECTED
        return UNUSABLE
    if _blank(facts.ipaddr) or _blank(facts.gateway):
        return UNUSABLE
    if not facts.ifname:
        return UNKNOWN
    if facts.route_ready is False:
        return UNUSABLE
    if facts.route_ready is None:
        return UNKNOWN
    return ELIGIBLE


@dataclass
class Snapshot:
    version: str
    publisher: str
    boot_id: str
    seq: int
    published_mono: float
    wan_state: Dict[int, str] = field(default_factory=dict)
    wan_eligible: Dict[int, bool] = field(default_factory=dict)
    wan_recovered: Dict[int, bool] = field(default_factory=dict)
    wan_raw: Dict[int, Dict[str, str]] = field(default_factory=dict)


def parse_snapshot(text: Optional[str]) -> Optional[Snapshot]:
    """Parse a versioned health snapshot, or return None if unusable."""
    if not isinstance(text, str) or not text.strip():
        return None
    top: Dict[str, str] = {}
    wan: Dict[str, Dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return None
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("wan") and "." in key:
            head, sub = key.split(".", 1)
            try:
                unit = int(head[3:])
            except ValueError:
                return None
            wan.setdefault(str(unit), {})[sub] = value
        else:
            top[key] = value

    if top.get("version") != SNAPSHOT_VERSION:
        return None
    if top.get("publisher") != SNAPSHOT_PUBLISHER:
        return None
    if not top.get("publisher_boot_id"):
        # publisher identity is part of the contract
        return None
    try:
        seq = int(top["seq"])
        published = float(top["published_mono"])
    except (KeyError, ValueError):
        return None
    if seq < 0 or published < 0:
        return None

    snap = Snapshot(
        version=top["version"],
        publisher=top["publisher"],
        boot_id=top["publisher_boot_id"],
        seq=seq,
        published_mono=published,
    )
    for unit in (0, 1):
        raw = wan.get(str(unit))
        if not raw:
            return None
        state = raw.get("state", UNKNOWN)
        if state not in (ELIGIBLE, UNUSABLE, UNKNOWN):
            return None
        snap.wan_state[unit] = state
        snap.wan_eligible[unit] = raw.get("eligible", "0") == "1"
        snap.wan_recovered[unit] = raw.get("recovered", "0") == "1"
        snap.wan_raw[unit] = dict(raw)
    return snap


class HealthView:
    """Composes optional enhanced health over native readiness."""

    def __init__(
        self,
        native: Optional[Dict[int, WanFacts]] = None,
        max_age: float = 15.0,
    ) -> None:
        self.native: Dict[int, WanFacts] = native or {}
        self.max_age = max_age
        self.snapshot: Optional[Snapshot] = None
        self.snapshot_mono: Optional[float] = None
        self.enhanced = False
        self.native_policy = False
        self.excluded: set = set()
        self.last_seq: Optional[int] = None
        self.rejected_snapshots = 0

    # -- enhanced snapshot -------------------------------------------------- #
    def apply_snapshot(self, snapshot: Optional[Snapshot], now: float) -> bool:
        if snapshot is None:
            self.rejected_snapshots += 1
            self.snapshot = None
            self.snapshot_mono = None
            self.enhanced = False
            return False
        # ignore an out-of-order/duplicate publication
        if self.last_seq is not None and snapshot.seq < self.last_seq:
            self.rejected_snapshots += 1
            return False
        self.snapshot = snapshot
        self.snapshot_mono = now
        self.enhanced = True
        self.last_seq = snapshot.seq
        self.excluded = {u for u in (0, 1) if not snapshot.wan_eligible.get(u, False)}
        return True

    def refresh(self, now: float) -> None:
        if self.snapshot is None or self.snapshot_mono is None:
            self.enhanced = False
            return
        self.enhanced = (now - self.snapshot_mono) <= self.max_age

    def switch_to_native_policy(self) -> None:
        """Explicit operator switch: drop enhanced exclusions."""
        self.native_policy = True
        self.excluded = set()

    # -- decision ----------------------------------------------------------- #
    def eligibility(self, unit: int) -> str:
        if self.native_policy:
            return native_eligibility(self.native.get(unit))
        if self.enhanced and self.snapshot is not None:
            return self.snapshot.wan_state.get(unit, UNKNOWN)
        # enhanced unavailable
        if unit in self.excluded:
            return UNKNOWN  # preserve exclusion; never read absence as recovery
        return native_eligibility(self.native.get(unit))

    @property
    def enhanced_available(self) -> bool:
        return self.enhanced

    def to_status(self) -> str:
        return (
            "enhanced=%s policy=%s wan0=%s wan1=%s excluded=%s"
            % (
                "yes" if self.enhanced else "no",
                "native" if self.native_policy else "auto",
                self.eligibility(0),
                self.eligibility(1),
                ",".join(str(u) for u in sorted(self.excluded)) or "-",
            )
        )
