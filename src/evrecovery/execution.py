"""Proposed trusted adapter and conservative central reservation accounting.

Times are seconds, caps are kW. This is an explicit simulation contract, not
an assertion about guarantees supplied by an ordinary OCPP response.
"""
from dataclasses import dataclass
from math import inf, isfinite, isnan


def _power(value):
    if not isfinite(value) or value < 0:
        raise ValueError("Power must be finite and nonnegative")


def _identity(station, generation, epoch):
    if any(not isinstance(x, int) or x < 0 for x in (station, generation, epoch)):
        raise ValueError("Station, generation and epoch must be nonnegative integers")


@dataclass(frozen=True)
class Command:
    station: int
    generation: int
    issued: float
    start: float
    end: float
    cap_kw: float
    epoch: int = 0

    def __post_init__(self):
        _identity(self.station, self.generation, self.epoch)
        _power(self.cap_kw)
        if not all(isfinite(x) for x in (self.issued, self.start, self.end)):
            raise ValueError("Commands require finite absolute times")
        if self.start >= self.end or self.issued >= self.end:
            raise ValueError("Command must have a nonempty executable interval")


@dataclass(frozen=True)
class Certificate:
    station: int
    generation: int
    cap_kw: float
    applied: float
    end: float
    fallback_kw: float
    epoch: int = 0

    def __post_init__(self):
        _identity(self.station, self.generation, self.epoch)
        _power(self.cap_kw)
        _power(self.fallback_kw)
        if not isfinite(self.applied) or isnan(self.end) or self.end <= self.applied:
            raise ValueError("Certificate requires a nonempty applied profile interval")


class Station:
    """One durable generation fence, one effective overlay, one pending overlay.

    Receipt fences older work immediately; application completes at
    max(start, receive_time + actuation_s). The hard expiry is not extended by
    actuation lag. Reboot preserves all modeled trusted adapter state.
    """

    def __init__(self, station, initial_cap_kw, fallback_kw, initial_end=inf,
                 actuation_s=0):
        _identity(station, 0, 0)
        _power(initial_cap_kw)
        _power(fallback_kw)
        if isnan(initial_end) or initial_end <= 0:
            raise ValueError("Initial profile must be valid at time zero")
        if not isfinite(actuation_s) or actuation_s < 0:
            raise ValueError("Actuation lag must be finite and nonnegative")
        self.station = station
        self.fallback_kw = fallback_kw
        self.actuation_s = actuation_s
        self.epoch = 0
        self._now = 0.0
        self._high_water = 0
        self._active = Certificate(station, 0, initial_cap_kw, 0, initial_end,
                                   fallback_kw)
        self._pending = None
        self._apply_at = inf

    @property
    def high_water(self):
        return self._high_water

    @property
    def applied_generation(self):
        return self._active.generation

    @property
    def pending(self):
        return self._pending

    @property
    def next_transition(self):
        """Next physical breakpoint, or infinity; simulator use only."""
        return min(self._active.end, self._apply_at)

    def receive(self, command, t):
        self.advance(t)
        if (command.station != self.station or command.epoch != self.epoch
                or command.generation <= self._high_water):
            return False
        if t < command.issued:
            raise ValueError("A command cannot arrive before it is issued")
        self._high_water = command.generation
        self._pending = None
        self._apply_at = inf
        ready = max(command.start, t + self.actuation_s)
        if ready >= command.end:
            return False
        self._pending = command
        self._apply_at = ready
        self.advance(t)
        return True

    def advance(self, t):
        if not isfinite(t) or t < self._now:
            raise ValueError("Physical time must be finite and monotonic")
        # Consume every breakpoint even when a caller jumps over several events.
        while self.next_transition <= t:
            at = self.next_transition
            if self._active.end == at:
                self._active = Certificate(self.station, self.applied_generation,
                                           self.fallback_kw, at, inf,
                                           self.fallback_kw, self.epoch)
            if self._apply_at == at:
                cmd = self._pending
                self._active = Certificate(self.station, cmd.generation,
                                           cmd.cap_kw, at, cmd.end,
                                           self.fallback_kw, self.epoch)
                self._pending = None
                self._apply_at = inf
        self._now = t

    def power_cap(self, t):
        self.advance(t)
        return self._active.cap_kw

    def certificate(self, t):
        self.advance(t)
        return self._active

    def reboot(self, t):
        """Durable-state reboot; no power discontinuity or epoch reset."""
        self.advance(t)


