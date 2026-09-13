"""TEST-only checks for the offline scenario harness, not physical RF evidence."""
import copy
import json
from pathlib import Path

import pytest

from scripts import simulation_scenarios as simulation


@pytest.fixture(scope="module")
def report_pair():
    options = dict(seeds=(1729,), long_seconds=0, history_cycles=101, cpu_budget_seconds=110)
    return simulation.run_suite(**options), simulation.run_suite(**options)


def test_production_scenarios_pass_and_are_repeatable(report_pair):
    first, second = report_pair
    assert first["status"] == second["status"] == "PASS"
    assert first == second  # no random event UUID or wall-clock receipts exported
    assert first["expected_scenarios"] == 12
    assert first["passed_scenarios"] == 12
    assert first["production_source_unchanged"] is True
    assert len(first["production_sha256"]) >= 8


def test_every_result_and_event_is_explicitly_synthetic_test(report_pair):
    report, _ = report_pair
    assert report["source_mode"] == "TEST" and report["synthetic"] is True
    assert "not an RF model" in " ".join(report["limitations"])
    for result in report["scenarios"]:
        assert result["source_mode"] == "TEST" and result["synthetic"] is True
        assert result["status"] == "PASS"
        for invariant in result["invariants"]:
            assert invariant["source_mode"] == "TEST" and invariant["synthetic"] is True
        for event in result["synthetic_event_examples"]:
            assert event["source_mode"] == "TEST" and event["synthetic"] is True
            assert "event_id" not in event
    serialized = json.dumps(report)
    assert '"source_mode": "LIVE"' not in serialized
    assert '"source_mode": "REPLAY"' not in serialized


def test_long_virtual_run_exercises_history_eviction_and_bounds(report_pair):
    report, _ = report_pair
    result = next(r for r in report["scenarios"] if r["scenario"] == "long_virtual_time_bounded_memory")
    assert result["synthetic_alarm_events"] == 101
    assert result["peak_memory_items"]["events"] == 100
    assert result["peak_memory_items"]["graph"] == 300
    assert result["virtual_elapsed_s"] >= 505
    assert result["final_alarm_state"] == "ARMED"
    for key, value in result["peak_memory_items"].items():
        assert value <= simulation.MEMORY_BOUNDS[key]


def test_report_covers_required_fault_and_alarm_invariants(report_pair):
    report, _ = report_pair
    named = {r["scenario"]: r for r in report["scenarios"]}
    assert named["quiet_baseline"]["synthetic_alarm_events"] == 0
    assert named["short_disturbance_debounce"]["synthetic_alarm_events"] == 0
    assert named["sustained_shape_change_latch"]["synthetic_alarm_events"] == 1
    assert named["acknowledge_quiet_rearm"]["synthetic_alarm_events"] == 2
    assert named["disarmed_suppression"]["synthetic_alarm_events"] == 1
    assert named["missing_packets"]["final_health"] == "stale"
    assert named["missing_packets"]["sequence_gap_estimate"] == 12
    assert named["malformed_rejection_burst"]["invalid_packets"] == 30
    assert named["malformed_rejection_burst"]["baseline_ready"] is False
    assert named["disconnect_during_alarm"]["final_health"] == "disconnected"
    assert named["fault_recovery_recalibration"]["synthetic_alarm_events"] == 1
    assert named["sequence_device_clock_wrap"]["sequence_gap_estimate"] == 0


@pytest.mark.parametrize("kwargs", [
    {"seeds": ()}, {"seeds": tuple(range(9))}, {"seeds": (-1,)},
    {"seeds": (True,)}, {"long_seconds": 3601}, {"long_seconds": -1},
    {"history_cycles": 100}, {"history_cycles": 121}, {"cpu_budget_seconds": 111},
])
def test_resource_bounds_reject_unbounded_requests(kwargs):
    with pytest.raises(ValueError):
        simulation.run_suite(**kwargs)


def test_cpu_budget_exhaustion_never_reports_pass(monkeypatch):
    def exhausted(*args, **kwargs):
        raise simulation.BudgetExceeded("TEST-only forced budget exhaustion")
    monkeypatch.setattr(simulation.VirtualStream, "_observe", exhausted)
    report = simulation.run_suite(seeds=(1,), long_seconds=0, history_cycles=101)
    assert report["status"] == "INCOMPLETE"
    assert report["passed_scenarios"] == 0
    assert report["completed_scenarios"] == 0
    assert report["reported_scenarios"] == 1
    assert report["scenarios"][0]["status"] == "INCOMPLETE"


def test_evidence_writer_refuses_unowned_paths(tmp_path):
    with pytest.raises(ValueError, match="detector-"):
        simulation.write_report({"source_mode": "TEST", "synthetic": True}, tmp_path / "unsafe.json")
    assert not (tmp_path / "unsafe.json").exists()


def test_harness_uses_production_detector_and_test_adapter_without_sources_opened():
    from threshold.adapters import TestAdapter
    from threshold.detector import Detector
    stream = simulation.VirtualStream("type-check", 1729)
    assert type(stream.detector) is Detector
    assert type(stream.source) is TestAdapter
    assert stream.step(motion=False) is None
    assert stream.detector.source_mode == "TEST"
