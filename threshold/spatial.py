"""Bounded geometry drafts and explicit TEST positions. Never CSI localization."""
from __future__ import annotations

import copy
import math
from uuid import uuid4

from .detector import TransitionError
from .parser import utc_now

EPS = 1e-7
POSITION_STALE_SECONDS = 0.75
ENTRY_VALID_SECONDS = 4.0
TEST_WALK_SECONDS = 4.5


class SpatialInputError(ValueError):
    pass


def number(value, *, limit=10000):
    if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > limit:
        raise SpatialInputError(f"Coordinates must be finite numbers within +/-{limit}; booleans are not coordinates")
    return float(value)


def text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SpatialInputError(f"{name} must be a nonempty bounded text string ({limit} characters maximum)")
    return value.strip()


def cross(a, b, c):
    return (b["x"]-a["x"])*(c["z"]-a["z"]) - (b["z"]-a["z"])*(c["x"]-a["x"])


def on_segment(a, b, p):
    return abs(cross(a, b, p)) <= EPS and min(a["x"], b["x"])-EPS <= p["x"] <= max(a["x"], b["x"])+EPS and min(a["z"], b["z"])-EPS <= p["z"] <= max(a["z"], b["z"])+EPS


def intersects(a, b, c, d):
    ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if ((ab_c > EPS and ab_d < -EPS) or (ab_c < -EPS and ab_d > EPS)) and ((cd_a > EPS and cd_b < -EPS) or (cd_a < -EPS and cd_b > EPS)):
        return True
    return any((on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b)))


def point_inside(vertices, x, z):
    """Strict interior only. Boundary contact and malformed points are outside."""
    try:
        p = {"x": number(x, limit=20000), "z": number(z, limit=20000)}
    except SpatialInputError:
        return False
    inside = False
    for index, a in enumerate(vertices):
        b = vertices[(index + 1) % len(vertices)]
        if on_segment(a, b, p):
            return False
        if (a["z"] > z) != (b["z"] > z):
            edge_x = a["x"] + (z-a["z"])*(b["x"]-a["x"])/(b["z"]-a["z"])
            if x < edge_x:
                inside = not inside
    return inside


def validate_zone(payload):
    vertices = payload.get("vertices")
    if not isinstance(vertices, list) or not 3 <= len(vertices) <= 16:
        raise SpatialInputError("A zone requires 3..16 vertices")
    points = []
    for vertex in vertices:
        if not isinstance(vertex, dict) or set(vertex) != {"x", "z"}:
            raise SpatialInputError("Each vertex requires only x and z")
        point = {"x": number(vertex["x"]), "z": number(vertex["z"])}
        if any(math.hypot(point["x"]-other["x"], point["z"]-other["z"]) <= EPS for other in points):
            raise SpatialInputError("Duplicate zone vertices are not allowed")
        points.append(point)
    area2 = sum(a["x"]*points[(i+1) % len(points)]["z"] - points[(i+1) % len(points)]["x"]*a["z"] for i, a in enumerate(points))
    if abs(area2) < 2e-6:
        raise SpatialInputError("Zone is degenerate or too small")
    for i, a in enumerate(points):
        previous, following = points[(i-1) % len(points)], points[(i+1) % len(points)]
        if abs(cross(previous, a, following)) <= EPS and (previous["x"]-a["x"])*(following["x"]-a["x"]) + (previous["z"]-a["z"])*(following["z"]-a["z"]) > EPS:
            raise SpatialInputError("Adjacent zone edges must not double back")
        b = points[(i+1) % len(points)]
        for j in range(i+1, len(points)):
            if j == i+1 or (i == 0 and j == len(points)-1):
                continue
            if intersects(a, b, points[j], points[(j+1) % len(points)]):
                raise SpatialInputError("Zone edges must not cross or touch nonadjacent edges")
    coordinate_space = payload.get("coordinate_space")
    if coordinate_space not in ("test-plan", "webxr-local", "arkit-world"):
        raise SpatialInputError("Unsupported coordinate_space")
    return {"name": text(payload.get("name"), "name", 64), "vertices": points,
            "coordinate_space": coordinate_space, "frame_id": text(payload.get("frame_id"), "frame_id", 96),
            "floor_y": number(payload["floor_y"]) if "floor_y" in payload else None,
            "units": "unmeasured TEST-plan units" if coordinate_space == "test-plan" else "metres (platform estimate, not radio accuracy)"}


