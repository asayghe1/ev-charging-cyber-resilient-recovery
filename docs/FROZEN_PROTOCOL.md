# Evaluation protocol frozen before evaluation outputs

Date: 3 November 2026. Development cohorts are seeds101–105. Evaluation cohorts have not been examined at this freeze.

## Design and units of inference

- Main: seeds1000–1029 × clean/T1/T2/T3/T4 × background centers0.9/1.0 × seven policies =2100 episodes. Each episode lasts1800s and has156 synthetic sessions. The primary impaired comparison contains240 episodes per policy;60 clean episodes per policy are reported separately.
- Sensitivity: seeds2000–2009 ×12 settings × seven policies =840 episodes. Settings are three certificate-delay extensions(15/90/180s), three affected-group counts(1/2/4), three actuation lags(0/5/15s), and IEEE13 T1/T2/T3. Non-feeder settings use T3 on IEEE123 at background center1.0.
- Ablations: seeds1000–1004 × T1/T2/T3/T4 at background1.0 × four ablations =80 episodes. Full-P comparators are the matching main episodes.
- All seven policies share the same cohort, exogenous channel law, actual electrical model, local EDF allocation, booking interface, delayed energy telemetry and forward-channel capability within each paired condition. R differs by immediate applied certificates. B4 changes the allocation rule.
- T1–T4 are distinct stress conditions, not a distribution-matched causal comparison of selective versus random delay.
- Primary service quantity is energy delivered before each session's departure. Requested energy minus delivered energy gives deadline shortfall. Electrical violation is any actual replay interval outside voltage[0.95,1.05] or transformer terminal-phase loading<=1, using the simulator's fixed numerical tolerance. Integrate exposure over actual elapsed seconds.
- Primary P−B3 effect averages eight impaired conditions within each of30 independent cohort seeds, then bootstraps30 seed blocks with10000 draws and seed697213. By-pattern effects average the two loading levels within each seed. Report95% percentile intervals; neither intervals nor timepoints are independent samples. These intervals describe the specified synthetic population, not field populations.
- Sensitivity paired estimates have10 seed blocks. Ablations have five seed blocks and20 paired cases; treat them as mechanism stress tests.
- Representative trace: among main T1 cases at background1.0, choose the cohort whose P−B3 energy difference is closest to that set's median; break ties by smaller seed. Replay B0/B3/P/R with trace retention. This descriptive selection is not a favorable-case search, and its reruns are not added to the3020 unique evaluation episodes.

## Development choices

IEEE123 affine voltage tightening0.005p.u.; IEEE13 tightening0.002p.u.; loading limit0.98 for both. Background interval is center±0.01. These are empirical development margins, not certified AC error bounds. Public feeder background is scaled to80% before scenario scaling; static regulator taps are frozen after nominal initialization. EV groups are unity-power-factor MV equivalents.

B1 headroom candidates0.05/0.10/0.20 were assessed on the same50 development cases. Unsafe episode counts were6/8/0. Select the smallest zero-unsafe candidate,0.20. No other controller parameter is tuned after the development pilot. The competent serialized baseline B3 is retained even if it outperforms P. R is a feedback reference, not a service upper bound.

## Exact launch commands

From the project root, with the supplied environment:

```bash
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_study.py --phase main --workers 4 --feedback-margin .20
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_study.py --phase sensitivity --workers 4 --feedback-margin .20
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_study.py --phase ablations --workers 4 --feedback-margin .20
```

Each output has a configuration sidecar with all resolved session requests, job mapping, code hashes, dependency versions, source data manifest hash and runtime metadata. The full launch command above supplies the interpreter/environment prefix that Python's argv does not record. `results/freeze.json` records the core commit and pre-execution hashes. Analysis and presentation changes may follow the freeze but cannot silently alter the event engine, controller or scenario laws. Any necessary correction requires a documented reason and a complete rerun of affected cases under a new output name.
