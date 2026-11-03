"""Reproduce the declared development-only margin sweep, not policy results.

Run from repository root:
    PYTHONPATH=src .venv/bin/python data/feeders/validate_margins.py
"""
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from evrecovery.feeder import FeederModel


def run():
    started = perf_counter()
    cases = {}
    candidates = [(v, l) for v in (.002, .005, .008) for l in (.98, .96)]
    for name, maxkw, p0 in [('ieee123', 300., 100.), ('ieee13', 200., 50.)]:
        model = FeederModel(name)
        case = {}
        for stage, seed, draws in [('calibration', 17701, 3000), ('validation', 27701, 10000)]:
            stageout = {}
            for center in (.9, 1.):
                rows = model.linear_constraints(background_scale=center, p0=np.full(6, p0),
                                                voltage_margin=0., loading_limit=1.)
                nvolt = len(model.evaluate(np.full(6, p0), center)['voltage_pu'])
                nv2 = 2 * nvolt
                stratum_seed = seed + (0 if center == .9 else 100)
                rng = np.random.default_rng(stratum_seed)
                power = rng.uniform(0, maxkw, (draws, 6))
                background = rng.uniform(center-.01, center+.01, draws)
                lhs = (power @ rows['A'].T + np.maximum(rows['C'], 0.)*(center+.01)
                       + np.minimum(rows['C'], 0.)*(center-.01) + rows['offset'])
                stats, masks = {}, {}
                for v, l in candidates:
                    key = f'v{v}_l{l}'
                    stats[key] = dict(voltage_margin=v, loading_limit=l, admitted=0, ac_violations=0,
                                      max_voltage_abs_error=0., max_loading_underprediction=0.,
                                      worst_v_min=2., worst_v_max=0., worst_loading=0.)
                    bound = np.r_[np.full(nvolt, -(.95+v)), np.full(nvolt, 1.05-v),
                                  np.full(len(rows['C'])-nv2, l)]
                    masks[key] = np.all(lhs <= bound+1e-10, axis=1)
                for i in np.flatnonzero(np.logical_or.reduce(list(masks.values()))):
                    ac = model.evaluate(power[i], background[i])
                    prediction = rows['A'] @ power[i] + rows['C']*background[i] + rows['offset']
                    truth = model._signed_outputs(ac)
                    for key, stat in stats.items():
                        if not masks[key][i]:
                            continue
                        stat['admitted'] += 1
                        stat['ac_violations'] += int(not ac['converged'] or ac['v_min'] < .95-1e-9
                                                    or ac['v_max'] > 1.05+1e-9
                                                    or ac['loading_max'] > 1.+1e-9)
                        stat['max_voltage_abs_error'] = max(stat['max_voltage_abs_error'],
                                                            float(np.max(np.abs(truth[:nv2]-prediction[:nv2]))))
                        stat['max_loading_underprediction'] = max(stat['max_loading_underprediction'],
                                                                  float(np.max(truth[nv2:]-prediction[nv2:])))
                        stat['worst_v_min'] = min(stat['worst_v_min'], ac['v_min'])
                        stat['worst_v_max'] = max(stat['worst_v_max'], ac['v_max'])
                        stat['worst_loading'] = max(stat['worst_loading'], ac['loading_max'])
                stageout[str(center)] = dict(seed=stratum_seed, draws=draws, results=stats)
                print(name, stage, center, [(key, x['admitted'], x['ac_violations'])
                                           for key, x in stats.items()], flush=True)
            case[stage] = stageout
        case.update(group_max_kw=maxkw, p0_kw=p0, background_uncertainty_halfwidth=.01)
        cases[name] = case
    result = dict(purpose='Development-only calibration followed by independent random validation; no global AC guarantee',
                  cases=cases, elapsed_seconds=perf_counter()-started)
    Path(__file__).with_name('margin_validation.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    run()
