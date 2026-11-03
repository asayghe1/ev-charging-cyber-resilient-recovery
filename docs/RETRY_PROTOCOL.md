# Additional post-review retry robustness experiment

This is an **additional experiment prompted by independent review after the main
analysis**. It is not a prespecified primary experiment. The primary source
freeze, its 3,020 evaluation outputs, and their original interpretation remain
identifiable. No original `execution.py`, `feeder.py`, `policies.py`, `scenarios.py`,
or `simulator.py` source is changed by this extension.

## Question and paired design

The experiment checks whether the observed P/B3 comparison depends on their
original command-retry behavior. It adds the same application-level retry
capability to both methods while retaining their different admission rules.
There is no assumption that retries improve either method or that P dominates.

The additional evaluation contains 30 distinct synthetic session blocks, seeds
3000 through 3029 inclusive, crossed with T1/T2/T3/T4, loading 0.9/1.0, and four
policies: B3, P, B3_retry, P_retry. This yields 240 paired scenarios and **960
policy episodes**. Every result row has `phase='retry'`, `label='retry60'`, and
`study_kind='additional_post_review_retry_robustness'`. The resolved scenario
retains `feedback_margin=0.20`, matching the common frozen configuration; this
field does not enter B3/P admission. Synthetic session generation, backgrounds,
feeder, network margins, local allocation, delays, telemetry, certificates,
actuation and absolute lease ends retain the original implementations.

Compare B3_retry with P_retry as the equal-retry comparison; also compare each
retry variant with its original version on the same additional seeds. Analysis
must preserve dependence within seed blocks and distinguish this review-driven
extension from the earlier primary analysis. Report electrical exposure and
actual service together, including unfavorable effects. Retry timing is not
tuned on the evaluation seeds.

## Same-generation application retry

`RetryController` subclasses the frozen Controller, with `B3_retry` mapped to B3
and `P_retry` mapped to P for all admission decisions. At each existing 30-second
control tick it first calls the original controller. A new logical command takes
precedence for its station. Otherwise, if the station has unresolved work, the
latest unresolved generation is eligible when at least 60 seconds have elapsed
since that generation's last original/retry attempt. The first retry is therefore
60 seconds after its original issue, and subsequent retries are at least 60
seconds apart. If a newer logical generation is issued, the older one is never
selected for retry. Expired logical commands are not retried.

A retry preserves station, generation, epoch, cap, absolute start and absolute
end. The original logical Command remains the one in the real Ledger and policy
history. The extension neither reserves a retry again nor renews a lease. The
new attempt receives `issued=t` as its transport-attempt timestamp because the
frozen simulator schedules prospective commitment events using this field.
Thus this is a **same-generation retry, not a byte-identical retransmission**.
The effective start still cannot precede actual receipt plus bounded actuation,
and expiry is never extended. An authenticated duplicate of an already accepted
generation is rejected by the existing adapter fence; it does not reapply a cap,
restart actuation, or extend expiry. Such rejection is distinct from an unsafe
execution or a failure to execute a previously lost original.

Retries consume the same one command-or-query RPC opportunity per station/tick.
The inherited query capability remains available on ticks without a new command
or retry. In particular a retry at time 60 replaces that station's query at 60;
its query can still occur at 30/90. Both variants have identical energy telemetry
and certificate uplinks. Retry computation is included in decision runtime.

The simulator's previous-request delta becomes zero for a repeated cap. Passing
that zero to selective attacks would incorrectly let retries evade T2/T3.
`RetryScenario` therefore records the **first command direction per
(station,generation)**, including when that first command is lost, and forwards
that original delta on every command attempt of that generation. Each attempt
still uses its actual current send time and the original exogenous delay law.
A different generation has its own direction. Noncommand channels are forwarded
unchanged. This wrapper is fresh for every episode and does not inspect plant
state or future delivery outcomes.

## Non-invasive execution and metrics

`run_retry_episode` wraps the scenario freshly. For standard B3/P it directly
calls the original simulator with its unpatched original Controller. For the two
retry variants it scopes a `unittest.mock.patch` of `policies.Controller` to that
single call; the simulator imports Controller locally. The original class is
restored on return or exception. The wrapper must not run concurrently in
threads inside one interpreter. The study runner uses **spawned process workers**
and sequential episode calls within each worker, avoiding shared patch state.

