"""Deterministic simulation helpers for the gate-1 tests."""

from __future__ import annotations

from typing import Iterable, List

from .model import State, wan_mark
from .reconcile import END_SENTINEL, HEADER_PREFIX


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


def make_state(
    ctid: str,
    src: str,
    dst: str,
    proto: str = "tcp",
    wan: int | None = None,
    inbound: bool = False,
    assured: bool = True,
    direction: str = "orig",
) -> State:
    return State(
        ctid=ctid,
        src=src,
        dst=dst,
        proto=proto,
        direction=direction,
        wan=wan,
        inbound=inbound,
        assured=assured,
    )


def _row(state: State) -> str:
    mark = 0 if state.wan is None else wan_mark(state.wan)
    return "%s %s %s %s %s mark=0x%08x inbound=%d assured=%d" % (
        state.ctid,
        state.proto,
        state.src,
        state.dst,
        state.direction,
        mark,
        int(state.inbound),
        int(state.assured),
    )


def dump(states: Iterable[State]) -> str:
    """A *valid*, complete conntrack dump."""
    rows: List[str] = [_row(s) for s in states]
    lines = [HEADER_PREFIX + str(len(rows))]
    lines.extend(rows)
    lines.append(END_SENTINEL)
    return "\n".join(lines) + "\n"


def truncated(states: Iterable[State]) -> str:
    """Missing the end sentinel -> must be treated as invalid."""
    text = dump(states)
    return text.replace(END_SENTINEL + "\n", "")


def miscounted(states: Iterable[State]) -> str:
    """Header count disagrees with rows -> invalid."""
    text = dump(states)
    lines = text.splitlines()
    lines[0] = HEADER_PREFIX + str(len(states) + 1)
    return "\n".join(lines) + "\n"


def partial_line(states: Iterable[State]) -> str:
    """A row that fails to parse -> invalid."""
    text = dump(states)
    lines = text.splitlines()
    if len(lines) > 2:
        lines[1] = "garbage"
    return "\n".join(lines) + "\n"
