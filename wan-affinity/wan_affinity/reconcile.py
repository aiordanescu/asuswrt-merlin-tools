"""Validated conntrack dump reading.

The component never expires an association from an *empty or error* read.  A
reconcile snapshot is only valid when it is a complete, self-describing dump:

    #wan-affinity-conntrack v1 count=N
    <row>
    ...
    --end--

Missing header, missing ``--end--`` sentinel, non-numeric count, a row that
fails to parse, or ``count != len(rows)`` all make the snapshot invalid.  An
invalid snapshot never removes state and never confirms an expiry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .model import State, mark_to_wan

HEADER_PREFIX = "#wan-affinity-conntrack v1 count="
END_SENTINEL = "--end--"


@dataclass
class ReconcileResult:
    valid: bool
    states: List[State] = field(default_factory=list)
    error: str = ""
    taken_mono: float = 0.0
    raw_rows: int = 0
    covers_unconfirmed: bool = False


def _parse_row(line: str) -> Optional[State]:
    parts = line.split()
    if len(parts) < 5:
        return None
    ctid, proto, src, dst, direction = parts[0], parts[1], parts[2], parts[3], parts[4]
    kv = {}
    for token in parts[5:]:
        if "=" in token:
            key, value = token.split("=", 1)
            kv[key] = value
    try:
        mark = int(kv.get("mark", "0"), 16)
    except ValueError:
        return None
    return State(
        ctid=ctid,
        src=src,
        dst=dst,
        proto=proto,
        direction=direction,
        wan=mark_to_wan(mark),
        inbound=kv.get("inbound", "0") == "1",
        assured=kv.get("assured", "0") == "1",
    )


def parse_dump(text: Optional[str]) -> Optional[List[State]]:
    """Return parsed states, or ``None`` if the dump is invalid/truncated."""
    if not isinstance(text, str):
        return None
    lines = text.splitlines()
    if not lines:
        return None
    header = lines[0]
    if not header.startswith(HEADER_PREFIX):
        return None
    try:
        count = int(header[len(HEADER_PREFIX):])
    except ValueError:
        return None
    if count < 0:
        return None
    if lines[-1].strip() != END_SENTINEL:
        return None
    body = [line for line in lines[1:-1] if line.strip()]
    if len(body) != count:
        return None
    states: List[State] = []
    for line in body:
        state = _parse_row(line)
        if state is None:
            return None
        states.append(state)
    return states


def read_dump(ok: bool, text: Optional[str], taken_mono: float) -> ReconcileResult:
    """Turn a conntrack read into a validated reconcile result."""
    if not ok:
        return ReconcileResult(valid=False, error="read-error", taken_mono=taken_mono)
    states = parse_dump(text)
    if states is None:
        return ReconcileResult(
            valid=False, error="unparseable-or-truncated", taken_mono=taken_mono
        )
    return ReconcileResult(
        valid=True, states=states, taken_mono=taken_mono, raw_rows=len(states)
    )


def read_lifecycle(unconfirmed_ok: bool, unconfirmed_text: Optional[str],
                   confirmed_ok: bool, confirmed_text: Optional[str],
                   started_mono: float) -> ReconcileResult:
    """Model a complete U-then-C observation. Caller must collect in that order.

    ``started_mono`` precedes the unconfirmed read. The union covers a live
    tuple moving U -> C between reads; either failed/incomplete read invalidates
    the whole result. The C adapter uses full five-tuples as its identities.
    """
    u = read_dump(unconfirmed_ok, unconfirmed_text, started_mono)
    c = read_dump(confirmed_ok, confirmed_text, started_mono)
    if not u.valid or not c.valid:
        return ReconcileResult(False, error="incomplete-lifecycle-view", taken_mono=started_mono)
    union = {state.ctid: state for state in u.states}
    union.update({state.ctid: state for state in c.states})
    return ReconcileResult(True, list(union.values()), taken_mono=started_mono,
                           raw_rows=u.raw_rows+c.raw_rows, covers_unconfirmed=True)
