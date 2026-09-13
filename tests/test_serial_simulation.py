"""Pure harness safety checks. These tests never open even a virtual serial port."""
import pytest

import asyncio
from types import SimpleNamespace

from scripts.serial_simulation import (
    OwnedPTY, TestSerialAdapter, SimulationFailure, mark_test_record, main,
    require_active_samples, producer_cleanup_error, audit_recording, block_result, finish_result,
)
from threshold.recorder import Recorder
from scripts.virtual_sensor import csv25_line
from threshold.adapters import LiveAdapter
from threshold.parser import ParserConfig, ParseError, parse_line, validate_live_record


def parsed_fixture():
    return parse_line(csv25_line(1, 50000), ParserConfig("02:ca:5c:ad:1a:01", 6), "synthetic-session")


def test_synthetic_serial_records_are_downgraded_without_mutating_parser_result():
    original = parsed_fixture()
    assert original["source_mode"] == "LIVE"  # parser unit fixture, never recorded
    marked = mark_test_record(original)
    assert marked["source_mode"] == "TEST"
    assert marked["quality"]["profile"] == "synthetic-raw-int8-v1"
    assert "synthetic_virtual_serial_not_hardware" in marked["quality"]["warnings"]
    assert marked["simulation_provenance"]["radio_sensing"] is False
    assert marked["csi_raw"] == original["csi_raw"]
    assert original["source_mode"] == "LIVE"
    assert original["quality"]["profile"] == "esp32-s3-raw-int8-v1"
    with pytest.raises(ParseError):
        validate_live_record(marked)


def test_no_serial_open_is_possible_without_process_owned_pty(monkeypatch):
    def forbidden_open(_self):
        pytest.fail("Underlying serial open must not be reached")
    monkeypatch.setattr(LiveAdapter, "_open", forbidden_open)
    owner = OwnedPTY()  # constructor opens nothing
    adapter = TestSerialAdapter("synthetic-session", owner)
    adapter.port = "/dev/cu.NEVER_OPEN_THIS"
    with pytest.raises(OSError):
        adapter._open()
    assert owner.created == []
    assert adapter.opens == 0


@pytest.mark.asyncio
async def test_adapter_marks_source_before_emit_and_marks_health_detail(monkeypatch):
    async def fake_run(self, emit, reject, status):
        await status(True, "fixture connected")
        await emit(parsed_fixture())
    monkeypatch.setattr(LiveAdapter, "run", fake_run)
    adapter = TestSerialAdapter("synthetic-session", OwnedPTY())
    records, statuses = [], []
    async def emit(record):
        records.append(record)
    async def reject(reason):
        pytest.fail("No rejection expected in this fixture")
    async def status(connected, detail):
        statuses.append((connected, detail))
    await adapter.run(emit, reject, status)
    assert len(records) == 1 and records[0]["source_mode"] == "TEST"
    assert statuses == [(True, "TEST virtual serial: fixture connected")]
    assert adapter.emitted == 1 and adapter.opens == 0


