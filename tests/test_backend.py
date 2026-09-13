import asyncio
import json
import stat
from uuid import uuid4

import httpx
import pytest

from threshold.app import Config, create_app
from threshold.auth import load_token
from threshold.detector import DetectorConfig
from threshold.recorder import Recorder


class FakeCalls:
    def __init__(self, wait=False):
        self.events = []
        self.wait = wait
        self.entered = asyncio.Event()
        self.cancelled = False

    async def handle_event(self, event):
        self.events.append(event)
        self.entered.set()
        try:
            if self.wait:
                await asyncio.Event().wait()
            return {"status": "dry_run", "detail": "No network called"}
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def snapshot(self):
        return {"enabled": False, "dry_run": True, "status": "disabled", "detail": "Offline test"}


def make_app(tmp_path, **kwargs):
    config = Config(root=tmp_path, **kwargs)
    calls = FakeCalls()
    return create_app(config, call_manager=calls), calls


@pytest.mark.asyncio
async def test_all_api_state_and_controls_authenticated_cookie_bearer_logout(tmp_path):
    app, calls = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        assert (await client.get("/api/state")).status_code == 401
        assert (await client.post("/api/control", json={"action": "disarm"})).status_code == 401
        assert (await client.post("/api/logout")).status_code == 401
        assert (await client.get("/api/unknown")).status_code == 401
        assert (await client.get("/api/state", headers={"Authorization": "Bearer incorrect"})).status_code == 401
        token = (tmp_path / ".local/control-token").read_text().strip()
        response = await client.post("/api/session", json={"token": token})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        response = await client.get("/api/state")
        assert response.status_code == 200
        state = response.json()
        assert state["source_mode"] == "TEST"
        assert state["alarm_state"] == "DISARMED"
        assert state["events"] == []
        assert token not in response.text
        assert (await client.post("/api/control", json={"action": "arm"})).status_code == 409
        assert (await client.post("/api/control", json={"action": "disarm"})).status_code == 200
        assert (await client.post("/api/logout")).status_code == 200
        assert (await client.get("/api/state")).status_code == 401
        assert (await client.get("/api/state", headers={"Authorization": f"Bearer {token}"})).status_code == 200
    assert not calls.events


@pytest.mark.asyncio
async def test_foreign_origin_untrusted_host_and_bounded_input(tmp_path):
    app, _ = make_app(tmp_path)
    token = (tmp_path / ".local/control-token").read_text().strip()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {token}"}) as client:
        for origin in ("https://evil.test", "null", "http://127.0.0.1:9999", "http://localhost:8765", "http://127.0.0.1:8765@evil.test"):
            assert (await client.post("/api/control", json={"action": "disarm"}, headers={"Origin": origin})).status_code == 403
            assert (await client.post("/api/session", json={"token": token}, headers={"Origin": origin})).status_code == 403
        assert (await client.post("/api/control", json={"action": "disarm"}, headers={"Origin": "http://127.0.0.1:8765"})).status_code == 200
        assert (await client.get("/api/state", headers={"Host": "rebinding.evil.test"})).status_code == 400
        assert (await client.post("/api/control", content="x" * 4097)).status_code == 413
        assert (await client.post("/api/control", json={"action": "set_threshold", "threshold": True})).status_code == 422
        assert (await client.post("/api/control", json={"action": "disarm", "injected": True})).status_code == 422


@pytest.mark.asyncio
async def test_login_limiter_and_token_file_created_once_private(tmp_path):
    app, _ = make_app(tmp_path)
    token_path = tmp_path / ".local/control-token"
    token = token_path.read_text()
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert load_token(token_path) + "\n" == token
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        for _ in range(5):
            assert (await client.post("/api/session", json={"token": "wrong"})).status_code == 401
        assert (await client.post("/api/session", json={"token": "wrong"})).status_code == 429


def test_lan_requires_explicit_optin_and_modes_never_fall_back(tmp_path):
    with pytest.raises(ValueError, match="allow-lan"):
        Config(root=tmp_path, host="0.0.0.0")
    assert Config(root=tmp_path, host="0.0.0.0", allow_lan=True)
    with pytest.raises(ValueError, match="requires --port"):
        Config(root=tmp_path, mode="live")
    with pytest.raises(ValueError, match="requires --replay"):
        Config(root=tmp_path, mode="replay")


@pytest.mark.asyncio
async def test_lifespan_test_source_tick_stale_and_clean_stop(tmp_path):
    app, _ = make_app(tmp_path)
    service = app.state.service
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.25)
        state = service.snapshot()
        assert state["health"]["status"] == "healthy"
        assert state["health"]["valid_packets"] >= 5
        assert state["events"] == []
        # Stop only the synthetic producer; independent health ticker must continue.
        service.tasks[0].cancel()
        await asyncio.gather(service.tasks[0], return_exceptions=True)
        assert service.snapshot()["health"]["status"] == "disconnected"
        await service.status(True, "Fake silent connected source")
        await asyncio.sleep(1.1)
        assert service.snapshot()["health"]["status"] == "stale"
    assert service.tasks == []
    assert service.call_tasks == {}


