"""Retry follow-up must stay separate, complete, and paired by new cohorts."""
import importlib.util
from pathlib import Path

import pytest


def module():
    path=Path(__file__).resolve().parents[1]/'scripts/analyze_retry.py'
    spec=importlib.util.spec_from_file_location('retry_analysis',path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture_rows():
    """Hand-checkable artificial data, used only inside these unit tests."""
    rows=[]
    for seed in range(3000,3030):
        for pattern in ('T1','T2','T3','T4'):
            for loading in (.9,1.):
                values={'B3':100.,'P':105.,'B3_retry':102.,
                        'P_retry':102.+(seed-3000)/10.}
                for policy,energy in values.items():
                    rows.append(dict(phase='retry',label='retry60',seed=seed,
                        pattern=pattern,loading=loading,feeder='ieee123',policy=policy,
                        requested_kwh=120.,delivered_kwh=energy,deadline_shortfall_kwh=120.-energy,
                        duration_s=1800.,simulated_seconds=1800.,failed=False,
                        convergence_failures=0,session_delivered_kwh={'0':energy},
                        violation_seconds=0.,voltage_exposure_pu_s=0.,loading_exposure_s=0.,
                        initial_electrical_violation=False,v_min=.96,v_max=1.04,loading_max=.98,
                        max_affine_excess=0.,envelope_containment_failures=0,
                        envelope_containment_seconds=0.,max_envelope_excess_kw=0.,
                        reserved_unused_kwh=2.,commands=30 if policy.endswith('_retry') else 10,
                        queries=2,filter_seconds=.1,filter_median_ms=.2,filter_max_ms=.5))
    return rows


def test_retry_comparisons_use_all_new_seed_blocks_and_correct_own_baselines():
    result=module().build_summary(fixture_rows())
    assert result['metadata']['study_stage']=='post_review_robustness'
    assert result['metadata']['episodes']==960
    assert result['metadata']['seed_min']==3000
    assert result['metadata']['seed_max']==3029
    effect=result['comparisons']['P_retry_minus_B3_retry']['overall']
    assert effect['n_blocks']==30
    assert effect['n_pairs']==240
    assert effect['conditions_per_block']==8
    assert effect['mean']==pytest.approx(1.45)
    assert effect['block_means']['3000']==0.
    assert effect['block_means']['3029']==pytest.approx(2.9)
    assert result['comparisons']['B3_retry_minus_B3']['overall']['mean']==2.
    assert result['comparisons']['P_retry_minus_P']['overall']['mean']==pytest.approx(-1.55)
    assert result['policies']['B3_retry']['episodes']==240
    assert result['policies']['B3_retry']['commands_mean']==30.
    assert result['policies']['P']['commands_mean']==10.


@pytest.mark.parametrize('fault,match',[
    ('missing','missing'),('duplicate','Duplicate'),('old_seed','missing'),
    ('nonfinite','finite'),('unfair_requests','cohort')])
def test_retry_layout_rejects_incomplete_reused_or_unfair_evidence(fault,match):
    rows=fixture_rows()
    if fault=='missing':
        rows.pop()
    elif fault=='duplicate':
        rows.append(rows[0].copy())
    elif fault=='old_seed':
        rows[0]['seed']=1000
    elif fault=='nonfinite':
        rows[0]['session_delivered_kwh']['0']=float('nan')
    else:
        rows[0]['requested_kwh']=121.
    with pytest.raises(ValueError,match=match):
        module().build_summary(rows)


def test_retry_summary_retains_observed_violation_without_changing_pairing():
    rows=fixture_rows()
    target=next(r for r in rows if r['policy']=='P_retry')
    target['violation_seconds']=12.
    target['loading_exposure_s']=.6
    target['loading_max']=1.05
    result=module().build_summary(rows)
    assert result['policies']['P_retry']['unsafe_episodes']==1
    assert result['policies']['P_retry']['violation_seconds_total']==12.
    assert result['policies']['P_retry']['loading_exposure_s_total']==.6
    assert result['policies']['P_retry']['loading_max']==1.05
    assert result['comparisons']['P_retry_minus_B3_retry']['overall']['n_blocks']==30
