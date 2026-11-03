# Public IEEE AC feeders and the affine surrogate

`src/evrecovery/feeder.py` provides the `FeederModel` interface in the task brief. It runs real unbalanced nonlinear AC snapshot power flow through OpenDSSDirect.py 0.9.4 (DSS C-API 0.14.5), with a separate engine context for each instance. No power flow is replaced by a synthetic network formula. The circuits and changes described here define a simulation study, not a representation of a measured charging installation.

## Source and provenance

The bundled public models are the maintained DSS-Extensions copy of the OpenDSS IEEE 13-node and IEEE 123-node test feeders. The exact repository revision is `3b208397160213cae4a9e2d0a7d1aa3528ce26e1`. The source repository is <https://github.com/dss-extensions/electricdss-tst>; the upstream EPRI test-case index is <https://opendss.epri.com/ExamplesDocsandTestCases.html>. Every downloaded file has its immutable raw URL and SHA-256 in `data/feeders/manifest.json`. The downloaded DSS/coordinate text is unmodified. Both case-local `IEEELineCodes.DSS` files redirect to the shared parent file, which is included. The IEEE123 master redirects to the loads and regulators files; its switches are already inline, so a separate switches file is unnecessary. The 13-node master uses the included bus coordinate CSV. The 123-node coordinates are included as optional reference geometry and do not affect power flow.

Primary API references are the locally installed OpenDSSDirect docstrings and the EPRI documentation they reference: <https://opendss.epri.com/AllBusVmagPu.html>, <https://opendss.epri.com/NodeOrder.html>, and <https://opendss.epri.com/NormalAmps.html>. The last reference explains the engine's normal-current field; this implementation deliberately does not interpret undocumented default normal-current values as test-feeder asset ratings.

## Declared development operating point

The published circuits are preserved on disk. Initialization makes these explicit runtime changes:

1. Multiply every original background load's kW and kvar by 0.8. Thus `background_scale=1.0` means 80% of the published demand, while `background_scale=0.9` means 72%. Original load models, connections, power factors, line impedances, capacitor banks, transformer kVA/kV, and source parameters are preserved.
2. Set every regulator's forward voltage target to 120 V, solve the nominal background with zero EV demand, and freeze controls with `controlmode=off`. All subsequent solves retain those taps. Exact solved winding taps are in `validation.json`.
3. Add fixed-phase, single-phase, wye-connected, unity-power-factor, constant-kW equivalent EV charging groups at the medium-voltage buses below. Each group represents aggregate charging transferred to a feeder connection. No secondary charging-transformer, secondary cable, individual connector, or voltage-dependent acceptance model is asserted. Constant-power EV operation uses `vminpu=0.5` and `vmaxpu=1.5`, encompassing the tested range. Capacitors retain their source states.

These are declared development choices. The IEEE13 published operating point has a maximum voltage of about 1.05605 pu, exceeding the study's 1.05 pu bound. Its small load transformer also has a heavily loaded phase. The above changes provide feasible initial settings before charging policies or held-out outcomes are examined. Changing background loading changes regulator calibration only when a new `FeederModel` is constructed, not during later evaluations.

| Case | Station buses, in order | Phase numbers, in order | Default available group maxima |
| --- | --- | --- | --- |
| IEEE13 | 632, 671, 675, 680, 645, 611 | 1, 2, 3, 1, 2, 3 | 100 kW each |
| IEEE123 | 48, 76, 83, 67, 95, 105 | 1, 2, 3, 1, 2, 3 | 200 kW each |

Six groups are the main interface default. Up to eight are supported: IEEE13 adds 633.1 and 652.1; IEEE123 adds 49.1 and 65.2. `nominal_station_kw` is an explicitly chosen aggregate charging capability, not a public IEEE feeder rating. `evaluate` permits powers above these available station maxima for stress analysis; the scheduler must enforce the maxima. It rejects negative, nonfinite, or incorrectly sized power vectors.

## Voltage and thermal scope

The voltage arrays contain every phase node reported by `Circuit.AllBusMagPu`, with matching `Circuit.AllNodeNames` identifiers. The source nodes and modeled MV/LV nodes are included. `v_min` and `v_max` are extrema over this array.

