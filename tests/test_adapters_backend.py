import asyncio
import json
from uuid import uuid4

import pytest

from threshold.adapters import LiveAdapter, ReplayAdapter, SerialFramer, TestAdapter
from threshold.parser import MAX_LINE_BYTES, ParseError, ParserConfig, parse_line
from test_parser_backend import wire


def test_framer_bounded_oversized_tail_and_partial_lines():
    framer = SerialFramer()
    assert framer.feed(b"CSI_") == []
    assert framer.feed(b"DATA,partial\n") == [b"CSI_DATA,partial\n"]
    assert framer.feed(b"a" * (MAX_LINE_BYTES + 1)) == [None]
    assert len(framer.pending) == 0
    assert framer.feed(b"malicious_tail\nnext\n") == [b"next\n"]


class FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.is_open = True
        self.closed = False

    def read_until(self, delimiter, size):
        assert size == MAX_LINE_BYTES + 1
        if not self.chunks:
            raise OSError("unplugged")
        return self.chunks.pop(0)

    def close(self):
        self.is_open = False
        self.closed = True


@pytest.mark.asyncio
async def test_live_requires_receiver_profile_and_recovers_only_same_selected_port():
    banner = b"# CASCADIA_CSI role=receiver profile=esp32-s3-raw-int8-v1 state=READY\n"
    row = wire().encode()
    devices = [FakeSerial([row, banner, b"# STATS packets=1\n", row]), FakeSerial([row, banner, wire(id=2).encode()])]
    opened = []
    rejected, emitted, statuses = [], [], []
    complete = asyncio.Event()

    def factory(port, baud, timeout):
        opened.append((port, baud, timeout))
        return devices[len(opened) - 1]

    async def emit(record):
        emitted.append(record)
        if len(emitted) == 2:
            complete.set()

    async def reject(reason):
        rejected.append(reason)

    async def status(connected, detail):
        statuses.append((connected, detail))

    adapter = LiveAdapter(str(uuid4()), "/fake/owned-by-test", ParserConfig(), serial_factory=factory, reconnect_seconds=0.01)
    task = asyncio.create_task(adapter.run(emit, reject, status))
    try:
        await asyncio.wait_for(complete.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(emitted) == 2
    assert len(rejected) == 2  # each new connection resets profile confirmation
    assert all(item[0] == "/fake/owned-by-test" for item in opened)
    assert any(not connected and "disconnected" in detail for connected, detail in statuses)
    assert all(device.closed for device in devices)


@pytest.mark.asyncio
async def test_wrong_profile_never_accepted():
    device = FakeSerial([b"# CASCADIA_CSI role=receiver profile=compensated-int16 state=READY\n", wire().encode()])
    done = asyncio.Event()
    emitted = []

    async def emit(record):
        emitted.append(record)

    async def reject(reason):
        done.set()

    async def status(connected, detail):
        pass

    adapter = LiveAdapter(str(uuid4()), "/fake", ParserConfig(), serial_factory=lambda *args: device)
    task = asyncio.create_task(adapter.run(emit, reject, status))
    try:
        await asyncio.wait_for(done.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not emitted
    assert device.closed


def live_records():
    session = str(uuid4())
    return [parse_line(wire(id=i), ParserConfig(), session, now=100 + i / 1000) for i in range(1, 4)]


@pytest.mark.parametrize("mode", ["TEST", "REPLAY"])
def test_replay_rejects_nonlive_even_after_valid_first_line(tmp_path, mode):
    records = live_records()
    records[-1]["source_mode"] = mode
    path = tmp_path / "data.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    adapter = ReplayAdapter(str(uuid4()), path)
    with pytest.raises(ParseError, match="invalid or non-LIVE"):
        adapter.validate()


@pytest.mark.asyncio
async def test_replay_retains_provenance_and_new_clock(tmp_path):
    records = live_records()
    path = tmp_path / "live.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    session = str(uuid4())
    adapter = ReplayAdapter(session, path)
    output, statuses = [], []

    async def emit(record):
        output.append(record)

    async def reject(reason):
        pytest.fail(reason)

    async def status(connected, detail):
        statuses.append((connected, detail))

    await adapter.run(emit, reject, status)
    assert len(output) == 3
    assert all(r["source_mode"] == "REPLAY" and r["session_id"] == session for r in output)
    assert output[0]["recorded_provenance"]["source_mode"] == "LIVE"
    assert output[0]["recorded_provenance"]["received_at"] == records[0]["received_at"]
    assert output[0]["received_monotonic_s"] != records[0]["received_monotonic_s"]
    assert statuses[-1][0] is False
    assert adapter.complete


def test_replay_rejects_tampered_magnitude_and_clock(tmp_path):
    for key, value in [("magnitudes", [0] * 64), ("received_monotonic_s", float("nan")), ("first_word_invalid", 1)]:
        records = live_records()
        records[0][key] = value
        path = tmp_path / "bad.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        with pytest.raises(ParseError):
            ReplayAdapter(str(uuid4()), path).validate()


def test_synthetic_start_is_quiet_and_explicit():
    adapter = TestAdapter(str(uuid4()))
    quiet = adapter.sample(100)
    assert quiet["source_mode"] == "TEST"
    assert quiet["quality"]["warnings"] == ["synthetic_test_data_not_hardware"]
    assert adapter.motion_until == 0


def test_nano_transport_sets_stable_both_asserted_without_opening_hardware(monkeypatch):
    import sys
    from types import SimpleNamespace
    observed = {}

    class StubSerial:
        def __init__(self, **kwargs):
            observed["constructor"] = kwargs
            self.dtr = self.rts = None
            self.port = None

        def open(self):
            observed["open"] = (self.port, self.dtr, self.rts)

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=StubSerial))
    adapter = LiveAdapter(str(uuid4()), "/FAKE-NEVER-OPENED", ParserConfig())
    adapter._open()
    assert observed["constructor"]["port"] is None
    assert observed["constructor"]["baudrate"] == 115200
    assert observed["open"] == ("/FAKE-NEVER-OPENED", True, True)
