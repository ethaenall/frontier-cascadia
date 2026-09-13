"""Deterministic offline TEST-only scenarios for the production detector.

No serial, HTTP, provider, or source edits. Virtual time advances without sleeps.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import NAMESPACE_URL, uuid5

# Script execution and pytest use the same project-native environment.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from threshold.adapters import TestAdapter
from threshold.detector import Detector, TransitionError

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SOURCE_MODE = "TEST"
LIMITATIONS = [
    "All samples, disturbances, alarm events, and results are synthetic TEST data.",
    "This validates software invariants, not radio reception or physical motion performance.",
    "No human, crossing, direction, occupancy, or doorway classification is measured.",
    "The production synthetic generator is a development fixture, not an RF model.",
    "Seeds vary fixture noise only; they do not represent people, locations, or physical trials.",
    "Thresholds and duration settings remain unvalidated tuning hypotheses.",
    "No serial devices, network requests, provider calls, or wall-clock sleeps are used.",
]
MEMORY_BOUNDS = {
    "arrivals": 600, "outcomes": 1200, "calibration_samples": 1500,
    "score_window": 150, "graph": 300, "events": 100,
}


class ScenarioFailure(AssertionError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class VirtualStream:
    """Drive real Detector/TestAdapter methods. Only fixture timing/noise vary."""
    def __init__(self, name: str, seed: int, *, start: float = 100.0,
                 sequence: int = 0, cpu_deadline: float | None = None):
        self.name, self.seed = name, seed
        self.start = self.now = start
        self.session_id = str(uuid5(NAMESPACE_URL, f"threshold:TEST:synthetic:{name}:{seed}"))
        self.source = TestAdapter(self.session_id)
        self.source.rng = np.random.default_rng(seed)
        self.source.sequence = sequence
        self.detector = Detector(self.session_id, SOURCE_MODE, "Synthetic TEST fixture only")
        self.detector.set_connection(True, "Synthetic TEST fixture connected", self.now)
        self.cpu_deadline = cpu_deadline
        self.generated_samples = 0
        self.event_total = 0
        self.event_ids = set()
        self.event_examples = []
        self.invariants = []
        self.peak_memory = {key: 0 for key in MEMORY_BOUNDS}
        self.max_activity_score = 0.0
        self.observed_sequence_wrap = False
        self.observed_device_clock_wrap = False
        self.previous_sequence = self.previous_clock = None

    def require(self, name: str, condition: bool):
        self.invariants.append({"name": name, "status": "PASS" if condition else "FAIL", "source_mode": SOURCE_MODE, "synthetic": True})
        if not condition:
            raise ScenarioFailure(name)

    def _observe(self):
        for key, bound in MEMORY_BOUNDS.items():
            length = len(getattr(self.detector, key))
            self.peak_memory[key] = max(self.peak_memory[key], length)
            if length > bound:
                raise ScenarioFailure(f"{key} exceeded bounded memory: {length}>{bound}")
        score = self.detector.activity_score
        if score is not None:
            if not np.isfinite(score) or score < 0:
                raise ScenarioFailure("Score must remain finite and nonnegative")
            self.max_activity_score = max(self.max_activity_score, score)
        if self.generated_samples % 300 == 0 and self.cpu_deadline is not None and time.process_time() > self.cpu_deadline:
            raise BudgetExceeded("CPU budget reached; remaining results are incomplete, not PASS")

    def step(self, *, motion=False, dt=1/30, malformed: str | None = None):
        self.now += dt
        record = self.source.sample(self.now, motion=motion)
        record["received_at"] = (EPOCH + timedelta(seconds=self.now-self.start)).isoformat(timespec="milliseconds")
        if record["source_mode"] != SOURCE_MODE or "synthetic_test_data_not_hardware" not in record["quality"]["warnings"]:
            raise ScenarioFailure("Every generated sample must retain explicit synthetic TEST provenance")
        self.generated_samples += 1
        if self.previous_sequence is not None and record["sequence"] < self.previous_sequence:
            self.observed_sequence_wrap = True
        if self.previous_clock is not None and record["device_timestamp_us"] < self.previous_clock:
            self.observed_device_clock_wrap = True
        self.previous_sequence, self.previous_clock = record["sequence"], record["device_timestamp_us"]
        if malformed == "nonfinite":
            record["magnitudes"][10] = float("nan")
        elif malformed == "missing":
            record.pop("magnitudes")
        elif malformed == "negative":
            record["magnitudes"][10] = -1.0
        elif malformed == "wrong_shape":
            record["magnitudes"] = "not a numeric array"
        elif malformed == "invalid_quality":
            record["quality"]["valid"] = False
        event = self.detector.ingest(record, self.now)
        if event is not None:
            if event["source_mode"] != SOURCE_MODE:
                raise ScenarioFailure("An emitted event lost its TEST provenance")
            if event["event_id"] in self.event_ids:
                raise ScenarioFailure("Duplicate event identifier was emitted twice")
            self.event_ids.add(event["event_id"])
            self.event_total += 1
            if len(self.event_examples) < 8:
                # Random production UUIDs are checked above, not exported. Thus
                # the evidence is deterministic without mocking event creation.
                self.event_examples.append({
                    "source_mode": SOURCE_MODE, "synthetic": True,
                    "synthetic_event_ordinal": self.event_total,
                    "type": event["type"], "occurred_at": event["occurred_at"],
                    "activity_score": round(event["activity_score"], 6),
                    "threshold": event["threshold"],
                })
        self._observe()
        return event

    def feed(self, seconds: float, *, motion=False, hz=30):
        before = self.event_total
        for _ in range(round(seconds * hz)):
            self.step(motion=motion, dt=1/hz)
        return self.event_total - before

    def control(self, action: str):
        self.detector.control(action, self.now)
        self._observe()

    def denied(self, action: str):
        try:
            self.control(action)
        except TransitionError:
            return True
        return False

    def calibrate(self):
        self.feed(0.5)
        self.require("fresh synthetic stream before calibration", self.detector.health_status == "healthy")
        self.control("calibrate")
        self.feed(5.2)
        self.require("empty synthetic baseline becomes READY", self.detector.ready and self.detector.alarm_state == "READY")

    def gap(self, seconds: float, *, missing_sequences: int = 0):
        self.now += seconds
        self.source.sequence = (self.source.sequence + missing_sequences) % (2**32)
        self.detector.tick(self.now)
        self._observe()

    def report(self, status: str, error: str | None = None):
        return {
            "source_mode": SOURCE_MODE, "synthetic": True, "scenario": self.name,
            "seed": self.seed, "status": status, "error": error,
            "virtual_elapsed_s": round(self.now-self.start, 6),
            "synthetic_samples_generated": self.generated_samples,
            "synthetic_alarm_events": self.event_total,
            "max_activity_score": round(self.max_activity_score, 6),
            "final_alarm_state": self.detector.alarm_state,
            "final_health": self.detector.health_status,
            "baseline_ready": self.detector.ready,
            "valid_packets": self.detector.valid_packets,
            "invalid_packets": self.detector.invalid_packets,
            "sequence_gap_estimate": self.detector.dropped_packets_estimate,
            "peak_memory_items": self.peak_memory,
            "invariants": self.invariants, "synthetic_event_examples": self.event_examples,
        }


def quiet_baseline(s):
    s.require("cannot arm without baseline", s.denied("arm"))
    s.calibrate()
    s.control("arm")
    s.feed(10)
    s.require("quiet synthetic fixture creates no alarm", s.event_total == 0)
    s.require("quiet fixture remains ARMED and healthy", s.detector.alarm_state == "ARMED" and s.detector.health_status == "healthy")


def short_disturbance(s):
    s.calibrate()
    s.control("arm")
    s.feed(0.3, motion=True)
    s.feed(2)
    s.require("short synthetic disturbance fails sustained debounce", s.event_total == 0 and s.detector.alarm_state == "ARMED")


def sustained_latch(s):
    s.calibrate()
    s.control("arm")
    s.feed(2, motion=True)
    active = s.detector.active_event_id
    s.require("sustained synthetic shape change emits one alarm", s.event_total == 1 and s.detector.alarm_state == "ALARM")
    s.feed(6, motion=True)
    s.require("continued disturbance retains one latch", s.event_total == 1 and s.detector.active_event_id == active)


def acknowledge_and_rearm(s):
    s.calibrate()
    s.control("arm")
    s.feed(2, motion=True)
    s.require("first synthetic alarm is latched", s.event_total == 1)
    first_event_id = s.detector.active_event_id
    s.control("acknowledge")
    s.require("acknowledgement clears active alarm and marks event", s.detector.active_event_id is None and s.detector.events[0]["acknowledged"])
    s.feed(3, motion=True)
    s.require("acknowledge does not retrigger continued disturbance", s.event_total == 1)
    s.feed(1)
    s.feed(1, motion=True)
    s.require("short quiet interval is insufficient", s.event_total == 1)
    s.feed(3)
    s.feed(2, motion=True)
    s.require("adequate quiet interval permits one new synthetic event", s.event_total == 2 and s.detector.active_event_id != first_event_id)


def disarmed_suppression(s):
    s.calibrate()
    s.feed(3, motion=True)
    s.require("unarmed disturbance cannot emit an alarm", s.event_total == 0)
    s.feed(1)
    s.control("arm")
    s.feed(2, motion=True)
    s.require("armed disturbance creates the expected synthetic latch", s.event_total == 1)
    s.control("disarm")
    s.feed(5, motion=True)
    s.require("disarm clears latch and suppresses future synthetic events", s.event_total == 1 and s.detector.active_event_id is None and s.detector.alarm_state == "DISARMED")


def missing_packets(s):
    s.calibrate()
    s.control("arm")
    s.gap(0.4, missing_sequences=12)
    s.feed(1)
    s.require("short missing-packet gap stays an estimate", s.detector.dropped_packets_estimate == 12)
    s.require("fresh adequate recovery from short gap preserves readiness", s.detector.ready and s.event_total == 0)
    s.gap(1.2, missing_sequences=36)
    s.require("long missing-data gap becomes stale and disarms", s.detector.health_status == "stale" and not s.detector.ready and s.detector.alarm_state == "DISARMED")
    s.require("stale stream cannot arm or calibrate", s.denied("arm") and s.denied("calibrate"))


def low_packet_rate(s):
    s.calibrate()
    s.control("arm")
    s.feed(3, hz=4)
    s.require("fresh but low-rate data is degraded", s.detector.health_status == "degraded")
    s.require("low-rate fault invalidates baseline and blocks arm", not s.detector.ready and s.denied("arm"))
    s.feed(1)
    s.require("healthy recovered rate does not restore baseline", s.detector.health_status == "healthy" and not s.detector.ready and s.denied("arm"))


def malformed_rejection_burst(s):
    s.calibrate()
    s.control("arm")
    kinds = ("nonfinite", "missing", "negative", "wrong_shape", "invalid_quality")
    before = s.detector.valid_packets
    for index in range(30):
        s.step(dt=1/300, malformed=kinds[index % len(kinds)])
    s.require("all malformed synthetic normalized samples are rejected", s.detector.valid_packets == before and s.detector.invalid_packets == 30)
    s.require("rejection burst degrades health and invalidates baseline", s.detector.health_status == "degraded" and not s.detector.ready)
    s.require("malformed samples never create an alarm", s.event_total == 0 and s.denied("arm"))


def disconnect_with_latch(s):
    s.calibrate()
    s.control("arm")
    s.feed(2, motion=True)
    active = s.detector.active_event_id
    s.require("synthetic event exists before disconnect", s.event_total == 1)
    s.detector.set_connection(False, "Synthetic TEST disconnect injection", s.now)
    s.require("disconnect keeps alarm latch but exposes health fault", s.detector.alarm_state == "ALARM" and s.detector.active_event_id == active and s.detector.health_status == "disconnected")
    s.require("faulted alarm cannot acknowledge into blind arm", not s.detector.ready and s.denied("acknowledge"))
    s.control("disarm")
    s.require("explicit disarm still clears faulted latch", s.detector.active_event_id is None and s.detector.alarm_state == "DISARMED")


def recovery_requires_calibration(s):
    s.calibrate()
    s.control("arm")
    s.gap(1.2)
    s.feed(1)
    s.require("fresh data after fault is healthy but uncalibrated", s.detector.health_status == "healthy" and not s.detector.ready)
    s.require("no automatic rearm after fault", s.detector.alarm_state == "DISARMED" and s.denied("arm"))
    s.calibrate()
    s.control("arm")
    s.feed(2, motion=True)
    s.require("explicit recalibration then arm restores synthetic detection", s.event_total == 1 and s.detector.alarm_state == "ALARM")


def sequence_and_clock_wrap(s):
    s.calibrate()
    s.control("arm")
    s.feed(2)
    s.require("fixture actually crosses uint32 sequence wrap", s.observed_sequence_wrap)
    s.require("fixture actually crosses uint32 device-microsecond wrap", s.observed_device_clock_wrap)
    s.require("wrap does not invent missing packets or invalidate baseline", s.detector.dropped_packets_estimate == 0 and s.detector.invalid_packets == 0 and s.detector.ready)
    s.require("quiet wrapped fixture stays armed without an alarm", s.event_total == 0 and s.detector.alarm_state == "ARMED")


SCENARIOS = [
    ("quiet_baseline", quiet_baseline), ("short_disturbance_debounce", short_disturbance),
    ("sustained_shape_change_latch", sustained_latch), ("acknowledge_quiet_rearm", acknowledge_and_rearm),
    ("disarmed_suppression", disarmed_suppression), ("missing_packets", missing_packets),
    ("low_packet_rate", low_packet_rate), ("malformed_rejection_burst", malformed_rejection_burst),
    ("disconnect_during_alarm", disconnect_with_latch), ("fault_recovery_recalibration", recovery_requires_calibration),
    ("sequence_device_clock_wrap", sequence_and_clock_wrap),
]


def long_bounded_run(s, *, long_seconds: float, history_cycles: int):
    s.calibrate()
    s.control("arm")
    s.feed(long_seconds)
    s.require("long quiet synthetic interval has no event", s.event_total == 0)
    for _ in range(history_cycles):
        before = s.event_total
        s.feed(2, motion=True)
        if s.event_total != before + 1:
            raise ScenarioFailure("Each eligible synthetic pulse must create exactly one latch")
        s.control("acknowledge")
        s.feed(3)
    s.require("generated more events than bounded history capacity", s.event_total == history_cycles and history_cycles > 100)
    s.require("history retains only newest 100 synthetic events", len(s.detector.events) == 100)
    s.require("all configured production deques stay bounded", all(s.peak_memory[key] <= bound for key, bound in MEMORY_BOUNDS.items()))
    s.require("long run ends armed, fresh, calibrated, and quiet", s.detector.alarm_state == "ARMED" and s.detector.ready and s.detector.health_status == "healthy" and s.detector.active_event_id is None)


def production_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "threshold").glob("*.py"))}


def run_suite(*, seeds=(7, 42, 1729, 2026, 65537), long_seconds=1200,
              history_cycles=105, cpu_budget_seconds=110):
    if not seeds or len(seeds) > 8 or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds):
        raise ValueError("Choose 1..8 integer seeds from 0..2**32-1")
    if not 0 <= long_seconds <= 3600 or not 101 <= history_cycles <= 120 or not 1 <= cpu_budget_seconds <= 110:
        raise ValueError("Bounded limits: long_seconds0..3600, history_cycles101..120, CPU1..110 seconds")
    before_hashes = production_hashes()
    deadline = time.process_time() + cpu_budget_seconds
    results = []
    for seed in seeds:
        for name, scenario in SCENARIOS:
            kwargs = {"start": (2**32 / 1e6) - 6.1, "sequence": 2**32-185} if name == "sequence_device_clock_wrap" else {}
            stream = VirtualStream(name, seed, cpu_deadline=deadline, **kwargs)
            try:
                scenario(stream)
                results.append(stream.report("PASS"))
            except BudgetExceeded as exc:
                results.append(stream.report("INCOMPLETE", str(exc)))
                return _report(results, seeds, long_seconds, history_cycles, before_hashes)
            except Exception as exc:
                results.append(stream.report("FAIL", f"{type(exc).__name__}: {exc}"))
    stream = VirtualStream("long_virtual_time_bounded_memory", seeds[0], cpu_deadline=deadline)
    try:
        long_bounded_run(stream, long_seconds=long_seconds, history_cycles=history_cycles)
        results.append(stream.report("PASS"))
    except BudgetExceeded as exc:
        results.append(stream.report("INCOMPLETE", str(exc)))
    except Exception as exc:
        results.append(stream.report("FAIL", f"{type(exc).__name__}: {exc}"))
    return _report(results, seeds, long_seconds, history_cycles, before_hashes)


def _report(results, seeds, long_seconds, history_cycles, before_hashes):
    unchanged = before_hashes == production_hashes()
    expected = len(seeds) * len(SCENARIOS) + 1
    passed = sum(result["status"] == "PASS" for result in results)
    incomplete = any(result["status"] == "INCOMPLETE" for result in results)
    status = "PASS" if unchanged and len(results) == expected and passed == expected else "INCOMPLETE" if unchanged and incomplete else "FAIL"
    return {
        "schema_version": 1, "source_mode": SOURCE_MODE, "synthetic": True,
        "suite": "production-detector-offline-synthetic-invariants", "status": status,
        "seeds": list(seeds), "long_quiet_virtual_seconds": long_seconds,
        "synthetic_history_cycles": history_cycles, "expected_scenarios": expected,
        "passed_scenarios": passed,
        "completed_scenarios": sum(result["status"] != "INCOMPLETE" for result in results),
        "reported_scenarios": len(results),
        "production_source_unchanged": unchanged, "production_sha256": before_hashes,
        "synthetic_samples_generated": sum(r["synthetic_samples_generated"] for r in results),
        "total_virtual_elapsed_s": round(sum(r["virtual_elapsed_s"] for r in results), 6),
        "memory_bounds_items": MEMORY_BOUNDS, "limitations": LIMITATIONS,
        "scenarios": results,
    }


def write_report(report: dict, output: Path):
    directory = (ROOT / "evidence" / "simulation").resolve()
    output = Path(output).resolve()
    if not directory.is_relative_to(ROOT) or output.parent != directory or not output.name.startswith("detector-") or output.suffix != ".json":
        raise ValueError("Output must be evidence/simulation/detector-*.json inside this project")
    directory.mkdir(parents=True, exist_ok=True)
    pending = directory / (output.stem + ".pending.json")
    pending.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    pending.replace(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 1729, 2026, 65537])
    parser.add_argument("--long-seconds", type=int, default=1200)
    parser.add_argument("--history-cycles", type=int, default=105)
    parser.add_argument("--cpu-budget-seconds", type=int, default=110)
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/simulation/detector-scenarios.json")
    args = parser.parse_args(argv)
    started = time.process_time()
    report = run_suite(seeds=tuple(args.seeds), long_seconds=args.long_seconds,
                       history_cycles=args.history_cycles, cpu_budget_seconds=args.cpu_budget_seconds)
    write_report(report, args.output)
    print(f"TEST SYNTHETIC {report['status']}: {report['passed_scenarios']}/{report['expected_scenarios']} scenarios; "
          f"{report['synthetic_samples_generated']} fixture samples; CPU {time.process_time()-started:.2f}s")
    print(f"Evidence: {args.output}")
    print("Software invariants only. No hardware, physical trials, network, or provider calls.")
    for result in report["scenarios"]:
        if result["status"] != "PASS":
            print(f"TEST SYNTHETIC {result['status']} seed={result['seed']} {result['scenario']}: {result['error']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
