import numpy as np
import pytest


def test_pending_reduction_cannot_fund_another_station_increase():
    from evrecovery.policies import Controller, NetworkConstraints
    from evrecovery.scenarios import Scenario, Session
    cfg=Scenario(seed=1,pattern='clean',loading=1.,n_stations=2,group_max_kw=8.,
                 duration=300.,sessions=(Session(0,0,0,300,1,8),Session(1,1,0,100,1,8)))
    net=NetworkConstraints(np.array([[1.,1.]]),np.array([10.]))
    p=Controller('P',np.array([8.,2.]),np.array([2.,2.]),net,cfg)
    cmds=p.decide(0,cfg.sessions,{0:0.,1:0.},np.array([8.,2.]))
    assert any(c.station==0 and c.cap_kw<=2.+1e-9 for c in cmds)
    assert not any(c.station==1 and c.cap_kw>2.+1e-9 for c in cmds)
    assert p.reservation(0,1).sum()<=10.+1e-9


def test_proposed_can_supersede_unresolved_nonexpanding_operation():
    from evrecovery.policies import Controller, NetworkConstraints
    from evrecovery.scenarios import Scenario, Session
    from evrecovery.execution import Command
    cfg=Scenario(seed=1,pattern='clean',loading=1.,n_stations=2,group_max_kw=8.,
                 duration=300.,sessions=(Session(0,0,0,300,1,8),Session(1,1,0,100,1,8)))
    net=NetworkConstraints(np.array([[1.,1.]]),np.array([10.]))
    choices={}
    for name in ('P','B3'):
        p=Controller(name,np.array([8.,2.]),np.array([2.,2.]),net,cfg)
        p.ledgers[0].reserve(Command(0,1,0,0,600,6))
        p.last_requested[0]=6
        choices[name]=p.decide(30,cfg.sessions,{0:0.,1:0.},np.array([8.,2.]))
    assert any(c.station==0 and c.cap_kw<6 for c in choices['P'])
    assert not any(c.station==0 for c in choices['B3'])


def test_later_pending_increase_still_blocks_reallocation_after_certificate():
    from evrecovery.policies import Controller, NetworkConstraints
    from evrecovery.scenarios import Scenario
    from evrecovery.execution import Command,Certificate
    cfg=Scenario(seed=1,pattern='clean',loading=1.,n_stations=2,group_max_kw=8.,duration=300.)
    net=NetworkConstraints(np.array([[1.,1.]]),np.array([10.]))
    p=Controller('P',np.array([8.,2.]),np.array([2.,2.]),net,cfg)
    p.ledgers[0].reserve(Command(0,1,0,0,600,2))
    p.ledgers[0].reserve(Command(0,2,5,5,600,8))
    p.observe_certificate(Certificate(0,1,2,1,600,2),10)
    assert p.reservation(10,20)[0]==8


def test_signed_background_robust_counterpart_uses_both_endpoints():
    from evrecovery.policies import robust_rhs
    assert np.allclose(robust_rhs(np.array([10.,10.]),np.array([2.,-2.]),.9,1.1),[7.8,11.8])


def test_minimum_peak_respects_individual_power_and_deadline():
    from evrecovery.policies import minimum_peak
    from evrecovery.scenarios import Session
    sessions=[Session(0,0,0,3600,6,6),Session(1,0,0,1800,3,6)]
    # First job must draw6 throughout the hour; second adds6 in the first half.
    assert minimum_peak(sessions,{0:0.,1:0.},0)==pytest.approx(12.)


def test_scenarios_are_paired_and_use_declared_background_set():
    from evrecovery.scenarios import make_scenario
    a=make_scenario(102,'T1',.9)
    b=make_scenario(102,'T2',.9)
    assert a.sessions==b.sessions
    assert a.attack_start==b.attack_start and a.attack_end==b.attack_end
    for t in np.arange(0,a.duration,30):
        assert abs(a.background(t)-a.loading)<=a.background_uncertainty+1e-12
    assert a.delay('command',a.attack_start+1,0,-1)>=0


def test_session_before_arrival_cannot_change_nominal_allocation():
    from evrecovery.policies import nominal_targets,NetworkConstraints
    from evrecovery.scenarios import Session
    net=NetworkConstraints(np.array([[1.,1.]]),np.array([10.]))
    known=[Session(0,0,0,300,1,8)]
    future=Session(1,1,60,120,1,8)
    q0,_=nominal_targets(known,{},0,np.array([8.,8.]),net)
    q1,_=nominal_targets(known+[future],{},0,np.array([8.,8.]),net)
    assert np.allclose(q0,q1)


def test_higher_fallback_is_included_beyond_candidate_end():
    from evrecovery.policies import Controller, NetworkConstraints
    from evrecovery.scenarios import Scenario,Session
    from evrecovery.execution import Certificate
    cfg=Scenario(seed=1,pattern='clean',loading=1.,n_stations=2,group_max_kw=8.,duration=300.,
                 sessions=(Session(0,0,0,300,1,8),Session(1,1,0,100,1,8)))
    net=NetworkConstraints(np.array([[1.,1.]]),np.array([10.]))
    p=Controller('P',np.array([8.,2.]),np.array([6.,2.]),net,cfg)
    p.observe_certificate(Certificate(0,1,2,0,50,6),0)
    cmds=p.decide(0,cfg.sessions,{},np.array([2.,2.]))
    # B's long profile cannot reserve8 past A's known expiry to6.
    assert all(c.cap_kw<=4.+1e-8 for c in cmds if c.station==1)


def test_short_candidate_cannot_create_second_fallback_expansion():
    from evrecovery.policies import Controller,NetworkConstraints
    from evrecovery.scenarios import Scenario,Session
    from evrecovery.execution import Command
    class ShortLease(Scenario):
        def lease_end(self,station,t):return 50.
    cfg=ShortLease(seed=1,pattern='clean',loading=1.,n_stations=2,group_max_kw=8.,duration=300.,
                   sessions=(Session(0,0,0,300,1,.5),))
    p=Controller('P',np.array([2.,0.]),np.array([8.,0.]),
                 NetworkConstraints(np.ones((1,2)),np.array([10.])),cfg)
    p.ledgers[0].reserve(Command(0,1,0,0,100,1))
    p.last_requested[0]=1
    commands=p.decide(10,cfg.sessions,{},np.array([2.,0.]))
    assert not any(c.station==0 for c in commands)
