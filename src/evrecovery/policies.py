"""Common deadline scheduling with explicit alternative execution accounting."""
from dataclasses import dataclass
from math import inf
from time import perf_counter
import numpy as np
from scipy.optimize import linprog
from .execution import Command,Certificate,Ledger

POLICIES=('B0','B1','B2','B3','B4','P','R')


@dataclass
class NetworkConstraints:
    M: np.ndarray
    rhs: np.ndarray
    full: dict | None = None

    def __post_init__(self):
        self.M=np.asarray(self.M,dtype=float)
        self.rhs=np.asarray(self.rhs,dtype=float)
        if self.M.ndim!=2 or self.rhs.shape!=(self.M.shape[0],) or np.any(self.M<0):
            raise ValueError('Expected nonnegative robust charging matrix and matching RHS')

    def feasible(self,p,tol=1e-9):
        return bool(np.all(self.M@np.asarray(p)<=self.rhs+tol))

    def room(self,other,i,rhs=None):
        rhs=self.rhs if rhs is None else rhs
        rows=self.M[:,i]>1e-12
        if not np.any(rows):return inf
        residual=rhs-self.M@other+self.M[:,i]*other[i]
        return float(np.min(residual[rows]/self.M[rows,i]))


def robust_rhs(b,c,lower,upper):
    return np.asarray(b)-np.maximum(c,0)*upper-np.minimum(c,0)*lower


def build_network(feeder,scenario):
    n=scenario.n_stations
    name=getattr(feeder,'name',scenario.feeder_name)
    p0=np.full(n,100. if name=='ieee123' else 50.)
    margin=.005 if name=='ieee123' else .002
    rows=feeder.linear_constraints(background_scale=scenario.loading,p0=p0,
                                  voltage_margin=margin,loading_limit=.98)
    matrix=np.maximum(np.asarray(rows['A']),0)
    rhs=robust_rhs(rows['b'],rows['C'],scenario.loading-scenario.background_uncertainty,
                   scenario.loading+scenario.background_uncertainty)
    if np.any(rhs < -1e-8):
        raise ValueError('Background uncertainty alone violates the affine region')
    # Keep all rows in provenance; remove rows never binding within hardware maxima.
    keep=matrix@np.full(n,scenario.group_max_kw)>rhs-1e-10
    if not np.any(keep):
        keep=np.ones(len(rhs),dtype=bool)
    return NetworkConstraints(matrix[keep],rhs[keep],dict(rows,robust_rhs=rhs,kept_rows=keep))


def _service(sessions,delivered,t,n,maxima):
    demand=np.zeros(n);deadline=np.full(n,inf)
    for s in sessions:
        rem=max(0.,s.request_kwh-delivered.get(s.sid,0.))
        if s.arrival<=t<s.departure and rem>1e-7:
            demand[s.station]+=s.accept_kw
            deadline[s.station]=min(deadline[s.station],s.departure)
    return np.minimum(demand,maxima),deadline


def _greedy(demand,order,network):
    q=np.zeros(len(demand))
    for i in order:
        q[i]=max(0.,min(float(demand[i]),network.room(q,i)))
    return q


def nominal_targets(sessions,delivered,t,maxima,network):
    demand,deadline=_service(sessions,delivered,t,len(maxima),maxima)
    order=np.lexsort((np.arange(len(maxima)),deadline))
    return _greedy(demand,order,network),order


def initial_allocation(scenario,network):
    q,_=nominal_targets([s for s in scenario.sessions if s.arrival<=0],{},0,
                        np.full(scenario.n_stations,scenario.group_max_kw),network)
    if not network.feasible(q):raise RuntimeError('Initial charging caps are infeasible')
    return q


