"""Deterministic harness evidence tests. No app imports, servers or real controls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from scripts import spatial_smoke_test as smoke

SESSION = "harness-fixture-session"
ZONE = {"id": "fixture-zone", "revision": 7, "name": "API TEST zone", "coordinate_space": "test-plan",
        "frame_id": "api-test-frame", "floor_y": 0.0, "units": "unmeasured TEST-plan units",
        "vertices": [{"x": 0, "z": 0}, {"x": 4, "z": 0}, {"x": 4, "z": 3}, {"x": 0, "z": 3}]}
BASE = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
REJECTED = (smoke.CheckFailure, smoke.ObservationError)


def wall(t):
    return (BASE + timedelta(seconds=t)).isoformat()


def snapshot(t, *, inside=False, score=6.0, alarm="ARMED", running=False):
    return {"session_id": SESSION, "source_mode": "TEST", "server_time": wall(t + 0.02),
            "calls": {"enabled": False}, "alarm_state": alarm, "active_event_id": None, "events": [],
            "calibration": {"ready": True}, "recording": {"error": None},
            "health": {"status": "healthy", "valid_packets": 100 + round(t * 30), "sample_age_s": 0.02,
                       "packet_rate_hz": 30, "last_sample_at": wall(t), "serial_port": None},
            "features": {"monotonic_s": 10000 + t, "activity_score": score, "threshold": 3.0},
            "spatial": {"target": "zone-entry", "zone": deepcopy(ZONE),
                        "localization": {"status": "test-simulated", "provenance": "TEST synthetic position simulator",
                                         "exact_zone_verified": False},
                        "position": {"x": 2.0 if inside else -1.0, "z": 1.0, "frame_id": "api-test-frame",
                                     "inside": inside, "updated_at": wall(t), "source_mode": "TEST", "synthetic": True},
                        "test_actor": {"running": running, "phase": "inside" if inside else "outside",
                                       "can_start": not inside and not running, "can_reset": True}}}


def event(t=1.2):
    return {"event_id": "fixture-event", "session_id": SESSION, "type": "zone_entry", "source_mode": "TEST",
            "occurred_at": wall(t + 0.02), "acknowledged": False, "call_status": "disabled",
            "spatial": {"zone_id": ZONE["id"], "zone_revision": ZONE["revision"], "coordinate_space": "test-plan",
                        "frame_id": "api-test-frame", "source_mode": "TEST", "synthetic": True,
                        "exact_zone_verified": False, "confirmation": smoke.CONFIRMATION,
                        "position": snapshot(t, inside=True)["spatial"]["position"]}}


def change(data, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        data = data[key]
    data[keys[-1]] = value


@pytest.mark.parametrize("inside", [False, True])
def test_joint_activity_counts_only_fresh_radio_and_matching_position(inside):
    proof = smoke.ActivityProof(ZONE, SESSION, inside)
    for i in range(7):
        proof.add(snapshot(i * 0.2, inside=inside), i * 0.2)
    result = proof.finish()
    assert result["fresh_radio_packets"] == 36
    assert result["fresh_position_updates"] == 6
    assert result["observations"] == 7
    assert result["longest_above_seconds"] == pytest.approx(1.2)
    assert result["duration_seconds"] == pytest.approx(1.2)
    assert result["expected_inside"] is inside


@pytest.mark.parametrize("path,value", [
    ("source_mode", "LIVE"), ("session_id", "other"), ("calls.enabled", True), ("calls.enabled", 0),
    ("health.status", "stale"), ("health.sample_age_s", 1.1), ("health.sample_age_s", -0.1),
    ("health.sample_age_s", False), ("health.packet_rate_hz", 9), ("health.valid_packets", True),
    ("health.valid_packets", 100), ("health.last_sample_at", wall(0)),
    ("health.last_sample_at", wall(-100)), ("health.serial_port", "/dev/not-opened"),
    ("features.monotonic_s", 10000.0), ("features.monotonic_s", float("nan")),
    ("features.activity_score", float("inf")), ("features.activity_score", True),
    ("features.threshold", float("nan")), ("features.threshold", 4),
    ("spatial.position", None), ("spatial.position.frame_id", "wrong-frame"),
    ("spatial.position.updated_at", wall(0)), ("spatial.position.updated_at", wall(-100)),
    ("spatial.position.updated_at", "not-a-time"), ("spatial.position.updated_at", "2026-09-12T00:00:00"),
    ("spatial.position.updated_at", wall(100)), ("spatial.position.inside", True),
    ("spatial.position.inside", 0), ("spatial.position.x", 2), ("spatial.position.x", False),
    ("spatial.position.x", float("nan")), ("spatial.position.source_mode", "REPLAY"),
    ("spatial.position.synthetic", 1), ("spatial.position.synthetic", False),
    ("spatial.localization.status", "stale"), ("spatial.localization.status", "unavailable"),
    ("spatial.localization.exact_zone_verified", True), ("spatial.localization.exact_zone_verified", 0),
    ("spatial.localization.provenance", "measured person"), ("spatial.zone.revision", True),
    ("spatial.zone.revision", 8), ("spatial.zone.coordinate_space", "arkit-world"),
])
def test_joint_proof_rejects_wrong_frame_inside_and_false_freshness(path, value):
    proof = smoke.ActivityProof(ZONE, SESSION, False)
    proof.add(snapshot(0), 0)
    data = snapshot(0.2)
    change(data, path, value)
    with pytest.raises(REJECTED):
        proof.add(data, 0.2)


def test_expected_inside_is_not_inferred_from_backend_flag():
    with pytest.raises(smoke.CheckFailure):
        smoke.ActivityProof(ZONE, SESSION, False).add(snapshot(0, inside=True), 0)
    with pytest.raises(smoke.CheckFailure):
        smoke.ActivityProof(ZONE, SESSION, True).add(snapshot(0), 0)


def test_cannot_bridge_observation_gap_or_nonadvancing_monotonic_sample():
    proof = smoke.ActivityProof(ZONE, SESSION, False)
    proof.add(snapshot(0), 0)
    with pytest.raises(smoke.CheckFailure):
        proof.add(snapshot(0.8), 0.8)
    data = snapshot(0.2)
    data["health"]["sample_age_s"] = 0.3  # Healthy/advancing count cannot hide old sample clock.
    with pytest.raises(smoke.CheckFailure):
        proof.add(data, 0.2)


def test_above_threshold_must_be_sustained_not_totalled_across_quiet():
    proof = smoke.ActivityProof(ZONE, SESSION, False)
    for i in range(7):
        proof.add(snapshot(i * 0.2, score=0 if i == 3 else 6), i * 0.2)
    with pytest.raises(smoke.CheckFailure, match="sustained"):
        proof.finish()


def test_walk_activity_counts_inside_span_not_earlier_outside_activity():
    proof = smoke.ActivityProof(ZONE, SESSION, None, activity_inside=True)
    for i in range(7):
        proof.add(snapshot(i * 0.2, inside=i >= 5), i * 0.2)
    with pytest.raises(smoke.CheckFailure, match="sustained"):
        proof.finish()


def test_wall_clock_jumps_cannot_supply_monotonic_debounce_time():
    proof = smoke.ActivityProof(ZONE, SESSION, False)
    for i in range(7):
        data = snapshot(i * 1000)
        data["features"]["monotonic_s"] = 10000 + i * 0.05
        proof.add(data, i * 0.05)
    with pytest.raises(smoke.CheckFailure, match="sustained"):
        proof.finish()


def test_complete_annotation_is_accepted():
    assert smoke.validate_annotation(event(), ZONE, SESSION)["exact_zone_verified"] is False


@pytest.mark.parametrize("path,value", [
    ("spatial", None), ("spatial", {}), ("spatial", []), ("session_id", "other"),
    ("source_mode", "LIVE"), ("type", "motion_near_entrance"), ("spatial.zone_id", "wrong-zone"),
    ("spatial.zone_revision", 1), ("spatial.zone_revision", True), ("spatial.zone_revision", "7"),
    ("spatial.coordinate_space", "arkit-world"), ("spatial.frame_id", "old-frame"),
    ("spatial.source_mode", "LIVE"), ("spatial.synthetic", 1), ("spatial.synthetic", False),
    ("spatial.synthetic", "true"), ("spatial.exact_zone_verified", True),
    ("spatial.exact_zone_verified", 0), ("spatial.exact_zone_verified", None),
    ("spatial.confirmation", "physical localization confirmed"),
    ("spatial.position", {}), ("spatial.position.x", float("nan")),
    ("spatial.position.x", float("inf")), ("spatial.position.x", True),
    ("spatial.position.x", "2"), ("spatial.position.x", 0), ("spatial.position.x", 4),
    ("spatial.position.x", 8), ("spatial.position.z", 0), ("spatial.position.z", 3),
    ("spatial.position.frame_id", "old-frame"), ("spatial.position.inside", False),
    ("spatial.position.inside", 1), ("spatial.position.source_mode", "LIVE"),
    ("spatial.position.synthetic", 1), ("spatial.position.updated_at", "broken"),
    ("spatial.position.updated_at", wall(-10)), ("occurred_at", None),
])
def test_malformed_or_wrong_annotation_never_passes(path, value):
    data = event()
    change(data, path, value)
    with pytest.raises(REJECTED):
        smoke.validate_annotation(data, ZONE, SESSION)


@pytest.mark.parametrize("field", ["zone_id", "zone_revision", "frame_id", "coordinate_space", "position", "confirmation"])
def test_missing_annotation_fields_are_unknown(field):
    data = event()
    del data["spatial"][field]
    with pytest.raises(smoke.ObservationError):
        smoke.validate_annotation(data, ZONE, SESSION)


@pytest.mark.parametrize("armed", [False, True])
def test_walk_proves_observed_transition_completes_inside_and_checks_every_state(armed):
    proof = smoke.WalkProof(ZONE, SESSION, set(), armed=armed)
    for i in range(9):
        data = snapshot(i * 0.2, inside=i >= 2, running=0 < i < 8, alarm="ARMED" if armed else "DISARMED")
        if armed and i >= 6:
            data.update(alarm_state="ALARM", active_event_id="fixture-event", events=[event()])
            data["events"][0]["call_status"] = "pending" if i == 6 else "disabled"
        proof.add(data, i * 0.2)
    result = proof.finish()
    assert result["completed_inside"] is True and result["observed_outside_to_inside"] is True
    assert result["entry_after_seconds"] == pytest.approx(0.4)
    assert result["completion_after_seconds"] == pytest.approx(1.6)
    assert result["activity_inside"] is True
    assert (proof.event is not None) is armed


def test_positive_walk_rejects_alarm_while_still_outside():
    proof = smoke.WalkProof(ZONE, SESSION, set(), armed=True)
    proof.add(snapshot(0), 0)
    data = snapshot(0.2, running=True, alarm="ALARM")
    data.update(events=[event()], active_event_id="fixture-event")
    with pytest.raises(smoke.CheckFailure, match="outside-to-inside"):
        proof.add(data, 0.2)


def test_walk_cannot_start_inside_or_complete_outside():
    proof = smoke.WalkProof(ZONE, SESSION, set(), armed=False)
    with pytest.raises(smoke.CheckFailure):
        proof.add(snapshot(0, inside=True, alarm="DISARMED"), 0)
    proof = smoke.WalkProof(ZONE, SESSION, set(), armed=False)
    proof.add(snapshot(0, alarm="DISARMED"), 0)
    proof.add(snapshot(0.2, running=True, alarm="DISARMED"), 0.2)
    with pytest.raises(smoke.CheckFailure, match="complete inside"):
        proof.add(snapshot(0.4, alarm="DISARMED"), 0.4)


def test_completed_walk_without_alarm_and_unobserved_running_do_not_pass():
    proof = smoke.WalkProof(ZONE, SESSION, set(), armed=True)
    proof.add(snapshot(0), 0)
    with pytest.raises(smoke.CheckFailure, match="observed TEST trajectory"):
        proof.add(snapshot(0.2, inside=True), 0.2)
    proof = smoke.WalkProof(ZONE, SESSION, set(), armed=True)
    for i in range(9):
        proof.add(snapshot(i * 0.2, inside=i >= 2, running=0 < i < 8), i * 0.2)
    with pytest.raises(smoke.CheckFailure, match="did not alarm"):
        proof.finish()


@pytest.mark.parametrize("path,value", [("spatial.target", "radio-motion"), ("spatial.zone.revision", 8),
                                        ("alarm_state", "ARMED"), ("active_event_id", "new"),
                                        ("calibration.ready", False), ("features.threshold", 9)])
def test_denied_mutation_fingerprint_catches_side_effects(path, value):
    before = snapshot(0, alarm="DISARMED")
    after = snapshot(0.2, alarm="DISARMED")
    assert smoke.mutation_snapshot(before) == smoke.mutation_snapshot(after)
    change(after, path, value)
    assert smoke.mutation_snapshot(before) != smoke.mutation_snapshot(after)


class FakeClient:
    """Immediate in-memory HTTP results; never opens sockets or app controls."""
    def __init__(self, data=None, *, post_error=None, disarm=True):
        self.data = deepcopy(data if data is not None else snapshot(0))
        self.calls = []
        self.post_error, self.disarm = post_error, disarm

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "POST" and kwargs["json"]["action"] == "disarm":
            if self.disarm:
                self.data["alarm_state"] = "DISARMED"
            if self.post_error:
                raise self.post_error
        return httpx.Response(200, json=deepcopy(self.data))


@pytest.mark.asyncio
async def test_expired_scenario_body_still_attempts_and_verifies_independent_cleanup():
    client = FakeClient()
    with pytest.raises(smoke.CheckFailure, match="deadline expired"):
        await smoke.read_state(client, 99, SESSION, clock=lambda: 100)
    assert client.calls == []
    result = await smoke.cleanup(client, SESSION, clock=lambda: 100)
    assert result["status"] == "PASS" and result["disarm_attempted"] and result["disarmed"]
    assert result["final_verified"] and result["same_session"]
    assert [(m, p) for m, p, _ in client.calls] == [
        ("GET", "/api/state"), ("POST", "/api/control"), ("GET", "/api/state"),
        ("POST", "/api/spatial/control"), ("GET", "/api/state")]
    assert all(0 < kwargs["timeout"] <= 3 for _, _, kwargs in client.calls)
    assert smoke.final_status("FAIL", False, result["status"], True) == "FAIL"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [("source_mode", "LIVE"), ("source_mode", "REPLAY"),
                                        ("calls.enabled", True), ("calls.enabled", 0), ("session_id", "other")])
async def test_cleanup_refuses_mutation_without_same_test_calls_disabled_session(path, value):
    data = snapshot(0)
    change(data, path, value)
    client = FakeClient(data)
    result = await smoke.cleanup(client, SESSION, clock=lambda: 100)
    assert result["status"] != "PASS" and not result["disarm_attempted"]
    assert all(method == "GET" for method, _, _ in client.calls)


@pytest.mark.asyncio
async def test_cleanup_verifies_even_after_post_timeout_but_does_not_promote_unknown():
    client = FakeClient(post_error=TimeoutError("fixture"))
    result = await smoke.cleanup(client, SESSION, clock=lambda: 100)
    assert result["status"] == "UNKNOWN" and result["disarmed"] and result["final_verified"]
    assert result["disarm_error_type"] == "TimeoutError"
    assert [method for method, _, _ in client.calls] == ["GET", "POST", "GET"]
    assert smoke.final_status("RUNNING", True, result["status"], True) == "UNKNOWN"


@pytest.mark.asyncio
async def test_cleanup_failed_disarm_or_expired_independent_budget_cannot_pass():
    client = FakeClient(disarm=False)
    result = await smoke.cleanup(client, SESSION, clock=lambda: 100)
    assert result["status"] == "FAIL" and not result["disarmed"]
    assert [method for method, _, _ in client.calls] == ["GET", "POST", "GET"]
    client = FakeClient()
    result = await smoke.cleanup(client, SESSION, seconds=0, clock=lambda: 100)
    assert result["status"] == "FAIL" and not result["disarm_attempted"] and client.calls == []


@pytest.mark.asyncio
async def test_cleanup_rechecks_session_after_disarm():
    class RestartClient(FakeClient):
        async def request(self, method, path, **kwargs):
            response = await super().request(method, path, **kwargs)
            if method == "POST":
                self.data["session_id"] = "restarted"
            return response
    client = RestartClient()
    result = await smoke.cleanup(client, SESSION, clock=lambda: 100)
    assert result["status"] == "FAIL" and not result["final_verified"]
    assert [method for method, _, _ in client.calls] == ["GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_malformed_state_is_unknown_in_cleanup():
    client = FakeClient()
    client.data["alarm_state"] = None
    client.disarm = False
    result = await smoke.cleanup(client, SESSION, clock=lambda: 100)
    assert result["status"] == "UNKNOWN" and result["error_type"] == "ObservationError"


@pytest.mark.asyncio
@pytest.mark.parametrize("last_alarm", ["ARMED", None])
async def test_cleanup_cannot_reuse_earlier_disarmed_proof_after_actor_reset(last_alarm):
    class ResetClient(FakeClient):
        async def request(self, method, path, **kwargs):
            response = await super().request(method, path, **kwargs)
            if method == "POST" and path == "/api/spatial/control":
                self.data["alarm_state"] = last_alarm
            return response
    result = await smoke.cleanup(ResetClient(), SESSION, clock=lambda: 100)
    assert result["status"] != "PASS" and result["disarmed"] is False
    assert result["initial_disarm_verified"] is True


@pytest.mark.parametrize("scenario", ["FAIL", "UNKNOWN"])
@pytest.mark.parametrize("cleanup_status", ["PASS", "FAIL", "UNKNOWN"])
def test_cleanup_success_cannot_promote_body_failure(scenario, cleanup_status):
    assert smoke.final_status(scenario, True, cleanup_status, True) == scenario


def test_pass_requires_all_proofs_cleanup_and_stable_hashes():
    assert smoke.final_status("RUNNING", True, "PASS", True) == "PASS"
    assert smoke.final_status("RUNNING", False, "PASS", True) == "UNKNOWN"
    assert smoke.final_status("RUNNING", True, "PASS", False) == "UNKNOWN"
    assert smoke.final_status("RUNNING", True, "missing", True) == "UNKNOWN"
    assert smoke.final_status("missing", True, "PASS", True) == "UNKNOWN"
    assert smoke.final_status("RUNNING", 1, "PASS", True) == "UNKNOWN"


@pytest.mark.parametrize("port", [True, 0, 80, 65536, 8765.0, "8765", "https://example.invalid"])
def test_endpoint_is_fixed_loopback_and_only_a_reserved_numeric_port(port):
    with pytest.raises(ValueError):
        smoke.loopback_url(port)
    assert smoke.loopback_url(18867) == "http://127.0.0.1:18867"


def test_token_path_is_confined_to_project_local_even_through_symlinks(tmp_path, monkeypatch):
    root = tmp_path / "project"
    local = root / ".local"
    local.mkdir(parents=True)
    nested = local / "api" / ".local"
    nested.mkdir(parents=True)
    token = nested / "control-token"
    token.write_text("fixture-not-a-secret")
    outside = tmp_path / "control-token"
    outside.write_text("fixture-not-a-secret")
    (local / "escape").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(smoke, "ROOT", root)
    assert smoke.token_path(".local/api/.local/control-token") == token
    assert smoke.token_path(token) == token
    for bad in (outside, local / "escape" / "control-token"):
        with pytest.raises(ValueError):
            smoke.token_path(bad)
