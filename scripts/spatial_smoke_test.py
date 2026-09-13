"""Lead-owned bounded black-box TEST check of the shared spatial alarm API.

Mutates the existing loopback TEST app. Run ONLY after the lead has control.
No physical ports, camera, credentials in logs, provider calls, or LAN exposure.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4
import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
# Pin the software geometry oracle independently of threshold.spatial.
RECTANGLE = [{"x": 0, "z": 0}, {"x": 4, "z": 0}, {"x": 4, "z": 3}, {"x": 0, "z": 3}]


class CheckFailure(Exception):
    """A well-formed observation disproves a required invariant."""


class ObservationError(Exception):
    """Malformed or unavailable evidence is UNKNOWN, never a passing check."""


POSITION_STALE_SECONDS = 0.75  # Frozen backend contract, not a wall-clock duration.
RADIO_STALE_SECONDS = 1.0
CONFIRMATION = "TEST simulated outside-to-inside transition; not physical localization"
SOURCE_PATHS = ("scripts/spatial_smoke_test.py", "tests/test_spatial_smoke.py",
                "docs/SPATIAL-CONTRACT.md", "docs/SPATIAL-BACKEND.md") + tuple(
                    str(path.relative_to(ROOT)) for path in sorted((ROOT / "threshold").glob("*.py")))


def source_hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_PATHS}


def loopback_url(port):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Use only the lead-reserved TEST port (1024..65535)")
    return f"http://127.0.0.1:{port}"


def require(ok, reason):
    if not ok:
        raise CheckFailure(reason)


def obj(value, required=()):
    if not isinstance(value, dict) or not set(required) <= set(value):
        raise ObservationError("Expected a complete observation object")
    return value


def boolean(value):
    if type(value) is not bool:
        raise ObservationError("Expected an exact boolean")
    return value


def alarm_state(data):
    value = data.get("alarm_state")
    if value not in ("DISARMED", "CALIBRATING", "READY", "ARMED", "ALARM"):
        raise ObservationError("Malformed shared alarm state")
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ObservationError("Expected a finite number, not a boolean")
    return value


def integer(value):
    if type(value) is not int or value < 0:
        raise ObservationError("Expected a nonnegative integer")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ObservationError("Expected nonempty text")
    return value


def stamp(value):
    # Wall times are display/update identities. Only compare wall with wall;
    # NEVER use these to accumulate the monotonic debounce duration.
    try:
        parsed = datetime.fromisoformat(text(value))
        if parsed.utcoffset() is None:
            raise ValueError
    except (ValueError, OverflowError) as error:
        raise ObservationError("Expected a timezone-aware wall timestamp") from error
    return value


def wall_fresh(updated_at, server_time, limit):
    age = (datetime.fromisoformat(stamp(server_time)) - datetime.fromisoformat(stamp(updated_at))).total_seconds()
    if age < 0:
        raise ObservationError("Wall clock ordering is uncertain")
    require(age <= limit, "reported fresh timestamp is stale in its own wall-clock domain")


def safe_session(data, session_id=None):
    data = obj(data)
    require(text(data.get("source_mode")) == "TEST", "refusing non-TEST server")
    enabled = obj(data.get("calls")).get("enabled")
    if type(enabled) is not bool:
        raise ObservationError("Malformed calls-enabled flag")
    require(enabled is False, "refusing call-enabled server")
    current = text(data.get("session_id"))
    require(session_id is None or current == session_id, "server session changed during check")
    return current


def validate_zone(zone):
    zone = obj(zone, ("id", "revision", "name", "coordinate_space", "frame_id", "vertices", "floor_y", "units"))
    text(zone.get("id"))
    require(integer(zone.get("revision")) > 0, "invalid saved zone revision")
    require(zone.get("name") == "API TEST zone" and zone.get("coordinate_space") == "test-plan"
            and zone.get("frame_id") == "api-test-frame", "saved zone frame/configuration mismatch")
    vertices = zone.get("vertices")
    if not isinstance(vertices, list) or len(vertices) != 4:
        raise ObservationError("Expected the four-vertex TEST rectangle")
    for vertex, expected in zip(vertices, RECTANGLE):
        vertex = obj(vertex)
        require(set(vertex) == {"x", "z"}, "unexpected rectangle fields")
        require(all(number(vertex.get(axis)) == expected[axis] for axis in ("x", "z")),
                "saved rectangle geometry mismatch")
    require(number(zone.get("floor_y")) == 0 and zone.get("units") == "unmeasured TEST-plan units",
            "saved TEST units/floor mismatch")
    return zone


def validate_position(position, zone, expected_inside=None):
    position = obj(position, ("x", "z", "inside", "frame_id", "source_mode", "synthetic", "updated_at"))
    x, z = number(position.get("x")), number(position.get("z"))
    inside = position.get("inside")
    if type(inside) is not bool:
        raise ObservationError("Malformed inside flag")
    require(position.get("frame_id") == zone["frame_id"], "position frame mismatch")
    require(text(position.get("source_mode")) == "TEST" and boolean(position.get("synthetic")) is True,
            "position lacks exact TEST/synthetic provenance")
    require(inside is (0 < x < 4 and 0 < z < 3), "independent rectangle membership disagrees")
    require(expected_inside is None or inside is expected_inside, "unexpected inside/outside position")
    stamp(position.get("updated_at"))
    return position


def validate_spatial(data, zone, expected_inside=None):
    spatial = obj(data.get("spatial"), ("zone", "target", "localization", "position"))
    require(validate_zone(spatial.get("zone")) == zone and spatial.get("target") == "zone-entry",
            "saved zone/target changed during observation")
    localization = obj(spatial.get("localization"), ("status", "provenance", "exact_zone_verified"))
    require(localization.get("status") == "test-simulated"
            and localization.get("provenance") == "TEST synthetic position simulator"
            and boolean(localization.get("exact_zone_verified")) is False,
            "fresh explicit TEST localization is unavailable")
    position = validate_position(spatial.get("position"), zone, expected_inside)
    wall_fresh(position["updated_at"], data.get("server_time"), POSITION_STALE_SECONDS)
    return position


def event_ids(data):
    events = data.get("events")
    if not isinstance(events, list):
        raise ObservationError("Malformed event history")
    ids = [text(obj(event).get("event_id")) for event in events]
    require(len(set(ids)) == len(ids), "duplicate event identity")
    return set(ids)


def validate_annotation(event, zone, session_id):
    event = obj(event, ("type", "source_mode", "session_id", "spatial", "occurred_at"))
    require(text(event.get("type")) == "zone_entry" and text(event.get("source_mode")) == "TEST"
            and event.get("session_id") == session_id, "event type/source/session mismatch")
    annotation = obj(event.get("spatial"), ("zone_id", "zone_revision", "coordinate_space", "frame_id",
                                                "source_mode", "synthetic", "exact_zone_verified", "confirmation", "position"))
    require(annotation.get("zone_id") == zone["id"]
            and integer(annotation.get("zone_revision")) == zone["revision"]
            and annotation.get("coordinate_space") == zone["coordinate_space"]
            and annotation.get("frame_id") == zone["frame_id"], "event zone revision/frame mismatch")
    require(text(annotation.get("source_mode")) == "TEST" and boolean(annotation.get("synthetic")) is True
            and boolean(annotation.get("exact_zone_verified")) is False
            and annotation.get("confirmation") == CONFIRMATION, "event TEST confirmation mismatch")
    position = validate_position(annotation.get("position"), zone, True)
    wall_fresh(position["updated_at"], event.get("occurred_at"), POSITION_STALE_SECONDS)
    return annotation


class ActivityProof:
    """Count only consecutive jointly valid radio + position observations."""

    def __init__(self, zone, session_id, expected_inside, *, activity_inside=None):
        self.zone, self.session_id, self.expected_inside = zone, session_id, expected_inside
        self.activity_inside = expected_inside if activity_inside is None else activity_inside
        self.previous = None
        self.observations = self.packets = self.position_updates = 0
        self.peak = self.longest_above = 0.0
        self.above_since = self.started = None
        self.threshold = None
        self.position_stamps, self.sample_stamps = set(), set()

    def add(self, data, now):
        safe_session(data, self.session_id)
        position = validate_spatial(data, self.zone, self.expected_inside)
        health = obj(data.get("health"), ("status", "sample_age_s", "packet_rate_hz", "valid_packets", "last_sample_at", "serial_port"))
        features = obj(data.get("features"), ("monotonic_s", "activity_score", "threshold"))
        require(health.get("status") == "healthy", "unhealthy TEST source during proof")
        age, rate = number(health.get("sample_age_s")), number(health.get("packet_rate_hz"))
        require(0 <= age <= RADIO_STALE_SECONDS and rate >= 10, "radio sample is stale or inadequate")
        require(health.get("serial_port") is None, "TEST source unexpectedly reports a serial port")
        packets = integer(health.get("valid_packets"))
        sample_stamp = stamp(health.get("last_sample_at"))
        wall_fresh(sample_stamp, data.get("server_time"), RADIO_STALE_SECONDS)
        snapshot_time = number(features.get("monotonic_s"))
        sample_time = snapshot_time - age  # Both values use the backend monotonic clock.
        score, threshold = number(features.get("activity_score")), number(features.get("threshold"))
        require(score >= 0 and threshold > 0, "invalid activity/threshold range")
        now = number(now)
        if self.threshold is not None:
            require(threshold == self.threshold, "threshold changed during proof")
        if self.previous is not None:
            previous = self.previous
            require(0 < now - previous["now"] <= POSITION_STALE_SECONDS,
                    "observation gap exceeds position freshness bound")
            require(snapshot_time > previous["snapshot"] and sample_time > previous["sample_time"]
                    and packets > previous["packets"], "TEST radio samples did not advance")
            require(sample_stamp not in self.sample_stamps and position["updated_at"] not in self.position_stamps,
                    "radio/position update identity did not advance")
            self.packets += packets - previous["packets"]
            self.position_updates += 1
        else:
            self.started = now
        self.threshold = threshold
        self.sample_stamps.add(sample_stamp)
        self.position_stamps.add(position["updated_at"])
        self.observations += 1
        self.peak = max(self.peak, score)
        if score > threshold and (self.activity_inside is None or position["inside"] is self.activity_inside):
            self.above_since = now if self.above_since is None else self.above_since
            self.longest_above = max(self.longest_above, now - self.above_since)
        else:
            self.above_since = None
        self.previous = {"now": now, "snapshot": snapshot_time, "sample_time": sample_time, "packets": packets}
        return position

    def finish(self, *, require_activity=True):
        require(self.observations >= 2 and self.packets >= 10, "insufficient fresh joint observations")
        require(not require_activity or self.longest_above >= 0.7,
                "no jointly fresh sustained >=0.7s above-threshold activity")
        return {"observations": self.observations, "fresh_radio_packets": self.packets,
                "fresh_position_updates": self.position_updates, "expected_inside": self.expected_inside,
                "activity_inside": self.activity_inside, "peak_activity": self.peak, "threshold": self.threshold,
                "longest_above_seconds": round(self.longest_above, 6),
                "duration_seconds": round(self.previous["now"] - self.started, 6)}


class WalkProof:
    def __init__(self, zone, session_id, before_ids, *, armed):
        self.activity = ActivityProof(zone, session_id, None, activity_inside=True)
        self.before_ids, self.armed = before_ids, armed
        self.previous_inside = None
        self.saw_running = self.transition = self.complete = False
        self.event = None
        self.outside_at = self.inside_at = self.completed_at = None

    def add(self, data, now):
        position = self.activity.add(data, now)
        actor = obj(obj(data.get("spatial")).get("test_actor"))
        if type(actor.get("running")) is not bool:
            raise ObservationError("Malformed trajectory running flag")
        require(actor.get("phase") in ("outside", "approaching", "inside"), "TEST actor unavailable")
        if self.previous_inside is None:
            require(position["inside"] is False and actor["running"] is False, "walk must be observed outside before start")
            self.outside_at = now
        if self.previous_inside is False and position["inside"] is True:
            require(self.saw_running or actor["running"], "entry without an observed TEST trajectory")
            self.transition, self.inside_at = True, now
        require(not self.transition or position["inside"] is True, "TEST walk left the rectangle after entry")
        self.previous_inside = position["inside"]
        self.saw_running |= actor["running"]
        ids = event_ids(data)
        if self.armed:
            new_ids = ids - self.before_ids
            require(self.before_ids <= ids and len(new_ids) <= 1, "unexpected shared event history change")
            if new_ids:
                require(self.transition and position["inside"] is True, "alarm without observed outside-to-inside transition")
                event = next(event for event in data["events"] if event["event_id"] in new_ids)
                validate_annotation(event, self.activity.zone, self.activity.session_id)
                require(alarm_state(data) == "ALARM" and data.get("active_event_id") == event["event_id"],
                        "zone event is not the shared latched alarm")
                require(self.event is None or all(event.get(key) == self.event.get(key) for key in
                        ("event_id", "session_id", "type", "source_mode", "spatial", "occurred_at", "acknowledged")),
                        "latched event changed during walk")
                self.event = copy.deepcopy(event)
            else:
                require(self.event is None and alarm_state(data) == "ARMED", "unexpected alarm before entry")
        else:
            require(ids == self.before_ids and alarm_state(data) == "DISARMED", "disarmed walk produced an alarm/event")
        if self.saw_running and actor["running"] is False:
            require(self.transition and position["inside"] is True and actor["phase"] == "inside",
                    "trajectory did not complete inside")
            self.complete, self.completed_at = True, now

    def finish(self):
        require(self.saw_running and self.transition and self.complete, "outside-to-inside walk not completed")
        require(not self.armed or self.event is not None, "completed armed walk did not alarm")
        return {**self.activity.finish(), "observed_outside_to_inside": True, "completed_inside": True,
                "entry_after_seconds": round(self.inside_at - self.outside_at, 6),
                "completion_after_seconds": round(self.completed_at - self.outside_at, 6)}


def mutation_snapshot(data):
    spatial = obj(data.get("spatial"))
    event_ids(data)
    return copy.deepcopy({"alarm_state": data["alarm_state"], "active_event_id": data["active_event_id"],
                          "events": data["events"], "target": spatial["target"], "zone": spatial["zone"],
                          "threshold": data["features"]["threshold"], "ready": data["calibration"]["ready"],
                          "actor": spatial["test_actor"]})


async def request(client, method, path, deadline, *, clock=time.monotonic, **kwargs):
    budget = deadline - clock()
    require(budget > 0, "request deadline expired")
    response = await asyncio.wait_for(client.request(method, path, timeout=min(3.0, budget), **kwargs), timeout=min(3.0, budget))
    require(clock() < deadline, "response arrived after deadline")
    return response


async def read_state(client, deadline, session_id=None, *, clock=time.monotonic):
    response = await request(client, "GET", "/api/state", deadline, clock=clock)
    require(response.status_code == 200, "state API unavailable")
    try:
        data = response.json()
    except ValueError as error:
        raise ObservationError("Malformed state JSON") from error
    safe_session(data, session_id)
    return data


async def cleanup(client, session_id, *, seconds=12, clock=time.monotonic):
    # This deadline is deliberately independent of the exhausted scenario budget.
    started = clock()
    deadline = started + seconds
    result = {"status": "UNKNOWN", "disarm_attempted": False, "disarmed": False,
              "final_verified": False, "same_session": False}
    try:
        require(session_id is not None, "cannot safely clean up an unverified session")
        await read_state(client, deadline, session_id, clock=clock)
        result["same_session"] = True
        result["disarm_attempted"] = True
        try:
            response = await request(client, "POST", "/api/control", deadline, clock=clock, json={"action": "disarm"})
            require(response.status_code == 200, "cleanup disarm rejected")
        except Exception as error:
            # A timed-out POST may still have reached the server. Always attempt
            # the independent GET below; verified disarm does not erase this error.
            result["disarm_error_type"] = type(error).__name__
        current = await read_state(client, deadline, session_id, clock=clock)
        result.update(disarmed=alarm_state(current) == "DISARMED", source_mode=current["source_mode"],
                      calls_enabled=current["calls"]["enabled"], final_verified=True)
        require(result["disarmed"], "cleanup did not disarm the shared alarm")
        if "disarm_error_type" in result:
            return result
        if obj(current.get("spatial")).get("zone") is not None:
            result.update(initial_disarm_verified=True, final_verified=False, disarmed=False)
            response = await request(client, "POST", "/api/spatial/control", deadline, clock=clock,
                                     json={"action": "reset_test_actor"})
            require(response.status_code == 200, "cleanup actor reset rejected")
            clean = await read_state(client, deadline, session_id, clock=clock)
            result.update(disarmed=alarm_state(clean) == "DISARMED", final_verified=True)
            require(result["disarmed"], "cleanup reset changed disarmed state")
        result["status"] = "PASS"
    except CheckFailure as error:
        result.update(status="FAIL", error_type=type(error).__name__)
    except Exception as error:
        result.update(status="UNKNOWN", error_type=type(error).__name__)
    finally:
        result["duration_seconds"] = round(clock() - started, 6)
    return result


def final_status(scenario_status, checks_complete, cleanup_status, stable_sources):
    if scenario_status != "RUNNING":
        return scenario_status if scenario_status in ("FAIL", "UNKNOWN") else "UNKNOWN"
    if stable_sources is not True or checks_complete is not True:
        return "UNKNOWN"
    return "PASS" if cleanup_status == "PASS" else cleanup_status if cleanup_status in ("FAIL", "UNKNOWN") else "UNKNOWN"


def token_path(raw=None):
    candidate = Path(raw) if raw is not None else ROOT / ".local" / "control-token"
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve(strict=True)
    local = (ROOT / ".local").resolve(strict=True)
    if not local.is_relative_to(ROOT) or not candidate.is_relative_to(local) or candidate.name != "control-token":
        raise ValueError("Token file must be a control-token within the project .local directory")
    return candidate


async def run(port=8765, token_file=None):
    start = time.monotonic()
    body_deadline = start + 100
    url = loopback_url(port)
    report = {"status": "RUNNING", "source_mode": "TEST", "synthetic": True,
              "physical_sensing_verified": False, "exact_zone_verified": False,
              "provenance": "Software-only loopback TEST API and synthetic radio/position simulator",
              "limits": "No serial, camera, physical walk, audio, browser, or provider verification",
              "checks": [], "observations": {}, "source_sha256": source_hashes(),
              "started_at": datetime.now(timezone.utc).isoformat()}
    stage = "initial read"
    session_id = None
    checks_complete = False
    cleanup_result = {"status": "UNKNOWN", "disarm_attempted": False, "disarmed": False}
    try:
        secret = token_path(token_file).read_text().strip()
        if not secret or len(secret) > 256:
            raise ObservationError("Invalid local control token")
        headers = {"Authorization": "Bearer " + secret, "Origin": url}
        secret = ""
        async with httpx.AsyncClient(base_url=url, headers=headers, timeout=3, trust_env=False) as first, \
                   httpx.AsyncClient(base_url=url, headers=headers, timeout=3, trust_env=False) as second:
            headers = {}

            async def state(client=first, *, deadline=body_deadline):
                nonlocal session_id
                data = await read_state(client, min(deadline, body_deadline), session_id)
                session_id = data["session_id"]
                return data

            def check(ok, name):
                nonlocal stage
                stage = name
                require(ok, name)
                report["checks"].append(name)
                print("PASS", name, flush=True)

            async def post(action, *, spatial=False, client=first, allowed=(200,), **fields):
                await state(client)
                response = await request(client, "POST", "/api/spatial/control" if spatial else "/api/control",
                                         body_deadline, json={"action": action, **fields})
                require(response.status_code in allowed, "control transition rejected")
                return response

            async def until(predicate, name, timeout=12, client=first):
                nonlocal stage
                stage = name
                deadline = min(body_deadline, time.monotonic() + timeout)
                while True:
                    data = await state(client, deadline=deadline)
                    if predicate(data):
                        return data
                    require(time.monotonic() < deadline, name)
                    await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))

            async def observe(duration, expected_event_ids, expected_alarm, *, expected_inside, require_activity=False):
                nonlocal stage
                stage = "joint fresh TEST radio/position negative observation"
                proof = ActivityProof(zone, session_id, expected_inside)
                deadline = min(body_deadline, time.monotonic() + duration)
                while True:
                    data = await state()
                    require(alarm_state(data) == expected_alarm and event_ids(data) == expected_event_ids,
                            "unexpected alarm/event during negative case")
                    proof.add(data, time.monotonic())
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(0.2)
                result = proof.finish(require_activity=require_activity)
                report["observations"][f"{expected_alarm.lower()}_{'inside' if expected_inside else 'outside'}"] = result
                return data

            async def walk(before_ids, *, armed, client=first):
                nonlocal stage
                stage = "observed synthetic outside-to-inside walk" if armed else "disarmed synthetic walk"
                proof = WalkProof(zone, session_id, before_ids, armed=armed)
                proof.add(await state(client), time.monotonic())
                await post("test_walk", spatial=True, client=client)
                deadline = min(body_deadline, time.monotonic() + 18)
                while not proof.complete:
                    # TEST actor ticks at 10 Hz. Poll below the 0.75s freshness bound.
                    await asyncio.sleep(0.2)
                    data = await state(client, deadline=deadline)
                    proof.add(data, time.monotonic())
                report["observations"]["armed_walk" if armed else "disarmed_walk"] = proof.finish()
                return data, proof.event

            try:
                data = await state()
                check(isinstance(data.get("spatial"), dict), "spatial state available under authenticated TEST session")
                await post("disarm")
                await post("set_zone", spatial=True, name="API TEST zone", vertices=RECTANGLE,
                           coordinate_space="test-plan", frame_id="api-test-frame", floor_y=0)
                zone = copy.deepcopy(validate_zone((await state())["spatial"]["zone"]))
                check(True, "saved zone identity, revision, frame and independent rectangle verified")
                await post("set_target", spatial=True, target="zone-entry")
                await post("reset_test_actor", spatial=True)
                before = mutation_snapshot(await state())
                async with httpx.AsyncClient(base_url=url, timeout=3, trust_env=False) as anonymous:
                    denied = await request(anonymous, "POST", "/api/spatial/control", body_deadline,
                                           json={"action": "set_target", "target": "radio-motion"})
                    check(denied.status_code == 401 and mutation_snapshot(await state()) == before,
                          "unauthenticated spatial mutation denied without state changes")
                before = mutation_snapshot(await state())
                denied = await request(first, "POST", "/api/spatial/control", body_deadline,
                                       headers={"Origin": "https://foreign.invalid"},
                                       json={"action": "set_target", "target": "radio-motion"})
                check(denied.status_code == 403 and mutation_snapshot(await state()) == before,
                      "foreign-origin spatial mutation denied without state changes")
                before = mutation_snapshot(await state())
                await post("set_zone", spatial=True, allowed=(400, 409, 422), name="invalid crossing edges",
                           vertices=[{"x": 0, "z": 0}, {"x": 4, "z": 4}, {"x": 0, "z": 4}, {"x": 4, "z": 0}],
                           coordinate_space="test-plan", frame_id="api-test-frame")
                check(mutation_snapshot(await state()) == before, "invalid polygon cannot alter shared state")
                await until(lambda s: s["health"]["status"] == "healthy" and validate_spatial(s, zone, False) is not None,
                            "healthy TEST source and outside TEST position")
                await post("calibrate")
                await until(lambda s: s["alarm_state"] == "READY", "zone target calibration", timeout=10)
                await post("arm")
                data = await state(second)
                check(data["alarm_state"] == "ARMED" and data["spatial"]["target"] == "zone-entry", "both clients share armed zone target")
                before_ids = event_ids(data)
                before = mutation_snapshot(await state())
                await post("set_target", spatial=True, target="radio-motion", allowed=(409,))
                check(mutation_snapshot(await state()) == before, "armed target denial cannot alter shared state")
                await until(lambda s: s["features"]["activity_score"] is not None
                            and number(s["features"]["activity_score"]) >= 0,
                            "first scored TEST sample after calibration")
                await post("test_motion")
                await observe(4.2, before_ids, "ARMED", expected_inside=False, require_activity=True)
                check(True, "fresh sustained radio activity outside zone does not become zone entry")
                alarm, event = await walk(before_ids, armed=True, client=second)
                check(event is not None, "one explicit TEST zone-entry event after observed outside-to-inside walk")
                check(validate_annotation(event, zone, session_id) is not None, "complete zone-entry TEST provenance verified")
                check(validate_spatial(alarm, zone, True) is not None, "positive TEST trajectory completed inside")
                alarm_ids = event_ids(alarm)
                await post("acknowledge", client=second)
                check((await state(first))["alarm_state"] == "ARMED", "acknowledgement is shared across clients")
                await post("test_motion")
                await observe(4.2, alarm_ids, "ARMED", expected_inside=True, require_activity=True)
                check(True, "fresh sustained motion while already inside does not invent another entry")
                await post("disarm", client=second)
                check((await state(first))["alarm_state"] == "DISARMED", "disarm is shared across clients")
                await post("reset_test_actor", spatial=True)
                await until(lambda s: s["spatial"]["test_actor"]["can_start"] is True, "reset TEST actor ready")
                await walk(alarm_ids, armed=False)
                final = await observe(1.0, alarm_ids, "DISARMED", expected_inside=True)
                check(True, "disarmed completed zone entry produces no alarm")
                changed = next(row for row in final["events"] if row["event_id"] == event["event_id"])
                validate_annotation(changed, zone, session_id)
                check(changed["acknowledged"] is True and changed["call_status"] in ("disabled", "blocked_mode"),
                      "zone event acknowledged and phone call path blocked")
                check(final["recording"]["error"] is None, "recording remains healthy")
                report["spatial_provenance"] = event["spatial"]
                checks_complete = True
            except CheckFailure:
                report.update(status="FAIL", failed_check=stage)
            except Exception as error:
                report.update(status="UNKNOWN", failed_check=stage, error_type=type(error).__name__)
            finally:
                cleanup_result = await cleanup(first, session_id)
    except CheckFailure:
        report.update(status="FAIL", failed_check=stage)
    except Exception as error:
        report.update(status="UNKNOWN", failed_check=stage, error_type=type(error).__name__)
    report["cleanup"] = cleanup_result
    report["scenario_status"] = "PASS" if checks_complete and report["status"] == "RUNNING" else report["status"]
    report["closing_source_sha256"] = source_hashes()
    stable = report["source_sha256"] == report["closing_source_sha256"]
    report["stable_sources"] = stable
    report["status"] = final_status(report["status"], checks_complete, cleanup_result["status"], stable)
    report["duration_seconds"] = round(time.monotonic() - start, 3)
    output = ROOT / "evidence" / "spatial" / ("resume-harness-api-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6] + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "source_mode": "TEST", "checks": len(report["checks"]), "evidence": str(output.relative_to(ROOT))}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Lead-reserved TEST port on fixed 127.0.0.1 only")
    parser.add_argument("--token-file", help="Optional control-token path strictly within this project's .local/")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.port, args.token_file)))
