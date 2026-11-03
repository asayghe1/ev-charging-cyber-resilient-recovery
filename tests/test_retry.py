"""Post-review retry robustness: logical identity, timing, and unchanged core."""
from dataclasses import asdict
import hashlib
from pathlib import Path
import numpy as np
import pytest
from evrecovery.execution import Command, Station
from evrecovery.policies import NetworkConstraints
from evrecovery.scenarios import Scenario, Session

ROOT=Path(__file__).resolve().parents[1]
CORE=('execution.py','feeder.py','policies.py','scenarios.py','simulator.py')


def configured():
    return Scenario(7,'T2',1.,n_stations=1,group_max_kw=30.,duration=300.,
        sessions=(Session(0,0,0.,300.,100.,20.),))


def controller(name='B3_retry'):
    from evrecovery.retry import RetryController, RetryScenario
    return RetryController(name,np.array([10.]),np.array([6.5]),
        NetworkConstraints(np.ones((1,1)),np.array([30.])),RetryScenario(configured()))


def decision(c,t,sessions=None):
    return c.decide(t,configured().sessions if sessions is None else sessions,{},np.zeros(1))


def test_retry_preserves_logical_fields_and_ledger_without_renewal():
    c=controller(); original,=decision(c,0.)
    assert decision(c,30.)==[]
    retry,=decision(c,60.)
    assert retry.issued==60. and original.issued==0.
    for field in ('station','generation','start','end','cap_kw','epoch'):
        assert getattr(retry,field)==getattr(original,field)
    assert c.ledgers[0].unresolved()==(original,)
    assert c.history[0]==[original]
    assert c.retries==1
    assert decision(c,90.)==[]
    assert decision(c,120.)[0].generation==original.generation


def test_queries_continue_between_retry_ticks_and_not_on_retry_ticks():
    c=controller(); decision(c,0.)
    assert decision(c,30.)==[]
    assert c.query_stations(30.,set())==[0]
    retry,=decision(c,60.)
    assert c.query_stations(60.,{retry.station})==[]
    assert decision(c,90.)==[]
    assert c.query_stations(90.,set())==[0]


def test_new_command_preempts_due_retry_and_latest_generation_retries():
    c=controller('P_retry'); first,=decision(c,0.)
    reduction=(Session(1,0,0.,300.,10.,5.),)
    new,=decision(c,60.,reduction)
    assert new.generation==first.generation+1 and c.retries==0
    assert decision(c,90.,reduction)==[]
    retry,=decision(c,120.,reduction)
    assert retry.generation==new.generation and c.retries==1


def test_already_accepted_retry_does_not_apply_again_or_extend_expiry():
    c=controller(); original,=decision(c,0.); retry,=decision(c,60.)
    plant=Station(0,10.,6.5,1000.,actuation_s=100.)
    assert plant.receive(original,0.)
    assert not plant.receive(retry,60.)
    assert plant.next_transition==100.
    cert=plant.certificate(100.)
    assert cert.applied==100. and cert.end==original.end
    assert plant.certificate(original.end).cap_kw==6.5


def test_retry_of_lost_original_applies_with_original_absolute_expiry():
    c=controller(); original,=decision(c,0.); retry,=decision(c,60.)
    plant=Station(0,10.,6.5,1000.,actuation_s=10.)
    assert plant.receive(retry,60.)
    assert plant.certificate(70.).generation==original.generation
    assert plant.certificate(70.).end==original.end
    assert plant.certificate(original.end).cap_kw==6.5


def test_expired_logical_command_is_not_retried():
    c=controller(); original,=decision(c,0.)
    # The absolute end excludes retry even if a caller jumps straight to expiry.
    assert decision(c,original.end,())==[]
    assert c.retries==0


