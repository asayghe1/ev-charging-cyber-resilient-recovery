"""Tests for independent experimental units and honest evidence aggregation."""
import importlib.util
from pathlib import Path

import pytest


def analysis():
    path = Path(__file__).resolve().parents[1] / 'scripts/analyze_study.py'
    spec = importlib.util.spec_from_file_location('analysis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paired_rows():
    # Two independent cohorts, each replayed in two conditions. Cohort effects
    # are (4-2)/2 = 1 and (-1+5)/2 = 2; conditions are not independent units.
    return [dict(seed=seed, pattern=condition, loading=1., feeder='ieee123',
                 policy=policy, delivered_kwh=value)
            for seed, condition, p_value in [(1, 'T1', 14.), (1, 'T2', 8.),
                                               (2, 'T1', 9.), (2, 'T2', 15.)]
            for policy, value in [('P', p_value), ('B3', 10.)]]


def test_bootstrap_uses_cohort_blocks_and_preserves_condition_pairing():
    result = analysis().paired_effect(paired_rows(), 'P', 'B3', draws=10000, seed=4821)
    assert result['n_blocks'] == 2
    assert result['n_pairs'] == 4
    assert result['block_means'] == {'1': 1., '2': 2.}
    assert result['mean'] == pytest.approx(1.5)
    assert result['ci95'] == pytest.approx([1., 2.])
    reversed_result = analysis().paired_effect(list(reversed(paired_rows())), 'P', 'B3',
                                               draws=10000, seed=4821)
    assert reversed_result == result


def test_missing_or_duplicate_policy_pair_is_rejected():
    module = analysis()
    with pytest.raises(ValueError, match='pair'):
        module.paired_effect(paired_rows()[:-1], 'P', 'B3')
    with pytest.raises(ValueError, match='Duplicate'):
        module.paired_effect(paired_rows() + [paired_rows()[0]], 'P', 'B3')


def test_incomplete_condition_block_is_not_treated_as_an_extra_replication():
    rows = [r for r in paired_rows() if not (r['seed'] == 1 and r['pattern'] == 'T2')]
    with pytest.raises(ValueError, match='balanced'):
        analysis().paired_effect(rows, 'P', 'B3')


def test_episode_summary_counts_unsafe_episodes_and_integrated_exposure():
    rows = []
    for violation, voltage, loading in [(0., 0., 0.), (10., .2, 0.), (40., .4, 2.)]:
        rows.append(dict(delivered_kwh=100., deadline_shortfall_kwh=20., requested_kwh=120.,
            violation_seconds=violation, voltage_exposure_pu_s=voltage,
            loading_exposure_s=loading, v_min=.94 if violation else .96,
            v_max=1.04, loading_max=1.02 if loading else .9,
            commands=10, queries=2, reserved_unused_kwh=5., filter_seconds=.1,
            filter_median_ms=.2, filter_max_ms=1., convergence_failures=0,
            envelope_containment_failures=0, envelope_containment_seconds=0.,
            max_envelope_excess_kw=0., max_affine_excess=0.,
            initial_electrical_violation=False, failed=False))
    result = analysis().summarize(rows)
    assert result['episodes'] == 3
    assert result['unsafe_episodes'] == 2
    assert result['violation_seconds_total'] == 50.
    assert result['voltage_exposure_pu_s_total'] == pytest.approx(.6)
    assert result['loading_exposure_s_total'] == 2.
    assert result['delivered_kwh_mean'] == 100.
    assert result['deadline_shortfall_kwh_mean'] == 20.
    assert result['v_min'] == .94


def test_manifest_shape_validation_rejects_duplicate_missing_and_nonfinite_results():
    module = analysis()
    rows = [dict(phase='main', label='main', seed=1, pattern='T1', loading=1.,
                 feeder='ieee123', policy='P', requested_kwh=20.,
                 duration_s=1800., simulated_seconds=1800., failed=False,
                 convergence_failures=0, session_delivered_kwh={'0': 10.})]
    expected = {('main', 'main', 1, 'T1', 1., 'ieee123', 'P')}
    module.validate_rows(rows, expected)
    with pytest.raises(ValueError, match='Duplicate'):
        module.validate_rows(rows + rows, expected)
    with pytest.raises(ValueError, match='missing'):
        module.validate_rows([], expected)
    rows[0]['session_delivered_kwh']['0'] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        module.validate_rows(rows, expected)


def test_cohort_pair_validation_rejects_unequal_requested_energy():
    module = analysis()
    rows = [dict(phase='main', label='main', seed=1, pattern='T1', loading=1.,
                 feeder='ieee123', policy=policy, requested_kwh=request,
                 duration_s=1800., simulated_seconds=1800., failed=False,
                 convergence_failures=0, session_delivered_kwh={'0': 10.})
            for policy, request in [('B3', 20.), ('P', 21.)]]
    expected = {('main', 'main', 1, 'T1', 1., 'ieee123', p) for p in ['B3', 'P']}
    with pytest.raises(ValueError, match='cohort'):
        module.validate_rows(rows, expected)


def test_trace_plot_includes_energy_in_final_interval():
    path = Path(__file__).resolve().parents[1] / 'scripts/make_figures.py'
    spec = importlib.util.spec_from_file_location('figures', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trace = [dict(t=0., dt=60., total_actual_kw=60., delivered_kwh=0., loading_max=.9),
             dict(t=60., dt=120., total_actual_kw=30., delivered_kwh=1., loading_max=.8)]
    series = module.trace_series(trace)
    assert series['minutes'].tolist() == [0., 1., 3.]
    assert series['energy_kwh'].tolist() == [0., 1., 2.]
    assert series['loading'].tolist() == [.9, .8, .8]