def minimum_peak(sessions,delivered,t):
    jobs=[s for s in sessions if s.arrival<=t<s.departure and
          s.request_kwh-delivered.get(s.sid,0.)>1e-7]
    if not jobs:return 0.
    times=np.array(sorted({float(t)}|{s.departure for s in jobs}))
    intervals=len(times)-1; n=len(jobs);dt=np.diff(times)/3600.
    c=np.zeros(1+n*intervals);c[0]=1.
    aub=[];bub=[];bounds=[(0,None)]
    for j,s in enumerate(jobs):
        for k in range(intervals):
            bounds.append((0,s.accept_kw if times[k]<s.departure else 0))
        row=np.zeros(len(c));row[1+j*intervals:1+(j+1)*intervals]=-dt
        target=min(max(0,s.request_kwh-delivered.get(s.sid,0)),s.accept_kw*(s.departure-t)/3600.)
        aub.append(row);bub.append(-target)
    for k in range(intervals):
        row=np.zeros(len(c));row[0]=-1;row[1+k::intervals]=1
        aub.append(row);bub.append(0.)
    result=linprog(c,A_ub=np.array(aub),b_ub=np.array(bub),bounds=bounds,method='highs')
    if not result.success:raise RuntimeError('Minimum-peak scheduling LP failed: '+result.message)
    return float(result.x[0])


