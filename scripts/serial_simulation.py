"""Lead-only software-in-loop check. Opens ONLY PTYs created by this process.

Run from the project root: uv run --locked python -m scripts.serial_simulation
No --port option exists. No physical board, listener, cloud, or RF model is used.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pty
import stat
import threading
import time
import tty
from uuid import uuid4

import httpx

from threshold.adapters import LiveAdapter
from threshold.app import Config, Service
from threshold.calls import CallConfig, CallManager
from threshold.detector import TransitionError
from threshold.parser import MAX_LINE_BYTES, ParserConfig, ParseError, validate_live_record
from scripts.virtual_sensor import READY_BANNER, SYNTHETIC_PREAMBLE, csv25_line

ROOT = Path(__file__).resolve().parents[1]
MODULUS = 2**32


class SimulationFailure(Exception):
    pass


def mark_test_record(record: dict) -> dict:
    """Downgrade parsed transport data BEFORE detector, recorder, or call entry."""
    result = dict(record)
    result["source_mode"] = "TEST"
    result["quality"] = dict(record["quality"])
    result["quality"]["profile"] = "synthetic-raw-int8-v1"
    result["quality"]["warnings"] = list(record["quality"].get("warnings", [])) + [
        "synthetic_virtual_serial_not_hardware"
    ]
    result["simulation_provenance"] = {
        "transport": "process_owned_pty", "fixture": "deterministic_raw25",
        "radio_sensing": False,
    }
    return result


def require_active_samples(producer_task, accepted_before: int, accepted_after: int, minimum: int = 1):
    """A quiet event counter proves nothing if the producer stopped or data stalled."""
    if producer_task is None or producer_task.done():
        if producer_task is not None and not producer_task.cancelled():
            producer_task.exception()  # Retrieve a prior failure; never discard it silently.
        raise SimulationFailure("Producer stopped during active sample phase")
    if accepted_after - accepted_before < minimum:
        raise SimulationFailure("Active sample phase did not receive enough fresh records")


def producer_cleanup_error(cancel_requested: bool, outcome) -> str | None:
    if cancel_requested and isinstance(outcome, asyncio.CancelledError):
        return None
    if isinstance(outcome, BaseException):
        return type(outcome).__name__
    return "UnexpectedProducerExit"


def block_result(result: dict, status: str, **detail):
    """An observation failure stays UNKNOWN even if a later check detects a fault."""
    if status not in {"FAIL", "UNKNOWN"}:
        raise ValueError("Only blocking states are accepted")
    result.update(status="UNKNOWN" if "UNKNOWN" in (result["status"], status) else "FAIL", **detail)


def finish_result(result: dict, *, scenarios_passed: bool, producer_stopped_safely: bool, recording_verified: bool):
    if result["status"] == "RUNNING":
        result["status"] = "PASS" if all((scenarios_passed, producer_stopped_safely, recording_verified)) else "UNKNOWN"


def audit_recording(recorder, accepted_packets: int, expected_event_ids: set[str]) -> dict:
    """Validate the whole completed journal, not merely a good prefix of it."""
    if recorder.error is not None:
        raise SimulationFailure("Recorder reported an error")
    recorded = replay_rejected = 0
    with (recorder.path / "raw.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record["source_mode"] != "TEST" or not record.get("simulation_provenance"):
                raise SimulationFailure("Synthetic record was not labelled TEST")
            recorded += 1
            if recorded > accepted_packets:
                raise SimulationFailure("Recording contains unexpected extra samples")
            try:
                validate_live_record(record)
            except ParseError:
                replay_rejected += 1
    if recorded < 1 or recorded != accepted_packets or replay_rejected != recorded:
        raise SimulationFailure("Accepted samples and complete TEST recording do not match")
    event_path = recorder.path / "events.jsonl"
    event_ids = []
    if event_path.exists() or expected_event_ids:
        with event_path.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event["source_mode"] != "TEST":
                    raise SimulationFailure("Event journal contains non-TEST provenance")
                event_ids.append(event["event_id"])
    if set(event_ids) != expected_event_ids or len(event_ids) != len(expected_event_ids):
        raise SimulationFailure("Event journal is incomplete or duplicated")
    if recorder.error is not None:
        raise SimulationFailure("Recorder fault appeared during journal verification")
    return {"recorded": recorded, "replay_rejected": replay_rejected, "recorded_events": len(event_ids)}


class OwnedPTY:
    """No caller-supplied path. A held slave fd proves every opened tty identity."""
    def __init__(self):
        self.lock = threading.RLock()
        self.master = self.slave = None
        self.path = None
        self.created = []

    def create(self):
        with self.lock:
            if self.master is not None or self.slave is not None:
                raise SimulationFailure("PTY must be closed before replacement")
            master, slave = pty.openpty()
            try:
                tty.setraw(slave)
                os.set_blocking(master, False)
                path = os.ttyname(slave)
                info = os.fstat(slave)
                if not stat.S_ISCHR(info.st_mode) or os.stat(path).st_rdev != info.st_rdev:
                    raise SimulationFailure("Owned PTY identity mismatch")
            except BaseException:
                os.close(master)
                os.close(slave)
                raise
            self.master, self.slave, self.path = master, slave, path
            self.created.append(path)

    def checked_path(self):
        if self.slave is None or self.master is None or self.path is None:
            raise OSError("Virtual sensor deliberately disconnected")
        if os.ttyname(self.slave) != self.path or os.stat(self.path).st_rdev != os.fstat(self.slave).st_rdev:
            raise OSError("Owned PTY identity changed")
        return self.path

    def close(self):
        with self.lock:
            for fd in (self.master, self.slave):
                if fd is not None:
                    os.close(fd)
            self.master = self.slave = self.path = None


class TestSerialAdapter(LiveAdapter):
    __test__ = False

    def __init__(self, session_id: str, owner: OwnedPTY):
        super().__init__(session_id, "NO_EXTERNAL_PORT_ALLOWED", ParserConfig("02:ca:5c:ad:1a:01", 6),
                         baud=115200, reconnect_seconds=0.15)
        self.owner = owner
        self.opens = self.emitted = self.sequence_wraps = self.timestamp_wraps = 0
        self.previous = None

    def _open(self):
        # This is the sole serial opening point; it calls the real production
        # pyserial path, but only after matching our held PTY descriptor.
        with self.owner.lock:
            self.port = self.owner.checked_path()
            device = super()._open()
            if os.fstat(device.fileno()).st_rdev != os.fstat(self.owner.slave).st_rdev:
                device.close()
                raise OSError("Opened tty is not our virtual sensor")
            self.opens += 1
            return device

    async def run(self, emit, reject, status):
        async def test_emit(record):
            marked = mark_test_record(record)
            if self.previous is not None:
                prior_seq, prior_stamp = self.previous
                self.sequence_wraps += int(marked["sequence"] < prior_seq and (marked["sequence"] - prior_seq) % MODULUS == 1)
                self.timestamp_wraps += int(marked["device_timestamp_us"] < prior_stamp)
            self.previous = (marked["sequence"], marked["device_timestamp_us"])
            self.emitted += 1
            await emit(marked)

        async def test_status(connected, detail):
            await status(connected, "TEST virtual serial: " + detail)

        await super().run(test_emit, reject, test_status)


class Producer:
    def __init__(self, owner: OwnedPTY):
        self.owner = owner
        self.write_lock = asyncio.Lock()
        self.paused = False
        self.motion = False
        self.sequence = MODULUS - 128
        self.stamp = MODULUS - 128 * 50000
        self.sample_time = 0.0
        self.written = 0

    async def send(self, payload: bytes):
        async with self.write_lock:
            view = memoryview(payload)
            deadline = time.monotonic() + 2.0
            while view:
                if self.owner.master is None:
                    raise OSError("Virtual sensor disconnected")
                try:
                    count = os.write(self.owner.master, view)
                    if count <= 0:
                        raise SimulationFailure("Virtual serial write made no progress")
                    view = view[count:]
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise SimulationFailure("Virtual serial write stalled")
                    await asyncio.sleep(0.005)

    async def pause(self):
        self.paused = True
        async with self.write_lock:
            pass

    async def run(self):
        deadline = time.monotonic()
        while True:
            if not self.paused:
                await self.send(csv25_line(self.sequence, self.stamp, motion=self.motion, sample_time=self.sample_time))
                self.sequence = (self.sequence + 1) % MODULUS
                self.stamp = (self.stamp + 50000) % MODULUS
                self.sample_time += 0.05
                self.written += 1
            deadline += 0.05
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            if deadline < time.monotonic() - 0.2:
                deadline = time.monotonic()


async def run_simulation(deadline_seconds: float = 95.0) -> tuple[dict, Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    job_root = (ROOT / ".local" / "simulation" / run_id).resolve()
    if not job_root.is_relative_to(ROOT):
        raise SimulationFailure("Simulation output escapes project")
    job_root.mkdir(parents=True, exist_ok=False)
    owner = OwnedPTY()
    checks = []
    provider_requests = 0
    started = time.monotonic()
    stage = "setup"
    result = {"status": "RUNNING", "source_mode": "TEST", "run_id": run_id,
              "physical_sensing_verified": False, "checks": checks}

    def deny_provider(request):
        nonlocal provider_requests
        provider_requests += 1
        raise SimulationFailure("Provider request is forbidden in simulation")

    calls = CallManager(CallConfig(enabled=False, dry_run=True, allow_nonlive=False),
                        job_root / "calls.sqlite3", transport=httpx.MockTransport(deny_provider))
    service = Service(Config(root=job_root, mode="test", recording=True,
                             area_name="Synthetic virtual-serial lab"), calls, source=object())
    source = TestSerialAdapter(service.session_id, owner)
    service.source = source
    producer = Producer(owner)
    producer_task = None
    scenarios_passed = producer_stopped_safely = recording_verified = False

    def check(condition, name, detail=""):
        nonlocal stage
        stage = name
        if not condition:
            raise SimulationFailure(name)
        checks.append({"name": name, "detail": detail})
        print("PASS", name, flush=True)

    async def until(predicate, name, timeout=5.0):
        nonlocal stage
        stage = name
        end = time.monotonic() + timeout
        while not predicate():
            if producer_task is not None and producer_task.done():
                error = producer_task.exception()
                if error is not None:
                    raise SimulationFailure("Virtual producer stopped") from error
            if time.monotonic() >= end:
                raise SimulationFailure(name)
            await asyncio.sleep(0.05)

    try:
        async with asyncio.timeout(deadline_seconds):
            owner.create()
            await service.start()
            producer_task = asyncio.create_task(producer.run(), name="synthetic-pty-writer")
            await until(lambda: service.detector.invalid_packets >= 3, "pre-READY rejection")
            check(service.detector.valid_packets == 0, "CSI blocked before READY")
            await producer.send(SYNTHETIC_PREAMBLE)
            await producer.send(b"# CASCADIA_CSI role=receiver profile=WRONG state=READY\n")
            await asyncio.sleep(0.35)
            check(service.detector.valid_packets == 0, "wrong profile stays blocked")
            await producer.send(READY_BANNER)
            await until(lambda: service.snapshot()["health"]["status"] == "healthy", "healthy TEST serial input", timeout=6)
            check(service.snapshot()["source_mode"] == "TEST", "serial samples labelled TEST")
            await service.control("calibrate")
            await until(lambda: service.detector.alarm_state == "READY", "quiet virtual baseline", timeout=8)
            check(service.detector.ready, "calibration over actual PTY bytes")
            check(source.sequence_wraps >= 1 and source.timestamp_wraps >= 1,
                  "sequence and device-clock wraps survive transport")
            await service.control("arm")
            producer.motion = True
            await asyncio.sleep(0.25)
            producer.motion = False
            await asyncio.sleep(1.1)
            check(len(service.detector.events) == 0, "short synthetic disturbance debounced")
            producer.motion = True
            await until(lambda: service.detector.alarm_state == "ALARM", "sustained activity alarm", timeout=4)
            first_id = service.detector.active_event_id
            await asyncio.sleep(1.1)
            check(len(service.detector.events) == 1, "sustained activity creates one latch")
            await service.control("acknowledge")
            await asyncio.sleep(1.1)
            check(len(service.detector.events) == 1 and not service.detector.trigger_ready,
                  "acknowledge does not retrigger ongoing activity")
            producer.motion = False
            await until(lambda: service.detector.trigger_ready, "quiet rearm", timeout=5)
            producer.motion = True
            await until(lambda: service.detector.alarm_state == "ALARM", "second separated activity", timeout=4)
            check(len(service.detector.events) == 2 and service.detector.active_event_id != first_id,
                  "quiet interval permits a distinct second event")
            await producer.pause()
            owner.close()
            await until(lambda: service.snapshot()["health"]["status"] == "disconnected", "virtual disconnect", timeout=3)
            check(service.detector.alarm_state == "ALARM" and not service.detector.ready,
                  "disconnect preserves alarm and invalidates baseline")
            try:
                await service.control("acknowledge")
            except TransitionError:
                check(True, "faulted alarm cannot acknowledge into armed state")
            else:
                raise SimulationFailure("faulted acknowledgement incorrectly succeeded")
            await service.control("disarm")
            valid_before = service.detector.valid_packets
            owner.create()
            producer.motion = False
            producer.paused = False
            await until(lambda: source.opens >= 2, "new owned PTY opened", timeout=3)
            await asyncio.sleep(0.4)
            check(service.detector.valid_packets == valid_before, "reconnect requires fresh READY")
            await producer.send(READY_BANNER)
            await until(lambda: service.snapshot()["health"]["status"] == "healthy", "recovered stream", timeout=6)
            check(not service.detector.ready and service.detector.alarm_state == "DISARMED",
                  "healthy recovery never auto-arms")
            try:
                await service.control("arm")
            except TransitionError:
                check(True, "recovered stream must recalibrate before arm")
            else:
                raise SimulationFailure("recovered stream armed without calibration")
            rejected_before = service.detector.invalid_packets
            await producer.send(b"X" * (MAX_LINE_BYTES + 128) + b"\nCSI_DATA,bad\n")
            await until(lambda: service.detector.invalid_packets >= rejected_before + 2,
                        "oversize and malformed wire rejection", timeout=3)
            check(True, "oversize and malformed lines rejected through serial framer")
            await until(lambda: service.snapshot()["health"]["status"] == "healthy", "health after malformed lines", timeout=6)
            await service.control("calibrate")
            await until(lambda: service.detector.alarm_state == "READY", "recalibration after recovery", timeout=8)
            await service.control("arm")
            await service.control("disarm")
            stage = "final activity producer and fresh samples"
            accepted_before_final_activity = service.detector.valid_packets
            producer.motion = True
            await asyncio.sleep(1.6)
            require_active_samples(producer_task, accepted_before_final_activity,
                                   service.detector.valid_packets, minimum=16)
            check(True, "final activity receives fresh samples from a running producer")
            check(len(service.detector.events) == 2 and service.detector.alarm_state == "DISARMED",
                  "disarmed synthetic activity suppressed")
            check(provider_requests == 0 and all(event["source_mode"] == "TEST" and event["call_status"] == "disabled"
                  for event in service.detector.events), "all events TEST and provider calls disabled")
            scenarios_passed = True
    except SimulationFailure:
        block_result(result, "FAIL", failed_check=stage)
    except TimeoutError:
        block_result(result, "UNKNOWN", failed_check=stage, error_type="DeadlineExceeded")
    except Exception as error:
        block_result(result, "UNKNOWN", failed_check=stage, error_type=type(error).__name__)
    finally:
        if producer_task is not None:
            cancel_requested = producer_task.cancel()
            outcomes = await asyncio.gather(producer_task, return_exceptions=True)
            cleanup_error = producer_cleanup_error(cancel_requested, outcomes[0])
            if cleanup_error is not None:
                block_result(result, "FAIL", producer_cleanup_error_type=cleanup_error)
            else:
                producer_stopped_safely = True
        try:
            await asyncio.wait_for(service.stop(), timeout=5)
        except Exception as error:
            block_result(result, "UNKNOWN", cleanup_error_type=type(error).__name__)
        owner.close()

    recorded = replay_rejected = None
    try:
        journal = audit_recording(service.recorder, service.detector.valid_packets,
                                  {event["event_id"] for event in service.detector.events})
        recorded, replay_rejected = journal["recorded"], journal["replay_rejected"]
        recording_verified = True
        result["journal_verification"] = journal
        checks.append({"name": "complete TEST recording matches accepted samples and events", "detail": f"{recorded} raw records; {journal['recorded_events']} unique events; no recorder error"})
        checks.append({"name": "recorded TEST samples cannot become LIVE replay", "detail": f"{recorded} records rejected by LIVE provenance validator"})
    except OSError as error:
        block_result(result, "UNKNOWN", recording_error_type=type(error).__name__)
    except Exception as error:
        block_result(result, "FAIL", recording_error_type=type(error).__name__)
    finish_result(result, scenarios_passed=scenarios_passed,
                  producer_stopped_safely=producer_stopped_safely, recording_verified=recording_verified)
    result.update(duration_seconds=round(time.monotonic()-started, 3),
                  opened_process_owned_ptys=len(owner.created), serial_open_successes=source.opens,
                  provider_requests=provider_requests, emitted_test_records=source.emitted,
                  recorded_test_records=recorded, replay_rejected_records=replay_rejected,
                  sequence_wraps=source.sequence_wraps, device_clock_wraps=source.timestamp_wraps,
                  snapshot=service.snapshot(), recording_directory=str(service.recorder.path.relative_to(ROOT)),
                  limitations=["Synthetic software behavior only; no RF propagation model or physical sensing accuracy.",
                               "PTYs do not verify Nano USB descriptors, modem-control electrical behavior, reboot, firmware, or CSI callbacks.",
                               "No actual board, physical serial port, HTTP listener, provider request, or phone sound test."])
    output = ROOT / "evidence" / "simulation" / f"serial-{run_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline-seconds", type=float, default=95.0)
    args = parser.parse_args()
    if not 60 <= args.deadline_seconds <= 180:
        parser.error("deadline must be between 60 and 180 seconds")
    result, output = asyncio.run(run_simulation(args.deadline_seconds))
    print(json.dumps({"status": result["status"], "source_mode": "TEST", "checks": len(result["checks"]),
                      "evidence": str(output.relative_to(ROOT)), "physical_sensing_verified": False}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
