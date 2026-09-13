"""Explainable amplitude-shape/temporal detector. Thresholds are hypotheses."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from uuid import uuid4

import numpy as np

from .parser import utc_now


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class DetectorConfig:
    threshold: float = 3.0
    calibration_seconds: float = 5.0
    calibration_min_samples: int = 90
    minimum_rate_hz: float = 10.0
    stale_seconds: float = 1.0
    debounce_seconds: float = 0.7
    quiet_seconds: float = 2.0
    shape_noise_floor: float = 0.025
    history_size: int = 100


class Detector:
    def __init__(self, session_id: str, source_mode: str, area_name: str = "Front entrance", config: DetectorConfig | None = None):
        self.config = config or DetectorConfig()
        self.session_id, self.source_mode, self.area_name = session_id, source_mode, area_name
        self.threshold = self.config.threshold
        self.alarm_state = "DISARMED"
        self.connected = False
        self.connection_detail = "Waiting for source"
        self.started_at = None
        self.last_sample_at = None
        self.last_sample_time = None
        self.valid_packets = self.invalid_packets = self.dropped_packets_estimate = 0
        self.arrivals = deque(maxlen=600)
        self.outcomes = deque(maxlen=1200)
        self.sequence = None
        self.transmitter_mac = None
        self.channel = None
        self.layout = None
        self.baseline = self.scale = self.previous_shape = None
        self.calibration_samples = deque(maxlen=1500)
        self.calibration_start = None
        self.calibration_detail = "Not calibrated"
        self.calibration_progress = 0.0
        self.score_window = deque(maxlen=150)
        self.activity_score = None
        self.graph = deque(maxlen=300)
        self.graph_at = -math.inf
        self.events = deque(maxlen=self.config.history_size)
        self.active_event_id = None
        self.above_since = self.quiet_since = None
        self.trigger_ready = True
        self.health_status, self.health_detail = "connecting", "Waiting for source"

    @property
    def ready(self):
        return self.baseline is not None

    def set_connection(self, connected: bool, detail: str, now: float):
        self.connected, self.connection_detail = connected, detail
        self.tick(now)

    def invalidate(self, reason: str):
        self.baseline = self.scale = self.previous_shape = None
        self.calibration_samples.clear()
        self.calibration_start = None
        self.calibration_progress = 0.0
        self.calibration_detail = reason + "; calibrate an empty area again"
        self.score_window.clear()
        self.activity_score = None
        self.above_since = self.quiet_since = None
        self.trigger_ready = False
        if self.alarm_state != "ALARM":
            self.alarm_state = "DISARMED"

    def _prune(self, now: float):
        while self.arrivals and self.arrivals[0] < now - 2.0:
            self.arrivals.popleft()
        while self.outcomes and self.outcomes[0][0] < now - 2.0:
            self.outcomes.popleft()
        while self.score_window and self.score_window[0][0] < now - 0.35:
            self.score_window.popleft()

    def rate(self, now: float) -> float:
        if len(self.arrivals) < 2:
            return 0.0
        span = now - self.arrivals[0]
        return (len(self.arrivals) - 1) / span if span > 0 else 0.0

    def tick(self, now: float):
        if self.started_at is None:
            self.started_at = now
        self._prune(now)
        age = None if self.last_sample_time is None else max(0.0, now - self.last_sample_time)
        if not self.connected:
            status, detail = "disconnected", self.connection_detail
        elif age is None:
            status, detail = "connecting", "Waiting for valid CSI samples"
        elif age > self.config.stale_seconds:
            status, detail = "stale", "No fresh valid CSI; alarms blocked"
        elif len(self.arrivals) < 5 or self.rate(now) < self.config.minimum_rate_hz:
            status, detail = "degraded", "Insufficient valid packet rate; alarms blocked"
        elif len(self.outcomes) >= 5 and sum(not valid for _, valid in self.outcomes) / len(self.outcomes) > 0.2:
            status, detail = "degraded", "Too many rejected packets; alarms blocked"
        else:
            status, detail = "healthy", "Fresh valid CSI stream" if self.source_mode != "TEST" else "Fresh synthetic TEST stream; not board data"
        self.health_status, self.health_detail = status, detail
        if status != "healthy":
            self.above_since = self.quiet_since = None
            if self.ready or self.alarm_state == "CALIBRATING":
                self.invalidate(detail)
        if self.calibration_start is not None:
            elapsed = max(0.0, now - self.calibration_start)
            self.calibration_progress = min(0.99, elapsed / self.config.calibration_seconds, len(self.calibration_samples) / self.config.calibration_min_samples)
            if elapsed > self.config.calibration_seconds * 3:
                self.invalidate("Calibration timed out without adequate samples")

    def reject(self, now: float, reason: str = "Rejected CSI"):
        self.invalid_packets += 1
        self.outcomes.append((now, False))
        self.tick(now)

    def ingest(self, record: dict, now: float, *, event_allowed: bool = True) -> dict | None:
        # The parser/replay validator is the trust boundary; defend numeric shape too.
        try:
            mag = np.asarray(record["magnitudes"], dtype=float)
            # Always exclude the unreliable first two complex bins. The parser
            # already excluded them when first_word_invalid was set.
            if not record.get("first_word_invalid", False):
                mag = mag[2:]
        except (ValueError, KeyError, TypeError):
            self.reject(now)
            return None
        if mag.ndim != 1 or len(mag) < 8 or len(mag) > 512 or not np.isfinite(mag).all() or (mag < 0).any() or np.median(mag) < 1:
            self.reject(now, "Unusable CSI amplitude shape")
            return None
        if record.get("source_mode") != self.source_mode or record.get("quality", {}).get("valid") is not True:
            self.reject(now, "Wrong source mode or invalid sample")
            return None
        if self.last_sample_time is not None and now <= self.last_sample_time:
            self.reject(now, "Non-increasing sample clock")
            return None
        # Detect a gap BEFORE accepting the new sample. Recovery never hides faults.
        self.tick(now)
        layout = (len(mag), record["sig_mode"], record["mcs"], record["bandwidth"], record["ant"], record["transmitter_mac"], record["channel"])
        if self.layout is not None and layout != self.layout:
            self.invalidate("CSI layout or transmitter changed")
            self.sequence = None
        self.layout = layout
        seq = record["sequence"]
        missing = None
        if self.sequence is not None:
            delta = (seq - self.sequence) % (2**32)
            if delta == 0:
                self.reject(now, "Duplicate sequence")
                return None
            if delta >= 2**31:
                self.invalidate("Transmitter sequence reset or reordered")
                missing = None
            else:
                missing = delta - 1
                self.dropped_packets_estimate += missing
        self.sequence = seq
        record["quality"]["missing_packets_estimate"] = missing
        self.valid_packets += 1
        self.arrivals.append(now)
        self.outcomes.append((now, True))
        self.last_sample_time, self.last_sample_at = now, record["received_at"]
        self.transmitter_mac, self.channel = record["transmitter_mac"], record["channel"]
        self.tick(now)
        shape = np.log1p(mag)
        shape -= np.median(shape)
        if self.health_status != "healthy":
            return None
        if self.alarm_state == "CALIBRATING":
            self.calibration_samples.append(shape)
            elapsed = now - self.calibration_start
            if elapsed >= self.config.calibration_seconds and len(self.calibration_samples) >= self.config.calibration_min_samples:
                samples = np.stack(self.calibration_samples)
                baseline = np.median(samples, axis=0)
                deviation = 1.4826 * np.median(np.abs(samples - baseline), axis=0)
                quarter = max(1, len(samples) // 4)
                drift = np.median(np.abs(np.median(samples[:quarter], axis=0) - np.median(samples[-quarter:], axis=0)))
                if float(np.median(deviation)) > 0.08 or drift > 0.12:
                    self.invalidate("Area was not stable during calibration")
                else:
                    self.baseline = baseline
                    self.scale = np.maximum(deviation, self.config.shape_noise_floor)
                    self.alarm_state = "READY"
                    self.calibration_progress = 1.0
                    self.calibration_detail = f"Empty-area baseline ready ({len(samples)} samples); threshold is a tuning hypothesis"
                    self.calibration_start = None
                    self.calibration_samples.clear()
                    self.previous_shape = shape
            return None
        if not self.ready:
            return None
        spatial = float(np.median(np.abs(shape - self.baseline) / self.scale))
        temporal = 0.0 if self.previous_shape is None else float(np.median(np.abs(shape - self.previous_shape) / (self.scale * math.sqrt(2))))
        self.previous_shape = shape
        self.score_window.append((now, max(spatial, temporal)))
        self.activity_score = max(0.0, float(np.median([score for _, score in self.score_window])))
        if now - self.graph_at >= 0.1:
            self.graph.append({"timestamp": record["received_at"], "activity_score": self.activity_score})
            self.graph_at = now
        if self.alarm_state != "ARMED":
            return None
        if not self.trigger_ready:
            if self.activity_score < self.threshold * 0.6:
                if self.quiet_since is None:
                    self.quiet_since = now
                if now - self.quiet_since >= self.config.quiet_seconds:
                    self.trigger_ready = True
            else:
                self.quiet_since = None
            self.above_since = None
            return None
        # Spatial mode gates this same latch, not a second alarm state. Quiet
        # rearm above still runs while the entry gate is closed.
        if not event_allowed:
            self.above_since = None
            return None
        if self.activity_score >= self.threshold:
            if self.above_since is None:
                self.above_since = now
            if now - self.above_since >= self.config.debounce_seconds:
                event = {
                    "event_id": str(uuid4()), "session_id": self.session_id,
                    "occurred_at": record["received_at"], "source_mode": self.source_mode,
                    "type": "motion_near_entrance", "area_name": self.area_name,
                    "activity_score": self.activity_score, "threshold": self.threshold,
                    "acknowledged": False, "call_status": "pending", "call_detail": "Checking local call policy",
                }
                self.events.appendleft(event)
                self.active_event_id = event["event_id"]
                self.alarm_state = "ALARM"
                self.above_since = None
                return event
        else:
            self.above_since = None
        return None

    def control(self, action: str, now: float, threshold: float | None = None):
        self.tick(now)
        if action == "disarm":
            self.alarm_state = "DISARMED"
            self.active_event_id = None
            self.above_since = self.quiet_since = None
            self.trigger_ready = True
            if self.calibration_start is not None:
                self.invalidate("Calibration cancelled")
        elif action == "calibrate":
            if self.alarm_state not in ("DISARMED", "READY"):
                raise TransitionError("Disarm before calibration")
            if self.health_status != "healthy":
                raise TransitionError("Calibration requires a fresh, adequate, valid stream")
            self.invalidate("Starting calibration")
            self.alarm_state = "CALIBRATING"
            self.calibration_start = now
            self.calibration_detail = "Keep the area empty and still for at least 5 seconds"
        elif action == "arm":
            if self.alarm_state not in ("DISARMED", "READY") or not self.ready or self.health_status != "healthy":
                raise TransitionError("Arm requires a fresh stream and completed empty-area baseline")
            self.alarm_state = "ARMED"
            self.above_since = self.quiet_since = None
            self.trigger_ready = True
        elif action == "acknowledge":
            if self.alarm_state != "ALARM":
                raise TransitionError("There is no latched alarm to acknowledge")
            if not self.ready or self.health_status != "healthy":
                raise TransitionError("Stream fault: disarm, recover, and calibrate again")
            for event in self.events:
                if event["event_id"] == self.active_event_id:
                    event["acknowledged"] = True
            self.active_event_id = None
            self.alarm_state = "ARMED"
            self.trigger_ready = False
            self.above_since = self.quiet_since = None
        elif action == "set_threshold":
            if self.alarm_state not in ("DISARMED", "READY"):
                raise TransitionError("Disarm before changing threshold")
            if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0.1 <= threshold <= 100:
                raise TransitionError("Threshold must be a finite number from 0.1 to 100")
            self.threshold = float(threshold)
            self.invalidate("Threshold changed")
        else:
            raise TransitionError("Unsupported action")

    def snapshot(self, now: float) -> dict:
        self.tick(now)
        rate = round(self.rate(now), 2)
        return {
            "alarm_state": self.alarm_state,
            "health": {"status": self.health_status, "detail": self.health_detail,
                       "last_sample_at": self.last_sample_at,
                       "sample_age_s": None if self.last_sample_time is None else round(max(0.0, now-self.last_sample_time), 3),
                       "packet_rate_hz": rate, "valid_packets": self.valid_packets,
                       "invalid_packets": self.invalid_packets, "dropped_packets_estimate": self.dropped_packets_estimate,
                       "serial_port": None, "transmitter_mac": self.transmitter_mac},
            "calibration": {"progress": round(self.calibration_progress, 3), "ready": self.ready, "detail": self.calibration_detail},
            "features": {"timestamp": utc_now(), "monotonic_s": now, "activity_score": self.activity_score,
                         "threshold": self.threshold, "packet_rate_hz": rate,
                         "samples_in_window": len(self.score_window), "baseline_ready": self.ready,
                         "quality": self.health_status if self.ready else "waiting"},
            "graph": list(self.graph), "events": [dict(e) for e in self.events], "active_event_id": self.active_event_id,
        }
