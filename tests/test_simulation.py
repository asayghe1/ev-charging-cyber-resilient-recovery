"""Physical integration tests use a real Station/Ledger and scripted admissions."""
from dataclasses import dataclass
import sys
from types import SimpleNamespace
import numpy as np
import pytest
from evrecovery.execution import Command, Ledger


@dataclass(frozen=True)
class Session:
    sid: int = 0
    station: int = 0
    arrival: float = 0.
    departure: float = 720.
    request_kwh: float = 1.
    accept_kw: float = 10.


class ToyFeeder:
    name = 'toy'
    n_stations = 1
    def evaluate(self, power_kw, background_scale=1.):
        return dict(converged=True, voltage_pu=np.array([1.]), loading_pu=np.array([.5]),
                    v_min=1., v_max=1., loading_max=.5)


def scenario(sessions=None, duration=720., delay=None, **overrides):
    x = SimpleNamespace(seed=1, pattern='clean', loading=1., feeder_name='toy',
        n_stations=1, group_max_kw=30., duration=duration, control_step=30.,
        telemetry_step=30., actuation_s=0., background_uncertainty=0.,
        initial_ends=lambda: np.array([10000.]), background=lambda t: 1.,
        delay=delay or (lambda kind,t,station,delta=0.,generation=0: 0.),
        sessions=tuple(sessions or [Session()]))
    for k,v in overrides.items(): setattr(x,k,v)
    return x


def install_policy(monkeypatch, script=None, initial=10., queries=False):
    made=[]
    class Controller:
        def __init__(self, policy_name, initial_caps, fallback_caps, network, scenario):
            self.ledgers=[Ledger(i,float(c),float(f),float(e)) for i,(c,f,e) in
                          enumerate(zip(initial_caps,fallback_caps,scenario.initial_ends()))]
            self.inputs=[]; self.receipts=[]; self.filter_seconds=0.; self.decision_times=[]
            made.append(self)
        def decide(self,t,known_sessions,delivered_est,measured_kw):
            self.inputs.append((t,tuple(s.sid for s in known_sessions),dict(delivered_est),measured_kw.copy()))
            commands=list(script(t) if script else [])
            for c in commands: self.ledgers[c.station].reserve(c)
            return commands
        def observe_certificate(self,c,received_at):
            self.receipts.append((received_at,c));self.ledgers[c.station].observe(c)
        def observe_acceptance(self,c,received_at): pass
        def reservation(self,t0,t1): return np.array([x.envelope(t0,t1) for x in self.ledgers])
        def query_stations(self,t,exclude): return [0] if queries and 0 not in exclude else []
        def affine_excess(self,t): return 0.
    monkeypatch.setitem(sys.modules,'evrecovery.policies',SimpleNamespace(
        Controller=Controller,build_network=lambda f,s: None,
        initial_allocation=lambda s,n: np.full(s.n_stations,initial)))
    return made


def run(s,policy='P'):
    from evrecovery.simulator import run_episode
    return run_episode(s,policy,ToyFeeder(),retain_trace=True)


@pytest.mark.parametrize('requested,departure,expected',[(1.,720.,1.),(10.,100.,10*100/3600)])
def test_actual_energy_integrates_to_completion_or_departure(monkeypatch,requested,departure,expected):
    install_policy(monkeypatch)
    out=run(scenario([Session(request_kwh=requested,departure=departure)]))
    assert out.metrics['delivered_kwh']==pytest.approx(expected,abs=1e-10)
    assert out.metrics['deadline_shortfall_kwh']==pytest.approx(max(0,requested-expected))
    assert sum(r['total_actual_kw']*r['dt']/3600 for r in out.trace)==pytest.approx(expected)
    if requested==1.:
        assert all(r['total_actual_kw']==0 for r in out.trace if r['t']>=360.)


def test_reduction_waits_for_execution_and_no_private_state_leaks(monkeypatch):
    made=install_policy(monkeypatch,lambda t: [Command(0,1,0,0,600,0.)] if t==0 else [])
    def delay(kind,t,station,delta=0.,generation=0):
        return 100. if kind in ('command','certificate') else float('inf') if kind=='telemetry' else 0.
    out=run(scenario([Session(request_kwh=10.)],duration=240.,delay=delay))
    assert all(r['group_caps'][0]==10 for r in out.trace if r['t']<100.)
    assert all(r['group_reservations'][0]>=10 for r in out.trace if r['t']<200.)
    assert all(data[2][0]==0 and data[3][0]==0 for data in made[0].inputs)
    assert made[0].receipts[0][0]==200.


