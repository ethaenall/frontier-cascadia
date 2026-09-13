"""Explicit sources. Constructing a LIVE adapter does not open the serial port."""
from __future__ import annotations

import asyncio
import copy
import json
import math
from pathlib import Path
import time

import numpy as np

from .parser import MAX_LINE_BYTES, ParseError, ParserConfig, magnitudes, parse_line, utc_now, validate_live_record


class SerialFramer:
    """Bound memory and discard a whole oversized line, including its tail."""
    def __init__(self):
        self.pending = bytearray()
        self.discarding = False

    def feed(self, chunk: bytes) -> list[bytes | None]:
        output = []
        for part in chunk.splitlines(keepends=True):
            ended = part.endswith(b"\n")
            if self.discarding:
                if ended:
                    self.discarding = False
                continue
            if len(self.pending) + len(part) > MAX_LINE_BYTES:
                self.pending.clear()
                self.discarding = not ended
                output.append(None)
                continue
            self.pending.extend(part)
            if ended:
                output.append(bytes(self.pending))
                self.pending.clear()
        return output


class TestAdapter:
    __test__ = False

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.motion_until = 0.0
        self.sequence = 0
        self.rng = np.random.default_rng(1729)
        self.angles = np.linspace(0, 2 * math.pi, 64, endpoint=False)

    def motion(self):
        self.motion_until = time.monotonic() + 3.5

    def sample(self, now: float, *, motion: bool | None = None) -> dict:
        if motion is None:
            motion = now < self.motion_until
        amplitude = 45 + 9 * np.cos(self.angles * 3) + 4 * np.sin(self.angles * 7)
        amplitude = amplitude + self.rng.normal(0, 0.22, len(amplitude))
        if motion:
            amplitude = amplitude + 19 * np.sin(self.angles * 5 + now * 2.0) + 8 * np.cos(self.angles * 2 - now)
        phase = self.angles * 0.4 + 0.35
        raw = np.column_stack((np.rint(amplitude * np.sin(phase)), np.rint(amplitude * np.cos(phase)))).astype(int).ravel().tolist()
        self.sequence = (self.sequence + 1) % (2**32)
        return {"schema_version": 1, "source_mode": "TEST", "session_id": self.session_id,
                "received_at": utc_now(), "received_monotonic_s": now, "sequence": self.sequence,
                "transmitter_mac": "02:00:00:00:00:01", "channel": 6,
                "device_timestamp_us": int(now * 1e6) % (2**32), "rssi_dbm": -45, "noise_floor_dbm": -95,
                "sig_mode": 1, "mcs": 0, "bandwidth": 0, "ant": 0, "first_word_invalid": False,
                "csi_raw": raw, "magnitudes": magnitudes(raw, False),
                "quality": {"valid": True, "warnings": ["synthetic_test_data_not_hardware"],
                            "missing_packets_estimate": None, "profile": "synthetic-raw-int8-v1"}}

    async def run(self, emit, reject, status):
        await status(True, "Synthetic TEST source")
        deadline = time.monotonic()
        try:
            while True:
                await emit(self.sample(time.monotonic()))
                deadline += 1 / 30
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                if deadline < time.monotonic() - 0.2:
                    deadline = time.monotonic()
        finally:
            await status(False, "TEST source stopped")

    async def close(self):
        pass


