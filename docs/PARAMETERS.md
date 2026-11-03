# Frozen study parameters

This record describes the implemented study, not optional settings from the original research outline. `results/freeze.json`, the resolved scenarios in each `*.config.json`, and the source hashes are the machine-readable execution provenance. Time is in **seconds**, power in **kW**, and energy in **kWh**; integration uses `kW × seconds / 3600`. Voltage and current loading are per unit.

## Electrical and controller settings

| Parameter | IEEE123 main study | IEEE13 transfer cases |
| --- | --- | --- |
| Charging groups | Six single-phase MV equivalents | Six single-phase MV equivalents |
| Bus.phase, index order | 48.1, 76.2, 83.3, 67.1, 95.2, 105.3 | 632.1, 671.2, 675.3, 680.1, 645.2, 611.3 |
| Available group maximum | 300 kW | 200 kW |
| Session acceptance limit | 22 kW | 22 × 2/3 kW |
| Affine charging operating point | 100 kW per group | 50 kW per group |
| Affine voltage tightening | 0.005 pu | 0.002 pu |
| Affine transformer loading limit | 0.98 pu | 0.98 pu |
| Physical voltage limits | 0.95 to 1.05 pu | Same |
| Physical transformer current limit | 1.0 pu | Same |
| Background center | 0.9 and 1.0 | 1.0 in transfer sensitivity |
| Background uncertainty halfwidth | 0.01 | 0.01 |

Every original feeder load's kW and kvar is multiplied by 0.8 before scenario scaling. Thus a center of 0.9 represents 72% of published background demand, and 1.0 represents 80%. At initialization regulator forward targets are 120 V; the zero-EV nominal solution supplies frozen taps, after which controls are disabled. Source capacitor states and source transformer nameplates are preserved. EV groups are unity-power-factor, constant-kW, wye-connected equivalents with `vminpu=0.5` and `vmaxpu=1.5`. They include no secondary cable, charging transformer, protection, or voltage-dependent acceptance model. Each `FeederModel` has an independent engine context.

Electrical violation duration uses strictly positive raw voltage or transformer-current excess beyond the stated physical limits, with no added numerical tolerance. An unsafe episode has `violation_seconds > 0`. The separate ledger containment check uses a 1e−7 kW tolerance. Calibration sampling below uses its own explicitly coded 1e−9 electrical comparison tolerance; it does not change the physical replay definition.

The physical background is piecewise constant on control intervals: `center + 0.008 sin(0.17 floor(t/30) + 0.37 seed)`, inside the controller's declared center ±0.01 box. Signed sensitivities use centered 1 kW charging differences at the nonzero operating point and a centered 0.001 background-scale step. Admission uses the positive charging coefficients and sign-correct background endpoint for each row. Rows cover all modeled phase-node voltages and source-nameplate transformer winding terminal-phase current magnitudes, excluding neutrals. Source files contain no explicit line ampacities, so line thermal security is outside the reported metric and proposition. See `docs/FEEDER.md` for source URLs, ratings, and caveats.

| Shared timing/control parameter | Value or rule |
| --- | --- |
| Episode duration | 1,800 s, or 30 min |
| Control and telemetry sampling periods | 30 s each |
| Main actuation lag | 1 s |
| Normal one-way channel delay | `2 + 0.15 × ((round(10t) + 17i + 31seed) mod 7)` s, implemented with integer rounding |
| Affected groups | First three zero-indexed groups by default |
| Staggered disturbance interval | `[270 + 8i, 690 + 13i)` s for affected group i |
| Extra certificate delay | 45 s in the applicable stress law |
| Delayed-increase extension | 180 s after that group's disturbance end |
| Initial/common lease absolute end | 2,100 s |
| T4 affected temporary-profile end | 900 s for commands issued before 900 s |
| Persistent fallback | 0.65 × that group's initially established cap |
| Command-change deadband | 0.05 kW |
| Directional impairment classification | Reduction below −0.1 kW or increase above 0.1 kW relative to previous requested cap |
| Adapter clocks and tracking | Exact shared clock; zero modeled overshoot; hard absolute expiry |
| RPC availability | At most one command or query per group per control tick |
| B1 selected headroom | 0.20; scales the robust affine RHS by 0.80 |

All policies share bookings revealed at arrival, delayed per-session energy and group-power telemetry, forward-channel laws, physical adapters, and local earliest-departure allocation. A completed vehicle remains in the booking set until departure; telemetry conveys actual completion. B0 uses nominal requested caps; B1 replans using measured/last-requested bounds and the selected headroom; B2 retains static initial quotas. B3 uses the full evidence ledger and per-group stop-and-wait. B4 is a DCS-inspired minimum-peak quota-sharing adaptation with that serialization, not a reproduction of the published DCS architecture. P allows admitted whole-span nonexpanding supersession; R uses the same controller with immediate **applied** certificates, while retaining ordinary forward-channel uncertainty and energy telemetry. R is not an omniscient performance upper bound.

## Synthetic sessions

Each cohort has 156 sessions, 26 per group, generated with NumPy `default_rng(seed)`. Uniform draws are independent in the source generation order. For request fraction a, acceptance P, arrival u, and departure v, request energy is `aP(v−u)/3600`.