Standard B3/P EpisodeResult metrics are returned unchanged. Retry variants add
`retries` and `logical_commands`; the study runner normalizes the corresponding
standard row values to zero and `commands`, respectively. Existing `commands`
includes all original and retry attempts; query and channel counters retain their
original definitions. No extra scientific measurement is supplied to either
controller. All original AC failure and containment diagnostics remain active.

## Preparation, independent review and evaluation

From the repository root, prepare the complete 960-job configuration without
executing episodes:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_retry_study.py --workers 4 --freeze-only
```

This writes `results/retry.freeze.json` and reports `evaluated=0`. It records all
resolved synthetic sessions and actual scenario parameters, the exact job list,
60-second retry rule, command-field semantics, source/test SHA-256 hashes, every
original frozen-source hash, original freeze digest, hashes of the original
primary raw/config outputs, feeder-data manifest digest, exact preparation argv,
Python/dependency/platform/git/thread details, and process start method.
Preparation refuses to proceed if any original frozen source has changed.

**Root must independently review and freeze this new protocol and implementation
before executing the additional evaluation.** After that review, run:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_retry_study.py --workers 4 --reviewed-manifest results/retry.freeze.json
```

The runner requires a reviewed manifest to evaluate. It verifies the reviewed
jobs, retry semantics, dependencies, all recorded source/test hashes, original
freeze and raw/config snapshots, feeder manifest, and resolved scenarios. A
change requires another explicit preparation/review. Different unrelated source
edits can also invalidate a prepared all-source snapshot; finish such edits
before preparing the reviewed manifest. Worker count may differ because it does
not change scenario or policy semantics; the actual count is recorded.

The runner refuses to overwrite prior evidence. It writes
`results/retry.config.json`, containing actual execution argv plus the reviewed
manifest's path/digest, then flushes each completed result to
`results/retry.jsonl`. After evaluation it rechecks the original frozen sources
and primary raw/config snapshots. It exits nonzero for any failed AC episode,
which remains in the raw output. No main/sensitivity/ablation result is replaced.

## Implementation verification and development-only observations

Initial retry tests failed for the absent retry module. The runner-plan test
separately failed for the absent runner before its implementation. Tests cover
field preservation, unchanged Ledger/history, 60-second cadence, query
alternation, new-generation precedence, adapter duplicate rejection, lost-original
recovery, no lease renewal, expiry stopping, actual T2/T3 direction classification,
per-episode isolation, scoped patch restoration, standard-policy equivalence,
full 960-job design, and original frozen hashes.

A development-only check used seed 101, T2, loading 1.0, actual IEEE123 replay,
feedback margin 0.20 and the original 1800-second duration. Separate fresh feeder
instances produced **exactly equal complete scientific metrics** for standard
B3/P through `run_retry_episode` versus direct `run_episode`, excluding only the
three decision-runtime fields. B3 delivered 393.1836378922 kWh; P delivered
392.3942809941 kWh in both paths.

The same development case produced the following implementation checks; they are
not evaluation findings and are not a basis for selecting retry timing:

| Policy | Delivered kWh | Deadline shortfall kWh | Logical commands | Retries | Queries |
| --- | ---: | ---: | ---: | ---: | ---: |
| B3_retry | 393.1599313171 | 384.8837899843 | 77 | 12 | 13 |
| P_retry | 392.3556202749 | 385.6881010264 | 78 | 9 | 11 |

Both development retry runs had zero electrical violation seconds, zero
containment failures and no failed AC solve. The slight service decreases were
preserved. They illustrate why additional retries must be evaluated rather than
assumed beneficial: retry RPCs also replace some queries.

Fresh verification at delivery: `PYTHONPATH=src .venv/bin/python -m pytest
tests/test_retry.py -q` passed **17 tests**; the complete shared suite passed
**144 tests**. The preparation smoke test wrote a temporary full 960-job manifest
and verified that zero episodes/feeder evaluations were executed; the review
guard and scoped-patch exception restoration also passed. No seed 3000–3029 episode has been evaluated by
this implementation task; preparation and tests do not evaluate that cohort.