@pytest.mark.asyncio
async def test_event_call_once_in_background_and_disarm_cancels(tmp_path):
    from test_detector_backend import Stream
    app, _ = make_app(tmp_path)
    service = app.state.service
    calls = FakeCalls(wait=True)
    service.call_manager = calls
    stream = Stream()
    service.detector = stream.detector
    stream.calibrate()
    # No armed event: no queued call.
    for _ in range(60):
        stream.now += 1 / 30
        await service.emit(stream.source.sample(stream.now, motion=True))
    assert not service.call_tasks and not calls.events
    stream.feed(1)
    stream.control("arm")
    for _ in range(60):
        stream.now += 1 / 30
        await service.emit(stream.source.sample(stream.now, motion=True))
    await asyncio.wait_for(calls.entered.wait(), 1)
    assert len(calls.events) == 1
    assert len(service.detector.events) == 1
    # Direct engine time is synthetic. Service's real-time tick on disarm still
    # disarms safely and cancellation cannot enqueue further work.
    await service.control("disarm")
    assert calls.cancelled
    assert not service.call_tasks
    assert service.detector.active_event_id is None
    assert service.detector.events[0]["call_status"] == "disabled"
    initial = (service.recorder.path / "events.jsonl").read_text().splitlines()
    updates = (service.recorder.path / "event_updates.jsonl").read_text().splitlines()
    assert len(initial) == 1 and len(updates) == 1
    assert json.loads(updates[0])["event_id"] == json.loads(initial[0])["event_id"]
    assert json.loads(updates[0])["call_status"] == "disabled"


@pytest.mark.asyncio
async def test_test_motion_blocked_outside_test_without_opening_serial(tmp_path):
    app = create_app(Config(root=tmp_path, mode="live", serial_port="/DO-NOT-OPEN"), call_manager=FakeCalls())
    token = (tmp_path / ".local/control-token").read_text().strip()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {token}"}) as client:
        response = await client.post("/api/control", json={"action": "test_motion"})
        assert response.status_code == 409
    assert app.state.service.source.serial is None


def test_recorder_jsonl_no_secret_inputs_and_size_limit(tmp_path):
    recorder = Recorder(tmp_path, str(uuid4()), max_bytes=100)
    recorder.raw({"source_mode": "TEST", "sequence": 1})
    recorder.event({"event_id": "one"})
    assert json.loads((recorder.path / "raw.jsonl").read_text())["sequence"] == 1
    recorder.raw({"large": "x" * 200})
    assert "size limit" in recorder.error
    assert "token" not in (recorder.path / "raw.jsonl").read_text()


def test_token_symlink_refused(tmp_path):
    source = tmp_path / "source"
    source.write_text("a" * 43)
    link = tmp_path / "token"
    link.symlink_to(source)
    with pytest.raises(OSError):
        load_token(link)


def test_empty_existing_token_is_not_silently_regenerated(tmp_path):
    path = tmp_path / "token"
    path.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_token(path)
    assert path.read_text() == ""


def test_recording_symlink_escape_rejected(tmp_path):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "recordings").symlink_to(outside, target_is_directory=True)
    recorder = Recorder(root, str(uuid4()))
    assert recorder.error
    assert not list(outside.iterdir())


def test_raw_budget_exhaustion_keeps_independent_event_evidence(tmp_path):
    recorder = Recorder(tmp_path, str(uuid4()), max_bytes=1, event_max_bytes=4096)
    recorder.raw({"sequence": 1})
    assert recorder.raw_error and "size limit" in recorder.snapshot()["error"]
    event = {"event_id": str(uuid4()), "acknowledged": False, "call_status": "pending", "call_detail": "Queued"}
    recorder.event(event)
    event["call_status"] = "disabled"
    recorder.event_update(event)
    assert not recorder.event_error
    assert len((recorder.path / "events.jsonl").read_text().splitlines()) == 1
    assert json.loads((recorder.path / "event_updates.jsonl").read_text())["call_status"] == "disabled"


def test_event_budget_error_is_visible_and_does_not_block_raw(tmp_path):
    recorder = Recorder(tmp_path, str(uuid4()), event_max_bytes=1)
    recorder.event({"event_id": "one"})
    assert recorder.event_error and "Event recording" in recorder.snapshot()["error"]
    recorder.raw({"sequence": 2})
    assert not recorder.raw_error
    assert json.loads((recorder.path / "raw.jsonl").read_text())["sequence"] == 2