| Session population per group | Count, groups 0–2 / groups 3–5 | Arrival interval (s) | Departure interval (s) | Request fraction |
| --- | --- | --- | --- | --- |
| Initially present | 16 / 5 | 0 | Uniform [1500,1795) | Uniform [0.61,0.87) |
| Mid-episode, groups 0–2 | 6 / none | Uniform [390,600) | Uniform [1180,1550) | Uniform [0.64,0.91) |
| Mid-episode, groups 3–5 | none / 17 | Uniform [300,480) | Uniform [900,1260) | Uniform [0.64,0.91) |
| Late arrivals | 4 / 4 | Uniform [1040,1200) | Uniform [1650,1795) | Uniform [0.55,0.80) |

## Disturbance laws

| Condition | Implemented change from normal delay |
| --- | --- |
| Clean | Normal delays only |
| T1 | Messages of every modeled kind sent during the group's staggered interval are lost |
| T2 | Reduction commands sent during the interval arrive after its end plus normal delay; certificate and acceptance replies receive the extra 45 s delay |
| T3 | Increase commands sent during the interval arrive after its end plus 180 s plus normal delay; certificates receive the extra 45 s delay |
| T4 | Affected-group commands/query requests sent in [780,990) s arrive at 990 s plus normal delay; other message kinds in that window receive the extra 45 s delay; temporary leases expire at 900 s as specified above |

These are distinct stress patterns, not matched random/adversarial counterfactuals. Pair policies within a condition. Delayed commands represent application retries following timeout/reconnection, not frame reordering within an intact TCP stream. Trusted generation fencing and truthful post-application certificates are added adapter assumptions.

## Evaluation and calibration

| Set | Frozen layout | Episodes or samples |
| --- | --- | ---: |
| Main | Seeds 1000–1029 × five conditions × two centers × seven policies | 2,100 episodes |
| Sensitivity | Seeds 2000–2009 × twelve settings × seven policies | 840 episodes |
| Mechanism ablations | Seeds 1000–1004 × T1–T4 × center 1.0 × four variants | 80 episodes |
| Initial evaluation total | Main + sensitivity + ablations | 3,020 episodes |
| Post-review retry follow-up | New seeds 3000–3029 × T1–T4 × two centers × B3/P/B3_retry/P_retry | 960 episodes |
| Combined evaluation total | Initial evaluation + separate retry follow-up | 3,980 episodes |
| Representative trace | Protocol-selected main T1/1.0 seed, B0/B3/P/R descriptive replay | Four separate reruns |
| Feedback development | Seeds 101–105 × five conditions × two centers per margin | 50 cases per margin |

The twelve sensitivity settings are certificate-delay extensions of 15/90/180 s, affected-group counts of 1/2/4, actuation lags of 0/5/15 s, and IEEE13 under T1/T2/T3. The first nine use IEEE123, T3, and background center 1.0. Unchanged parameters retain their main values. Four ablations omit pending-command accounting, omit fallback accounting, treat acceptance as application, or replace locational affine rows by an aggregate budget. Matching full-P episodes come from main results rather than additional formal runs.

Main impaired outcomes have 240 episodes per policy and clean outcomes have 60. For primary P-minus-B3 service effects, average the eight impaired conditions within each seed and bootstrap the 30 seed means with 10,000 draws and seed 697213; report 95% percentile intervals. Pattern comparisons average two centers per seed. Sensitivity and ablations have ten and five seed blocks. Timepoints are not independent samples.

In the primary study, P/B3/B4 query unresolved execution without retransmitting commands. The separate retry extension adds one attempt every 60 seconds for the latest currently unresolved generation if no new logical command uses that group's control-tick RPC. It preserves generation, station, epoch, cap, absolute start and absolute end. Only the transport issue timestamp changes; no second reservation or lease extension occurs. A retry replaces that tick's query, and original per-generation command direction is retained for T2/T3 selectivity. The declared factory lease ends are nondecreasing; arbitrary shorter custom leases are outside this follow-up. Both variants use exactly the same retry capability. Follow-up inference averages eight cases within each of 30 new cohort blocks with the same bootstrap settings. See `docs/RETRY_PROTOCOL.md` for the immutable design, source guards and implementation validation.

B1's candidate headrooms 0.05, 0.10, and 0.20 yielded 6, 8, and 0 unsafe development episodes among their respective 50 common cases. The prespecified choice is the smallest zero-unsafe candidate, 0.20. Pilot/tuning outputs and any earlier incomplete metadata pilot remain separate from evaluation.

The feeder margin sweep crosses voltage margins 0.002/0.005/0.008 pu with transformer affine loading limits 0.98/0.96. Each feeder uses six independent uniform charging powers on [0,300) kW for IEEE123 or [0,200) kW for IEEE13. For each center 0.9/1.0, calibration draws 3,000 vectors with seeds 17701/17801; independent validation draws 10,000 with seeds 27701/27801. Realized background is uniform within center ±0.01, while admission checks the full background box with signed coefficients. Selection uses the smallest voltage margin passing both calibration centers, then the largest passing loading limit, yielding the parameters in the first table. The saved `margin_validation.json` includes every candidate's admitted counts, violations, and residual errors. These finite samples provide empirical calibration, not certified nonlinear error bounds.
