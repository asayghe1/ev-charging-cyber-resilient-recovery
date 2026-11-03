# Physical event replay

`evrecovery.simulator.run_episode(scenario, policy_name, feeder=None,
retain_trace=False)` returns `EpisodeResult(metrics, trace, events)`. The scenario
and controller interfaces are defined in `src/evrecovery/scenarios.py` and
`src/evrecovery/policies.py`.
The replay uses `execution.Station` for the private physical adapter and the
controller's real `Ledger` reservations. It does not infer charging energy from
requested commands.

## Physical plant and numerical integration

Each station represents one feeder-connected phase group. The initially established
cap is `initial_allocation(...)`; its persistent fallback is 65% of this initial
cap. Initial profile ends and every commanded absolute start/expiry are modeled
exactly. Station receipt rejects stale generations; application finishes at the
adapter's bounded actuation time. Every effective application or expiry emits a
frozen, truthful `Certificate` snapshot.

A connected session receives power in earliest-departure order within its group,
with session ID breaking ties. Allocation fills each session's acceptance rating
from the remaining effective station cap. No session charges before arrival,
after departure, or beyond its requested energy. Actual power is integrated in
kWh as `power_kw * dt_s / 3600`. The next integration endpoint is the earliest
queued event or exact session-energy completion. All simultaneous completions
are applied before the next allocation. Requested energy and acceptance ratings
are synthetic input parameters, not ACN-Data measurements.

The event heap includes physical arrivals/departures, command receipts, adapter
application/expiry, telemetry sampling and receipt, certificate receipt, query
receipt, acceptance receipt, control ticks, and command commitment start/end.
The latter remain necessary even when a command is lost: the evidence-only
reservation can change at its declared start/expiry without a physical event.
Stale transition tokens are ignored, preventing duplicate application reports.

At an equal timestamp, queued physical transitions precede sampled/received
evidence, which precedes a controller tick. A control action can causally create
another immediate physical event at that timestamp; it cannot change an already
captured sample. Background demand changes only on the scenario's declared
control grid. Timestamps are continuous seconds; no control-step rounding is
applied to link delay, actuation, expiry, departure, or energy completion.

## Information available to the controller

The decision input contains arrived, nondeparted booking requests; last received
per-session energy estimates; and last received station power. Bookings arrive
on an independent interface at physical arrival time. A physically completed
session remains a booking until departure; completion does not enter the decision
input until energy telemetry reports it. All delivered estimates begin at zero.

At each telemetry tick, separate station packets snapshot cumulative energy for
all of that station's arrived sessions and its current physical power. Each packet
uses the same exogenous delay law for every policy. A packet is accepted only
when its sample time is later than that station's last accepted sample time.
Thus out-of-order telemetry cannot rewind energy estimates or measurements.

A returned command is already reserved by its controller. The simulator then
computes direction relative to that station's previous requested cap and applies
the declared command-channel law. Acceptance acknowledges receipt only. A
truthful application certificate is emitted only after physical application.
Rejected commands produce no fabricated execution certificate. A query snapshots
the actual certificate at query receipt, then uses the common certificate uplink.
A station can receive at most one command or query RPC per control tick.
Queries, delayed commands, and losses are counted separately. Link-dependent
late command receipts model application retries after timeout or reconnection,
rather than reordering frames on one TCP stream.

Policy `R` receives every physical cap-transition certificate immediately, while
its forward command and energy-telemetry channels are unchanged. Delayed ordinary
certificates and query replies remain in the same channel. The real Ledger
chronological checks ignore obsolete witnesses and preserve later pending
commands. R has no future-session or future-delivery information.

## Nonlinear AC replay and failures

Whenever station draw or background demand changes, the simulator calls the
provided feeder's `evaluate(power_kw, background_scale=...)`. Consecutive identical
physical load/background states reuse the most recent result; control and
evidence events with unchanged physical draw need no extra AC solve. The initial
physical operating point is evaluated before any zero-time controller correction,
and its violation flag is preserved.

Voltage exposure sums each modeled phase node's distance below 0.95 or above
1.05 pu, then integrates seconds. Loading exposure sums the positive excess above
1.0 of each explicitly source-rated transformer winding/phase loading ratio.
Unrated lines are outside this thermal metric. `violation_seconds` counts the
union of intervals with any electrical violation. Extrema cover evaluated physical
states. The affine reservation test is reported separately from nonlinear AC
behavior; neither constitutes a hardware or utility validation.