def test_stale_telemetry_does_not_rewind_energy(monkeypatch):
    made=install_policy(monkeypatch)
    def delay(kind,t,station,delta=0.,generation=0):
        return 90. if kind=='telemetry' and t==0 else 0.
    run(scenario([Session(request_kwh=10.)],duration=120.,delay=delay))
    at90=next(x for x in made[0].inputs if x[0]==90.)
    assert at90[2][0]==pytest.approx(.25)


def test_ideal_feedback_preserves_later_pending_forward_command(monkeypatch):
    def script(t):
        return [Command(0,1,0,50,300,5.)] if t==0 else [Command(0,2,30,30,300,20.)] if t==30 else []
    made=install_policy(monkeypatch,script)
    def delay(kind,t,station,delta=0.,generation=0):
        return 100. if kind=='command' and generation==2 else 1000. if kind=='certificate' else 0.
    out=run(scenario([Session(request_kwh=10.)],duration=160.,delay=delay),'R')
    row=next(r for r in out.trace if r['t']==60.)
    assert row['group_reservations'][0]==20.
    assert any(t==50 and c.generation==1 for t,c in made[0].receipts)
    assert out.metrics['envelope_containment_failures']==0


def test_scientific_metrics_repeat_and_cap_is_contained(monkeypatch):
    install_policy(monkeypatch)
    first=run(scenario())
    second=run(scenario())
    assert first.metrics==second.metrics
    assert first.metrics['envelope_containment_failures']==0
    for row in first.trace:
        assert np.all(np.array(row['group_powers'])<=np.array(row['group_caps'])+1e-9)
        assert np.all(np.array(row['group_caps'])<=np.array(row['group_reservations'])+1e-9)


def test_lost_command_commitment_start_end_split_reservation_integral(monkeypatch):
    install_policy(monkeypatch,lambda t: [Command(0,1,0,5,25,20.)] if t==0 else [])
    def delay(kind,t,station,delta=0.,generation=0):
        return float('inf') if kind=='command' else 0.
    out=run(scenario([Session(request_kwh=10.)],duration=30.,delay=delay))
    assert out.metrics['reserved_unused_kwh']==pytest.approx(10*20/3600)
    assert {5.,25.}.issubset({r['t'] for r in out.trace})


def test_future_bookings_and_plant_completion_are_not_early_controller_inputs(monkeypatch):
    made=install_policy(monkeypatch)
    s=scenario([Session(sid=4,request_kwh=.01),Session(sid=8,arrival=45.,request_kwh=1.)],
               duration=90.,delay=lambda kind,t,station,delta=0.,generation=0:
                   float('inf') if kind=='telemetry' else 0.)
    run(s)
    assert made[0].inputs[1][1]==(4,)
    assert made[0].inputs[1][2]=={4:0.}
    assert made[0].inputs[2][1]==(4,8)


def test_query_response_is_snapshot_at_query_arrival(monkeypatch):
    def script(t):
        return [Command(0,1,0,0,300,5.)] if t==0 else [Command(0,2,60,60,300,2.)] if t==60 else []
    made=install_policy(monkeypatch,script,queries=True)
    def delay(kind,t,station,delta=0.,generation=0):
        return 100. if kind=='certificate' else 0.
    out=run(scenario([Session(request_kwh=10.)],duration=140.,delay=delay))
    at130=[c for received,c in made[0].receipts if received==130.]
    assert len(at130)==1 and at130[0].generation==1 and at130[0].cap_kw==5.
    assert any(r['t']==120. and r['group_caps']==[2.] for r in out.trace)


def test_ac_exposure_sums_each_phase_and_preserves_initial_violation(monkeypatch):
    install_policy(monkeypatch)
    class ViolatingFeeder(ToyFeeder):
        def evaluate(self,power_kw,background_scale=1.):
            return dict(converged=True,voltage_pu=np.array([.94,1.07]),
                loading_pu=np.array([1.2,1.3]),v_min=.94,v_max=1.07,loading_max=1.3)
    from evrecovery.simulator import run_episode
    out=run_episode(scenario(duration=60.),'P',ViolatingFeeder())
    assert out.metrics['voltage_exposure_pu_s']==pytest.approx(.03*60)
    assert out.metrics['loading_exposure_s']==pytest.approx(.5*60)
    assert out.metrics['violation_seconds']==60.
    assert out.metrics['initial_electrical_violation']


