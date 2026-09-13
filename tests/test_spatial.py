import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest

from threshold.adapters import TestAdapter
from threshold.app import Config, create_app
from threshold.detector import Detector, DetectorConfig, TransitionError
from threshold.spatial import SpatialController, SpatialInputError, point_inside, validate_control, validate_zone


def zone_payload(**changes):
    payload = {"action": "set_zone", "name": "Synthetic TEST zone", "vertices": [{"x": 0, "z": 0}, {"x": 3, "z": 0}, {"x": 3, "z": 3}, {"x": 0, "z": 3}], "coordinate_space": "test-plan", "frame_id": "TEST-frame-1"}
    payload.update(changes)
    return payload


class Harness:
    def __init__(self):
        self.now = 100.0
        self.session = str(uuid4())
        self.detector = Detector(self.session, "TEST")
        self.source = TestAdapter(self.session)
        self.spatial = SpatialController("TEST")
        self.detector.set_connection(True, "TEST synthetic stream", self.now)
        self.motion_until = 0.0
        self.spatial.control(zone_payload(), self.detector, self.now)
        self.spatial.control({"action": "set_target", "target": "zone-entry"}, self.detector, self.now)

    def feed(self, seconds, *, motion=False, positions=True):
        events = []
        for _ in range(round(seconds * 30)):
            self.now += 1/30
            self.detector.tick(self.now)
            if positions and self.spatial.tick(self.now, self.detector):
                self.motion_until = self.now + 3.5
            record = self.source.sample(self.now, motion=motion or self.now < self.motion_until)
            allowed = self.spatial.target == "radio-motion" or self.spatial.guard(self.now, self.detector)["eligible"]
            event = self.detector.ingest(record, self.now, event_allowed=allowed)
            if event:
                self.spatial.clear_pending()
                events.append(event)
        return events

    def shared(self, action):
        self.detector.tick(self.now)
        self.spatial.ensure_shared_control(action, self.detector, self.now)
        self.detector.control(action, self.now)
        self.spatial.clear_pending()

    def control(self, action):
        self.spatial.control({"action": action}, self.detector, self.now)

    def calibrate(self):
        self.feed(0.5)
        self.shared("calibrate")
        self.feed(5.2)
        assert self.detector.ready


def test_geometry_simple_concave_clockwise_and_boundary_policy():
    vertices = [{"x": 0, "z": 0}, {"x": 3, "z": 0}, {"x": 3, "z": 1}, {"x": 1, "z": 1}, {"x": 1, "z": 3}, {"x": 0, "z": 3}]
    for shape in (vertices, vertices[::-1]):
        zone = validate_zone(zone_payload(vertices=shape))
        assert point_inside(zone["vertices"], 0.5, 2)
        assert not point_inside(zone["vertices"], 2, 2)
        assert not point_inside(zone["vertices"], 1, 2)
        assert not point_inside(zone["vertices"], True, 1)
        assert not point_inside(zone["vertices"], float("nan"), 1)
    assert validate_zone(zone_payload(coordinate_space="webxr-local", floor_y=0.2))["floor_y"] == 0.2


@pytest.mark.parametrize("vertices", [
    [{"x": 0, "z": 0}] * 3,
    [{"x": 0, "z": 0}, {"x": 1, "z": 1}, {"x": 2, "z": 2}],
    [{"x": 0, "z": 0}, {"x": 3, "z": 3}, {"x": 0, "z": 3}, {"x": 3, "z": 0}],
    [{"x": 0, "z": 0}, {"x": 2, "z": 0}, {"x": 1, "z": 0}, {"x": 1, "z": 1}, {"x": 0, "z": 1}],
    [{"x": 0, "z": 0}, {"x": 1, "z": 0}],
    [{"x": index, "z": index % 2} for index in range(17)],
    [{"x": 0, "z": 0}, {"x": 1, "z": 0}, {"x": 1, "z": 1, "y": 0}],
])
def test_geometry_rejects_degenerate_crossing_duplicate_and_unbounded_vertices(vertices):
    with pytest.raises(SpatialInputError):
        validate_zone(zone_payload(vertices=vertices))


@pytest.mark.parametrize("coordinate", [True, False, "1", None, float("nan"), float("inf"), -float("inf"), 10001])
def test_geometry_rejects_nonfinite_boolean_and_excessive_coordinates(coordinate):
    payload = zone_payload()
    payload["vertices"][1]["x"] = coordinate
    with pytest.raises(SpatialInputError):
        validate_zone(payload)


