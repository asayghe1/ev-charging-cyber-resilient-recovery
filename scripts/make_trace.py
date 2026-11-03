#!/usr/bin/env python3
"""Replay the prespecified median-effect example without selecting a favorable case."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from evrecovery.feeder import FeederModel
from evrecovery.scenarios import make_scenario
from evrecovery.simulator import run_episode

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--main',type=Path,default=ROOT/'results/main.jsonl')
    parser.add_argument('--out',type=Path,default=ROOT/'results/representative_trace.json')
    parser.add_argument('--feedback-margin',type=float,default=.20)
    args=parser.parse_args()
    rows=[json.loads(line) for line in args.main.read_text().splitlines() if line.strip()]
    selected={(r['seed'],r['policy']):r for r in rows
              if r['pattern']=='T1' and r['loading']==1. and r['feeder']=='ieee123'}
    seeds=sorted({key[0] for key in selected})
    if seeds!=list(range(1000,1030)):
        raise ValueError('Representative selection requires all30 main cohort seeds')
    differences={seed:selected[seed,'P']['delivered_kwh']-selected[seed,'B3']['delivered_kwh'] for seed in seeds}
    center=median(differences.values())
    seed=min(seeds,key=lambda x:(abs(differences[x]-center),x))
    reference=selected[seed,'P']
    options=reference['overrides'].copy()
    if options.get('feedback_margin')!=args.feedback_margin:
        raise ValueError('Requested margin does not match the frozen main configuration')
    options.pop('feeder_name',None)
    scenario=make_scenario(seed,'T1',1.,'ieee123',label=reference['label'],**options)
    output={'selection':{'seed':seed,'pattern':'T1','loading':1.,
        'rule':'Closest P minus B3 energy difference to the median among all30 T1/high-loading main cohorts; smaller seed breaks ties.',
        'median_difference_kwh':center,'selected_difference_kwh':differences[seed]},
        'main_sha256':hashlib.sha256(args.main.read_bytes()).hexdigest(),
        'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted([*ROOT.glob('src/**/*.py'),Path(__file__).resolve()])},'episodes':{}}
    feeder=FeederModel('ieee123')
    for policy in ('B0','B3','P','R'):
        result=run_episode(scenario,policy,feeder=feeder,retain_trace=True)
        expected=selected[seed,policy]
        for metric in ('delivered_kwh','deadline_shortfall_kwh','violation_seconds',
                       'v_min','v_max','loading_max','commands','queries'):
            if not math.isclose(result.metrics[metric],expected[metric],rel_tol=0,abs_tol=1e-7):
                raise ValueError(f'Trace rerun disagrees with main: {policy}/{metric}')
        output['episodes'][policy]={'metrics':result.metrics,'trace':result.trace,'events':result.events}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(output,allow_nan=False,separators=(',',':'))+'\n')
    print(json.dumps({'output':str(args.out),'selection':output['selection'],'trace_reruns':4}))


if __name__=='__main__':
    main()