class LiveAdapter:
    def __init__(self, session_id: str, port: str, parser_config: ParserConfig,
                 baud: int = 115200, serial_factory=None, reconnect_seconds: float = 1.0):
        self.session_id, self.port, self.parser_config = session_id, port, parser_config
        self.baud, self.serial_factory = baud, serial_factory
        self.reconnect_seconds = reconnect_seconds
        self.serial = None

    def _open(self):
        if self.serial_factory is not None:
            return self.serial_factory(self.port, self.baud, 0.2)
        import serial
        # This Nano TinyUSB profile requires BOTH DTR and RTS for readiness.
        # Firmware disables its line-state reboot handler. Set stable true/true
        # before opening at 115200; never perform the reset-toggle dance.
        device = serial.Serial(port=None, baudrate=self.baud, timeout=0.2, write_timeout=0.2)
        device.dtr = True
        device.rts = True
        device.port = self.port
        device.open()
        return device

    async def run(self, emit, reject, status):
        try:
            while True:
                await status(False, "Connecting to selected serial port")
                try:
                    self.serial = await asyncio.to_thread(self._open)
                    await status(True, "Serial open; waiting for valid CSI")
                    framer = SerialFramer()
                    profile_confirmed = False
                    while True:
                        chunk = await asyncio.to_thread(self.serial.read_until, b"\n", MAX_LINE_BYTES + 1)
                        if not self.serial.is_open:
                            raise OSError("Serial closed")
                        for line in framer.feed(chunk):
                            try:
                                if line is None:
                                    raise ParseError("Oversized serial line discarded")
                                if line.startswith(b"# CASCADIA_CSI "):
                                    profile_confirmed = line.strip() == b"# CASCADIA_CSI role=receiver profile=esp32-s3-raw-int8-v1 state=READY"
                                    if not profile_confirmed:
                                        await status(False, "Receiver raw-int8 READY profile not confirmed")
                                    else:
                                        await status(True, "Receiver raw-int8 profile confirmed; waiting for CSI")
                                    continue
                                record = parse_line(line, self.parser_config, self.session_id)
                                if record is not None:
                                    if not profile_confirmed:
                                        raise ParseError("CSI rejected before receiver raw-int8 READY banner")
                                    await emit(record)
                            except ParseError as exc:
                                await reject(str(exc))
                        await asyncio.sleep(0)
                except (OSError, ValueError):
                    await status(False, "Serial unavailable or disconnected; retrying selected port")
                finally:
                    await self.close()
                await asyncio.sleep(self.reconnect_seconds)
        finally:
            await self.close()
            await status(False, "Serial collector stopped")

    async def close(self):
        device, self.serial = self.serial, None
        if device is not None:
            try:
                await asyncio.to_thread(device.close)
            except OSError:
                pass


class ReplayAdapter:
    """Preflight ALL records before playback; original LIVE provenance is retained."""
    def __init__(self, session_id: str, path: Path, *, max_gap_seconds: float = 2.0):
        self.session_id, self.path = session_id, Path(path)
        self.max_gap_seconds = max_gap_seconds
        self.complete = False

    def _read(self):
        previous = None
        session_id = None
        with self.path.open("r", encoding="utf-8") as stream:
            while True:
                line = stream.readline(MAX_LINE_BYTES * 4 + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES * 4:
                    raise ParseError("Oversized replay record")
                try:
                    record = validate_live_record(json.loads(line))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ParseError("Replay contains an invalid or non-LIVE record") from exc
                if session_id is None:
                    session_id = record["session_id"]
                elif record["session_id"] != session_id:
                    raise ParseError("Replay must contain one LIVE recording session")
                stamp = record["received_monotonic_s"]
                if previous is not None and stamp <= previous:
                    raise ParseError("Replay receipt clocks must increase")
                previous = stamp
                yield record

    def validate(self) -> int:
        count = sum(1 for _ in self._read())
        if count == 0:
            raise ParseError("Replay has no LIVE samples")
        return count

    async def run(self, emit, reject, status):
        await status(False, "Validating recorded LIVE provenance")
        try:
            await asyncio.to_thread(self.validate)
            await status(True, "REPLAY of recorded LIVE data; not a live sensor")
            previous = None
            for original in self._read():
                original_time = original["received_monotonic_s"]
                gap = 0.0 if previous is None else original_time - previous
                await asyncio.sleep(min(gap, self.max_gap_seconds))
                previous = original_time
                record = copy.deepcopy(original)
                record["recorded_provenance"] = {key: original[key] for key in ("session_id", "source_mode", "received_at", "received_monotonic_s")}
                record.update(source_mode="REPLAY", session_id=self.session_id,
                              received_at=utc_now(), received_monotonic_s=time.monotonic())
                if gap > self.max_gap_seconds:
                    record["quality"]["warnings"].append("replay_original_gap_capped")
                await emit(record)
            self.complete = True
            await status(False, "Replay finished; no live sensor connected")
        except (OSError, ParseError, UnicodeError):
            await status(False, "Replay rejected: file must contain valid recorded LIVE CSI only")
        finally:
            if not self.complete:
                await status(False, "Replay stopped or rejected")

    async def close(self):
        pass