@pytest.mark.parametrize("payload", [
    {"action": "test_walk", "x": 1}, {"action": "test_position", "x": 1, "z": 1},
    {"action": "clear_zone", "target": "radio-motion"}, {"action": "set_target"},
    {"action": "set_target", "target": "person-tracker"}, [], None,
    zone_payload(name=" "), zone_payload(frame_id="x" * 97), zone_payload(floor_y=True),
    zone_payload(coordinate_space="gps"), zone_payload(unknown=True),
])
def test_spatial_payload_strict_fields_and_names(payload):
    with pytest.raises(SpatialInputError):
        validate_control(payload)


def test_outside_radio_disturbance_cannot_bypass_zone_gate():
    h = Harness()
    h.calibrate()
    h.shared("arm")
    assert h.feed(4, motion=True) == []
    assert h.detector.activity_score >= h.detector.threshold
    assert h.detector.alarm_state == "ARMED"
    assert h.spatial.guard(h.now, h.detector)["eligible"] is False


def test_test_walk_uses_one_latch_ack_inside_does_not_reenter_and_quiet_rearms():
    h = Harness()
    h.calibrate()
    h.shared("arm")
    h.control("test_walk")
    assert len(h.feed(5)) == 1
    active = h.detector.active_event_id
    assert h.detector.alarm_state == "ALARM" and h.spatial.position["inside"]
    h.shared("acknowledge")
    assert h.detector.events[0]["acknowledged"] and h.detector.active_event_id is None
    assert h.feed(5, motion=True) == []  # actor still inside, not another entry
    with pytest.raises(TransitionError):
        h.control("test_walk")
    h.control("reset_test_actor")
    h.feed(3)
    h.control("test_walk")
    assert len(h.feed(5)) == 1
    assert h.detector.active_event_id != active
    assert len(h.detector.events) == 2


def test_walk_disarmed_suppression_reset_does_not_silence_alarm():
    h = Harness()
    h.calibrate()
    h.control("test_walk")
    assert h.feed(5) == []
    with pytest.raises(TransitionError):
        h.shared("arm")
    h.control("reset_test_actor")
    h.feed(3)
    h.shared("arm")
    h.control("test_walk")
    assert len(h.feed(5)) == 1
    event = h.detector.active_event_id
    h.control("reset_test_actor")
    assert h.detector.alarm_state == "ALARM" and h.detector.active_event_id == event
    h.shared("disarm")
    h.control("test_walk")
    assert h.feed(5) == []
    assert h.detector.active_event_id is None


def test_zone_target_before_geometry_allowed_only_test_and_arm_blocked():
    detector = Detector(str(uuid4()), "TEST")
    spatial = SpatialController("TEST")
    spatial.control({"action": "set_target", "target": "zone-entry"}, detector, 100)
    assert spatial.target == "zone-entry"
    with pytest.raises(TransitionError):
        spatial.ensure_shared_control("arm", detector, 100)
    for mode in ("LIVE", "REPLAY"):
        spatial = SpatialController(mode)
        spatial.control(zone_payload(), detector, 100)
        assert spatial.zone and spatial.position is None
        for action in ({"action": "set_target", "target": "zone-entry"}, {"action": "test_walk"}, {"action": "reset_test_actor"}):
            with pytest.raises(TransitionError):
                spatial.control(action, detector, 100)
        assert not spatial.snapshot(100, detector)["localization"]["exact_zone_verified"]


@pytest.mark.parametrize("coordinate_space", ["webxr-local", "arkit-world"])
def test_ar_authored_draft_supports_only_explicit_synthetic_actor(coordinate_space):
    h = Harness()
    h.spatial.control(zone_payload(coordinate_space=coordinate_space, frame_id="AR-session-1", floor_y=0), h.detector, h.now)
    assert h.spatial.zone["units"].startswith("metres")
    state = h.spatial.snapshot(h.now, h.detector)
    assert state["localization"]["status"] == "test-simulated"
    assert state["localization"]["exact_zone_verified"] is False
    assert state["position"]["source_mode"] == "TEST" and state["position"]["synthetic"] is True
    assert state["position"]["frame_id"] == "AR-session-1"
    h.calibrate()
    h.shared("arm")
    h.control("test_walk")
    assert len(h.feed(5)) == 1
    assert h.detector.events[0]["source_mode"] == "TEST"
    annotation = h.spatial.event_annotation()
    assert annotation["coordinate_space"] == coordinate_space
    assert annotation["frame_id"] == "AR-session-1"
    assert annotation["synthetic"] is True and annotation["exact_zone_verified"] is False


