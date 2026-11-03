#!/usr/bin/env python3
"""Additional post-review retry study. Preparation never executes an episode."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import multiprocessing
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from evrecovery.feeder import FeederModel
from evrecovery.retry import RETRY_PERIOD_S, RETRY_POLICIES, run_retry_episode
from evrecovery.scenarios import make_scenario

ROOT = Path(__file__).resolve().parents[1]
_FEEDERS = {}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jobs_for():
    return [dict(seed=seed, pattern=pattern, loading=loading, policy=policy,
                 phase='retry', label='retry60', overrides={'feedback_margin': .2})
            for seed in range(3000, 3030)
            for pattern in ('T1', 'T2', 'T3', 'T4')
            for loading in (.9, 1.) for policy in RETRY_POLICIES]


def resolved_scenario(job):
    return make_scenario(job['seed'], job['pattern'], job['loading'],
                         label=job['label'], **job['overrides'])


def verify_primary_freeze():
    """Check every frozen source, not just the subset used by the extension."""
    frozen = json.loads((ROOT/'results/freeze.json').read_text())['source_sha256']
    changed = [name for name, expected in frozen.items()
               if not (ROOT/name).is_file() or digest(ROOT/name) != expected]
    if changed:
        raise RuntimeError('Original primary freeze changed: ' + ', '.join(changed))
    return frozen


def primary_data_snapshot():
    names = ('main', 'sensitivity', 'ablations')
    return {str(p.relative_to(ROOT)): digest(p) for name in names
            for suffix in ('.jsonl', '.config.json')
            if (p := ROOT/'results'/f'{name}{suffix}').is_file()}


def build_manifest(jobs, workers, invocation):
    frozen = verify_primary_freeze()
    sources = sorted([*ROOT.glob('src/**/*.py'), *ROOT.glob('scripts/*.py'),
                      ROOT/'docs/RETRY_PROTOCOL.md'])
    tests = sorted(ROOT.glob('tests/*.py'))
    scenarios, job_scenarios = {}, []
    for job in jobs:
        # Dataclass session tuples must match the persisted JSON representation.
        # Normalize before storing/hashing so reviewed disk manifests compare
        # exactly, without weakening the scientific configuration change guard.
        resolved = json.loads(json.dumps(asdict(resolved_scenario(job)), allow_nan=False))
        key = hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()
        scenarios[key] = resolved
        job_scenarios.append(key)
    revision = subprocess.run(['git','rev-parse','HEAD'], cwd=ROOT, capture_output=True, text=True)
    status = subprocess.run(['git','status','--porcelain'], cwd=ROOT, capture_output=True, text=True)
    return dict(schema_version=1, phase='retry', label='retry60',
        study_kind='additional_post_review_retry_robustness', not_prespecified_primary=True,
        created_utc=datetime.now(timezone.utc).isoformat(), jobs=jobs, workers=workers,
        invocation=list(invocation), executable=sys.executable,
        retry_period_s=RETRY_PERIOD_S, retry_fields_preserved=['station','generation','start','end','cap_kw','epoch'],
        retransmission_issued_stamp='current retry time; original logical Ledger command retained',
        selective_attack_classification='first command delta keyed by station and generation',
        process_start_method='spawn', python=platform.python_version(), platform=platform.platform(),
        cpu_count=os.cpu_count(), git_revision=revision.stdout.strip(), git_status=status.stdout,
        dependencies={name:version(name) for name in ('numpy','scipy','matplotlib','opendssdirect.py',
            'dss-python','dss-python-backend','pytest')},
        thread_environment={k:os.environ.get(k) for k in
            ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','PYTHONPATH')},
        source_sha256={str(p.relative_to(ROOT)):digest(p) for p in sources},
        tests_sha256={str(p.relative_to(ROOT)):digest(p) for p in tests},
        core_frozen_sha256=frozen, primary_freeze_sha256=digest(ROOT/'results/freeze.json'),
        primary_data_sha256=primary_data_snapshot(),
        feeder_manifest_sha256=digest(ROOT/'data/feeders/manifest.json'),
        resolved_scenarios=scenarios, job_scenarios=job_scenarios)


def validate_reviewed_manifest(reviewed, current):
    for field in ('phase','label','study_kind','not_prespecified_primary','jobs','retry_period_s',
                  'retry_fields_preserved','retransmission_issued_stamp','selective_attack_classification',
                  'process_start_method','dependencies','source_sha256','tests_sha256',
                  'core_frozen_sha256','primary_freeze_sha256','primary_data_sha256',
                  'feeder_manifest_sha256','resolved_scenarios','job_scenarios'):
        if reviewed.get(field) != current.get(field):
            raise RuntimeError(f'Reviewed retry manifest no longer matches {field}; review/freeze again')


def worker(job):
    cfg = resolved_scenario(job)
    key = (cfg.feeder_name, cfg.n_stations)
    if key not in _FEEDERS:
        _FEEDERS[key] = FeederModel(*key)
    result = run_retry_episode(cfg, job['policy'], _FEEDERS[key], False)
    row = {**job, **result.metrics}
    row['retries'] = result.metrics.get('retries', 0)
    row['logical_commands'] = result.metrics.get('logical_commands', result.metrics['commands'])
    row['retry_period_s'] = RETRY_PERIOD_S
    row['study_kind'] = 'additional_post_review_retry_robustness'
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--out', type=Path, default=ROOT/'results/retry.jsonl')
    parser.add_argument('--freeze-only', action='store_true',
                        help='Write .freeze.json for independent review; execute no episodes')
    parser.add_argument('--reviewed-manifest', type=Path,
                        help='Previously reviewed .freeze.json; required to execute')
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error('--workers must be positive')
    if args.freeze_only and args.reviewed_manifest:
        parser.error('Use --freeze-only separately from --reviewed-manifest')
    if not args.freeze_only and not args.reviewed_manifest:
        parser.error('Evaluation requires --reviewed-manifest after independent protocol review')
    invocation = sys.argv if argv is None else [str(Path(__file__)), *argv]
    manifest = build_manifest(jobs_for(), args.workers, invocation)
    destination = args.out.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.freeze_only:
        freeze = destination.with_suffix('.freeze.json')
        if freeze.exists():
            raise SystemExit(f'Refusing to overwrite {freeze}')
        freeze.write_text(json.dumps(manifest, indent=2, allow_nan=False))
        print(json.dumps(dict(prepared=str(freeze), jobs=len(manifest['jobs']), evaluated=0,
                              sha256=digest(freeze))))
        return
    reviewed = json.loads(args.reviewed_manifest.read_text())
    validate_reviewed_manifest(reviewed, manifest)
    config = destination.with_suffix('.config.json')
    if destination.exists() or config.exists():
        raise SystemExit(f'Refusing to overwrite existing retry outputs at {destination}')
    manifest['reviewed_manifest'] = str(args.reviewed_manifest.resolve())
    manifest['reviewed_manifest_sha256'] = digest(args.reviewed_manifest)
    config.write_text(json.dumps(manifest, indent=2, allow_nan=False))
    started, failed = time.perf_counter(), 0
    context = multiprocessing.get_context('spawn')
    with destination.open('x') as stream, ProcessPoolExecutor(max_workers=args.workers,
                                                            mp_context=context) as pool:
        futures = [pool.submit(worker, job) for job in manifest['jobs']]
        for count, future in enumerate(as_completed(futures), 1):
            row = future.result()
            stream.write(json.dumps(row, allow_nan=False)+'\n')
            stream.flush()
            failed += bool(row['failed'])
            if count % 50 == 0 or count == len(futures):
                print(f'retry: {count}/{len(futures)} saved; failed={failed}; '
                      f'elapsed={time.perf_counter()-started:.1f}s', flush=True)
    verify_primary_freeze()
    if primary_data_snapshot() != manifest['primary_data_sha256']:
        raise RuntimeError('Primary data changed during retry evaluation')
    print(json.dumps(dict(output=str(destination), completed=len(manifest['jobs']), failed=failed,
                          wall_seconds=time.perf_counter()-started)), flush=True)
    if failed:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