The public source defines transformer winding kVA and kV. For each winding, the implementation uses nameplate phase current `kVA/(sqrt(3)*kV)` for three-phase equipment and `kVA/kV` for single-phase equipment. `loading_pu` is each phase conductor's current magnitude divided by that current; neutral currents are excluded. `loading_max` is the maximum over transformer winding phase conductors. This includes feeder regulators and service transformers and can be more restrictive than checking only total three-phase apparent power. `thermal_ratings` exposes the exact ratings and provenance. Taps do not alter the nominal nameplate denominator.

The bundled line and line-code files provide no explicit normal ampacities. Consequently lines are listed in `unrated_lines` and are excluded from thermal inequalities. The engine's generic default normal ampacity is not promoted into an IEEE rating. These cases support voltage and source-nameplate transformer loading conclusions only. They do **not** establish line thermal security, full-network thermal security, protection coordination, dynamic security, harmonic performance, or physical charger performance.

## API and signed affine rows

```python
import numpy as np
from evrecovery.feeder import FeederModel

model = FeederModel('ieee123', n_stations=6)
ac = model.evaluate(np.zeros(6), background_scale=1.0)
rows = model.linear_constraints(background_scale=1.0, p0=np.zeros(6))
# Each row represents A @ power_kw + C * background_scale <= b.
```

`evaluate` returns `converged`, `v_min`, `v_max`, `loading_max`, detailed `voltage_pu`, `voltage_names`, `loading_pu`, `loading_names`, requested powers, background scale, and timing. Nonconvergence remains an explicit output and must count as an invalid electrical state in experiment analysis.

`linear_constraints` linearizes the signed physical output vector `y=[-v, +v, transformer_loading]` about `(p0, background_scale)`. Station derivatives use 1 kW centered differences when `p0[j]>=1 kW`, otherwise a forward difference. Background derivatives use a centered 0.001 scale step, clipped at zero. The intercept is `offset=y0-A@p0-C*background_scale`. The physical signed row limits are `limits=[-(vmin+voltage_margin), vmax-voltage_margin, loading_limit]`; returned `b=limits-offset`. The affine prediction therefore equals the nonlinear outputs at the linearization point up to solver tolerance. `offset`, `limits`, `p0`, and the linearization background are returned to make this identity auditable.

The sensitivities remain signed, including voltage rises on some phases and negative loading derivatives. A robust box calculation must use the upper station power when its row coefficient is positive and the lower power when it is negative. For `p∈[lower, upper]` and background `β∈[βlow, βhigh]`, its row maximum is `A_positive@upper + A_negative@lower + C_positive*βhigh + C_negative*βlow`. Simply using `A@upper` is invalid for signed rows. The model adds no station-capability rows to the network matrix.

Linearizations are cached by exact arguments, with at most 64 entries to bound memory in long Monte Carlo runs. Returned arrays are copied to prevent callers from corrupting cached rows. Distinct contexts avoid cross-instance circuit interference; individual instances should be used sequentially.

## Executed development verification and model limits

`data/feeders/validation.json` stores executed development results, source-nameplate rating details, and frozen taps. The zero-EV nominal states are:

| Case | Minimum voltage | Maximum voltage | Maximum transformer phase loading |
| --- | ---: | ---: | ---: |
| IEEE13 | 0.968078 pu | 1.037385 pu | 0.947198 pu |
| IEEE123 | 0.981741 pu | 1.024995 pu | 0.733625 pu |

Equal loading of all six groups reaches approximately the following AC-feasible powers per group under physical limits 0.95–1.05 pu voltage and 1.0 transformer loading. A bisection to 500 kW per group supplies these development capacity scales, not policy-study service claims.

| Case | Background scale 0.9 | Background scale 1.0 | Background scale 1.1 |
| --- | ---: | ---: | ---: |
| IEEE13 | 223.292 kW | 139.079 kW | Zero-EV background already infeasible |
| IEEE123 | 278.787 kW | 215.923 kW | 151.993 kW |

IEEE13's source load transformer makes some high background states infeasible without EV charging. A study must flag those external infeasible cases or declare a narrower background region; a charging controller cannot fix fixed background overload by setting EV power to zero.

The local affine model is not an AC certificate. A declared development check used seed 90210, 1000 random station vectors with each power uniform over [0,300] kW, background uniform over [0.9,1.1], and a zero-EV/1.0 affine point. With the API defaults of a 0.002 pu voltage margin and loading limit 1.0, IEEE13 admitted 353 vectors and 3 violated an AC limit; IEEE123 admitted 801 and 23 violated an AC limit. Maximum voltage prediction errors among admitted vectors were 0.002882 and 0.006847 pu, respectively; maximum transformer-loading underprediction was 0.026648 and 0.024591 pu. These are useful counterexamples to any interpretation of the default affine inequalities as a nonlinear safety guarantee. Experiment configurations may declare larger development margins or a different linearization point before held-out outcomes. Regardless of margin, nonlinear AC replay and reporting of every remaining violation are required.