class Ledger:
    """Evidence-only envelope; no access to station truth or message queues.

    reserve() records the prospective possibility before send. Admission and
    durable storage transactions are caller responsibilities. An expired
    unresolved overlay is no longer executable, but its possible fallback
    transition persists until superseding evidence excludes that history.
    """

    def __init__(self, station, initial_cap_kw, fallback_kw, initial_end=inf):
        self._base = Certificate(station, 0, initial_cap_kw, 0, initial_end,
                                 fallback_kw)
        self.station = station
        self.fallback_kw = fallback_kw
        self.epoch = 0
        self._now = 0.0
        self._pending = {}
        self._fallback_events = {}
        self._latest_generation = 0

    @property
    def latest_generation(self):
        return self._latest_generation

    @property
    def known_generation(self):
        return self._base.generation

    def reserve(self, command):
        if command.station != self.station or command.epoch != self.epoch:
            raise ValueError("Command belongs to another station or epoch")
        if command.generation <= self._latest_generation:
            if self._pending.get(command.generation) == command:
                return
            raise ValueError("Reservations require strictly increasing generations")
        if command.end <= self._now:
            raise ValueError("Cannot reserve an already expired command")
        self._pending[command.generation] = command
        self._fallback_events[command.generation] = command.end
        self._latest_generation = command.generation

    def observe(self, certificate):
        if (certificate.station != self.station or certificate.epoch != self.epoch
                or certificate.fallback_kw != self.fallback_kw):
            raise ValueError("Certificate violates trusted station identity or fallback")
        if certificate.generation < self.known_generation:
            return
        if (certificate.generation == self.known_generation
                and certificate.applied < self._base.applied):
            return
        self._base = certificate
        self._latest_generation = max(self._latest_generation, certificate.generation)
        self._pending = {g: c for g, c in self._pending.items()
                         if g > certificate.generation}
        self._fallback_events = {g: t for g, t in self._fallback_events.items()
                                 if g > certificate.generation}

    def advance(self, t):
        if not isfinite(t) or t < self._now:
            raise ValueError("Ledger time must be finite and monotonic")
        self._now = t
        self._pending = {g: c for g, c in self._pending.items() if c.end > t}

    def unresolved(self):
        return tuple(self._pending[g] for g in sorted(self._pending))

    def breakpoints(self, t0, t1):
        """Sorted exact envelope breakpoints, including both interval endpoints."""
        if not isfinite(t0) or isnan(t1) or t1 < t0:
            raise ValueError("Envelope interval must be ordered")
        if t0 < max(self._now, self._base.applied):
            raise ValueError("Current evidence cannot certify retrospective intervals")
        points = {t0, t1}
        points.add(self._base.end)
        points.update(self._fallback_events.values())
        for cmd in self._pending.values():
            points.update((max(cmd.start, cmd.issued), cmd.end))
        return tuple(sorted(t for t in points if t0 <= t <= t1))

    def _base_at(self, t):
        return self._base.cap_kw if t < self._base.end else self.fallback_kw

    def _at(self, t):
        cap = self._base_at(t)
        if any(end <= t for end in self._fallback_events.values()):
            cap = max(cap, self.fallback_kw)
        for cmd in self._pending.values():
            if max(cmd.start, cmd.issued) <= t < cmd.end:
                cap = max(cap, cmd.cap_kw)
        return cap

    def envelope(self, t0, t1):
        points = self.breakpoints(t0, t1)
        return max(self._at(t) for t in (points[:-1] if t1 > t0 else points))

    def can_expand(self, t0, t1):
        """Whether no unresolved possibility expands the evidence-only base.

        Recomputed after every certificate, so later pending commands remain
        recognized even when an older reduction has just been confirmed.
        """
        points = self.breakpoints(t0, t1)
        for t in (points[:-1] if t1 > t0 else points):
            baseline = self._base_at(t)
            # Completed uncertain expiries are settled known envelope commitments.
            for g, end in self._fallback_events.items():
                if g not in self._pending and end <= t:
                    baseline = max(baseline, self.fallback_kw)
            for cmd in self._pending.values():
                possible = (cmd.cap_kw if max(cmd.start, cmd.issued) <= t < cmd.end
                            else self.fallback_kw if t >= cmd.end else 0)
                if possible > baseline:
                    return False
        return True