def test_nonconverged_ac_run_stops_and_is_marked_failed(monkeypatch):
    install_policy(monkeypatch,lambda t: [Command(0,1,0,10,300,0.)] if t==0 else [])
    class FailingFeeder(ToyFeeder):
        def evaluate(self,power_kw,background_scale=1.):
            result=super().evaluate(power_kw,background_scale)
            result['converged']=bool(power_kw[0]>0)
            return result
    from evrecovery.simulator import run_episode
    out=run_episode(scenario(duration=60.),'P',FailingFeeder())
    assert out.metrics['failed'] and out.metrics['convergence_failures']==1
    assert out.metrics['simulated_seconds']==10.
    assert out.metrics['delivered_kwh']==pytest.approx(100/3600)


def test_expiry_records_support_strict_json(monkeypatch):
    import json
    install_policy(monkeypatch)
    out=run(scenario(duration=60.,initial_ends=lambda:np.array([15.])))
    json.dumps(dict(metrics=out.metrics,trace=out.trace,events=out.events),allow_nan=False)


def test_actual_proposed_controller_contains_physical_replay():
    from evrecovery.scenarios import Scenario
    from evrecovery.simulator import run_episode
    class ConstraintFeeder(ToyFeeder):
        def linear_constraints(self,**kwargs):
            return dict(A=np.ones((1,1)),b=np.array([10.]),C=np.zeros(1))
    s=Scenario(9,'clean',1.,feeder_name='toy',n_stations=1,group_max_kw=10.,
        duration=150.,sessions=(Session(request_kwh=.1,departure=75.),
                               Session(sid=1,arrival=30.,departure=110.,request_kwh=.2)))
    out=run_episode(s,'P',ConstraintFeeder(),True)
    assert not out.metrics['failed']
    assert out.metrics['max_affine_excess']<=1e-9
    assert out.metrics['envelope_containment_failures']==0
    assert out.metrics['delivered_kwh']<=.3+1e-9
    assert all(r['group_powers'][0]<=r['group_caps'][0]+1e-9<=r['group_reservations'][0]+2e-9
               for r in out.trace)


@pytest.mark.parametrize('policy',['P','R'])
def test_query_ties_cannot_suppress_application_or_its_later_expiry(monkeypatch,policy):
    made=install_policy(monkeypatch,
        lambda t:[Command(0,1,0,0,80,2.)] if t==0 else [],queries=True)
    def delay(kind,t,station,delta=0.,generation=0):
        return 40. if kind=='command' else 20. if kind=='query_down' else 100. if kind=='certificate' else 0.
    out=run(scenario([Session(request_kwh=10.)],duration=200.,delay=delay,actuation_s=10.),policy)
    emitted=[(e['t'],e['generation'],e['cap']) for e in out.events if e['type']=='certificate_emitted']
    assert emitted==[(50.,1,2.),(80.,1,6.5)]
    assert out.metrics['certificates']==2
    if policy=='R':
        assert any(t==50. and c.generation==1 and c.cap_kw==2. for t,c in made[0].receipts)
        assert any(t==80. and c.generation==1 and c.cap_kw==6.5 for t,c in made[0].receipts)
    else:
        assert min(t for t,c in made[0].receipts)==150.
    assert out.metrics['delivered_kwh']==pytest.approx((10*50+2*30+6.5*120)/3600)
    for at in (50.,80.):
        kinds=[e['type'] for e in out.events if e['t']==at]
        assert kinds.index('physical_cap')<kinds.index('query_received')


def test_command_receipt_tie_cannot_supersede_unreported_application(monkeypatch):
    def script(t):
        return [Command(0,1,0,0,100,2.)] if t==0 else [Command(0,2,30,30,150,4.)] if t==30 else []
    made=install_policy(monkeypatch,script)
    def delay(kind,t,station,delta=0.,generation=0):
        return (40. if generation==1 else 20.) if kind=='command' else 100. if kind=='certificate' else 0.
    out=run(scenario([Session(request_kwh=10.)],duration=90.,delay=delay,actuation_s=10.),'R')
    assert [(t,c.generation,c.cap_kw) for t,c in made[0].receipts]==[(50.,1,2.),(60.,2,4.)]
    assert out.metrics['certificates']==2
    kinds=[e['type'] for e in out.events if e['t']==50.]
    assert kinds.index('physical_cap')<kinds.index('command_received')
