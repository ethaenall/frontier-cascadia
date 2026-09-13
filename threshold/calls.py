"""Optional Twilio Voice adapter. Disabled and dry-run by default.

The detector never awaits network I/O inline. Durable event reservations deliberately
prevent retries even after an uncertain network result: missing one optional call is
safer than phoning twice. No emergency calling or arbitrary destinations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import uuid

import httpx


@dataclass(frozen=True, repr=False)
class CallConfig:
    enabled: bool = False
    dry_run: bool = True
    allow_nonlive: bool = False
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    to_number: str = ""
    confirmed_own_number: str = ""
    real_call_confirmation: str = ""

    @classmethod
    def from_env(cls) -> "CallConfig":
        return cls(
            enabled=os.environ.get("THRESHOLD_CALLS_ENABLED") == "1",
            dry_run=os.environ.get("THRESHOLD_CALLS_DRY_RUN", "1") != "0",
            allow_nonlive=os.environ.get("THRESHOLD_CALLS_ALLOW_NONLIVE") == "1",
            account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""),
            from_number=os.environ.get("TWILIO_FROM_NUMBER", ""),
            to_number=os.environ.get("THRESHOLD_CALL_TO", ""),
            confirmed_own_number=os.environ.get("THRESHOLD_CONFIRMED_OWN_NUMBER", ""),
            real_call_confirmation=os.environ.get("THRESHOLD_REAL_CALL_CONFIRMATION", ""),
        )

    def real_error(self) -> str | None:
        if self.real_call_confirmation != "I_CONFIRM_ONE_CALL_TO_MY_OWN_VERIFIED_NUMBER":
            return "Real calls need explicit owner confirmation."
        if not re.fullmatch(r"\+[1-9][0-9]{9,14}", self.to_number):
            return "Destination must be a full personal E.164 number; no emergency/service numbers."
        if self.to_number != self.confirmed_own_number:
            return "Destination does not match the explicitly confirmed own number."
        if not re.fullmatch(r"\+[1-9][0-9]{9,14}", self.from_number):
            return "A provider-approved E.164 caller ID is required."
        if not re.fullmatch(r"AC[0-9a-fA-F]{32}", self.account_sid) or not self.auth_token:
            return "Backend provider credentials are missing or invalid."
        return None


class CallManager:
    def __init__(self, config: CallConfig, ledger_path: Path, *, transport=None):
        self.config = config
        self.ledger_path = Path(ledger_path)
        # Injection is for offline unit tests only. No provider request at construction.
        self._transport = transport
        self._status = "disabled" if not config.enabled else "dry_run" if config.dry_run else "pending"
        self._detail = "No real calls enabled." if not config.enabled or config.dry_run else "Waiting for an eligible alarm; confirmation and provider verification required."

    def snapshot(self) -> dict:
        return {"enabled": self.config.enabled, "dry_run": self.config.dry_run,
                "status": self._status, "detail": self._detail}

    def _result(self, status: str, detail: str) -> dict:
        self._status, self._detail = status, detail
        return {"status": status, "detail": detail}

    def _reserve(self, event_id: str) -> bool:
        self.ledger_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.ledger_path.is_symlink():
            raise OSError("Unsafe ledger path")
        # Atomic open without truncation. Concurrent first events may both create
        # the ledger; O_CREAT is safe, O_EXCL would misreport the losing thread.
        fd = os.open(self.ledger_path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        os.chmod(self.ledger_path, 0o600)
        with sqlite3.connect(self.ledger_path, timeout=1.0) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS calls (event_id TEXT PRIMARY KEY, reserved_at TEXT NOT NULL)")
            cursor = connection.execute("INSERT OR IGNORE INTO calls VALUES (?, ?)",
                                        (event_id, datetime.now(timezone.utc).isoformat()))
            return cursor.rowcount == 1

    async def handle_event(self, event: dict) -> dict:
        if not self.config.enabled:
            return self._result("disabled", "Phone adapter disabled; local alarm remains available.")
        if event.get("source_mode") not in {"LIVE", "REPLAY", "TEST"}:
            return self._result("blocked_mode", "Unknown source mode; no call attempted.")
        if event["source_mode"] != "LIVE" and not self.config.allow_nonlive:
            return self._result("blocked_mode", "Calls are blocked for REPLAY and TEST.")
        if event.get("type") != "motion_near_entrance":
            return self._result("failed", "Unsupported event type; no call attempted.")
        event_id = event.get("event_id", "")
        try:
            event_id = str(uuid.UUID(event_id))
        except (ValueError, TypeError, AttributeError):
            return self._result("failed", "Invalid alarm event ID; no call attempted.")
        if not self.config.dry_run:
            problem = self.config.real_error()
            if problem:
                return self._result("failed", problem)
        try:
            reserved = await asyncio.to_thread(self._reserve, event_id)
        except (OSError, sqlite3.Error):
            return self._result("failed", "Call deduplication storage unavailable; no call attempted.")
        if not reserved:
            return self._result("deduplicated", "Event already reserved. No duplicate call or automatic retry.")
        if self.config.dry_run:
            return self._result("dry_run", "Dry run recorded. No provider request and no phone call.")
        self._result("pending", "Checking the verified destination before one call request.")
        # Fixed HTTPS host, no redirects, no environment proxy, no retries. Never log
        # provider response bodies, request URLs containing numbers, or exception text.
        base = f"https://api.twilio.com/2010-04-01/Accounts/{self.config.account_sid}"
        try:
            async with httpx.AsyncClient(
                auth=(self.config.account_sid, self.config.auth_token),
                timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=False,
                trust_env=False, transport=self._transport,
            ) as client:
                verified = await client.get(base + "/OutgoingCallerIds.json", params={"PhoneNumber": self.config.to_number, "PageSize": 50})
                if verified.status_code != 200:
                    return self._result("failed", "Provider destination verification failed. No call placed.")
                entries = verified.json().get("outgoing_caller_ids", [])
                if not isinstance(entries, list) or not any(isinstance(item, dict) and item.get("phone_number") == self.config.to_number for item in entries):
                    return self._result("failed", "Destination is not a provider-verified own number. No call placed.")
                # Inline fixed TwiML avoids a public webhook or user-controlled XML.
                twiml = ("<Response><Say>This is your Threshold prototype. Motion near your monitored entrance was detected. "
                         "This is not a verified intrusion or crossing. Please check your local dashboard.</Say><Hangup/></Response>")
                response = await client.post(base + "/Calls.json", data={
                    "To": self.config.to_number, "From": self.config.from_number,
                    "Twiml": twiml, "Timeout": "20", "TimeLimit": "30",
                })
                if response.status_code != 201:
                    return self._result("failed", "Provider rejected the call request. No automatic retry.")
                payload = response.json()
                if not isinstance(payload, dict) or not re.fullmatch(r"CA[0-9a-fA-F]{32}", str(payload.get("sid", ""))):
                    return self._result("failed", "Provider response could not be verified; outcome unknown. No retry.")
                return self._result("sent", "Provider accepted one call request. Ringing/answer/delivery is not verified.")
        except asyncio.CancelledError:
            self._result("failed", "Call task cancelled; outcome may be unknown. Event stays reserved; no retry.")
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return self._result("failed", "Provider/network result unavailable; outcome may be unknown. No automatic retry.")
