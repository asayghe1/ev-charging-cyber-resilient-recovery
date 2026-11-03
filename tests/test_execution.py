"""Behavioral checks for the proposed trusted execution adapter."""
from dataclasses import FrozenInstanceError
import importlib
import pytest


@pytest.fixture
def api():
    try:
        return importlib.import_module("evrecovery.execution")
    except ModuleNotFoundError:
        pytest.fail("The requested station execution and reservation API is absent")


def command(api, generation, cap, start=0, end=100, issued=0):
    return api.Command(0, generation, issued, start, end, cap)


def test_uncertain_decrease_retains_eight_kw(api):
    ledger = api.Ledger(0, 8, 0)
    ledger.reserve(command(api, 1, 2))
    assert ledger.envelope(0, 50) == 8
    assert ledger.can_expand(0, 50)


def test_prospective_increase_reserves_eight_before_send(api):
    ledger = api.Ledger(0, 2, 0)
    ledger.reserve(command(api, 1, 8))
    assert ledger.envelope(0, 50) == 8
    assert not ledger.can_expand(0, 50)


def test_older_certificate_keeps_later_pending_generation(api):
    ledger = api.Ledger(0, 8, 0)
    ledger.reserve(command(api, 1, 2))
    ledger.reserve(command(api, 2, 7))
    ledger.observe(api.Certificate(0, 1, 2, 3, 100, 0))
    assert [c.generation for c in ledger.unresolved()] == [2]
    assert ledger.envelope(3, 50) == 7


def test_expired_late_command_is_fenced_without_applying(api):
    station = api.Station(0, 8, 6)
    assert not station.receive(command(api, 2, 2, end=10), 10)
    assert station.high_water == 2
    assert not station.receive(command(api, 1, 1), 11)
    assert station.power_cap(12) == 8
    assert station.certificate(12).generation == 0


def test_expiry_exposes_higher_fallback(api):
    station = api.Station(0, 8, 6)
    ledger = api.Ledger(0, 8, 6)
    cmd = command(api, 1, 2, end=10)
    ledger.reserve(cmd)
    station.receive(cmd, 0)
    ledger.observe(station.certificate(0))
    assert ledger.envelope(1, 10) == 2
    assert ledger.envelope(9, 11) == 6
    assert station.power_cap(10) == 6
    assert ledger.envelope(10, 20) == 6


def test_reboot_preserves_generation_and_active_profile(api):
    station = api.Station(0, 8, 6)
    station.receive(command(api, 2, 2), 0)
    station.reboot(5)
    assert not station.receive(command(api, 1, 8), 6)
    assert station.power_cap(6) == 2


def test_stale_rejection_does_not_change_active_cap(api):
    station = api.Station(0, 8, 0)
    station.receive(command(api, 4, 2), 0)
    assert not station.receive(command(api, 3, 8), 1)
    assert station.power_cap(1) == 2


def test_certificate_waits_for_actual_application(api):
    station = api.Station(0, 8, 0, actuation_s=4)
    station.receive(command(api, 1, 2), 0)
    assert station.power_cap(3) == 8
    assert station.certificate(3).generation == 0
    cert = station.certificate(4)
    assert (cert.generation, cert.cap_kw, cert.applied) == (1, 2, 4)


def test_future_start_is_enforced_and_reserved_only_when_possible(api):
    station = api.Station(0, 2, 0, actuation_s=2)
    ledger = api.Ledger(0, 2, 0)
    cmd = command(api, 1, 8, start=10, end=20)
    ledger.reserve(cmd)
    station.receive(cmd, 0)
    assert station.power_cap(9) == 2
    assert ledger.envelope(0, 10) == 2
    assert ledger.envelope(9, 11) == 8
    assert station.power_cap(10) == 8


def test_newer_reduction_cancels_older_scheduled_increase(api):
    station = api.Station(0, 8, 0)
    ledger = api.Ledger(0, 8, 0)
    older = command(api, 1, 8, start=20)
    newer = command(api, 2, 2)
    ledger.reserve(older)
    station.receive(older, 0)
    ledger.reserve(newer)
    station.receive(newer, 2)
    ledger.observe(station.certificate(2))
    assert ledger.unresolved() == ()
    assert ledger.envelope(2, 50) == 2
    assert station.power_cap(25) == 2


def test_rejected_new_generation_cancels_predecessor_pending_work(api):
    station = api.Station(0, 8, 0)
    station.receive(command(api, 1, 2, start=20), 0)
    assert not station.receive(command(api, 2, 1, end=1), 2)
    assert station.power_cap(25) == 8


def test_no_activation_if_actuation_completes_at_expiry(api):
    station = api.Station(0, 8, 0, actuation_s=5)
    assert not station.receive(command(api, 1, 2, end=10), 5)
    assert station.power_cap(11) == 8


def test_verified_expiry_clears_pending_but_retains_non_delivery_state(api):
    ledger = api.Ledger(0, 2, 6, initial_end=50)
    ledger.reserve(command(api, 1, 8, end=10))
    ledger.advance(10)
    assert ledger.unresolved() == ()
    assert ledger.can_expand(10, 20)
    assert ledger.envelope(10, 20) == 6