The first nonconverged AC solve terminates physical integration, marks the run
`failed`, increments `convergence_failures`, and reports the completed
`simulated_seconds`. It must not be pooled as a successful full-duration run.
If no solve converged, finite placeholder extrema of 1 pu/0 loading accompany
that explicit failed flag; they are not successful electrical observations.

## Metrics and retained records

| Metric | Meaning / units |
| --- | --- |
| `delivered_kwh` | Sum of physical energy integrated over the episode |
| `requested_kwh` | Total request of sessions arriving by episode end |
| `deadline_shortfall_kwh` | Sum of nonnegative request minus delivered energy, only for deadlines within the episode |
| `voltage_exposure_pu_s` | Sum over phase-node limit deviation times seconds |
| `loading_exposure_s` | Sum over rated transformer phase loading excess times seconds |
| `violation_seconds` | Seconds with any voltage or rated thermal violation |
| `v_min`, `v_max`, `loading_max` | Worst evaluated AC conditions |
| `initial_electrical_violation` | Initial physical operating point violated an electrical limit |
| `reserved_unused_kwh` | Integral of nonnegative total reservation minus total draw, divided by 3600 |
| `max_affine_excess` | Maximum positive robust affine row residual |
| `envelope_containment_failures` | Number of positive-duration replay intervals with actual cap or power above controller Ledger reservation by more than 1e-7 kW |
| `envelope_containment_seconds` | Duration of those containment failures |
| `max_envelope_excess_kw` | Largest actual-cap/draw minus Ledger reservation |
| `commands`, `queries` | RPCs issued, including those later lost or delivered beyond the horizon |
| `message_losses` | Infinite-delay messages under the declared channel law |
| `delayed_messages` | Messages with finite positive channel delay, including normal baseline latency |
| `messages_by_kind`, `losses_by_kind` | Channel-specific counts |
| `command_rejections` | Arrived commands rejected by the adapter |
| `certificates`, `acceptance_replies` | Physical transition certificates emitted / acceptance replies received |
| `telemetry_received`, `stale_telemetry` | Accepted packets / obsolete sample packets ignored |
| `ac_evaluations` | Nonlinear evaluations actually needed for changing physical states |
| `filter_seconds`, `filter_median_ms`, `filter_max_ms` | Controller-measured decision computation; exclude these from deterministic scientific comparisons |
| `session_delivered_kwh` | Actual integrated energy by stringified session ID |

The reservation-minus-draw metric also includes unused charger capacity and
absence/completion of charging demand. It is not by itself the cost of delayed
communication. Use the prescribed paired reference or a decomposition.
Containment compares to the maintained full Ledger, not a controller's possibly
unsafe internal admission surrogate. The acceptance ablation deliberately can
corrupt this Ledger by treating acceptance as execution.

With `retain_trace=True`, each trace row describes the constant physical
interval `[t, t+dt)`, with cumulative delivered energy **at its beginning**.
Rows provide group powers/caps/reservations, aggregate actual/reserved power,
voltage/loading extrema, electrical violation flag, and continuing connected
acceptance demand. Final energy is in `metrics`; interval-end cumulative energy
can also be reconstructed as `delivered_kwh + total_actual_kw*dt/3600`.
Event rows record causal sends/receipts, generation, cap, expiry, applied time,
and evidence-availability time as applicable. An indefinite fallback expiry is
encoded as JSON `null`, rather than the nonstandard `Infinity` value.

With `retain_trace=False`, event and trace lists remain empty, reducing memory
for thousands of replays. Metrics are calculated in either mode. All result
records support strict JSON serialization. Secondary recovery/censoring is
computed by the experiment analysis, not inferred inside the plant simulator.

## Verification

Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_simulation.py -q`.
The tests isolate integration with a real Station/Ledger and deterministic
scripted admissions; a separate case exercises the actual P controller with a
simple affine feeder. They cover exact energy completion/departure, delayed
reductions, private-state isolation, future bookings, stale telemetry, immutable
query witnesses, later pending increases under ideal feedback, lost-command
reservation boundaries, phase-summed AC exposure, nonconvergence stopping,
strict JSON expiry records, deterministic scientific outputs, and cap/reservation
containment. Run the complete project test suite before experiment release.
