"""Continuous physical charging replay with causal, delayed controller evidence.

The controller never receives the mutable plant's Station or energy arrays.
See docs/SIMULATOR.md for event order, metric units, and model boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import itertools
import math
import numpy as np

from .execution import Station


@dataclass
class EpisodeResult:
    metrics: dict
    trace: list[dict]
    events: list[dict]


def run_episode(scenario, policy_name, feeder=None, retain_trace=False) -> EpisodeResult:
    """Replay one episode; stopping AC failure is explicitly marked in metrics."""
    from .policies import Controller, build_network, initial_allocation
    if feeder is None:
        from .feeder import FeederModel
        feeder = FeederModel(scenario.feeder_name, scenario.n_stations)
    n = int(scenario.n_stations)
    duration = float(scenario.duration)
    if duration <= 0 or scenario.control_step <= 5 or scenario.telemetry_step <= 0:
        raise ValueError('Positive duration/telemetry step and control_step > 5s required')
    network = build_network(feeder, scenario)
    initial = np.asarray(initial_allocation(scenario, network), dtype=float)
    if initial.shape != (n,) or np.any(initial < 0) or not np.all(np.isfinite(initial)):
        raise ValueError('Initial caps must be finite, nonnegative, and match station count')
    fallback = .65 * initial
    ends = np.asarray(scenario.initial_ends(), dtype=float)
    if ends.shape != (n,):
        raise ValueError('Initial profile ends must match station count')
    plant = [Station(i, float(initial[i]), float(fallback[i]), float(ends[i]),
                     float(scenario.actuation_s)) for i in range(n)]
    controller = Controller(policy_name, initial.copy(), fallback.copy(), network, scenario)
    sessions = tuple(scenario.sessions)
    ids = [s.sid for s in sessions]
    if len(set(ids)) != len(ids):
        raise ValueError('Session IDs must be unique')
    for s in sessions:
        if (not 0 <= s.station < n or not all(math.isfinite(float(v)) for v in
            (s.arrival, s.departure, s.request_kwh, s.accept_kw)) or s.arrival < 0
            or s.departure <= s.arrival or s.request_kwh < 0 or s.accept_kw < 0):
            raise ValueError('Invalid physical session')
    by_station = [sorted((j for j,s in enumerate(sessions) if s.station == i),
                         key=lambda j: (sessions[j].departure, sessions[j].sid)) for i in range(n)]
    energy = np.zeros(len(sessions))
    connected = np.array([s.arrival <= 0 < s.departure for s in sessions], dtype=bool)
    delivered_est = {s.sid: 0. for s in sessions if s.arrival <= 0}
    measured = np.zeros(n)
    last_sample = np.full(n, -math.inf)
    previous_request = initial.copy()
    queue, serial = [], itertools.count()
    trace, events = [], []
    stats = dict(commands=0, queries=0, message_losses=0, delayed_messages=0,
                 command_rejections=0, certificates=0, acceptance_replies=0,
                 telemetry_received=0, stale_telemetry=0, convergence_failures=0,
                 voltage_exposure_pu_s=0., loading_exposure_s=0., violation_seconds=0.,
                 reserved_unused_kwh=0., max_affine_excess=0.,
                 envelope_containment_failures=0, envelope_containment_seconds=0.,
                 max_envelope_excess_kw=0., failed=False)
    counts = {k: 0 for k in ('command','query_down','certificate','telemetry','ack')}
    losses = counts.copy()
    t = 0.

    def push(at, priority, kind, payload=None):
        if not math.isfinite(at) or at < t - 1e-9:
            raise ValueError('Event time must be finite and causal')
        if at <= duration:
            heapq.heappush(queue, (float(at), priority, next(serial), kind, payload))

    def log(kind, at=None, **fields):
        if retain_trace:
            # Indefinite persistent fallback is encoded as null, not JSON Infinity.
            fields = {k: None if isinstance(v, (float, np.floating)) and not math.isfinite(v)
                      else v for k,v in fields.items()}
            events.append(dict(type=kind, t=float(t if at is None else at), **fields))

    def send(kind, now, station, payload, event, priority, delta=0., generation=0):
        counts[kind] += 1
        delay = float(scenario.delay(kind, now, station, delta=delta, generation=generation))
        if math.isnan(delay) or delay < 0:
            raise ValueError('Channel delay must be nonnegative or positive infinity')
        if math.isinf(delay):
            losses[kind] += 1
            stats['message_losses'] += 1
            log('message_lost', at=now, channel=kind, station=station, generation=generation)
            return None
        if delay > 0:
            stats['delayed_messages'] += 1
        push(now + delay, priority, event, payload)
        return now + delay

    def emit(certificate):
        # Freeze the witness now: a delayed query cannot report a later state.
        stats['certificates'] += 1
        log('certificate_emitted', station=certificate.station,
            generation=certificate.generation, applied=certificate.applied,
            cap=certificate.cap_kw, end=certificate.end, evidence_available=t if policy_name=='R' else None)
        if policy_name == 'R':
            controller.observe_certificate(certificate, t)
            log('certificate_received', station=certificate.station,
                generation=certificate.generation, applied=certificate.applied,
                cap=certificate.cap_kw, end=certificate.end, evidence_available=t, ideal=True)
        send('certificate', t, certificate.station, certificate, 'certificate', 1,
             generation=certificate.generation)

    def schedule_transition(i):
        at = plant[i].next_transition
        if math.isfinite(at):
            # Tuple token invalidates older queue entries without mutating the heap.
            # Adapter transitions precede query/command operations at the same time,
            # regardless of insertion order. Accessors must not consume them first.
            push(at, -1, 'transition', (i, at, plant[i].pending))

    def advance_station(i, force_schedule=False):
        # The only replay-time certificate accessor: Station.certificate advances
        # physical state, so every use must reconcile the witness and next event.
        certificate = plant[i].certificate(t)
        changed = certificate != last_certificates[i]
        if changed:
            last_certificates[i] = certificate
            log('physical_cap', station=i, generation=certificate.generation,
                applied=certificate.applied, cap=certificate.cap_kw, end=certificate.end)
            emit(certificate)
        if changed or force_schedule:
            schedule_transition(i)
        return certificate

    def allocation():
        caps = np.array([advance_station(i).cap_kw for i in range(n)])
        session_power = np.zeros(len(sessions))
        powers = np.zeros(n)
        for i, indices in enumerate(by_station):
            left = caps[i]
            for j in indices:
                if connected[j] and energy[j] < sessions[j].request_kwh - 1e-12:
                    take = min(left, sessions[j].accept_kw)
                    session_power[j] = take
                    powers[i] += take
                    left -= take
                    if left <= 1e-12:
                        break
        return caps, session_power, powers

    last_certificates = [s.certificate(0.) for s in plant]
    for i in range(n):
        schedule_transition(i)
    for j,s in enumerate(sessions):
        if s.arrival > 0:
            push(s.arrival, 0, 'arrival', j)
        push(s.departure, 0, 'departure', j)
    # The control grid also carries background changes, independent of commands.
    for tick in np.arange(0., duration, float(scenario.control_step)):
        push(float(tick), 2, 'control')
    for tick in np.arange(0., duration + 1e-9, float(scenario.telemetry_step)):
        push(float(tick), 1, 'sample')
    push(duration, 3, 'end')
    cached_key = None
    cached_result = None
    worst_min, worst_max, worst_loading = math.inf, -math.inf, 0.
    initial_violation = False
    ac_evaluations = 0

    def evaluate(powers):
        nonlocal cached_key, cached_result, worst_min, worst_max, worst_loading, ac_evaluations
        key = (tuple(float(x) for x in powers), float(scenario.background(t)))
        if key != cached_key:
            cached_result = feeder.evaluate(powers, background_scale=key[1])
            cached_key = key
            ac_evaluations += 1
            if not cached_result['converged']:
                stats['convergence_failures'] += 1
                stats['failed'] = True
                log('ac_failure', powers=powers.tolist(), background=key[1])
                return None
        result = cached_result
        values = [result['v_min'], result['v_max'], result['loading_max']]
        if not all(math.isfinite(float(x)) for x in values):
            raise ValueError('Converged AC replay returned nonfinite extrema')
        worst_min = min(worst_min, float(result['v_min']))
        worst_max = max(worst_max, float(result['v_max']))
        worst_loading = max(worst_loading, float(result['loading_max']))
        return result

    # Preserve initial infeasibility even if an immediate control action removes it.
    initial_result = evaluate(allocation()[2])
    if initial_result is not None:
        initial_violation = (initial_result['v_min'] < .95 or initial_result['v_max'] > 1.05
                             or initial_result['loading_max'] > 1.)
    while queue and not stats['failed']:
        # Drain every causally available event at the present instant before integration.
        while queue and queue[0][0] <= t + 1e-10:
            _, _, _, kind, payload = heapq.heappop(queue)
            if kind == 'arrival':
                connected[payload] = True
                delivered_est[sessions[payload].sid] = 0.
                log('arrival', session=sessions[payload].sid, station=sessions[payload].station)
            elif kind == 'departure':
                connected[payload] = False
                log('departure', session=sessions[payload].sid, station=sessions[payload].station,
                    delivered_kwh=float(energy[payload]))
            elif kind == 'transition':
                i, at, pending = payload
                if plant[i].next_transition == at and plant[i].pending == pending:
                    advance_station(i, force_schedule=True)
            elif kind == 'command':
                cmd = payload
                # receive() also advances the adapter: reconcile old work before
                # accepting a new generation that can replace its pending slot.
                advance_station(cmd.station)
                accepted = plant[cmd.station].receive(cmd, t)
                log('command_received', station=cmd.station, generation=cmd.generation,
                    issued=cmd.issued, received=t, cap=cmd.cap_kw, end=cmd.end, accepted=accepted)
                if accepted:
                    send('ack', t, cmd.station, cmd, 'ack', 1, generation=cmd.generation)
                else:
                    stats['command_rejections'] += 1
                # Zero-lag application occurs inside receive().
                advance_station(cmd.station, force_schedule=True)
            elif kind == 'ack':
                stats['acceptance_replies'] += 1
                controller.observe_acceptance(payload, t)
                log('acceptance_received', station=payload.station, generation=payload.generation,
                    issued=payload.issued, received=t, cap=payload.cap_kw, end=payload.end)
            elif kind == 'certificate':
                controller.observe_certificate(payload, t)
                log('certificate_received', station=payload.station, generation=payload.generation,
                    applied=payload.applied, cap=payload.cap_kw, end=payload.end,
                    received=t, evidence_available=t, ideal=False)
            elif kind == 'query':
                i = payload
                cert = advance_station(i)
                log('query_received', station=i, generation=cert.generation, received=t)
                send('certificate', t, i, cert, 'certificate', 1, generation=cert.generation)
            elif kind == 'sample':
                powers = allocation()[2]
                for i, indices in enumerate(by_station):
                    sample_energy = {sessions[j].sid: float(energy[j]) for j in indices
                                     if sessions[j].arrival <= t}
                    send('telemetry', t, i, (i,t,sample_energy,float(powers[i])), 'telemetry', 1)
            elif kind == 'telemetry':
                i, sampled_at, sample_energy, sample_power = payload
                if sampled_at > last_sample[i]:
                    last_sample[i] = sampled_at
                    delivered_est.update(sample_energy)
                    measured[i] = sample_power
                    stats['telemetry_received'] += 1
                    log('telemetry_received', station=i, sampled_at=sampled_at,
                        received=t, measured_kw=sample_power, delivered_kwh=sum(sample_energy.values()))
                else:
                    stats['stale_telemetry'] += 1
            elif kind == 'control':
                known = tuple(s for s in sessions if s.arrival <= t < s.departure)
                cmds = list(controller.decide(t, known, dict(delivered_est), measured.copy()))
                destinations = set()
                for cmd in cmds:
                    if cmd.station in destinations:
                        raise ValueError('Only one command RPC per station per control tick')
                    destinations.add(cmd.station)
                    delta = cmd.cap_kw - previous_request[cmd.station]
                    previous_request[cmd.station] = cmd.cap_kw
                    stats['commands'] += 1
                    # A lost overlay still changes the knowledge envelope at its
                    # declared start/expiry, independently of physical receipts.
                    push(max(cmd.start, cmd.issued), 0, 'commitment')
                    push(cmd.end, 0, 'commitment')
                    log('command_sent', station=cmd.station, generation=cmd.generation,
                        issued=cmd.issued, cap=cmd.cap_kw, end=cmd.end)
                    send('command', t, cmd.station, cmd, 'command', 0, delta=delta,
                         generation=cmd.generation)
                query_set = list(controller.query_stations(t, destinations))
                if len(set(query_set)) != len(query_set) or destinations.intersection(query_set):
                    raise ValueError('Only one command or query RPC per station per tick')
                for i in query_set:
                    stats['queries'] += 1
                    log('query_sent', station=i, issued=t)
                    send('query_down', t, i, i, 'query', 0)
            elif kind not in ('end', 'commitment'):
                raise RuntimeError(f'Unknown event {kind}')
        if t >= duration:
            break
        caps, session_power, powers = allocation()
        result = evaluate(powers)
        if result is None:
            break
        next_at = queue[0][0] if queue else duration
        charging = session_power > 0
        if np.any(charging):
            remaining = np.array([s.request_kwh for s in sessions]) - energy
            complete_in = float(np.min(remaining[charging] / session_power[charging] * 3600.))
            next_at = min(next_at, t + complete_in)
        dt = next_at - t
        if dt <= 1e-11:
            raise RuntimeError('Physical integration failed to make progress')
        reserved = np.asarray(controller.reservation(t, t), dtype=float)
        if reserved.shape != (n,) or not np.all(np.isfinite(reserved)):
            raise ValueError('Reservation must be a finite station vector')
        excess = max(0., float(np.max(np.maximum(caps, powers) - reserved)))
        if excess > 1e-7:
            stats['envelope_containment_failures'] += 1
            stats['envelope_containment_seconds'] += dt
        stats['max_envelope_excess_kw'] = max(stats['max_envelope_excess_kw'], excess)
        stats['max_affine_excess'] = max(stats['max_affine_excess'],
                                          float(controller.affine_excess(t)))
        voltages = np.asarray(result.get('voltage_pu', [result['v_min'],result['v_max']]))
        loadings = np.asarray(result.get('loading_pu', [result['loading_max']]))
        voltage_excess = float(np.maximum(.95 - voltages,0).sum() + np.maximum(voltages-1.05,0).sum())
        loading_excess = float(np.maximum(loadings-1.,0).sum())
        violation = bool(voltage_excess > 0 or loading_excess > 0)
        stats['voltage_exposure_pu_s'] += voltage_excess * dt
        stats['loading_exposure_s'] += loading_excess * dt
        stats['violation_seconds'] += dt if violation else 0.
        stats['reserved_unused_kwh'] += max(0., float(reserved.sum()-powers.sum())) * dt / 3600.
        if retain_trace:
            trace.append(dict(t=float(t), dt=float(dt), total_actual_kw=float(powers.sum()),
                total_reserved_kw=float(reserved.sum()), group_powers=powers.tolist(),
                group_caps=caps.tolist(), group_reservations=reserved.tolist(),
                delivered_kwh=float(energy.sum()), v_min=float(result['v_min']),
                v_max=float(result['v_max']), loading_max=float(result['loading_max']),
                violation=violation, connected_demand_kw=float(sum(s.accept_kw for j,s in enumerate(sessions)
                    if connected[j] and energy[j] < s.request_kwh-1e-12))))
        energy += session_power * dt / 3600.
        for j in np.flatnonzero(charging):
            if energy[j] >= sessions[j].request_kwh - 1e-11:
                energy[j] = sessions[j].request_kwh
                log('energy_complete', at=next_at, session=sessions[j].sid,
                    station=sessions[j].station, delivered_kwh=float(energy[j]))
        t = float(next_at)
    requested = float(sum(s.request_kwh for s in sessions if s.arrival <= duration))
    shortfall = float(sum(max(0.,s.request_kwh-energy[j]) for j,s in enumerate(sessions)
                          if s.departure <= duration))
    stats.update(policy=policy_name, seed=int(scenario.seed), pattern=scenario.pattern,
        loading=float(scenario.loading), feeder=scenario.feeder_name,
        duration_s=duration, simulated_seconds=float(t), delivered_kwh=float(energy.sum()),
        requested_kwh=requested, deadline_shortfall_kwh=shortfall,
        v_min=float(worst_min) if math.isfinite(worst_min) else 1.,
        v_max=float(worst_max) if math.isfinite(worst_max) else 1., loading_max=float(worst_loading),
        initial_electrical_violation=bool(initial_violation), ac_evaluations=ac_evaluations,
        filter_seconds=float(controller.filter_seconds),
        filter_median_ms=float(np.median(controller.decision_times)*1000) if controller.decision_times else 0.,
        filter_max_ms=float(max(controller.decision_times)*1000) if controller.decision_times else 0.,
        messages_by_kind=counts, losses_by_kind=losses,
        session_delivered_kwh={str(s.sid):float(energy[j]) for j,s in enumerate(sessions)})
    return EpisodeResult(stats, trace, events)