@pytest.mark.parametrize('delta',[-10.,10.])
def test_retry_delay_preserves_original_command_direction(delta):
    from evrecovery.retry import RetryScenario
    class Channel:
        def __init__(self): self.seen=[]
        def delay(self,kind,t,station,delta=0.,generation=0):
            self.seen.append((kind,t,station,delta,generation))
            return abs(delta)
    channel=Channel(); wrapped=RetryScenario(channel)
    assert wrapped.delay('command',0.,0,delta=delta,generation=4)==abs(delta)
    assert wrapped.delay('command',60.,0,delta=0.,generation=4)==abs(delta)
    assert channel.seen[-1][3]==delta
    wrapped.delay('command',60.,0,delta=3.,generation=5)
    assert channel.seen[-1][3]==3.
    wrapped.delay('telemetry',60.,0,delta=0.,generation=4)
    assert channel.seen[-1][3]==0.
    fresh=RetryScenario(channel)
    fresh.delay('command',0.,0,delta=2.,generation=4)
    assert channel.seen[-1][3]==2.


class ToyFeeder:
    name='toy';n_stations=1
    def linear_constraints(self,**kwargs):
        return dict(A=np.ones((1,1)),b=np.array([30.]),C=np.zeros(1))
    def evaluate(self,power_kw,background_scale=1.):
        return dict(converged=True,voltage_pu=np.array([1.]),loading_pu=np.array([.5]),
                    v_min=1.,v_max=1.,loading_max=.5)


@pytest.mark.parametrize('policy',['B3','P'])
def test_standard_policy_wrapper_preserves_scientific_metrics_and_core_hashes(policy):
    from evrecovery.retry import run_retry_episode
    from evrecovery.simulator import run_episode
    import evrecovery.policies as policies
    original_class=policies.Controller
    before={f:hashlib.sha256((ROOT/'src/evrecovery'/f).read_bytes()).hexdigest() for f in CORE}
    s=configured()
    baseline=run_episode(s,policy,ToyFeeder(),True)
    wrapped=run_retry_episode(s,policy,ToyFeeder(),True)
    exclude={'filter_seconds','filter_median_ms','filter_max_ms'}
    assert {k:v for k,v in baseline.metrics.items() if k not in exclude}=={
        k:v for k,v in wrapped.metrics.items() if k not in exclude}
    assert baseline.trace==wrapped.trace and baseline.events==wrapped.events
    assert policies.Controller is original_class
    assert before=={f:hashlib.sha256((ROOT/'src/evrecovery'/f).read_bytes()).hexdigest() for f in CORE}
    assert asdict(s)==asdict(configured())


def test_scoped_retry_controller_patch_is_restored_after_episode():
    from evrecovery.retry import run_retry_episode
    import evrecovery.policies as policies
    original_class=policies.Controller
    out=run_retry_episode(configured(),'B3_retry',ToyFeeder(),True)
    assert out.metrics['policy']=='B3_retry' and out.metrics['retries']==0
    assert policies.Controller is original_class


@pytest.mark.parametrize('pattern,direction',[('T2',-10.),('T3',10.)])
def test_retry_cannot_escape_actual_selective_attack(pattern,direction):
    from evrecovery.retry import RetryScenario
    from dataclasses import replace
    s=replace(configured(),pattern=pattern)
    wrapped=RetryScenario(s)
    wrapped.delay('command',300.,0,delta=direction,generation=1)
    retried=wrapped.delay('command',360.,0,delta=0.,generation=1)
    assert retried==s.delay('command',360.,0,delta=direction,generation=1)
    assert retried>s.delay('command',360.,0,delta=0.,generation=1)


def test_actual_episode_lost_command_retries_after_recovery():
    from evrecovery.retry import run_retry_episode
    s=Scenario(8,'T1',1.,feeder_name='toy',n_stations=1,group_max_kw=30.,
        duration=300.,actuation_s=10.,sessions=(Session(0,0,0.,20.,100.,10.),))
    class LostCommands:
        def __getattr__(self,name): return getattr(s,name)
        def delay(self,kind,t,station,delta=0.,generation=0):
            return float('inf') if kind=='command' and t<100. else 0.
    out=run_retry_episode(LostCommands(),'B3_retry',ToyFeeder(),True)
    sent=[e for e in out.events if e['type']=='command_sent']
    assert [(e['t'],e['generation']) for e in sent]==[(30.,1),(90.,1),(150.,1)]
    applied=[e for e in out.events if e['type']=='physical_cap']
    assert [(e['t'],e['generation'],e['cap']) for e in applied]==[(160.,1,0.)]
    assert out.metrics['retries']==2 and out.metrics['logical_commands']==1
    assert out.metrics['envelope_containment_failures']==0