def validate_control(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        raise SpatialInputError("Spatial control requires an action object")
    fields = {
        "set_zone": ({"action", "name", "vertices", "coordinate_space", "frame_id"}, {"floor_y"}),
        "clear_zone": ({"action"}, set()), "set_target": ({"action", "target"}, set()),
        "test_walk": ({"action"}, set()), "reset_test_actor": ({"action"}, set()),
    }
    if payload["action"] not in fields:
        raise SpatialInputError("Unsupported spatial action")
    required, optional = fields[payload["action"]]
    if not required <= set(payload) or not set(payload) <= required | optional:
        raise SpatialInputError("Missing or irrelevant fields for spatial action")
    result = dict(payload)
    if payload["action"] == "set_zone":
        result["zone"] = validate_zone(payload)
    elif payload["action"] == "set_target" and payload["target"] not in ("radio-motion", "zone-entry"):
        raise SpatialInputError("target must be radio-motion or zone-entry")
    return result


class SpatialController:
    def __init__(self, source_mode):
        self.source_mode = source_mode.upper()
        self.target = "radio-motion"
        self.zone = None
        self.revision = 0
        self.position = None
        self.position_time = None
        self.position_error = None
        self.pending_until = None
        self.walk_started = None
        self.phase = "unavailable"
        self.inside_anchor = self.outside_anchor = None

    def simulation_available(self):
        return self.source_mode == "TEST" and self.zone is not None and self.inside_anchor is not None

    def clear_pending(self):
        self.pending_until = None

    def _fault(self, reason, detector):
        self.clear_pending()
        self.position_error = reason
        if self.target == "zone-entry" and (detector.ready or detector.alarm_state == "CALIBRATING"):
            detector.invalidate(reason)

    def fresh(self, now, detector):
        if self.position_time is None or self.position is None:
            return False
        if now < self.position_time or now-self.position_time > POSITION_STALE_SECONDS:
            self._fault("TEST position is stale or its clock is invalid", detector)
            return False
        if self.zone is None or self.position["frame_id"] != self.zone["frame_id"]:
            self._fault("Position frame does not match saved zone", detector)
            return False
        return self.position_error is None

    def accept_test_position(self, x, z, *, frame_id, coordinate_space, now, detector, source_mode="TEST", synthetic=True):
        """Internal simulator interface only. No phone-supplied position endpoint."""
        if not self.simulation_available() or source_mode != "TEST" or synthetic is not True or frame_id != self.zone["frame_id"] or coordinate_space != self.zone["coordinate_space"]:
            self._fault("Untrusted or mismatched TEST position frame", detector)
            return False
        try:
            x, z = number(x, limit=20000), number(z, limit=20000)
            now = number(now, limit=1e12)
        except SpatialInputError:
            self._fault("Malformed TEST position rejected", detector)
            return False
        if self.position_time is not None and now < self.position_time:
            self._fault("Out-of-order TEST position rejected", detector)
            return False
        # A recovered heartbeat must not hide a missing-position fault.
        if self.position_time is not None and now-self.position_time > POSITION_STALE_SECONDS:
            self._fault("TEST position stream gap; recalibration required", detector)
        previous_inside = None if self.position is None else self.position["inside"]
        inside = point_inside(self.zone["vertices"], x, z)
        self.position = {"x": x, "z": z, "frame_id": frame_id, "inside": inside,
                         "updated_at": utc_now(), "source_mode": "TEST", "synthetic": True}
        self.position_time, self.position_error = now, None
        entered = previous_inside is False and inside
        if not inside:
            self.clear_pending()
        if entered and self.target == "zone-entry" and detector.alarm_state == "ARMED" and detector.ready and detector.health_status == "healthy":
            self.pending_until = now + ENTRY_VALID_SECONDS
        return entered

    def _anchors(self):
        self.inside_anchor = self.outside_anchor = None
        if self.zone is None:
            return
        vertices = self.zone["vertices"]
        for index in range(len(vertices)):
            tri = [vertices[(index-1) % len(vertices)], vertices[index], vertices[(index+1) % len(vertices)]]
            x, z = sum(p["x"] for p in tri)/3, sum(p["z"] for p in tri)/3
            if point_inside(vertices, x, z):
                self.inside_anchor = (x, z)
                width = max(p["x"] for p in vertices)-min(p["x"] for p in vertices)
                self.outside_anchor = (min(p["x"] for p in vertices)-max(1.0, width*0.25), z)
                return

    def reset_actor(self, now, detector):
        self.clear_pending()
        self.walk_started = None
        self.position = self.position_time = self.position_error = None
        if self.simulation_available():
            self.phase = "outside"
            self.accept_test_position(*self.outside_anchor, frame_id=self.zone["frame_id"], coordinate_space=self.zone["coordinate_space"], now=now, detector=detector)
        else:
            self.phase = "unavailable"

    def tick(self, now, detector):
        if self.target == "zone-entry" and (detector.health_status != "healthy" or detector.alarm_state not in ("ARMED", "ALARM")):
            self.clear_pending()
        if not self.simulation_available():
            return False
        if self.pending_until is not None and now > self.pending_until:
            self.clear_pending()
        point = self.outside_anchor if self.phase == "outside" else self.inside_anchor
        if self.walk_started is not None:
            elapsed = now-self.walk_started
            fraction = min(1.0, max(0.0, (elapsed-0.6)/1.0))
            point = tuple(a + fraction*(b-a) for a, b in zip(self.outside_anchor, self.inside_anchor))
            self.phase = "outside" if fraction == 0 else "approaching" if fraction < 1 else "inside"
            if elapsed >= TEST_WALK_SECONDS:
                self.walk_started = None
                self.phase = "inside"
        return self.accept_test_position(*point, frame_id=self.zone["frame_id"], coordinate_space=self.zone["coordinate_space"], now=now, detector=detector)

    def ensure_shared_control(self, action, detector, now):
        if self.target != "zone-entry" or action not in ("arm", "calibrate"):
            return
        if not self.simulation_available():
            raise TransitionError("Exact-zone arming is unavailable without a validated localizer; only explicit TEST simulation in the saved zone frame is supported")
        if not self.fresh(now, detector):
            raise TransitionError("A fresh matching-frame TEST simulator position is required")
        if self.walk_started is not None or self.position["inside"]:
            raise TransitionError("Reset the TEST actor outside and stop its walk before calibration or arming")

    def control(self, payload, detector, now):
        request = validate_control(payload)
        if self.target == "zone-entry" and self.position is not None:
            self.fresh(now, detector)
        action = request["action"]
        if action in ("set_zone", "clear_zone", "set_target"):
            if detector.alarm_state not in ("DISARMED", "READY"):
                raise TransitionError("Disarm before changing the spatial target or zone")
            if action == "set_target" and request["target"] == "zone-entry" and self.source_mode != "TEST":
                raise TransitionError("Exact-zone localization is unavailable in LIVE and REPLAY; no simulated positions may enable it")
            if action == "set_zone":
                self.revision += 1
                self.zone = {"id": self.zone["id"] if self.zone else str(uuid4()), "revision": self.revision, **request["zone"]}
                self._anchors()
            elif action == "clear_zone":
                self.revision += 1
                self.zone = None
                self._anchors()
            else:
                self.target = request["target"]
            self.reset_actor(now, detector)
            detector.invalidate("Spatial target or geometry changed")
        else:
            if not self.simulation_available():
                raise TransitionError("TEST simulator requires TEST mode and a valid saved zone; geometry is not real-person localization")
            if action == "reset_test_actor":
                self.reset_actor(now, detector)
            elif action == "test_walk":
                if not self.fresh(now, detector) or self.walk_started is not None or self.position is None or self.position["inside"] or detector.alarm_state == "CALIBRATING":
                    raise TransitionError("Reset the TEST actor outside; a walk cannot start during calibration or another walk")
                self.clear_pending()
                self.walk_started = now
                self.phase = "outside"

    def guard(self, now, detector):
        if self.target == "radio-motion":
            return {"eligible": detector.alarm_state == "ARMED" and detector.ready and detector.health_status == "healthy", "reason": "Radio-motion selected; no exact-zone claim or position gate"}
        reason = None
        if not self.simulation_available():
            reason = "Exact-zone localization unavailable; save a valid zone draft in TEST mode for synthetic simulation"
        elif not self.fresh(now, detector):
            reason = self.position_error or "Waiting for a fresh matching-frame TEST position"
        elif detector.health_status != "healthy" or not detector.ready:
            self.clear_pending()
            reason = "Fresh CSI and a valid empty-area baseline are required"
        elif detector.alarm_state != "ARMED":
            self.clear_pending()
            reason = "Shared alarm is not armed for a new entry"
        elif self.pending_until is None or now > self.pending_until or not self.position["inside"]:
            self.clear_pending()
            reason = "Waiting for a new synthetic outside-to-inside transition"
        elif not detector.trigger_ready:
            reason = "Shared detector requires its quiet interval before another alarm"
        return {"eligible": reason is None, "reason": reason or "Fresh TEST entry permit; CSI threshold and sustained debounce still required"}

    def event_annotation(self):
        return {"source_mode": "TEST", "synthetic": True, "exact_zone_verified": False,
                "zone_id": self.zone["id"], "zone_revision": self.zone["revision"],
                "coordinate_space": self.zone["coordinate_space"], "frame_id": self.zone["frame_id"],
                "position": copy.deepcopy(self.position),
                "confirmation": "TEST simulated outside-to-inside transition; not physical localization"}

    def snapshot(self, now, detector):
        fresh = self.fresh(now, detector) if self.simulation_available() else False
        status = "test-simulated" if fresh else "stale" if self.simulation_available() and self.position is not None else "unavailable"
        detail = "Synthetic TEST actor only. No person is localized; exact-zone verification is false." if fresh else self.position_error or "No validated real localization source. Scanned geometry does not establish radio coverage."
        can_start = self.simulation_available() and fresh and self.walk_started is None and self.position is not None and not self.position["inside"] and detector.alarm_state != "CALIBRATING"
        return {"target": self.target, "zone": copy.deepcopy(self.zone),
                "localization": {"status": status, "provenance": "TEST synthetic position simulator" if self.simulation_available() else "none", "exact_zone_verified": False, "detail": detail},
                "position": copy.deepcopy(self.position),
                "test_actor": {"running": self.walk_started is not None, "phase": self.phase, "can_start": bool(can_start), "can_reset": self.simulation_available()},
                "guard": self.guard(now, detector)}
