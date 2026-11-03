# Cyber-Resilient Recovery of Electric Vehicle Charging Under Uncertain Command Execution

**Authors:** Ali Sayghe and Ahmed Sultan. Department of Electrical and Electronic Engineering, Yanbu Industrial College, Royal Commission for Jubail and Yanbu, Yanbu, Saudi Arabia.

**Research resources:** [Simulation code](src/evrecovery) · [SVG figures](manuscript/figures)

**The manuscript PDF and LaTeX paper sources have been removed from the current public branch at the authors' request.** Earlier Git commits may still contain copies.

**Repository:** <https://github.com/asayghe1/ev-charging-cyber-resilient-recovery>

This repository contains study configurations, a Python event simulator, public IEEE feeder source files, regression tests, analysis scripts, and editable SVG/vector PDF figures for the electric-vehicle charging recovery study. The study evaluates charging-capacity reservations under uncertain command execution. Its mathematical result is conditional on the stated affine network and trusted adapter contract; nonlinear AC replay supplies simulation evidence.

The charging requests are **synthetic**. Six single-phase charging populations are medium-voltage equivalents attached to public IEEE 123-node and IEEE 13-node feeders. The package does not represent field measurements, charger hardware tests, production OCPP conformance, secondary charging networks, or line-ampacity validation. Thermal metrics cover source-rated transformer winding terminal-phase currents. See [docs/PARAMETERS.md](docs/PARAMETERS.md), [docs/FEEDER.md](docs/FEEDER.md), and [docs/EXECUTION_CONTRACT.md](docs/EXECUTION_CONTRACT.md) for the exact scope.

The completed main impaired study gives the following saved outcomes. The per-group serialized ledger variant B3 delivers slightly more energy than P; supersession is a measured tradeoff rather than an assumed improvement. The paired P-minus-B3 mean is −2.48 kWh, with 95% cohort-bootstrap interval [−3.23, −1.69] kWh. B1's zero unsafe development cases do not transfer to zero unsafe held-out cases. These values come from `results/analysis/summary.json` and concern this declared synthetic population.

| Policy | Mean delivered energy (kWh) | Episodes with an AC limit violation, out of 240 |
| --- | ---: | ---: |
| P, evidence ledger with admitted supersession | 480.15 | 0 |
| B3, per-group serialized evidence ledger | 482.63 | 0 |
| B2, static initial quotas | 380.31 | 0 |
| B1, feedback with selected 20% headroom | 355.79 | 22 |
| B0, nominal requested caps | 546.17 | 192 |

The primary P/B3 implementations query unresolved execution without retransmitting lost commands. A **supplementary experiment specified after the primary analysis** adds the same 60-second, same-generation retry capability to both and evaluates 30 new cohorts. Its 960 episodes remain distinct from the initially frozen 3,020. With retries, P delivers 482.22 kWh and B3 delivers 483.04 kWh per episode; the paired difference is −0.825 kWh, with a 95% interval of [−1.137, −0.497] kWh. Both retry variants have zero observed electrical violations in their 240 cases. This preserves the adverse aggregate supersession result while addressing equal retry capability. See `results/retry_analysis/summary.json` and [docs/RETRY_PROTOCOL.md](docs/RETRY_PROTOCOL.md). The combined evaluation count is **3,980**.

## Environment and quick build

