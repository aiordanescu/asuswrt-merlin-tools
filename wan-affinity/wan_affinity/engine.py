"""Serialized association engine.

The whole reason this component can be correct is that **all** admission,
lifecycle and expiry decisions run in one single-threaded loop:

* the NFQUEUE file descriptor (admission of eligible first packets), and
* the conntrack event file descriptor plus the periodic validated reconcile

are serviced by one process, so ``Engine`` methods are never called
concurrently.  Each public method is therefore a linearization point.

Creation is atomic per source *by construction*: two simultaneous first
packets from one source are dequeued and admitted one after the other; the
first creates the association, the second observes it.

Expiry is deliberately **not** driven by conntrack event arrival order (which
is not ordered across CPUs).  A DESTROY only marks a source as an expiry
*candidate*; the association is actually removed only after a *validated*
reconcile confirms the mapping has zero relevant member states.  That confirmation and
the removal happen in the same loop as admission, so an expiry cannot race an
admission for the same source, and an empty/error/truncated read never expires
anything.

See DESIGN.md for the packet-path mechanism and its unproved dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .health import ELIGIBLE, UNKNOWN, UNUSABLE
from .model import (
    AdmissionResult,
    Association,
    Domain,
    Event,
    Packet,
    State,
    apply_wan_mark,
    packet_is_eligible,
)
from .reconcile import ReconcileResult


@dataclass
class Config:
    idle_seconds: float = 0.0
    max_reconcile_age: float = 15.0
    ratio: Tuple[int, int] = (2, 1)
    # Absence from the confirmed conntrack table cannot prove that an admitted
    # packet is gone: it can still be queued or awaiting confirmation. This
    # threshold reports uncertainty; it is never permission to discard state.
    provisional_warning_seconds: float = 2.0


class Engine:
    def __init__(self, domain: Domain, health, selector, clock, config: Optional[Config] = None):
        self.domain = domain
        self.health = health
        self.selector = selector
        self.clock = clock
        self.config = config or Config()

        self.assoc: Dict[str, Association] = {}
        self.states: Dict[str, State] = {}
        self.transitional: set = set()

        self.epoch = 0
        self.uncertain = False
        self.last_reconcile_mono: Optional[float] = None
        self.last_reconcile_error = ""
        self.expired: List[Tuple[str, int, float]] = []
        self.admissions = 0

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _next_epoch(self) -> int:
        self.epoch += 1
        return self.epoch

    def _relevant_srcs(self) -> set:
        return {
            st.src for st in self.states.values() if st.relevant_in(self.domain)
        }

    def _has_relevant(self, src: str) -> bool:
        return any(
            st.src == src and st.relevant_in(self.domain)
            and src in self.assoc and st.binding_id == self.assoc[src].binding_id
            for st in self.states.values()
        )

    def _register(self, pkt: Packet, wan: int, provisional: bool) -> None:
        now = self.clock()
        self.states[pkt.ctid] = State(
            ctid=pkt.ctid,
            src=pkt.src,
            dst=pkt.dst,
            proto=pkt.proto,
            direction=pkt.direction,
            inbound=pkt.inbound,
            wan=wan,
            provisional=provisional,
            created_mono=now,
            updated_mono=now,
            binding_id=self.assoc[pkt.src].binding_id,
        )

    def _sync_candidates(self) -> None:
        now = self.clock()
        for src, assoc in self.assoc.items():
            if self._has_relevant(src):
                assoc.candidate_since = None
                assoc.confirmed_empty_mono = None
            elif assoc.candidate_since is None:
                assoc.candidate_since = now

    # ------------------------------------------------------------------ #
    # admission (NFQUEUE)
    # ------------------------------------------------------------------ #
    def admission_decision(self, pkt: Packet) -> AdmissionResult:
        now = self.clock()
        self.health.refresh(now)

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

        assoc = self.assoc.get(pkt.src)
        if assoc is None:
            choices = [u for u in (0, 1) if self.health.eligibility(u) == ELIGIBLE]
            if not choices:
                self.uncertain = True
                return AdmissionResult("pass", None, "no-eligible-wan", None)
            preferred = self.selector.preferred_wan(self.config.ratio, pkt.src)
            wan = self.selector.choose(choices, self.config.ratio, pkt.src)
            assoc = Association(
                src=pkt.src,
                wan=wan,
                preferred_wan=preferred,
                epoch=self._next_epoch(),
                created_mono=now,
                reason="selected-mixed" if pkt.src in self.transitional else "selected",
            )
            assoc.binding_id = assoc.epoch
            for state in self.states.values():
                if state.src == pkt.src and state.relevant_in(self.domain) and not state.binding_id:
                    state.binding_id = -1
            self.transitional.discard(pkt.src)
            self.assoc[pkt.src] = assoc
        else:
            preferred = assoc.preferred_wan
            if (
                preferred is not None
                and assoc.wan != preferred
                and self.health.eligibility(preferred) == ELIGIBLE
            ):
                # affinity: the persistent preference is healthy again -> return to it
                assoc.wan = preferred
                assoc.epoch = self._next_epoch()
                assoc.reason = "affinity-return"
                assoc.last_wan_change_mono = now
            elif self.health.eligibility(assoc.wan) == UNUSABLE:
                choices = [
                    u
                    for u in (0, 1)
                    if u != assoc.wan and self.health.eligibility(u) == ELIGIBLE
                ]
                if not choices:
                    self.uncertain = True
                    return AdmissionResult("pass", None, "no-eligible-wan-for-failover", None)
                assoc.wan = self.selector.choose(choices, self.config.ratio, pkt.src)
                assoc.epoch = self._next_epoch()
                assoc.failovers += 1
                assoc.reason = "failover"
                assoc.last_wan_change_mono = now
            # ELIGIBLE or UNKNOWN -> retain the existing association.

        # A relevant connection exists again: cancel any pending expiry.
        assoc.candidate_since = None
        assoc.confirmed_empty_mono = None
        self._register(pkt, assoc.wan, provisional=True)
        self.admissions += 1
        return AdmissionResult(
            "mark", assoc.wan, assoc.reason, apply_wan_mark(pkt.mark, assoc.wan)
        )

    # ------------------------------------------------------------------ #
    # conntrack events (lifecycle tracking only)
    # ------------------------------------------------------------------ #
    def on_event(self, event: Event) -> None:
        now = self.clock()
        if event.kind in ("NEW", "UPDATE"):
            if event.state is None:
                return
            state = event.state
            old = self.states.get(state.ctid)
            if old and (old.src, old.dst, old.proto) == (state.src, state.dst, state.proto):
                state.binding_id = old.binding_id
            state.provisional = False
            state.updated_mono = now
            self.states[state.ctid] = state
        elif event.kind == "DESTROY":
            self.states.pop(event.ctid, None)  # idempotent for duplicates
        self._sync_candidates()

    # ------------------------------------------------------------------ #
    # reconcile (authoritative, validated)
    # ------------------------------------------------------------------ #
    def apply_reconcile(self, result: ReconcileResult) -> None:
        now = self.clock()
        if not result.valid:
            self.uncertain = True
            self.last_reconcile_error = result.error
            return  # retain every association; never expire from a bad read

        if result.covers_unconfirmed and not 0 <= now-result.taken_mono <= self.config.max_reconcile_age:
            self.uncertain = True
            self.last_reconcile_error = "stale-lifecycle-view"
            return

        self.last_reconcile_mono = result.taken_mono if result.covers_unconfirmed else now
        self.last_reconcile_error = ""
        self.uncertain = False

        taken = result.taken_mono
        merged: Dict[str, State] = {}
        for state in result.states:
            state.updated_mono = taken
            merged[state.ctid] = state
        for ctid, state in self.states.items():
            newer = (state.updated_mono >= taken if result.covers_unconfirmed
                     else state.updated_mono > taken)
            if ctid in merged:
                # Snapshot collection can overlap an admission in the real
                # adapter. Never erase a newer pending generation with it.
                if newer:
                    merged[ctid] = state
                elif (merged[ctid].src, merged[ctid].dst, merged[ctid].proto) == (state.src, state.dst, state.proto):
                    merged[ctid].binding_id = state.binding_id
                continue
            if newer:
                merged[ctid] = state
            elif state.provisional and not result.covers_unconfirmed:
                # The router test reproduced a packet remaining unconfirmed
                # beyond two seconds. Retain until confirmation or explicit
                # terminal lifecycle evidence; a timer is not such evidence.
                merged[ctid] = state
                if (now - state.created_mono) > self.config.provisional_warning_seconds:
                    self.uncertain = True
                    self.last_reconcile_error = "unconfirmed-admission"
        self.states = merged
        self.transitional.intersection_update(self._relevant_srcs())

        self._sync_candidates()
        for src, assoc in self.assoc.items():
            # Record the *first* validated confirmation of emptiness.  Re-running
            # reconciles while a source stays empty keeps it fresh (see the gate
            # in ``tick``) without pushing the expiry deadline forward forever.
            if not self._has_relevant(src) and assoc.confirmed_empty_mono is None:
                assoc.confirmed_empty_mono = now

    def reconstruct(self, result: ReconcileResult) -> None:
        """Restart reconciliation: rebuild associations from surviving marks."""
        self.apply_reconcile(result)
        if not result.valid:
            return
        if self.uncertain:
            return
        by_src = {}
        for state in self.states.values():
            if state.relevant_in(self.domain):
                by_src.setdefault(state.src, []).append(state)
        if len(by_src) > 1024:
            self.uncertain = True
            self.last_reconcile_error = "reconstruct-capacity"
            return
        now = self.clock()
        for src, states in by_src.items():
            if src in self.assoc:
                for state in states:
                    if not state.binding_id:
                        state.binding_id = -1
                continue
            wans = {state.wan for state in states}
            if None in wans or len(wans) > 1 or any(state.binding_id for state in states):
                self.transitional.add(src)
                for state in states:
                    if not state.binding_id:
                        state.binding_id = -1
                continue
            assoc = Association(src=src, wan=next(iter(wans)),
                                epoch=self._next_epoch(), created_mono=now,
                                preferred_wan=self.selector.preferred_wan(self.config.ratio, src),
                                adopted=True, reason="reconstructed")
            assoc.binding_id = assoc.epoch
            self.assoc[src] = assoc
            for state in states:
                state.binding_id = assoc.binding_id
            self.transitional.discard(src)

    # ------------------------------------------------------------------ #
    # expiry
    # ------------------------------------------------------------------ #
    def tick(self) -> List[Tuple[str, int, float]]:
        now = self.clock()
        if self.uncertain:
            return []
        if (
            self.last_reconcile_mono is None
            or (now - self.last_reconcile_mono) > self.config.max_reconcile_age
        ):
            self.uncertain = True
            return []  # no fresh validated data -> retain everything

        expired: List[Tuple[str, int, float]] = []
        for src in list(self.assoc):
            assoc = self.assoc[src]
            if assoc.candidate_since is None or assoc.confirmed_empty_mono is None:
                continue
            deadline = max(assoc.candidate_since, assoc.confirmed_empty_mono)
            if now - deadline >= self.config.idle_seconds:
                del self.assoc[src]
                self.transitional.discard(src)
                expired.append((src, assoc.wan, now))
        self.expired.extend(expired)
        return expired

    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        return {
            "mode": self.selector.name,
            "enhanced_health": self.health.enhanced_available,
            "uncertain": self.uncertain,
            "last_reconcile_error": self.last_reconcile_error,
            "associations": {
                src: {
                    "wan": a.wan,
                    "preferred_wan": a.preferred_wan,
                    "epoch": a.epoch,
                    "reason": a.reason,
                    "failovers": a.failovers,
                    "expiry_candidate": a.candidate_since is not None,
                }
                for src, a in sorted(self.assoc.items())
            },
            "awaiting_new": sorted(self.transitional),
        }
