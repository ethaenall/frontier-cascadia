"""Local JSONL recording. No environment, auth headers, or control bodies stored."""
from __future__ import annotations

import json
from pathlib import Path


class Recorder:
    def __init__(self, root: Path, session_id: str, enabled: bool = True,
                 max_bytes: int = 256 * 1024 * 1024, event_max_bytes: int = 8 * 1024 * 1024):
        self.path = Path(root) / "recordings" / session_id
        self.enabled, self.max_bytes, self.event_max_bytes = enabled, max_bytes, event_max_bytes
        self.directory_error = self.raw_error = self.event_error = None
        self.relative_path = f"recordings/{session_id}" if enabled else None
        self.bytes_written = self.event_bytes_written = 0
        if enabled:
            try:
                if not self.path.resolve().is_relative_to(Path(root).resolve()):
                    raise OSError("Recording path escapes project root")
                self.path.mkdir(parents=True, exist_ok=False)
            except OSError:
                self.directory_error = "Cannot create recording directory"

    @property
    def error(self):
        errors = [error for error in (self.directory_error, self.raw_error, self.event_error) if error]
        return "; ".join(errors) if errors else None

    def _append(self, name: str, record: dict):
        is_raw = name == "raw.jsonl"
        if not self.enabled or self.directory_error or (self.raw_error if is_raw else self.event_error):
            return
        error = None
        try:
            encoded = json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
            size = len(encoded.encode("utf-8"))
            used = self.bytes_written if is_raw else self.event_bytes_written
            budget = self.max_bytes if is_raw else self.event_max_bytes
            if used + size > budget:
                error = "Raw recording size limit reached; event recording continues" if is_raw else "Event recording size limit reached; sensing continues"
            else:
                # Open/close per bounded write: no writable handles survive checkpoints.
                with (self.path / name).open("a", encoding="utf-8") as stream:
                    stream.write(encoded)
                if is_raw:
                    self.bytes_written += size
                else:
                    self.event_bytes_written += size
        except (OSError, ValueError, TypeError):
            error = "Raw recording write failed; event recording still attempted" if is_raw else "Event recording write failed; sensing continues"
        if error:
            if is_raw:
                self.raw_error = error
            else:
                self.event_error = error

    def raw(self, record: dict):
        self._append("raw.jsonl", record)

    def event(self, event: dict):
        self._append("events.jsonl", event)

    def event_update(self, event: dict):
        from .parser import utc_now
        self._append("event_updates.jsonl", {
            "schema_version": 1, "event_id": event["event_id"],
            "updated_at": utc_now(), "acknowledged": event["acknowledged"],
            "call_status": event["call_status"], "call_detail": event["call_detail"],
        })

    def snapshot(self):
        return {"enabled": self.enabled, "session_path": self.relative_path, "error": self.error}