The recorded execution environment uses Python **3.12.14** on Linux. Install the seven pinned scientific/test packages in a virtual environment; pip resolves their ordinary transitive dependencies. The `git` executable must be available for simulation manifest metadata; an extracted archive does not need to be a Git checkout. No network access or charging-data credentials are needed to replay the bundled feeder cases after dependencies are installed.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make check
make check
```

The manuscript is not included in the current public branch. Saved-data plots can be regenerated with `make figures` when the expected raw result files are available; SVG and vector PDF figures remain in `manuscript/figures`.

For the revised typography, `scripts/format_figures.py` reuses the original frozen plot functions with eight-point serif text. It selects a TrueType Times New Roman font, with a TrueType Nimbus Roman fallback. The delivered plots use genuine Times New Roman at eight points. Install a suitable TrueType family to regenerate figures; CFF/OTF fonts are rejected to prevent PDF embedding warnings, and the supplied figure PDFs already embed their fonts. Historical plotting/analysis sources and all experiment manifests retain their original hashes.

## Reproduce the numerical study

From the project root, use a fresh destination to preserve the supplied evidence:

```bash
make reproduce REPRO_DIR=results/reproduced-001 WORKERS=4
```

This launches the 2,100 main, 840 sensitivity, 80 mechanism-ablation and 960 supplementary retry episodes, then validates their complete layouts, regenerates the selected descriptive trace, and writes analysis and primary figure views beneath that destination. `make simulate` runs only the four numerical phases. The retry phase uses the supplied reviewed manifest, which checks scientific sources, resolved sessions, dependencies and original evidence hashes before execution. Existing phase JSONL files are not rerun by Make. Runners refuse to overwrite an existing destination and have no resume mode. If a run is interrupted, retain its partial file and configuration sidecar, select a new destination, and rerun that phase. Analyzers reject missing, duplicated, nonfinite, nonconverged, incomplete, or unequally paired records, so a partial JSONL file cannot become an accepted analysis.

The equivalent explicit commands show the environment, evidence paths, and analysis boundaries:

```bash
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_study.py --phase main --workers 4 --feedback-margin .20 --out results/reproduced-001/main.jsonl
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_study.py --phase sensitivity --workers 4 --feedback-margin .20 --out results/reproduced-001/sensitivity.jsonl
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_study.py --phase ablations --workers 4 --feedback-margin .20 --out results/reproduced-001/ablations.jsonl
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/run_retry_study.py --workers 4 --reviewed-manifest results/retry.freeze.json --out results/reproduced-001/retry.jsonl
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/analyze_study.py --results results/reproduced-001 --out results/reproduced-001/analysis
env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/make_trace.py --main results/reproduced-001/main.jsonl --out results/reproduced-001/representative_trace.json --feedback-margin .20
.venv/bin/python scripts/analyze_retry.py --input results/reproduced-001/retry.jsonl --config results/reproduced-001/retry.config.json --out results/reproduced-001/retry_analysis
.venv/bin/python scripts/format_figures.py --summary results/reproduced-001/analysis/summary.json --retry-summary results/reproduced-001/retry_analysis/summary.json --trace results/reproduced-001/representative_trace.json --out results/reproduced-001/figures
```

Run either the Make recipe or these explicit commands, since they target the same new files. The runner's CLI default feedback margin is 0.05; the frozen evaluation explicitly sets **0.20**. It was selected from 0.05, 0.10, and 0.20 on 50 common development cases per candidate. The selected setting is not inferred from evaluation outcomes.

The 3,020 evaluation episodes exclude development pilots, feedback tuning, feeder margin calibration/validation, regression tests, and four B0/B3/P/R representative-trace reruns. Main inference averages the eight impaired conditions within each of 30 independent synthetic cohort seeds before a 10,000-draw percentile block bootstrap with seed 697213. Sensitivity and ablation comparisons use ten and five seed blocks, respectively. T1 through T4 are distinct stress conditions; cross-condition differences do not isolate a causal effect of attack selectivity. See [docs/FROZEN_PROTOCOL.md](docs/FROZEN_PROTOCOL.md).

The additional 960 retry episodes also exclude development and tests. They use 30 new seeds 3000–3029, four impaired conditions, two loading centers, and the original/retry variants of P and B3. Their equal-retry comparison uses the same 10,000-draw bootstrap on 30 cohort blocks. The follow-up was designed after primary analysis and frozen before these new outcomes were examined. It does not change the primary inference. The original eight frozen source hashes and original raw/configuration outputs were checked unchanged after execution. `make figures` invokes `scripts/format_figures.py` to generate all five figure views, including the retry comparison, in `manuscript/figures`.

## Files and provenance

| Path | Contents |
| --- | --- |
| `src/evrecovery/` | Trusted adapter/ledger model, policies, synthetic scenarios, event replay, AC feeder interface |
| `scripts/run_study.py` | Paired episode runner and configuration-sidecar writer |
| `scripts/analyze_study.py` | Complete-layout validation, cohort bootstrap, CSV/JSON/LaTeX summaries |
| `scripts/make_trace.py` | Frozen median-effect trace selection and checked descriptive replays |
| `scripts/make_figures.py` | Saved-data SVG/PDF rendering and figure provenance |
| `scripts/run_retry_study.py`, `src/evrecovery/retry.py` | Separately frozen same-generation retry extension, with original core unchanged |
| `scripts/analyze_retry.py`, `results/retry_analysis/` | New-cohort paired follow-up analysis, table and figure provenance |
| `results/freeze.json`, `*.config.json` | Frozen protocol hashes, source/test hashes, resolved sessions, job mapping, dependency versions, and runtime metadata |
| `results/analysis/` | Sorted episode CSV, summary JSON, paired-effect CSV, and LaTeX table |
| `data/feeders/manifest.json` | Immutable source URLs and SHA-256 hashes for every bundled feeder file |
| `data/feeders/validation.json`, `margin_validation.json` | Separate feeder checks and development margin sweep |
| `tests/` | Execution, causality, event integration, feeder, policy, experiment-layout, and analysis checks |

The feeder files come from the DSS-Extensions maintained copy of the OpenDSS test cases at revision `3b208397160213cae4a9e2d0a7d1aa3528ce26e1`: <https://github.com/dss-extensions/electricdss-tst>. EPRI's upstream model index is <https://opendss.epri.com/ExamplesDocsandTestCases.html>. Downloaded files remain unmodified on disk; declared runtime loading/regulator/EV changes are documented in `docs/FEEDER.md`. Separate OpenDSS engine contexts prevent circuit interference across feeder instances; each worker uses its instances sequentially.

The freeze and per-run hashes identify the executed core more precisely than the final package revision, which can also include analysis, presentation, and documentation changes. Process completion order can change JSONL line order. Analysis sorts episode identities; scientific outputs should be compared by those identities, excluding measured wall/computation times. The representative trace selects the T1, loading-1.0 seed nearest the median P-minus-B3 energy difference, breaking ties by smaller seed, and checks saved service, electrical, command-count, and query-count metrics against the main file with absolute tolerance 1e−7. The selected supplied trace uses seed 1011.

To regenerate development-only feeder margins, run `PYTHONPATH=src .venv/bin/python data/feeders/validate_margins.py` in a separate copy of this package. That script writes `data/feeders/margin_validation.json`, so it must not be used to overwrite the preserved supplied evidence inadvertently. Random finite validation is not a global nonlinear AC certificate. `docs/PARAMETERS.md` gives every calibration candidate and sampling seed.

The manuscript has been removed from the current public branch. This code-and-figure repository does not constitute a conference submission or claim acceptance. Historical Git revisions and execution paths in frozen manifests record the original study environment and need not occur in this publication repository's history.