def test_stale_certificate_cannot_release_later_certified_cap(api):
    ledger = api.Ledger(0, 8, 0)
    ledger.observe(api.Certificate(0, 2, 7, 10, 100, 0))
    ledger.observe(api.Certificate(0, 1, 2, 2, 100, 0))
    assert ledger.envelope(10, 20) == 7


def test_initial_expiry_has_no_evidence_requirement(api):
    ledger = api.Ledger(0, 8, 6, initial_end=10)
    assert ledger.envelope(0, 10) == 8
    assert ledger.envelope(10, 20) == 6


def test_future_fallback_can_expand_despite_lower_current_cap(api):
    ledger = api.Ledger(0, 8, 10)
    ledger.reserve(command(api, 1, 2, end=10))
    assert ledger.envelope(0, 10) == 8
    assert ledger.envelope(10, 20) == 10
    assert not ledger.can_expand(10, 20)


def test_wrong_station_or_epoch_cannot_mutate_station(api):
    station = api.Station(0, 8, 0)
    assert not station.receive(api.Command(1, 9, 0, 0, 100, 2), 0)
    assert not station.receive(api.Command(0, 9, 0, 0, 100, 2, epoch=1), 0)
    assert station.high_water == 0


def test_commands_are_immutable_and_invalid_power_is_rejected(api):
    cmd = command(api, 1, 2)
    with pytest.raises(FrozenInstanceError):
        cmd.cap_kw = 8
    with pytest.raises(ValueError):
        command(api, 2, float('nan'))
    with pytest.raises(ValueError):
        command(api, 2, -1)


def test_wrong_station_certificate_cannot_release_reservation(api):
    ledger = api.Ledger(0, 8, 0)
    with pytest.raises(ValueError):
        ledger.observe(api.Certificate(1, 1, 2, 0, 100, 0))
    assert ledger.envelope(0, 10) == 8


def test_physical_time_cannot_run_backwards(api):
    station = api.Station(0, 8, 0)
    station.advance(10)
    with pytest.raises(ValueError):
        station.power_cap(9)


def test_older_certificate_retains_later_expired_fallback_history(api):
    ledger = api.Ledger(0, 8, 6)
    ledger.reserve(command(api, 1, 2, end=100))
    ledger.reserve(command(api, 2, 3, end=10))
    ledger.advance(15)
    ledger.observe(api.Certificate(0, 1, 2, 1, 100, 6))
    assert ledger.unresolved() == ()
    assert ledger.envelope(15, 20) == 6


def test_later_pending_cap_is_expanding_after_older_reduction_evidence(api):
    ledger = api.Ledger(0, 8, 0)
    ledger.reserve(command(api, 1, 2))
    ledger.reserve(command(api, 2, 7))
    assert ledger.can_expand(0, 50)
    ledger.observe(api.Certificate(0, 1, 2, 3, 100, 0))
    assert not ledger.can_expand(3, 50)
    assert ledger.known_generation == 1
    assert ledger.latest_generation == 2


def test_breakpoints_cover_issued_start_expiry_and_initial_end(api):
    ledger = api.Ledger(0, 2, 6, initial_end=30)
    ledger.reserve(command(api, 1, 8, start=10, end=20, issued=5))
    assert ledger.breakpoints(0, 40) == (0, 10, 20, 30, 40)


def test_jump_over_application_and_expiry_yields_fallback_certificate(api):
    station = api.Station(0, 8, 6, actuation_s=2)
    station.receive(command(api, 1, 2, start=10, end=20), 0)
    cert = station.certificate(25)
    assert (cert.generation, cert.cap_kw, cert.applied, cert.end) == (1, 6, 20, float('inf'))


@pytest.mark.parametrize('first_delivery', [None, 0, 3, 12])
@pytest.mark.parametrize('second_delivery', [None, 2, 5, 15])
@pytest.mark.parametrize('evidence_delay', [0, 4, 30])
def test_envelope_contains_delayed_execution_histories(api, first_delivery,
                                                      second_delivery, evidence_delay):
    """Removing predecessors too early or dropping fallback breaks containment.

    Delivery permutations exercise the adapter fence, without claiming the
    network model reorders messages inside an intact transport stream.
    """
    station = api.Station(0, 4, 6, initial_end=18, actuation_s=2)
    ledger = api.Ledger(0, 4, 6, initial_end=18)
    cmds = [command(api, 1, 8, start=3, end=10),
            command(api, 2, 2, start=4, end=14, issued=2)]
    ledger.reserve(cmds[0])
    certificates = []
    for t in range(31):
        ledger.advance(t)
        if t == 2:
            ledger.reserve(cmds[1])
        for cmd, delivery in zip(cmds, (first_delivery, second_delivery)):
            if delivery == t:
                station.receive(cmd, t)
        station.advance(t)
        if t in (0, 4, 8, 12, 16, 20):
            certificates.append((t + evidence_delay, station.certificate(t)))
        for delivery, cert in certificates:
            if delivery == t:
                ledger.observe(cert)
        assert station.power_cap(t) <= ledger.envelope(t, t)


def test_envelope_cannot_claim_retrospective_evidence(api):
    ledger = api.Ledger(0, 8, 0)
    ledger.observe(api.Certificate(0, 1, 2, 10, 100, 0))
    with pytest.raises(ValueError):
        ledger.envelope(0, 5)