def test_retry_runner_frozen_job_plan_and_original_primary_snapshot():
    import importlib.util
    path=ROOT/'scripts/run_retry_study.py'
    assert path.exists(), 'Retry study runner must exist before evaluation can be prepared'
    spec=importlib.util.spec_from_file_location('retry_runner_test',path)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    jobs=runner.jobs_for()
    assert len(jobs)==960
    assert {j['seed'] for j in jobs}==set(range(3000,3030))
    assert {j['pattern'] for j in jobs}=={'T1','T2','T3','T4'}
    assert {j['loading'] for j in jobs}=={.9,1.}
    assert {j['policy'] for j in jobs}=={'B3','P','B3_retry','P_retry'}
    assert all(j['phase']=='retry' and j['label']=='retry60' and j['overrides']=={'feedback_margin':.2}
               for j in jobs)
    assert runner.verify_primary_freeze()
    cfg=runner.resolved_scenario(jobs[0])
    assert cfg.feedback_margin==.2 and cfg.label=='retry60'


def test_runner_preparation_executes_no_episodes_and_review_guard(tmp_path,monkeypatch):
    import importlib.util
    import json
    spec=importlib.util.spec_from_file_location('retry_runner_prepare_test',ROOT/'scripts/run_retry_study.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    def forbidden(*args,**kwargs): raise AssertionError('Preparation must not construct/evaluate a feeder')
    monkeypatch.setattr(runner,'FeederModel',forbidden)
    with pytest.raises(SystemExit) as missing:
        runner.main([])
    assert missing.value.code==2
    out=tmp_path/'retry.jsonl'
    runner.main(['--workers','1','--out',str(out),'--freeze-only'])
    frozen=json.loads(out.with_suffix('.freeze.json').read_text())
    assert len(frozen['jobs'])==960 and not out.exists()
    assert len(frozen['resolved_scenarios'])==240
    assert frozen['retry_period_s']==60. and len(frozen['core_frozen_sha256'])==8
    assert frozen['invocation'][-1]=='--freeze-only'
    runner.validate_reviewed_manifest(frozen,frozen)
    changed=dict(frozen,retry_period_s=30.)
    with pytest.raises(RuntimeError,match='retry_period_s'):
        runner.validate_reviewed_manifest(frozen,changed)


def test_scoped_controller_patch_restores_on_replay_exception():
    from evrecovery.retry import run_retry_episode
    import evrecovery.policies as policies
    original_class=policies.Controller
    class FailingFeeder(ToyFeeder):
        def evaluate(self,*args,**kwargs): raise RuntimeError('deliberate plant failure')
    with pytest.raises(RuntimeError,match='deliberate plant failure'):
        run_retry_episode(configured(),'B3_retry',FailingFeeder())
    assert policies.Controller is original_class


def test_reviewed_manifest_accepts_json_roundtrip_and_rejects_session_change():
    import importlib.util
    import json
    spec=importlib.util.spec_from_file_location('retry_runner_roundtrip_test',ROOT/'scripts/run_retry_study.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    current=runner.build_manifest(runner.jobs_for()[:1],1,['run_retry_study.py','--freeze-only'])
    reviewed=json.loads(json.dumps(current,allow_nan=False))
    runner.validate_reviewed_manifest(reviewed,current)
    scenario_key=reviewed['job_scenarios'][0]
    expected_identity=hashlib.sha256(json.dumps(
        asdict(runner.resolved_scenario(runner.jobs_for()[0])),sort_keys=True).encode()).hexdigest()
    assert scenario_key==expected_identity
    reviewed['resolved_scenarios'][scenario_key]['sessions'][0]['request_kwh']+=.001
    with pytest.raises(RuntimeError,match='resolved_scenarios'):
        runner.validate_reviewed_manifest(reviewed,current)
