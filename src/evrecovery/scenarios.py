"""Frozen synthetic charging cohorts and common communication disturbance laws."""
from dataclasses import dataclass, field, replace
from math import inf
import numpy as np


@dataclass(frozen=True)
class Session:
    sid: int
    station: int
    arrival: float
    departure: float
    request_kwh: float
    accept_kw: float


@dataclass(frozen=True)
class Scenario:
    seed: int
    pattern: str
    loading: float
    feeder_name: str = 'ieee123'
    n_stations: int = 6
    group_max_kw: float = 300.
    duration: float = 1800.
    control_step: float = 30.
    telemetry_step: float = 30.
    actuation_s: float = 1.
    base_delay_s: float = 2.
    background_uncertainty: float = .01
    attack_start: float = 270.
    attack_end: float = 690.
    attack_delay_s: float = 180.
    certificate_extra_s: float = 45.
    lease_expiry: float = 900.
    feedback_margin: float = .05
    affected_stations: int = 3
    sessions: tuple = field(default_factory=tuple)
    label: str = 'main'

    def background(self, t):
        # Exogenous, piecewise constant; controllers know the full enclosing set.
        step=np.floor(t/self.control_step)
        return self.loading + .8*self.background_uncertainty*np.sin(.17*step+.37*self.seed)

    def initial_ends(self):
        return np.full(self.n_stations,self.duration+300.)

    def lease_end(self, station, t):
        if self.pattern=='T4' and station<self.affected_stations and t<self.lease_expiry:
            return self.lease_expiry
        return self.duration+300.

    def delay(self, kind, t, station, delta=0., generation=0):
        # Deterministic jitter keyed by exogenous time and station, not mutable RNG.
        jitter=.15*((int(round(t*10))+17*station+31*self.seed)%7)
        delay=self.base_delay_s+jitter
        affected=station<min(self.affected_stations,self.n_stations)
        start=self.attack_start+8.*station
        end=self.attack_end+13.*station
        during=affected and start<=t<end
        if self.pattern=='T1' and during:
            return inf
        if self.pattern=='T2' and during:
            if kind=='command' and delta<-.1:
                return end-t+delay
            if kind in ('certificate','ack'):
                delay+=self.certificate_extra_s
        if self.pattern=='T3' and during:
            if kind=='command' and delta>.1:
                # Delayed application retry after a timed-out operation.
                return end-t+self.attack_delay_s+delay
            if kind=='certificate':
                delay+=self.certificate_extra_s
        if self.pattern=='T4' and affected:
            if self.lease_expiry-120<=t<self.lease_expiry+90:
                if kind in ('command','query_down'):
                    return self.lease_expiry+90-t+delay
                delay+=self.certificate_extra_s
        return delay


def make_scenario(seed, pattern, loading, feeder_name='ieee123', **overrides):
    """One synthetic cohort reused unchanged across policies/patterns/load levels.

    Aggregate MV groups represent populations of 22-kW charging sessions. Their
    loads are feeder-connected equivalents; this is not an LV cable/protection model.
    """
    rng=np.random.default_rng(int(seed))
    factor=1. if feeder_name=='ieee123' else 2./3.
    sessions=[]
    sid=0
    for i in range(6):
        early=16 if i<3 else 5
        for j in range(early):
            departure=float(rng.uniform(1500,1795))
            acceptance=22.*factor
            # Distant-deadline initial cohorts remain active during reallocation.
            request=float(rng.uniform(.61,.87)*acceptance*departure/3600.)
            sessions.append(Session(sid,i,0.,departure,request,acceptance));sid+=1
        count=6 if i<3 else 17
        for j in range(count):
            arrival=float(rng.uniform(390,600) if i<3 else rng.uniform(300,480))
            departure=float(rng.uniform(1180,1550) if i<3 else rng.uniform(900,1260))
            acceptance=22.*factor
            request=float(rng.uniform(.64,.91)*acceptance*(departure-arrival)/3600.)
            sessions.append(Session(sid,i,arrival,departure,request,acceptance));sid+=1
        for j in range(4):
            arrival=float(rng.uniform(1040,1200))
            departure=float(rng.uniform(1650,1795))
            acceptance=22.*factor
            request=float(rng.uniform(.55,.8)*acceptance*(departure-arrival)/3600.)
            sessions.append(Session(sid,i,arrival,departure,request,acceptance));sid+=1
    cfg=Scenario(int(seed),pattern,float(loading),feeder_name=feeder_name,
                 group_max_kw=300.*factor,sessions=tuple(sessions))
    return replace(cfg,**overrides)
