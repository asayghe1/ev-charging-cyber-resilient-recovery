import importlib.util
from pathlib import Path


def _module():
    path=Path(__file__).resolve().parents[1]/'scripts/run_study.py'
    spec=importlib.util.spec_from_file_location('study',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_planned_counts_and_pairing():
    module=_module()
    assert len(module.jobs_for('main'))==2100
    assert len(module.jobs_for('sensitivity'))==840
    assert len(module.jobs_for('ablations'))==80
    main=module.jobs_for('main')
    independent={j['seed'] for j in main}
    assert len(independent)==30
    assert not independent.intersection(j['seed'] for j in module.jobs_for('sensitivity'))
    assert not independent.intersection(j['seed'] for j in module.jobs_for('pilot'))


def test_worker_saves_metadata_and_actual_metrics_without_key_collision():
    result=_module().worker(dict(seed=101,pattern='clean',loading=.9,policy='P',
        phase='unit',label='short',overrides={'duration':60.}))
    assert result['feeder']=='ieee123'
    assert result['phase']=='unit'
    assert result['delivered_kwh']>0
    assert result['failed'] is False


def test_manifest_reconstructs_each_cohort_and_covers_the_event_engine():
    module=_module()
    jobs=module.jobs_for('pilot')[:7]
    manifest=module.build_manifest(jobs,'pilot',4,['run_study.py','--phase','pilot'])
    assert 'src/evrecovery/simulator.py' in manifest['source_sha256']
    assert 'src/evrecovery/execution.py' in manifest['source_sha256']
    assert 'scripts/run_study.py' in manifest['source_sha256']
    assert len(manifest['resolved_scenarios'])==1
    scenario=next(iter(manifest['resolved_scenarios'].values()))
    assert len(scenario['sessions'])==156
    assert scenario['seed']==101
    assert len(manifest['job_scenarios'])==7
    assert manifest['invocation']==['run_study.py','--phase','pilot']
    assert manifest['dependencies']['opendssdirect.py']


def test_feedback_tuning_uses_development_seeds_only():
    jobs=_module().jobs_for('tune_feedback')
    assert len(jobs)==100
    assert {j['policy'] for j in jobs}=={'B1'}
    assert {j['seed'] for j in jobs}==set(range(101,106))
    assert {j['overrides']['feedback_margin'] for j in jobs}=={.1,.2}

