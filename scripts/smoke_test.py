"""Bounded HTTP integration check against explicitly synthetic TEST mode only.

This is not a sensing or phone-call validation. It changes TEST alarm state.
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"


async def run():
    result = {"source_mode": "TEST", "physical_sensing_verified": False,
              "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    token = (ROOT/".local/control-token").read_text().strip()
    async with httpx.AsyncClient(base_url=URL, timeout=3, trust_env=False) as client:
        assert (await client.get("/api/state")).status_code == 401
        assert (await client.post("/api/control", json={"action":"arm"})).status_code == 401
        result["checks"].append("Unauthenticated state and controls denied")
        paired = await client.post("/api/session", json={"token":token})
        paired.raise_for_status()
        cookie_header = paired.headers.get("set-cookie", "").lower()
        assert "httponly" in cookie_header and "samesite=strict" in cookie_header
        result["checks"].append("Pairing uses HttpOnly SameSite=Strict cookie")
        assert (await client.post("/api/control", json={"action":"arm"}, headers={"Origin":"http://attacker.invalid"})).status_code == 403
        result["checks"].append("Foreign-origin mutation denied")

        async def state():
            response = await client.get("/api/state")
            response.raise_for_status()
            data = response.json()
            assert data["source_mode"] == "TEST", "Never mutate a LIVE or REPLAY server in this smoke test"
            return data

        async def control(action):
            await state()  # recheck explicit synthetic mode before every mutation
            response = await client.post("/api/control", json={"action":action})
            response.raise_for_status()
            return response.json()

        async def until(predicate, timeout=10):
            end = time.monotonic()+timeout
            while time.monotonic() < end:
                data = await state()
                if predicate(data): return data
                await asyncio.sleep(.15)
            raise AssertionError("Timed out waiting for expected TEST state")

        initial = await until(lambda s:s["health"]["status"] == "healthy", 5)
        assert initial["calls"]["enabled"] is False
        await control("disarm")
        await control("calibrate")
        ready = await until(lambda s:s["alarm_state"] == "READY" and s["calibration"]["ready"])
        result["checks"].append("Empty synthetic calibration reaches READY")
        seen = {e["event_id"] for e in ready["events"]}
        await control("arm")
        await control("test_motion")
        alarm = await until(lambda s:s["alarm_state"] == "ALARM", 8)
        new_events = [e for e in alarm["events"] if e["event_id"] not in seen]
        assert len(new_events) == 1 and new_events[0]["type"] == "motion_near_entrance"
        event_id = new_events[0]["event_id"]
        await asyncio.sleep(1.5)
        held = await state()
        assert len([e for e in held["events"] if e["event_id"] not in seen]) == 1
        assert held["alarm_state"] == "ALARM"
        result["checks"].append("Armed synthetic CSI changes create one latched motion alarm")
        assert next(e for e in held["events"] if e["event_id"] == event_id)["call_status"] == "disabled"
        result["checks"].append("Optional calls remain disabled")
        ack = await control("acknowledge")
        assert ack["alarm_state"] == "ARMED" and ack["active_event_id"] is None
        result["checks"].append("Acknowledge clears active alarm and requires quiet rearm")
        await control("disarm")
        await control("test_motion")
        await asyncio.sleep(2)
        final = await state()
        assert final["alarm_state"] == "DISARMED"
        assert len([e for e in final["events"] if e["event_id"] not in seen]) == 1
        assert final["recording"]["enabled"] and final["recording"]["error"] is None
        assert len(final["graph"]) <= 300
        result["checks"].append("Disarmed synthetic motion creates no alarm; bounded graph and recording active")
        result["session_id"] = final["session_id"]
        result["recording"] = final["recording"]
        result["status"] = "PASS"
    return result


if __name__ == "__main__":
    try:
        outcome = asyncio.run(run())
    except Exception as error:
        # Assertions/exceptions contain no tokens; do not dump HTTP request metadata.
        outcome = {"status":"FAIL", "source_mode":"TEST", "error_type":type(error).__name__, "physical_sensing_verified":False}
        path = ROOT/"evidence/test-api-smoke.json"
        path.write_text(json.dumps(outcome, indent=2)+"\n")
        print(json.dumps(outcome, indent=2))
        raise
    (ROOT/"evidence/test-api-smoke.json").write_text(json.dumps(outcome, indent=2)+"\n")
    print(json.dumps(outcome, indent=2))
