"""Authenticated local service. Sensing, health ticks, and call work are separate."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import math
from pathlib import Path
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .adapters import LiveAdapter, ReplayAdapter, TestAdapter
from .auth import Auth, COOKIE_NAME, allowed_host, same_origin
from .detector import Detector, DetectorConfig, TransitionError
from .parser import ParserConfig, utc_now
from .recorder import Recorder
from .spatial import SpatialController, SpatialInputError


@dataclass
class Config:
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    mode: str = "test"
    host: str = "127.0.0.1"
    http_port: int = 8765
    allow_lan: bool = False
    serial_port: str | None = None
    baud: int = 115200
    transmitter_mac: str = "02:ca:5c:ad:1a:01"
    channel: int = 6
    replay_path: Path | None = None
    recording: bool = True
    area_name: str = "Front entrance"
    detector: DetectorConfig = field(default_factory=DetectorConfig)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.mode = self.mode.lower()
        if self.mode not in ("live", "replay", "test"):
            raise ValueError("Choose explicit live, replay, or test mode")
        if self.mode == "live" and not self.serial_port:
            raise ValueError("LIVE requires --port; no automatic port selection")
        if self.mode == "replay" and self.replay_path is None:
            raise ValueError("REPLAY requires --replay recorded LIVE raw.jsonl")
        if self.host != "localhost":
            try:
                loopback = ipaddress.ip_address(self.host).is_loopback
            except ValueError as exc:
                raise ValueError("--host must be a numeric IP address or localhost") from exc
            if not loopback and not self.allow_lan:
                raise ValueError("Non-loopback binding requires --allow-lan")
        if not 1 <= self.http_port <= 65535 or not 9600 <= self.baud <= 3000000:
            raise ValueError("Invalid HTTP port or serial baud")
        if not self.area_name or len(self.area_name) > 80:
            raise ValueError("Area name must be 1..80 characters")
        ParserConfig(self.transmitter_mac, self.channel)


class Service:
    def __init__(self, config: Config, call_manager, source=None):
        self.config, self.call_manager = config, call_manager
        self.session_id = str(uuid4())
        self.detector = Detector(self.session_id, config.mode.upper(), config.area_name, config.detector)
        self.spatial = SpatialController(config.mode.upper())
        self.recorder = Recorder(config.root, self.session_id, config.recording)
        if source is not None:
            self.source = source
        elif config.mode == "live":
            self.source = LiveAdapter(self.session_id, config.serial_port, ParserConfig(config.transmitter_mac, config.channel), config.baud)
        elif config.mode == "replay":
            self.source = ReplayAdapter(self.session_id, config.replay_path)
        else:
            self.source = TestAdapter(self.session_id)
        self.tasks = []
        self.call_tasks = {}
        self.stopping = False

    async def emit(self, record):
        if self.stopping:
            return
        before = self.detector.valid_packets
        now = record["received_monotonic_s"]
        allowed = self.spatial.target == "radio-motion" or self.spatial.guard(now, self.detector)["eligible"]
        event = self.detector.ingest(record, now, event_allowed=allowed)
        if self.detector.valid_packets > before:
            self.recorder.raw(record)
        if event is not None:
            if self.spatial.target == "zone-entry":
                event.update(type="zone_entry", area_name=self.spatial.zone["name"], spatial=self.spatial.event_annotation())
                self.spatial.clear_pending()
            self.recorder.event(event)
            job = asyncio.create_task(self._call(event), name=f"call-{event['event_id']}")
            self.call_tasks[event["event_id"]] = job
            job.add_done_callback(lambda done, key=event["event_id"]: self.call_tasks.pop(key, None))

    async def reject(self, reason):
        self.detector.reject(time.monotonic(), reason)

    async def status(self, connected, detail):
        self.detector.set_connection(connected, detail, time.monotonic())

    async def _call(self, event):
        try:
            if self.stopping or self.detector.alarm_state != "ALARM" or self.detector.active_event_id != event["event_id"]:
                event.update(call_status="disabled", call_detail="Call work cancelled because alarm is no longer active")
                return
            if event.get("type") == "zone_entry":
                enabled = bool(self.call_manager.snapshot().get("enabled"))
                event.update(call_status="blocked_mode" if enabled else "disabled", call_detail="TEST zone-entry events cannot invoke phone providers; validated localization and separate approval are required")
                return
            result = await self.call_manager.handle_event(dict(event))
            statuses = {"disabled", "dry_run", "blocked_mode", "pending", "sent", "failed", "deduplicated"}
            if not isinstance(result, dict) or result.get("status") not in statuses:
                raise ValueError("Invalid call result")
            event.update(call_status=result["status"], call_detail=str(result.get("detail", ""))[:240])
        except asyncio.CancelledError:
            event.update(call_status="disabled", call_detail="Call work cancelled; already accepted provider calls cannot be recalled")
            raise
        except Exception:
            event.update(call_status="failed", call_detail="Optional call failed; sensing remains active")
        finally:
            self.recorder.event_update(event)

    async def _source_loop(self):
        try:
            await self.source.run(self.emit, self.reject, self.status)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.status(False, "Source stopped unexpectedly; no mode fallback")

    async def _ticks(self):
        while True:
            now = time.monotonic()
            self.detector.tick(now)
            entered = self.spatial.tick(now, self.detector)
            if entered and self.config.mode == "test" and isinstance(self.source, TestAdapter):
                self.source.motion()
            await asyncio.sleep(0.1)

    async def start(self):
        self.stopping = False
        self.tasks = [asyncio.create_task(self._source_loop(), name="csi-source"), asyncio.create_task(self._ticks(), name="health-tick")]

    async def stop(self):
        self.stopping = True
        for task in self.tasks + list(self.call_tasks.values()):
            task.cancel()
        await asyncio.gather(*self.tasks, *list(self.call_tasks.values()), return_exceptions=True)
        await self.source.close()
        self.tasks.clear()
        self.call_tasks.clear()

    async def control(self, action, threshold=None):
        if action == "test_motion":
            if self.config.mode != "test" or not isinstance(self.source, TestAdapter):
                raise TransitionError("test_motion is only available in explicitly synthetic TEST mode")
            self.source.motion()
        else:
            now = time.monotonic()
            self.detector.tick(now)
            self.spatial.ensure_shared_control(action, self.detector, now)
            previous_event_id = self.detector.active_event_id
            self.detector.control(action, now, threshold)
            if action in ("arm", "disarm", "calibrate", "acknowledge", "set_threshold"):
                self.spatial.clear_pending()
            if action == "acknowledge":
                for event in self.detector.events:
                    if event["event_id"] == previous_event_id:
                        self.recorder.event_update(event)
                        break
            if action == "disarm":
                for task in list(self.call_tasks.values()):
                    task.cancel()
                # Let cancellation update event status; no network wait on this path.
                if self.call_tasks:
                    await asyncio.gather(*list(self.call_tasks.values()), return_exceptions=True)
        return self.snapshot()

    def spatial_control(self, payload):
        now = time.monotonic()
        self.detector.tick(now)
        self.spatial.control(payload, self.detector, now)
        return self.snapshot()

    def snapshot(self):
        now = time.monotonic()
        self.detector.tick(now)
        spatial = self.spatial.snapshot(now, self.detector)
        result = self.detector.snapshot(now)
        calls = dict(self.call_manager.snapshot())
        if self.spatial.target == "zone-entry":
            calls.update(status="blocked_mode" if calls.get("enabled") else "disabled", detail="TEST zone-entry cannot invoke phone providers; real localization and separate approval are required")
        result["health"]["serial_port"] = self.config.serial_port if self.config.mode == "live" else None
        return {"schema_version": 1, "session_id": self.session_id,
                "source_mode": self.config.mode.upper(), "area_name": self.config.area_name,
                "server_time": utc_now(), **result, "calls": calls,
                "recording": self.recorder.snapshot(), "spatial": spatial,
                "limitations": ["Motion near entrance only. Crossing discrimination not validated.",
                                "No detected motion does not mean unoccupied.",
                                "Activity threshold is a tuning hypothesis, not a probability."]}


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=256)


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["calibrate", "arm", "disarm", "acknowledge", "test_motion", "set_threshold"]
    threshold: float | None = Field(default=None, ge=0.1, le=100, allow_inf_nan=False)

    @field_validator("threshold", mode="before")
    @classmethod
    def number_not_boolean(cls, value):
        if value is not None and type(value) not in (float, int):
            raise ValueError("Threshold must be a number")
        return value


def create_app(config: Config | None = None, *, call_manager=None, source=None) -> FastAPI:
    config = config or Config()
    auth = Auth(config.root / ".local" / "control-token")
    if call_manager is None:
        from .calls import CallConfig, CallManager
        call_manager = CallManager(CallConfig.from_env(), config.root / ".local" / "calls.sqlite3")
    service = Service(config, call_manager, source)

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Threshold", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service, app.state.auth, app.state.config = service, auth, config

    @app.middleware("http")
    async def security(request: Request, call_next):
        if not allowed_host(request.headers.get("host", ""), config.host, config.allow_lan):
            return JSONResponse({"detail": "Untrusted Host"}, status_code=400)
        path = request.url.path
        if path.startswith("/api/") or path == "/api":
            if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request):
                return JSONResponse({"detail": "Foreign Origin is not allowed"}, status_code=403)
            if path != "/api/session" and not auth.authorized(request):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            if request.method in ("POST", "PUT", "PATCH"):
                chunks = []
                total = 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > 4096:
                        return JSONResponse({"detail": "Request body too large"}, status_code=413)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = ("camera=(self), xr-spatial-tracking=(self), accelerometer=(self), gyroscope=(self), microphone=(), geolocation=(), magnetometer=()" if path == "/spatial" else "camera=(), xr-spatial-tracking=(), accelerometer=(), gyroscope=(), microphone=(), geolocation=(), magnetometer=()")
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/session")
    async def login(body: SessionBody, request: Request):
        peer = request.client.host if request.client else "unknown"
        if not auth.permit_login(peer):
            return JSONResponse({"detail": "Too many login attempts; wait one minute"}, status_code=429, headers={"Retry-After": "60"})
        if not auth.token_matches(body.token):
            raise HTTPException(401, "Invalid token")
        response = JSONResponse({"authenticated": True})
        response.set_cookie(COOKIE_NAME, auth.new_session(), httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=43200, path="/")
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        auth.sessions.pop(request.cookies.get(COOKIE_NAME), None)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/api/state")
    async def state():
        return service.snapshot()

    @app.post("/api/control")
    async def control(body: ControlBody):
        try:
            return await service.control(body.action, body.threshold)
        except TransitionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/spatial/control")
    async def spatial_control(request: Request):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid spatial JSON body") from exc
        try:
            return service.spatial_control(payload)
        except SpatialInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        except TransitionError as exc:
            raise HTTPException(409, str(exc)) from exc

    static_root = config.root / "static"
    if static_root.is_dir():
        app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/spatial")
    async def spatial_shell():
        if not (static_root / "spatial.html").is_file():
            return JSONResponse({"detail": "Spatial dashboard is not installed"}, status_code=503)
        return FileResponse(static_root / "spatial.html")

    @app.get("/")
    async def shell():
        if not (static_root / "index.html").is_file():
            return JSONResponse({"detail": "Static dashboard is not installed"}, status_code=503)
        return FileResponse(static_root / "index.html")

    return app
