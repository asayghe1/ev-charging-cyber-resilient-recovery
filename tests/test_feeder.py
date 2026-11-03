"""Scientific checks against real bundled OpenDSS circuits (no mocked solves)."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pytest
from evrecovery.feeder import FeederModel


@pytest.mark.parametrize('name', ['ieee13', 'ieee123'])
def test_nominal_background_is_electrically_feasible(name):
    model = FeederModel(name)
    result = model.evaluate(np.zeros(model.n_stations))
    assert result['converged']
    assert 0.95 <= result['v_min'] <= result['v_max'] <= 1.05
    assert result['loading_max'] <= 1.0
    assert len(result['voltage_pu']) == len(result['voltage_names'])
    assert len(result['loading_pu']) == len(result['loading_names'])
    assert result['solve_seconds'] > 0


@pytest.mark.parametrize('name', ['ieee13', 'ieee123'])
def test_added_ev_load_changes_real_ac_voltage_and_loading(name):
    model = FeederModel(name)
    zero = model.evaluate(np.zeros(model.n_stations))
    loaded = model.evaluate(model.nominal_station_kw)
    assert loaded['converged']
    assert loaded['v_min'] < zero['v_min'] - 1e-5
    assert loaded['loading_max'] > zero['loading_max'] + 1e-5
    assert all(p in (1, 2, 3) for p in model.station_phases)
    assert len(model.station_buses) == model.n_stations


def test_signed_affine_prediction_is_exact_at_linearization_point():
    model = FeederModel('ieee123')
    p0 = np.full(model.n_stations, 20.0)
    bg = 0.97
    linear = model.linear_constraints(background_scale=bg, p0=p0)
    physical = model.evaluate(p0, bg)
    lhs = linear['A'] @ p0 + linear['C'] * bg
    expected = np.r_[-physical['voltage_pu'], physical['voltage_pu'], physical['loading_pu']]
    # Subtract the explicit affine intercept from each signed physical quantity.
    np.testing.assert_allclose(lhs + linear['offset'], expected, atol=2e-6)
    np.testing.assert_allclose(linear['b'] + linear['offset'], linear['limits'])
    assert np.any(linear['A'] < 0), 'signed sensitivities must not be clipped'
    assert len(linear['row_names']) == len(lhs)
    assert np.all(np.isfinite(linear['A']))
    assert all(np.asarray(linear[k]).ndim == 1 for k in ('b', 'C'))


def test_small_change_matches_ac_to_first_order():
    model = FeederModel('ieee13')
    p0 = np.full(model.n_stations, 15.0)
    linear = model.linear_constraints(p0=p0)
    delta = np.array([1., -1., .5, -.5, .2, -.2])
    actual = model.evaluate(p0 + delta, 1.001)
    predicted = linear['A'] @ (p0 + delta) + linear['C'] * 1.001 + linear['offset']
    truth = np.r_[-actual['voltage_pu'], actual['voltage_pu'], actual['loading_pu']]
    np.testing.assert_allclose(predicted, truth, atol=2e-4)


def test_contexts_are_independent_and_repeated_solves_are_deterministic():
    a = FeederModel('ieee13', 8)
    original = a.evaluate(np.full(8, 10.0))
    b = FeederModel('ieee123', 6)
    b.evaluate(np.full(6, 70.0), 1.1)
    again = a.evaluate(np.full(8, 10.0))
    np.testing.assert_allclose(original['voltage_pu'], again['voltage_pu'], atol=1e-8)


def test_public_data_match_provenance_hashes():
    root = Path(__file__).resolve().parents[1] / 'data' / 'feeders'
    manifest = json.loads((root / 'manifest.json').read_text())
    for entry in manifest['files']:
        assert hashlib.sha256((root / entry['path']).read_bytes()).hexdigest() == entry['sha256']
        assert '/dss-extensions/electricdss-tst/' in entry['url']


@pytest.mark.parametrize('bad', [[-1] * 6, [1] * 5, [float('nan')] * 6])
def test_invalid_station_power_is_rejected(bad):
    with pytest.raises(ValueError):
        FeederModel('ieee13').evaluate(bad)
