# Execution and reservation contract

This module specifies a **proposed trusted local adapter** used in the study.
It does not claim that unmodified OCPP charging stations or OCPP acceptance
responses provide this contract. Time is in seconds and power is in kW.

## Trust and physical assumptions

There is one authenticated command authority and no untracked profile writer.
The local adapter enforces one aggregate station cap, a persistent known
fallback, and a durable generation fence. Power is nonnegative and at most the
effective cap. The simulation uses an exact shared clock and zero overshoot.
Actuation takes the declared fixed `actuation_s`, which can be varied between
runs. This code does not implement clock-error intervals or overshoot margins.
Every command has a finite absolute validity interval. Initial profiles and
the persistent fallback may have infinite duration.

The simulated `reboot` retains the complete adapter state, including its
generation high-water mark, active profile and pending schedule. This is the
durable-state branch of the research contract; epoch remains zero. There is no
implementation of changing boot epochs or physical hardware persistence.
Likewise, the Python ledger models durable logical state, but does not itself
provide a disk journal, transactions, locks or crash recovery. The caller must
make prospective reserve-before-send atomic and durable in a deployment.

## Data and station API

`Command(station, generation, issued, start, end, cap_kw, epoch=0)` and
`Certificate(station, generation, cap_kw, applied, end, fallback_kw, epoch=0)`
are frozen dataclasses. IDs and generations are nonnegative integers; powers
are finite and nonnegative. Commands require finite times, `start < end`, and
`issued < end`. Generation zero identifies initial state; issued commands
use increasing positive generations. Certificates describe a completed
effective profile from `applied` to `end`, followed by the known fallback.

`Station(station, initial_cap_kw, fallback_kw, initial_end=inf, actuation_s=0)`
begins at time zero. Its operations are:

| Operation | Meaning |
| --- | --- |
| `receive(command, t) -> bool` | Advance physical state to `t`; reject wrong station/epoch or stale generation. For a new current-epoch generation, advance the fence and cancel predecessor pending work even if the command has expired or cannot complete before expiry. Otherwise schedule application at `max(start, t + actuation_s)`. Return acceptance, not application evidence. |
| `advance(t)` | Apply every local breakpoint through `t`; time cannot decrease. |
| `power_cap(t) -> float` | Advance and return effective physical cap. Simulator-only information. |
| `certificate(t) -> Certificate` | Advance and return truthful completed-state evidence. Generating this object does not deliver it to the central controller. |
| `reboot(t)` | Advance and preserve modeled durable state; no cap discontinuity or fence reset. |

Read-only diagnostic properties are `high_water`, `applied_generation`,
`pending` (a Command or None), and `next_transition` (next physical breakpoint
or infinity). These describe station truth and must not be passed to causal
policies except through an explicitly delivered evidence channel.

Receipt of a newer generation cancels an older pending operation. A rejected
new command leaves the existing effective cap unchanged. Receipt never labels
the new generation as applied. If an old scheduled application is due exactly
at the new receipt time, it completes in `advance(t)` before the receipt is
processed; the newly received command then follows its own actuation lag.

A pending command applies only if application completes strictly before its
absolute end. At that end, the cap switches to fallback exactly; lag never
extends an absolute validity interval. When active expiry and pending
application coincide, expiry is processed first and the new application is
effective immediately at that same time. At expiry the certificate preserves
the applied generation, sets `cap_kw` to fallback, `applied` to the expiry
time, and `end` to infinity. This does not claim that a pending newer
generation has applied or that it has ceased to be possible.

## Central ledger API

`Ledger(station, initial_cap_kw, fallback_kw, initial_end=inf)` contains only
initial state, commands the controller has reserved, elapsed time, and
received certificates. It does not consult Station, physical power, future
deliveries, future sessions, or the simulator queue.

