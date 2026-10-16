"""wan-affinity gate-1 prototype (simulation + design only)."""

from .engine import Config, Engine
from .health import (
    ELIGIBLE,
    UNKNOWN,
    UNUSABLE,
    HealthView,
    Snapshot,
    WanFacts,
    native_eligibility,
    parse_snapshot,
)
from .model import (
    AdmissionResult,
    Association,
    Domain,
    Event,
    Packet,
    PinnedRule,
    State,
    apply_wan_mark,
    mark_to_wan,
    packet_is_eligible,
    wan_mark,
)
from .reconcile import ReconcileResult, parse_dump, read_dump
from .selection import AffinitySelector, StickySelector, derive_seed, parse_ratio

__all__ = [
    "Config",
    "Engine",
    "ELIGIBLE",
    "UNKNOWN",
    "UNUSABLE",
    "HealthView",
    "Snapshot",
    "WanFacts",
    "native_eligibility",
    "parse_snapshot",
    "AdmissionResult",
    "Association",
    "Domain",
    "Event",
    "Packet",
    "PinnedRule",
    "State",
    "apply_wan_mark",
    "mark_to_wan",
    "packet_is_eligible",
    "wan_mark",
    "ReconcileResult",
    "parse_dump",
    "read_dump",
    "AffinitySelector",
    "StickySelector",
    "derive_seed",
    "parse_ratio",
]
