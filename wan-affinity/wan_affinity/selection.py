"""Weighted WAN selection for the two policies.

The affinity hash is the single documented algorithm shared with the on-router C
engine (``src/wa_engine.c``): FNV-1a 64 over the seed text, then over the four
address bytes in network order, then a weighted bucket split. Keep both sides in
step; the golden vector tests pin it.
"""

from __future__ import annotations

import random
from typing import Dict, Optional, Sequence, Tuple

from .model import WAN_UNITS

_FNV_OFFSET = 1469598103934665603
_FNV_PRIME = 1099511628211
_MASK64 = 0xFFFFFFFFFFFFFFFF


def _fnv1a64(data: bytes, h: int = _FNV_OFFSET) -> int:
    for byte in data:
        h ^= byte
        h = (h * _FNV_PRIME) & _MASK64
    return h


def _ipv4_bytes(src: str) -> bytes:
    """Network-order bytes of a dotted quad, mirroring the C fnv_u32 helper."""
    try:
        octets = [int(part) for part in src.split(".")]
    except ValueError:
        return src.encode()
    if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
        return src.encode()
    return bytes(octets)


class StickySelector:
    """Weighted *random* selection at first contact (OPNsense-style stickiness)."""

    name = "sticky"

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self.rng = rng or random.Random()

    def choose(self, choices: Sequence[int], ratio: Tuple[int, int], src: str) -> int:
        options = list(choices)
        if not options:
            raise ValueError("no eligible WANs")
        if len(options) == 1:
            return options[0]
        weights = [max(1, int(ratio[unit])) for unit in options]
        return self.rng.choices(options, weights=weights, k=1)[0]

    def preferred_wan(self, ratio: Tuple[int, int], src: str) -> Optional[int]:
        # sticky has no persistent preference: recovery does not pull a source back
        return None


class AffinitySelector:
    """Stable per-LAN-IP preferred WAN from a persistent seed and the weights."""

    name = "affinity"

    def __init__(self, seed: str, ratio: Tuple[int, int]) -> None:
        self.seed = str(seed)
        self.ratio = (int(ratio[0]), int(ratio[1]))

    def preferred_wan(self, ratio: Tuple[int, int], src: str) -> Optional[int]:
        """Persistent preference, independent of current health.

        Identical to ``affinity_preferred`` in ``src/wa_engine.c``.
        """
        weights = {unit: max(1, int(ratio[unit])) for unit in WAN_UNITS}
        total = sum(weights.values())
        h = _fnv1a64(self.seed.encode())
        h = _fnv1a64(_ipv4_bytes(src), h)
        value = h % total
        acc = 0
        for unit in sorted(WAN_UNITS):
            acc += weights[unit]
            if value < acc:
                return unit
        return sorted(WAN_UNITS)[-1]

    def choose(self, choices: Sequence[int], ratio: Tuple[int, int], src: str) -> int:
        options = sorted(choices)
        if not options:
            raise ValueError("no eligible WANs")
        if len(options) == 1:
            return options[0]
        preferred = self.preferred_wan(ratio, src)
        if preferred in options:
            return preferred
        return options[0]


def derive_seed(machine_id: str, salt: str = "") -> str:
    """Reproducible seed from stable hardware identity (router base MAC).

    Same hardware reproduces the same map after a config wipe, with nothing
    stored.  Replacing the router (or changing the MAC) changes the map; a
    deliberate rebalance uses a new salt.  Identical to the adapter's
    ``derive_affinity_seed`` (FNV-1a 64 over the MAC text, 16 hex digits).
    """
    return "%016x" % _fnv1a64((salt + machine_id).encode())


def parse_ratio(value: str) -> Tuple[int, int]:
    """Parse the native ``wans_lb_ratio`` (e.g. ``2:1``) into weights."""
    try:
        left, right = value.split(":", 1)
        a, b = int(left), int(right)
    except (ValueError, AttributeError):
        return (1, 1)
    if a < 0 or b < 0 or (a + b) == 0:
        return (1, 1)
    return (a, b)