class Controller:
    def __init__(self,policy_name,initial_caps,fallback_caps,network,scenario):
        if policy_name not in POLICIES+('P_no_pending','P_no_fallback','P_acceptance','P_aggregate'):
            raise ValueError('Unknown policy '+policy_name)
        self.name=policy_name;self.network=network;self.cfg=scenario
        self.initial=np.asarray(initial_caps,dtype=float).copy()
        self.fallback=np.asarray(fallback_caps,dtype=float).copy()
        self.n=len(self.initial);self.maximum=np.full(self.n,scenario.group_max_kw)
        ends=scenario.initial_ends()
        self.ledgers=[Ledger(i,float(self.initial[i]),float(self.fallback[i]),float(ends[i])) for i in range(self.n)]
        self.certificates=[Certificate(i,0,float(self.initial[i]),0,float(ends[i]),float(self.fallback[i])) for i in range(self.n)]
        self.last_requested=self.initial.copy();self.measured=self.initial.copy()
        self.history=[[] for _ in range(self.n)]
        self.filter_seconds=0.;self.decision_times=[]
        self._now=0.
        if not network.feasible(self.initial) or not network.feasible(self.fallback):
            raise ValueError('Initial and persistent fallback reservations must be feasible')
        if self.name=='P_aggregate':
            # Deliberately remove locational rows, retaining a nominal common-budget envelope.
            q=_greedy(self.maximum,np.arange(self.n),network)
            self.admission_network=NetworkConstraints(np.ones((1,self.n)),np.array([q.sum()]))
        else:self.admission_network=network

    def _advance(self,t):
        if t<self._now-1e-9:raise ValueError('Controller time cannot reverse')
        for ledger in self.ledgers:ledger.advance(t)
        self._now=t

    def observe_certificate(self,certificate,received_at):
        if certificate.applied>received_at+1e-9:
            raise ValueError('Execution evidence cannot arrive before application')
        self._advance(received_at)
        i=certificate.station;old=self.certificates[i]
        self.ledgers[i].observe(certificate)
        if (certificate.generation,certificate.applied)>=(old.generation,old.applied):
            self.certificates[i]=certificate

    def observe_acceptance(self,command,received_at):
        if self.name=='P_acceptance' and received_at<command.end:
            self.observe_certificate(Certificate(command.station,command.generation,
                command.cap_kw,received_at,command.end,float(self.fallback[command.station])),received_at)

    def reservation(self,t0,t1):
        return np.array([x.envelope(t0,t1) for x in self.ledgers])

    def query_stations(self,t,exclude):
        self._advance(t)
        return [i for i,l in enumerate(self.ledgers) if i not in exclude and l.unresolved()]

    def affine_excess(self,t):
        return float(max(0.,np.max(self.network.M@self.reservation(t,t)-self.network.rhs)))

    def _bound_at(self,t):
        if self.name=='B1':return np.maximum(self.measured,self.last_requested)
        if self.name not in ('P_no_pending','P_no_fallback'):
            return self.reservation(t,t)
        bound=[]
        for i,cert in enumerate(self.certificates):
            base=cert.cap_kw if t<cert.end else (0. if self.name=='P_no_fallback' else self.fallback[i])
            for cmd in self.history[i]:
                if cmd.generation<=cert.generation:continue
                if cmd.start<=t<cmd.end and self.name!='P_no_pending':base=max(base,cmd.cap_kw)
                if t>=cmd.end and self.name!='P_no_fallback':base=max(base,self.fallback[i])
            bound.append(base)
        return np.array(bound)

    def _interval_points(self,t,end):
        future=[self.cfg.duration+300.,end]
        future.extend(c.end for c in self.certificates if np.isfinite(c.end))
        future.extend(c.end for l in self.ledgers for c in l.unresolved())
        horizon=max(future)
        points={t,end,horizon}
        for ledger in self.ledgers:points.update(ledger.breakpoints(t,horizon))
        return sorted(points)

    def _candidate_limit(self,i,t,end):
        net=self.admission_network
        rhs=net.rhs*(1-self.cfg.feedback_margin) if self.name=='B1' else net.rhs
        limit=self.maximum[i];nonexpanding=inf;post_nonexpanding=True
        points=self._interval_points(t,end)
        for z in points[:-1]:
            r=self._bound_at(z)
            if z<end:
                nonexpanding=min(nonexpanding,r[i])
                limit=min(limit,max(0.,net.room(r,i,rhs)))
            else:
                proposed=r.copy();proposed[i]=max(proposed[i],self.fallback[i])
                post_nonexpanding=post_nonexpanding and self.fallback[i]<=r[i]+1e-9
                if not net.feasible(proposed):return 0.,nonexpanding,False,post_nonexpanding
        return max(0.,limit),nonexpanding,True,post_nonexpanding

    def _quota_targets(self,sessions,delivered,t):
        demand,deadline=_service(sessions,delivered,t,self.n,self.maximum)
        required=np.array([minimum_peak([s for s in sessions if s.station==i],delivered,t) for i in range(self.n)])
        shares=self.initial.copy();surplus=np.maximum(shares-required,0.)
        deficits=np.maximum(np.minimum(required,self.maximum)-shares,0.)
        for receiver in np.lexsort((np.arange(self.n),deadline)):
            for donor in sorted(range(self.n),key=lambda j:(abs(j-receiver),j)):
                amount=min(deficits[receiver],surplus[donor])
                shares[receiver]+=amount;shares[donor]-=amount
                deficits[receiver]-=amount;surplus[donor]-=amount
        order=np.lexsort((np.arange(self.n),deadline))
        return _greedy(np.minimum(demand,shares),order,self.network),order

    def decide(self,t,known_sessions,delivered_est,measured_kw):
        start=perf_counter();self._advance(t)
        self.measured=np.asarray(measured_kw,dtype=float).copy()
        if self.name=='B4':target,order=self._quota_targets(known_sessions,delivered_est,t)
        else:target,order=nominal_targets(known_sessions,delivered_est,t,self.maximum,self.network)
        if self.name=='B2':target=np.minimum(target,self.initial)
        commands=[]
        # Record reductions before considering increases, without crediting unresolved relief.
        ordering=sorted(order,key=lambda i:(target[i]>self.last_requested[i]+.05,list(order).index(i)))
        for i in ordering:
            i=int(i);end=float(self.cfg.lease_end(i,t));want=float(target[i])
            if end<=t:continue
            unresolved=self.ledgers[i].unresolved()
            if self.name in ('B3','B4') and unresolved:continue
            if self.name not in ('B0','B2'):
                limit,nonexpanding,post_ok,post_nonexpanding=self._candidate_limit(i,t,end)
                if not post_ok:continue
                want=min(want,limit)
                horizon=self._interval_points(t,end)[-1]
                if self.name not in ('B1',) and not self.ledgers[i].can_expand(t,horizon):
                    if not post_nonexpanding:continue
                    want=min(want,nonexpanding)
            want=max(0.,want)
            cert=self.certificates[i]
            known=cert.cap_kw if t<cert.end else self.fallback[i]
            settled_mismatch=not unresolved and abs(known-want)>.05
            if abs(want-self.last_requested[i])<=.05 and not settled_mismatch:continue
            cmd=Command(i,self.ledgers[i].latest_generation+1,t,t,end,want)
            self.ledgers[i].reserve(cmd)  # Atomic prospective update before caller can send.
            self.history[i].append(cmd);self.last_requested[i]=want;commands.append(cmd)
        elapsed=perf_counter()-start
        self.filter_seconds+=elapsed;self.decision_times.append(elapsed)
        return commands