def test_zone_changes_invalidate_baseline_require_disarm_and_clear_positions():
    h = Harness()
    h.calibrate()
    previous = copy.deepcopy(h.spatial.zone)
    h.spatial.control(zone_payload(frame_id="TEST-new-frame"), h.detector, h.now)
    assert not h.detector.ready
    assert h.spatial.zone["revision"] == previous["revision"] + 1
    assert h.spatial.position["frame_id"] == "TEST-new-frame"
    h.calibrate()
    h.shared("arm")
    for payload in (zone_payload(), {"action": "clear_zone"}, {"action": "set_target", "target": "radio-motion"}):
        with pytest.raises(TransitionError):
            h.spatial.control(payload, h.detector, h.now)
    h.shared("disarm")
    h.control("clear_zone")
    assert h.spatial.zone is None and h.spatial.position is None and not h.detector.ready


def test_stale_position_clears_pending_invalidates_baseline_and_needs_recalibration():
    h = Harness()
    h.calibrate()
    h.shared("arm")
    assert h.feed(1, motion=True, positions=False) == []
    assert not h.detector.ready and h.detector.alarm_state == "DISARMED"
    assert h.spatial.snapshot(h.now, h.detector)["localization"]["status"] == "stale"
    h.feed(1)
    assert not h.detector.ready
    with pytest.raises(TransitionError):
        h.shared("arm")
    h.calibrate()
    h.shared("arm")
    assert h.detector.alarm_state == "ARMED"


@pytest.mark.parametrize("change", [{"frame_id": "foreign-frame"}, {"coordinate_space": "arkit-world"}, {"x": float("nan")}, {"x": True}, {"source_mode": "LIVE"}, {"synthetic": False}, {"now": 1}])
def test_malformed_or_frame_mismatched_internal_position_cannot_grant_entry(change):
    h = Harness()
    h.calibrate()
    h.shared("arm")
    payload = {"x": 1.5, "z": 1.5, "frame_id": "TEST-frame-1", "coordinate_space": "test-plan", "now": h.now, "source_mode": "TEST", "synthetic": True}
    payload.update(change)
    assert h.spatial.accept_test_position(**payload, detector=h.detector) is False
    assert not h.spatial.guard(h.now, h.detector)["eligible"]
    assert not h.detector.ready and h.detector.alarm_state == "DISARMED"


def test_position_and_radio_faults_keep_one_alarm_latched():
    h = Harness()
    h.calibrate()
    h.shared("arm")
    h.control("test_walk")
    h.feed(5)
    active = h.detector.active_event_id
    h.feed(1, positions=False)
    assert h.detector.alarm_state == "ALARM" and h.detector.active_event_id == active
    h.detector.set_connection(False, "TEST disconnect", h.now)
    assert h.detector.health_status == "disconnected" and h.detector.active_event_id == active
    with pytest.raises(TransitionError):
        h.shared("acknowledge")
    h.shared("disarm")
    assert h.detector.alarm_state == "DISARMED"


class FakeCalls:
    def __init__(self):
        self.events = []

    async def handle_event(self, event):
        self.events.append(event)
        return {"status": "disabled", "detail": "TEST fake handler; no provider"}

    def snapshot(self):
        return {"enabled": False, "dry_run": True, "status": "disabled", "detail": "No provider"}


@pytest.mark.asyncio
async def test_spatial_api_auth_origin_host_payload_and_shared_state(tmp_path):
    app = create_app(Config(root=tmp_path, recording=False), call_manager=FakeCalls())
    token = (tmp_path / ".local/control-token").read_text().strip()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        assert (await client.post("/api/spatial/control", json=zone_payload())).status_code == 401
        await client.post("/api/session", json={"token": token})
        assert (await client.post("/api/spatial/control", json=zone_payload(), headers={"Origin": "https://foreign.test"})).status_code == 403
        assert (await client.post("/api/spatial/control", json=zone_payload(), headers={"Host": "evil.test"})).status_code == 400
        assert (await client.post("/api/spatial/control", content="x" * 4097)).status_code == 413
        assert (await client.post("/api/spatial/control", json={"action": "test_walk", "x": 1})).status_code == 422
        response = await client.post("/api/spatial/control", json=zone_payload())
        assert response.status_code == 200
        state = response.json()
        assert state["spatial"]["target"] == "radio-motion"
        assert state["spatial"]["zone"]["frame_id"] == "TEST-frame-1"
        assert state["spatial"]["position"]["synthetic"] is True
        response = await client.post("/api/spatial/control", json={"action": "set_target", "target": "zone-entry"})
        assert response.status_code == 200
        assert (await client.get("/api/state")).json()["spatial"]["target"] == "zone-entry"
        assert (await client.post("/api/control", json={"action": "arm"})).status_code == 409
        assert (await client.post("/api/control", json={"action": "disarm"})).status_code == 200
        for path, camera in (("/spatial", "camera=(self)"), ("/", "camera=()")):
            response = await client.get(path)
            assert camera in response.headers["permissions-policy"]
            assert "microphone=()" in response.headers["permissions-policy"]
            assert "geolocation=()" in response.headers["permissions-policy"]
            assert "connect-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "replay"])