| Operation | Meaning |
| --- | --- |
| `reserve(command)` | Record the command before transmission, including nondelivery and delayed execution possibilities. Repeating the identical pending command is idempotent; conflicting or older generations raise ValueError. The caller applies network admission and expansion-slot rules. |
| `observe(certificate)` | Incorporate authenticated completed evidence delivered by the caller. Ignore older applied generations and older snapshots of the same generation. Remove only generations at or below the certificate generation; retain every later possible command and fallback history. Wrong station/epoch/fallback raises ValueError. |
| `advance(t)` | Advance the controller clock monotonically and remove expired commands from unresolved operations. Their possible fallback histories remain represented. |
| `unresolved() -> tuple[Command,...]` | Outstanding unexpired commands, sorted by generation. |
| `envelope(t0,t1) -> float` | Supremum of possible cap on the half-open interval `[t0,t1)`; equal endpoints request the point value. Queries must begin no earlier than current ledger time and the latest observed certificate application. |
| `can_expand(t0,t1) -> bool` | True if no unresolved command adds power above the evidence-only base envelope in this interval. Expired uncertainty already present in the base commitment is included. This is the station's unresolved-expansion gate, not a network-feasibility decision. |
| `breakpoints(t0,t1) -> tuple[float,...]` | Sorted interval endpoints and relevant initial/certified expiry, pending activation and possible fallback times. Enables exact piecewise comparisons; `t1` may be infinity. |

Read-only properties `latest_generation` and `known_generation` give the
highest reserved/observed generation and latest certified applied generation.
Issuers can use `latest_generation + 1`. Observations must be delivered at
their declared arrival time by the simulation. Since `observe` has no
receive-time argument, it cannot check message transit causality itself.

The envelope is the maximum of the latest certified profile (or initial
profile), every pending command during its possible active interval, and
fallback after every still-possible expiry. Pending activation cannot precede
either `issued` or `start`. The ledger deliberately need not know the actual
message receipt time or actuation completion: retaining the old profile
until its own expiry or completed evidence safely covers transition lag.

An unconfirmed command's expiry can expose fallback even if the command has
been removed from `unresolved()`. Nondelivery can instead leave the initial
or certified cap in force. Both possibilities remain. An older certificate
must also retain the fallback possibility of a **later already-expired**
generation. This is why an expired-operation cleanup does not simply delete
every trace of that command.

`can_expand` is recomputed against available evidence. For example, a pending
7 kW command may initially be nonexpanding against an 8 kW base; after an
older 2 kW reduction certificate arrives, that same pending 7 kW command
occupies the expansion slot. Admission also needs a prospective envelope
comparison across **all affected future intervals**, including fallback.
A smaller numerical setpoint alone does not establish nonexpansion.

## Event integration and causality

The physical simulator must split energy integration at every delivery,
application, expiry, session, demand, and relevant network breakpoint. It
integrates the cap and local allocation that applied during the preceding
open interval before processing the next event. At a timestamp it advances
station local transitions, processes declared deliveries and creates any
reports due there. Report delivery is a separate queue event with its own
delay; every causal policy receives the same declared capabilities. A
controller decision advances its ledger clock, processes evidence already
delivered, evaluates admission, reserves a command and only then transmits.

These classes do not introduce a network ordering model. Tests permute
adapter delivery histories to verify generation fencing; those permutations
are not assertions that an intact TCP/WebSocket stream can reorder messages.
The parent simulation must justify delays, timeout/retry, disconnection,
reconnection and buffering and maintain its stated ordering rules.

For energy, kW multiplied by physical interval seconds is divided by 3600
to obtain kWh. Power-cap queries return a bound, not delivered charging power.
Actual allocation and session acceptance are implemented outside this module.

## Checked mechanisms and limitations

Behavioral tests cover uncertain reductions, prospective increases, older
certificates with later pending commands, late-expiry rejection, higher
fallback, durable reboot fencing, stale rejection, delayed physical
application, future activation, safe supersession, rejected-generation
fencing, retrospective-evidence rejection, and 48 combinations of delayed
delivery and certificate timing with direct envelope containment checks.

The representation is conservative: it can retain impossible combinations
of old caps and fallback histories. It does not enumerate exact correlated
histories, guarantee minimum charging service, or prove AC network limits.
Containment assumes trusted authentic certificates; production signing,
device implementation, hardware validation, storage durability, bounded
clock error and physical overshoot require separate work.