def test_cli_has_no_physical_port_option(monkeypatch):
    monkeypatch.setattr("sys.argv", ["serial_simulation", "--port", "/dev/cu.NEVER_OPEN_THIS"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


@pytest.mark.asyncio
async def test_late_producer_failure_blocks_final_activity_and_cleanup():
    async def fail_late():
        raise RuntimeError("injected synthetic producer failure")
    task = asyncio.create_task(fail_late())
    await asyncio.sleep(0)  # Schedule the injected error; no wall-clock wait or port.
    with pytest.raises(SimulationFailure):
        require_active_samples(task, 100, 132, minimum=16)
    outcome = (await asyncio.gather(task, return_exceptions=True))[0]
    assert producer_cleanup_error(False, outcome) == "RuntimeError"


@pytest.mark.parametrize("advance", [0, 15])
def test_running_producer_without_enough_fresh_samples_is_not_success(advance):
    pending = SimpleNamespace(done=lambda: False)
    with pytest.raises(SimulationFailure):
        require_active_samples(pending, 100, 100 + advance, minimum=16)


def test_running_producer_with_fresh_samples_passes():
    require_active_samples(SimpleNamespace(done=lambda: False), 100, 132, minimum=16)


@pytest.mark.parametrize("cancel_requested,outcome,expected", [
    (False, OSError("injected"), "OSError"),
    (False, None, "UnexpectedProducerExit"),
    (False, asyncio.CancelledError(), "CancelledError"),
    (True, None, "UnexpectedProducerExit"),
    (True, asyncio.CancelledError(), None),
])
def test_only_intended_producer_cancellation_is_benign(cancel_requested, outcome, expected):
    assert producer_cleanup_error(cancel_requested, outcome) == expected


def fill_test_raw(recorder, count):
    marked = mark_test_record(parsed_fixture())
    for _ in range(count):
        recorder.raw(marked)


def test_raw_recording_failure_after_good_prefix_blocks_success(tmp_path):
    recorder = Recorder(tmp_path, "synthetic-raw-fault")
    fill_test_raw(recorder, 101)
    recorder.max_bytes = recorder.bytes_written
    fill_test_raw(recorder, 1)  # Real Recorder latches a raw budget/write error.
    assert recorder.raw_error is not None
    with pytest.raises(SimulationFailure):
        audit_recording(recorder, 102, set())


def test_event_only_recording_fault_blocks_success_despite_complete_raw(tmp_path):
    recorder = Recorder(tmp_path, "synthetic-event-fault", event_max_bytes=1)
    fill_test_raw(recorder, 101)
    recorder.event({"event_id": "synthetic-event", "source_mode": "TEST"})
    assert recorder.raw_error is None and recorder.event_error is not None
    with pytest.raises(SimulationFailure):
        audit_recording(recorder, 101, {"synthetic-event"})


def test_good_prefix_with_missing_accepted_samples_is_not_complete(tmp_path):
    recorder = Recorder(tmp_path, "synthetic-truncated")
    fill_test_raw(recorder, 100)
    assert recorder.error is None
    with pytest.raises(SimulationFailure):
        audit_recording(recorder, 101, set())


@pytest.mark.parametrize("journal_ids", [["a"], ["a", "a", "b"]])
def test_missing_or_duplicate_event_journal_blocks_success(tmp_path, journal_ids):
    recorder = Recorder(tmp_path, "synthetic-event-count")
    fill_test_raw(recorder, 1)
    for event_id in journal_ids:
        recorder.event({"event_id": event_id, "source_mode": "TEST"})
    with pytest.raises(SimulationFailure):
        audit_recording(recorder, 1, {"a", "b"})


def test_complete_test_journal_counts_and_replay_provenance(tmp_path):
    recorder = Recorder(tmp_path, "synthetic-complete")
    fill_test_raw(recorder, 1)
    recorder.event({"event_id": "synthetic-event", "source_mode": "TEST"})
    result = audit_recording(recorder, 1, {"synthetic-event"})
    assert result == {"recorded": 1, "replay_rejected": 1, "recorded_events": 1}


@pytest.mark.parametrize("prior_status", ["UNKNOWN", "FAIL"])
def test_later_good_evidence_never_promotes_blocking_status(prior_status):
    result = {"status": prior_status}
    finish_result(result, scenarios_passed=True, producer_stopped_safely=True, recording_verified=True)
    assert result["status"] == prior_status


@pytest.mark.parametrize("proof,expected", [
    ((True, True, True), "PASS"),
    ((False, True, True), "UNKNOWN"),
    ((True, False, True), "UNKNOWN"),
    ((True, True, False), "UNKNOWN"),
])
def test_final_pass_requires_all_completion_proof(proof, expected):
    result = {"status": "RUNNING"}
    finish_result(result, scenarios_passed=proof[0], producer_stopped_safely=proof[1], recording_verified=proof[2])
    assert result["status"] == expected


def test_recording_observation_unknown_is_not_relabelled_pass_or_known_success():
    result = {"status": "UNKNOWN"}
    block_result(result, "FAIL", recording_error_type="Injected")
    finish_result(result, scenarios_passed=True, producer_stopped_safely=True, recording_verified=True)
    assert result["status"] == "UNKNOWN"