A 500-evaluation timing run changed both EV powers and background scale on every evaluation. In this container, average full evaluations took approximately 0.107 ms for IEEE13 and 0.361 ms for IEEE123; median engine solve times were approximately 0.0295 and 0.1200 ms, with 95th percentiles 0.0568 and 0.1901 ms. These are environment-specific timings, not hardware guarantees. Every `evaluate` result includes its own `solve_seconds` and `evaluation_seconds`, and instances track `solve_count` and `total_solve_seconds`.

Run the meaningful feeder checks with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_feeder.py -q
```

The checks cover both initial AC-feasible cases, nonlinear voltage/loading response, first-order prediction near a nonzero point, the exact affine-point identity, preservation of signed coefficients, independent contexts, deterministic replay, invalid power inputs, and all source-file hashes. They do not turn the empirical checks into a proof of nonlinear safety.

## Declared development margin calibration

A subsequent requested development sweep uses group maxima of 300 kW on IEEE123 and 200 kW on IEEE13, with nonzero affine points of 100 kW/group and 50 kW/group respectively. These are experiment configuration overrides; the API's available-capability defaults remain as above. Background centers are 0.9 and 1.0, each with a declared uncertainty box of ±0.01. Every admitted deterministic station vector must satisfy the affine rows for the entire background box, using the sign-correct background extreme. The sampled AC replay uses an independently uniform realized background within that box.

The candidates are voltage margins 0.002, 0.005, and 0.008 pu crossed with transformer affine loading limits 0.98 and 0.96. Physical violation analysis always uses the unmodified 0.95–1.05 pu and transformer loading 1.0 limits. Calibration draws 3000 station vectors per background center, independently uniform over each group's [0,maximum] capability, with seeds 17701 (center 0.9) and 17801 (center 1.0). The selection rule is the smallest voltage margin with zero admitted AC violations at both centers, and then the largest passing transformer loading limit. Independent validation uses 10000 new draws per center with seeds 27701 and 27801. The finite calibration selects IEEE123's margin 0.005/loading limit 0.98 and IEEE13's margin 0.002/loading limit 0.98. No held-out policy-study outcome was used.

| Case and selected parameters | Background center | Calibration admitted / violations | Independent validation admitted / violations | Validation maximum absolute voltage error | Validation maximum loading underprediction |
| --- | ---: | ---: | ---: | ---: | ---: |
| IEEE123, p0=100 kW, margin 0.005, loading 0.98 | 0.9 | 2734 / 0 | 9110 / 0 | 0.003447 pu | 0.013096 pu |
| IEEE123, p0=100 kW, margin 0.005, loading 0.98 | 1.0 | 2063 / 0 | 6860 / 0 | 0.003730 pu | 0.013536 pu |
| IEEE13, p0=50 kW, margin 0.002, loading 0.98 | 0.9 | 2886 / 0 | 9629 / 0 | 0.001619 pu | 0.008765 pu |
| IEEE13, p0=50 kW, margin 0.002, loading 0.98 | 1.0 | 1810 / 0 | 5999 / 0 | 0.000798 pu | 0.009009 pu |

For IEEE123, the smaller 0.002 pu margin and 0.98 loading limit had 5 and 3 admitted AC violations in calibration at centers 0.9 and 1.0; independent validation had 6 and 8. The retained passing settings therefore reflect actual approximation limitations. Every candidate's counts, physical extrema, and residual maxima are preserved in `data/feeders/margin_validation.json`. The sweep took approximately 15 seconds in this container. Reproduce it from the root with `PYTHONPATH=src .venv/bin/python data/feeders/validate_margins.py`.

Zero observed violations in these finite independent random samples does not prove containment over the full power/background box, arbitrary upper/lower station envelopes, a time-varying uncertainty region, or another linearization point. The proposed mathematical guarantee remains conditional on its affine electrical model; all policy outcomes still require nonlinear AC replay. A uniform 0.005 pu voltage margin with loading limit 0.98 is available if a study prefers identical numerical margins across feeders, at additional conservatism on IEEE13.
