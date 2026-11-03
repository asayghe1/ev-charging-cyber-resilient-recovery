"""Post-review same-generation retry extension; frozen core remains untouched.

This additional comparator-fairness study is not a prespecified primary analysis.
Scoped Controller replacement requires sequential calls in isolated processes;
do not call run_retry_episode concurrently from threads in one interpreter.
"""
from __future__ import annotations

from dataclasses import replace
import math
from time import perf_counter
from unittest.mock import patch

from .execution import Command
from .policies import Controller as _BaseController

RETRY_PERIOD_S = 60.
RETRY_POLICIES = ('B3', 'P', 'B3_retry', 'P_retry')
BASE_POLICY = {'B3_retry': 'B3', 'P_retry': 'P'}


class RetryScenario:
    """Delegate unchanged exogenous inputs; classify retries by logical direction."""

    def __init__(self, scenario):
        self._scenario = scenario
        self._command_deltas = {}

    def __getattr__(self, name):
        return getattr(self._scenario, name)

    def delay(self, kind, t, station, delta=0., generation=0):
        if kind == 'command':
            key = (int(station), int(generation))
            delta = self._command_deltas.setdefault(key, float(delta))
        return self._scenario.delay(kind, t, station, delta=delta, generation=generation)


class RetryController(_BaseController):
    """Base admission plus retries of the latest unresolved logical operation.

    A retransmission gets a fresh issued transport timestamp. Its durable logical
    identity, absolute execution window and original Ledger record do not change.
    """

    def __init__(self, policy_name, initial_caps, fallback_caps, network, scenario,
                 retry_period=RETRY_PERIOD_S):
        if policy_name not in BASE_POLICY:
            raise ValueError('RetryController requires B3_retry or P_retry')
        if not math.isfinite(retry_period) or retry_period <= 0:
            raise ValueError('retry_period must be finite and positive')
        super().__init__(BASE_POLICY[policy_name], initial_caps, fallback_caps, network, scenario)
        self.retry_period = float(retry_period)
        self.retries = 0
        self.logical_commands = 0
        self._last_attempt = {}

    def decide(self, t, known_sessions, delivered_est, measured_kw):
        started = perf_counter()
        charged_before = self.filter_seconds
        commands = list(super().decide(t, known_sessions, delivered_est, measured_kw))
        destinations = {cmd.station for cmd in commands}
        self.logical_commands += len(commands)
        for cmd in commands:
            self._last_attempt[(cmd.station, cmd.generation)] = float(t)
        for i, ledger in enumerate(self.ledgers):
            if i in destinations:
                continue
            unresolved = ledger.unresolved()
            if not unresolved:
                continue
            logical = max(unresolved, key=lambda cmd: cmd.generation)
            previous = self._last_attempt.get((i, logical.generation), logical.issued)
            if t >= logical.end or t - previous < self.retry_period - 1e-9:
                continue
            # Preserve logical generation/start/absolute end; do not reserve again.
            # Frozen simulator commitment events require a current issued stamp.
            retransmission: Command = replace(logical, issued=float(t))
            commands.append(retransmission)
            self._last_attempt[(i, logical.generation)] = float(t)
            self.retries += 1
        # Charge the retry loop to the same decision-computation metric as its base.
        extra = max(0., perf_counter() - started - (self.filter_seconds - charged_before))
        self.filter_seconds += extra
        self.decision_times[-1] += extra
        return commands


def run_retry_episode(scenario, policy_name, feeder=None, retain_trace=False,
                      retry_period=RETRY_PERIOD_S):
    """Run a fresh wrapped episode; restore the original Controller on all exits."""
    from . import policies
    from .simulator import run_episode
    if policy_name not in RETRY_POLICIES:
        raise ValueError('Retry study permits B3, P, B3_retry, or P_retry')
    wrapped = RetryScenario(scenario)
    if policy_name not in BASE_POLICY:
        return run_episode(wrapped, policy_name, feeder, retain_trace)
    made = []

    def create(*args, **kwargs):
        controller = RetryController(*args, **kwargs, retry_period=retry_period)
        made.append(controller)
        return controller

    with patch.object(policies, 'Controller', create):
        result = run_episode(wrapped, policy_name, feeder, retain_trace)
    result.metrics['retries'] = made[0].retries
    result.metrics['logical_commands'] = made[0].logical_commands
    return result
