from uuid import uuid4

import pytest

from threshold.adapters import TestAdapter
from threshold.detector import Detector, TransitionError
from threshold.parser import magnitudes


class Stream:
    def __init__(self):
        self.now = 100.0
        self.session = str(uuid4())
        self.source = TestAdapter(self.session)
        self.detector = Detector(self.session, "TEST")
        self.detector.set_connection(True, "test", self.now)

    def feed(self, seconds, motion=False, first_word=None):
        events = []
        for _ in range(round(seconds * 30)):
            self.now += 1 / 30
            record = self.source.sample(self.now, motion=motion)
            if first_word is not None:
                record["first_word_invalid"] = bool(first_word)
                record["magnitudes"] = magnitudes(record["csi_raw"], bool(first_word))
            event = self.detector.ingest(record, self.now)
            if event:
                events.append(event)
        return events

    def calibrate(self):
        self.feed(0.5)
        self.detector.control("calibrate", self.now)
        self.feed(5.2)
        assert self.detector.ready
        assert self.detector.alarm_state == "READY"

    def control(self, action, threshold=None):
        return self.detector.control(action, self.now, threshold)


def test_empty_calibration_requires_time_samples_then_ready():
    stream = Stream()
    with pytest.raises(TransitionError):
        stream.control("arm")
    stream.feed(0.5)
    stream.control("calibrate")
    stream.feed(4.5)
    assert not stream.detector.ready
    assert stream.detector.alarm_state == "CALIBRATING"
    stream.feed(0.7)
    assert stream.detector.ready
    assert stream.detector.calibration_progress == 1
    stream.control("arm")
    assert stream.detector.alarm_state == "ARMED"


def test_quiet_startup_unarmed_motion_and_brief_motion_do_not_alarm():
    stream = Stream()
    assert stream.feed(2) == []
    assert not stream.detector.events
    stream.calibrate()
    assert stream.feed(2, motion=True) == []
    stream.feed(1)
    stream.control("arm")
    assert stream.feed(0.3, motion=True) == []
    assert stream.feed(1) == []
    assert stream.detector.alarm_state == "ARMED"


def test_sustained_debounce_latch_quiet_interval_and_dedup():
    stream = Stream()
    stream.calibrate()
    stream.control("arm")
    events = stream.feed(2, motion=True)
    assert len(events) == 1
    assert events[0]["type"] == "motion_near_entrance"
    assert stream.detector.alarm_state == "ALARM"
    assert stream.feed(3, motion=True) == []
    stream.control("acknowledge")
    assert stream.detector.events[0]["acknowledged"] is True
    assert stream.feed(3, motion=True) == []
    stream.feed(1)
    assert stream.feed(1, motion=True) == []  # insufficient quiet interval
    stream.feed(3)
    assert len(stream.feed(2, motion=True)) == 1
    assert len(stream.detector.events) == 2


def test_disarm_prevents_future_events_and_silences_latch():
    stream = Stream()
    stream.calibrate()
    stream.control("arm")
    stream.feed(2, motion=True)
    stream.control("disarm")
    assert stream.detector.active_event_id is None
    assert stream.detector.alarm_state == "DISARMED"
    assert stream.feed(3, motion=True) == []
    assert len(stream.detector.events) == 1


def test_stale_fault_invalidates_baseline_and_never_blind_rearms():
    stream = Stream()
    stream.calibrate()
    stream.control("arm")
    stream.now += 1.1
    stream.detector.tick(stream.now)
    assert stream.detector.health_status == "stale"
    assert not stream.detector.ready
    assert stream.detector.alarm_state == "DISARMED"
    stream.feed(1)
    assert stream.detector.health_status == "healthy"
    with pytest.raises(TransitionError):
        stream.control("arm")
    stream.calibrate()
    stream.control("arm")


def test_disconnected_health_stays_visible_with_latched_alarm():
    stream = Stream()
    stream.calibrate()
    stream.control("arm")
    stream.feed(2, motion=True)
    stream.detector.set_connection(False, "USB unplugged", stream.now)
    state = stream.detector.snapshot(stream.now)
    assert state["alarm_state"] == "ALARM"
    assert state["health"]["status"] == "disconnected"
    assert not state["calibration"]["ready"]
    with pytest.raises(TransitionError):
        stream.control("acknowledge")
    stream.control("disarm")
    assert stream.detector.alarm_state == "DISARMED"


def test_calibration_gap_cancels_and_threshold_change_requires_recalibration():
    stream = Stream()
    stream.feed(0.5)
    stream.control("calibrate")
    stream.feed(1)
    stream.now += 1.1
    stream.detector.tick(stream.now)
    assert stream.detector.alarm_state == "DISARMED"
    stream.calibrate()
    stream.control("set_threshold", 4)
    assert not stream.detector.ready
    for value in (True, float("nan"), 0, 101):
        with pytest.raises(TransitionError):
            stream.control("set_threshold", value)


def test_first_word_invalid_toggle_preserves_feature_layout_and_baseline():
    stream = Stream()
    stream.calibrate()
    stream.control("arm")
    stream.feed(1, first_word=True)
    stream.feed(1, first_word=False)
    assert stream.detector.ready
    assert stream.detector.alarm_state == "ARMED"
    assert not stream.detector.events


def test_duplicate_sequences_rejected_and_gaps_only_estimates():
    stream = Stream()
    stream.feed(1)
    record = stream.source.sample(stream.now + 1 / 30, motion=False)
    record["sequence"] = stream.detector.sequence
    stream.detector.ingest(record, stream.now + 1 / 30)
    assert stream.detector.invalid_packets == 1
    record["sequence"] += 4
    stream.detector.ingest(record, stream.now + 2 / 30)
    assert stream.detector.dropped_packets_estimate == 3
    assert record["quality"]["missing_packets_estimate"] == 3


def test_motion_during_calibration_is_rejected_not_a_baseline():
    stream = Stream()
    stream.feed(0.5)
    stream.control("calibrate")
    stream.feed(5.2, motion=True)
    assert not stream.detector.ready
    assert stream.detector.alarm_state == "DISARMED"
    assert "not stable" in stream.detector.calibration_detail


def test_excess_invalid_data_blocks_armed_detection():
    stream = Stream()
    stream.calibrate()
    stream.control("arm")
    for _ in range(30):
        stream.detector.reject(stream.now, "Wrong transmitter")
    assert stream.detector.health_status == "degraded"
    assert not stream.detector.ready
    assert stream.detector.alarm_state == "DISARMED"
