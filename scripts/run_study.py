#!/usr/bin/env python3
"""Execute paired episodes; persist each result before reporting it as complete."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from dataclasses import asdict
import hashlib,json,os,platform,time,sys,subprocess
from importlib.metadata import version
from pathlib import Path
import numpy as np
from evrecovery.scenarios import make_scenario
from evrecovery.policies import POLICIES
from evrecovery.feeder import FeederModel
from evrecovery.simulator import run_episode

ROOT=Path(__file__).resolve().parents[1]
_FEEDERS={}


def jobs_for(phase):
    jobs=[]
    seeds=range(101,106) if phase=='pilot' else range(1000,1030)
    if phase in ('pilot','main'):
        for seed in seeds:
            for pattern in ('clean','T1','T2','T3','T4'):
                for loading in (.9,1.):
                    for policy in POLICIES:
                        jobs.append(dict(seed=seed,pattern=pattern,loading=loading,
                                         policy=policy,phase=phase,label='main',overrides={}))
    if phase=='sensitivity':
        settings=[('confirmation15',dict(certificate_extra_s=15.)),
                  ('confirmation90',dict(certificate_extra_s=90.)),
                  ('confirmation180',dict(certificate_extra_s=180.)),
                  ('affected1',dict(affected_stations=1)),('affected2',dict(affected_stations=2)),
                  ('affected4',dict(affected_stations=4)),
                  ('actuation0',dict(actuation_s=0.)),('actuation5',dict(actuation_s=5.)),
                  ('actuation15',dict(actuation_s=15.)),
                  ('ieee13_t1',dict(feeder_name='ieee13')),
                  ('ieee13_t2',dict(feeder_name='ieee13')),
                  ('ieee13_t3',dict(feeder_name='ieee13'))]
        for label,options in settings:
            pattern={'ieee13_t1':'T1','ieee13_t2':'T2','ieee13_t3':'T3'}.get(label,'T3')
            for seed in range(2000,2010):
                for policy in POLICIES:
                    jobs.append(dict(seed=seed,pattern=pattern,loading=1.,policy=policy,
                                     phase=phase,label=label,overrides=options))
    if phase=='ablations':
        for seed in range(1000,1005):
            for pattern in ('T1','T2','T3','T4'):
                for policy in ('P_no_pending','P_no_fallback','P_acceptance','P_aggregate'):
                    jobs.append(dict(seed=seed,pattern=pattern,loading=1.,policy=policy,
                                     phase=phase,label='mechanism',overrides={}))
    if phase=='tune_feedback':
        for margin in (.1,.2):
            for seed in range(101,106):
                for pattern in ('clean','T1','T2','T3','T4'):
                    for loading in (.9,1.):
                        jobs.append(dict(seed=seed,pattern=pattern,loading=loading,policy='B1',
                            phase=phase,label=f'margin{margin}',overrides={'feedback_margin':margin}))
    return jobs


def resolved_scenario(job):
    kwargs=job['overrides'].copy()
    name=kwargs.pop('feeder_name','ieee123')
    return make_scenario(job['seed'],job['pattern'],job['loading'],name,label=job['label'],**kwargs)


def build_manifest(jobs,phase,workers,invocation):
    sources=sorted([*ROOT.glob('src/**/*.py'),*ROOT.glob('scripts/*.py')])
    tests=sorted(ROOT.glob('tests/*.py'))
    resolved={};job_scenarios=[]
    for job in jobs:
        scenario=asdict(resolved_scenario(job))
        key=hashlib.sha256(json.dumps(scenario,sort_keys=True).encode()).hexdigest()
        resolved[key]=scenario;job_scenarios.append(key)
    dependencies={name:version(name) for name in ('numpy','scipy','matplotlib',
        'opendssdirect.py','dss-python','dss-python-backend','pytest')}
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True)
    status=subprocess.run(['git','status','--porcelain'],cwd=ROOT,text=True,capture_output=True)
    return dict(phase=phase,jobs=jobs,workers=workers,invocation=invocation,
        python=platform.python_version(),platform=platform.platform(),cpu_count=os.cpu_count(),
        dependencies=dependencies,git_revision=revision.stdout.strip(),git_status=status.stdout,
        thread_environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','PYTHONPATH')},
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        tests_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in tests},
        feeder_manifest_sha256=hashlib.sha256((ROOT/'data/feeders/manifest.json').read_bytes()).hexdigest(),
        resolved_scenarios=resolved,job_scenarios=job_scenarios)


def worker(job):
    cfg=resolved_scenario(job)
    name=cfg.feeder_name
    if name not in _FEEDERS:_FEEDERS[name]=FeederModel(name)
    result=run_episode(cfg,job['policy'],feeder=_FEEDERS[name],retain_trace=False)
    row={k:v for k,v in job.items() if k!='overrides'}
    row.update(result.metrics)
    row.update(feeder=name,overrides=job['overrides'])
    return row


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--phase',choices=['pilot','main','sensitivity','ablations','tune_feedback'],required=True)
    ap.add_argument('--workers',type=int,default=4);ap.add_argument('--limit',type=int);ap.add_argument('--out')
    ap.add_argument('--feedback-margin',type=float,default=.05)
    args=ap.parse_args();jobs=jobs_for(args.phase)
    if args.phase!='tune_feedback':
        for job in jobs:job['overrides']['feedback_margin']=args.feedback_margin
    if args.limit:jobs=jobs[:args.limit]
    destination=Path(args.out) if args.out else ROOT/'results'/f'{args.phase}.jsonl'
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise SystemExit(f'Refusing to overwrite {destination}; preserve prior evidence explicitly.')
    config=destination.with_suffix('.config.json')
    config.write_text(json.dumps(build_manifest(jobs,args.phase,args.workers,sys.argv),indent=2))
    started=time.perf_counter();failed=0
    with destination.open('w') as stream,ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(worker,j):j for j in jobs}
        for k,f in enumerate(as_completed(futures),1):
            row=f.result()
            stream.write(json.dumps(row,allow_nan=False)+'\n');stream.flush()
            failed+=bool(row.get('failed'))
            if k%50==0 or k==len(jobs):
                print(f'{args.phase}: {k}/{len(jobs)} saved; failed={failed}; elapsed={time.perf_counter()-started:.1f}s',flush=True)
    print(json.dumps(dict(output=str(destination),completed=len(jobs),failed=failed,
                         wall_seconds=time.perf_counter()-started)),flush=True)
    if failed:raise SystemExit(2)


if __name__=='__main__':main()