@pytest.mark.parametrize("coordinate_space", ["test-plan", "webxr-local", "arkit-world"])
async def test_api_nonlive_localization_unavailable_without_opening_any_source(tmp_path, mode, coordinate_space):
    config = Config(root=tmp_path, mode=mode, serial_port="/DO-NOT-OPEN", replay_path=tmp_path/"not-read.jsonl", recording=False)
    app = create_app(config, call_manager=FakeCalls())
    token = (tmp_path / ".local/control-token").read_text().strip()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {token}"}) as client:
        assert (await client.post("/api/spatial/control", json=zone_payload(coordinate_space=coordinate_space))).status_code == 200
        for payload in ({"action": "set_target", "target": "zone-entry"}, {"action": "test_walk"}, {"action": "reset_test_actor"}):
            assert (await client.post("/api/spatial/control", json=payload)).status_code == 409
        spatial = (await client.get("/api/state")).json()["spatial"]
        assert spatial["position"] is None and spatial["localization"]["status"] == "unavailable"
    assert app.state.service.tasks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate_space", ["test-plan", "webxr-local", "arkit-world"])
async def test_service_shared_event_annotation_history_recording_and_disarm(tmp_path, coordinate_space):
    app = create_app(Config(root=tmp_path), call_manager=FakeCalls())
    service = app.state.service
    h = Harness()
    service.detector, service.spatial, service.source = h.detector, h.spatial, h.source
    h.spatial.control(zone_payload(coordinate_space=coordinate_space), h.detector, h.now)
    h.calibrate()
    h.shared("arm")
    h.control("test_walk")
    for _ in range(150):
        h.now += 1/30
        h.detector.tick(h.now)
        if h.spatial.tick(h.now, h.detector):
            h.motion_until = h.now + 3.5
        await service.emit(h.source.sample(h.now, motion=h.now < h.motion_until))
    await asyncio.gather(*list(service.call_tasks.values()))
    assert len(service.detector.events) == 1
    event = service.detector.events[0]
    assert event["type"] == "zone_entry" and event["source_mode"] == "TEST"
    assert event["spatial"]["synthetic"] is True and event["spatial"]["exact_zone_verified"] is False
    assert event["spatial"]["zone_revision"] == h.spatial.zone["revision"]
    assert event["spatial"]["coordinate_space"] == coordinate_space
    assert event["spatial"]["position"]["inside"] is True
    assert event["call_status"] == "disabled"
    assert service.call_manager.events == []  # no manager invocation even when disabled
    recorded = json.loads((service.recorder.path/"events.jsonl").read_text())
    assert recorded["type"] == "zone_entry" and recorded["spatial"]["synthetic"]
    await service.control("disarm")
    assert service.detector.active_event_id is None and service.detector.alarm_state == "DISARMED"
    assert service.spatial.pending_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_zone_event_never_invokes_call_manager_even_enabled_nonlive_config(tmp_path, enabled):
    class CallsWithNonliveOptin(FakeCalls):
        def snapshot(self):
            return {"enabled": enabled, "dry_run": False, "status": "pending", "detail": "Fake allow_nonlive enabled"}
    calls = CallsWithNonliveOptin()
    app = create_app(Config(root=tmp_path), call_manager=calls)
    service = app.state.service
    service.spatial.control(zone_payload(), service.detector, 100)
    service.spatial.control({"action": "set_target", "target": "zone-entry"}, service.detector, 100)
    event = {"event_id": str(uuid4()), "type": "zone_entry", "source_mode": "TEST", "acknowledged": False, "call_status": "pending", "call_detail": "Pending"}
    service.detector.alarm_state = "ALARM"
    service.detector.active_event_id = event["event_id"]
    await service._call(event)
    assert calls.events == []
    assert event["call_status"] == ("blocked_mode" if enabled else "disabled")
    assert "cannot invoke phone providers" in event["call_detail"]
    view = service.snapshot()["calls"]
    assert view["status"] == event["call_status"]
    assert calls.snapshot()["status"] == "pending"  # view did not mutate manager


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate_space", ["test-plan", "webxr-local", "arkit-world"])
async def test_real_test_source_tick_and_cross_view_http_controls_share_one_alarm(tmp_path, coordinate_space):
    calls = FakeCalls()
    app = create_app(Config(root=tmp_path, detector=DetectorConfig(calibration_seconds=0.2, calibration_min_samples=6)), call_manager=calls)
    service = app.state.service
    healthy, ready, alarm = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_emit = service.emit

    async def observe_emit(record):
        await original_emit(record)
        if service.detector.health_status == "healthy":
            healthy.set()
        if service.detector.alarm_state == "READY":
            ready.set()
        if service.detector.alarm_state == "ALARM":
            alarm.set()

    service.emit = observe_emit  # observation only; real source, detector, and ticks
    token = (tmp_path/".local/control-token").read_text().strip()
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(healthy.wait(), 2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {token}"}) as client:
            assert (await client.post("/api/spatial/control", json=zone_payload(coordinate_space=coordinate_space))).status_code == 200
            assert (await client.post("/api/spatial/control", json={"action": "set_target", "target": "zone-entry"})).status_code == 200
            assert (await client.post("/api/control", json={"action": "calibrate"})).status_code == 200
            await asyncio.wait_for(ready.wait(), 2)
            assert (await client.post("/api/control", json={"action": "arm"})).status_code == 200
            assert (await client.post("/api/spatial/control", json={"action": "test_walk"})).status_code == 200
            await asyncio.wait_for(alarm.wait(), 5)
            state = (await client.get("/api/state")).json()
            assert state["alarm_state"] == "ALARM" and len(state["events"]) == 1
            assert state["events"][0]["type"] == "zone_entry"
            assert state["spatial"]["position"]["inside"] is True
            active = state["active_event_id"]
            state = (await client.post("/api/spatial/control", json={"action": "reset_test_actor"})).json()
            assert state["active_event_id"] == active and state["alarm_state"] == "ALARM"
            state = (await client.post("/api/control", json={"action": "acknowledge"})).json()
            assert state["alarm_state"] == "ARMED" and state["active_event_id"] is None
            assert state["events"][0]["acknowledged"] is True
            state = (await client.post("/api/control", json={"action": "disarm"})).json()
            assert state["alarm_state"] == "DISARMED" and state["spatial"]["target"] == "zone-entry"
            assert calls.events == []
    assert not service.tasks and not service.call_tasks


def test_stale_position_cannot_start_walk_or_sneak_recovery_through_reset():
    h = Harness()
    h.calibrate()
    h.shared("arm")
    h.now += 0.8
    with pytest.raises(TransitionError):
        h.control("test_walk")
    assert not h.detector.ready
    h.control("reset_test_actor")
    assert h.spatial.fresh(h.now, h.detector)
    assert not h.detector.ready and h.detector.alarm_state == "DISARMED"


def test_first_observed_inside_position_is_not_an_outside_to_inside_entry():
    h = Harness()
    h.calibrate()
    h.shared("arm")
    h.spatial.position = h.spatial.position_time = None
    entered = h.spatial.accept_test_position(1, 1, frame_id="TEST-frame-1", coordinate_space="test-plan", now=h.now, detector=h.detector)
    assert entered is False and h.spatial.pending_until is None
    assert not h.spatial.guard(h.now, h.detector)["eligible"]


@pytest.mark.parametrize("coordinate_space", ["webxr-local", "arkit-world"])
@pytest.mark.parametrize("change", [{"frame_id": "new-AR-origin"}, {"coordinate_space": "test-plan"}, {"source_mode": "LIVE"}, {"synthetic": False}])
def test_ar_frame_does_not_relax_position_provenance_or_frame_guards(coordinate_space, change):
    h = Harness()
    h.spatial.control(zone_payload(coordinate_space=coordinate_space, frame_id="AR-original-frame"), h.detector, h.now)
    h.calibrate()
    h.shared("arm")
    payload = {"x": 1.5, "z": 1.5, "frame_id": "AR-original-frame", "coordinate_space": coordinate_space, "now": h.now, "source_mode": "TEST", "synthetic": True}
    payload.update(change)
    assert not h.spatial.accept_test_position(**payload, detector=h.detector)
    assert not h.spatial.guard(h.now, h.detector)["eligible"]
    assert not h.detector.ready and h.detector.alarm_state == "DISARMED"
    assert not h.detector.events
