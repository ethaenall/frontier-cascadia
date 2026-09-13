"""Strict, bounded parser for the documented ESP32-S3 raw-int8 profile."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass

PROFILE = "esp32-s3-raw-int8-v1"
MAX_LINE_BYTES = 16384
MAX_CSI_BYTES = 640
COLUMNS = "type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data".split(",")
MAC = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\Z", re.I)
INTEGER = re.compile(r"-?[0-9]+\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ParseError(ValueError):
    """A malformed or unsupported CSI record; never includes the source line."""


@dataclass(frozen=True)
class ParserConfig:
    transmitter_mac: str = "02:ca:5c:ad:1a:01"
    channel: int = 6

    def __post_init__(self):
        if not MAC.fullmatch(self.transmitter_mac):
            raise ValueError("Invalid transmitter MAC")
        if not 1 <= self.channel <= 11:
            raise ValueError("Channel must be 1..11")


def _integer(value: str, name: str, low: int, high: int) -> int:
    if not INTEGER.fullmatch(value):
        raise ParseError(f"Invalid integer: {name}")
    result = int(value)
    if not low <= result <= high:
        raise ParseError(f"Out-of-range: {name}")
    return result


def magnitudes(raw: list[int], invalid: bool) -> list[float]:
    valid = raw[4:] if invalid else raw
    return [math.hypot(valid[i + 1], valid[i]) for i in range(0, len(valid), 2)]


def parse_line(line: bytes | str, config: ParserConfig, session_id: str,
               *, now: float | None = None, received_at: str | None = None) -> dict | None:
    """Return LIVE data, None for known logs, or raise ParseError. No other schema."""
    if isinstance(line, bytes):
        if len(line) > MAX_LINE_BYTES:
            raise ParseError("Oversized serial line")
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ParseError("Non-ASCII serial line") from exc
    else:
        text = line
        if len(text) > MAX_LINE_BYTES:
            raise ParseError("Oversized serial line")
    text = text.strip()
    if not text or text == ",".join(COLUMNS) or text.startswith(("I (", "W (", "E (", "D (", "V (", "ESP-ROM:", "rst:", "boot:", "Build:", "Saved PC:", "SPIWP:", "mode:", "load:", "entry ", "# CASCADIA_CSI ", "# board", "# own_mac", "# STATS", "# tx_mac", "# channel", "# profile", "# upstream", "# first_word", "# ERROR")):
        return None
    if not text.startswith("CSI_DATA,"):
        raise ParseError("Unrecognized serial record")
    try:
        rows = list(csv.reader(io.StringIO(text), strict=True))
    except csv.Error as exc:
        raise ParseError("Invalid CSV quoting") from exc
    if len(rows) != 1 or len(rows[0]) != 25:
        raise ParseError("Expected exactly 25 S3 CSV fields")
    fields = dict(zip(COLUMNS, rows[0]))
    if fields["type"] != "CSI_DATA":
        raise ParseError("Unsupported CSI type")
    mac = fields["mac"].lower()
    if not MAC.fullmatch(mac) or mac != config.transmitter_mac.lower():
        raise ParseError("Unexpected transmitter MAC")
    ranges = {
        "id": (0, 2**32 - 1), "rssi": (-128, 0), "rate": (0, 255),
        "sig_mode": (0, 1), "mcs": (0, 31), "bandwidth": (0, 1),
        "smoothing": (0, 1), "not_sounding": (0, 1), "aggregation": (0, 1),
        "stbc": (0, 3), "fec_coding": (0, 1), "sgi": (0, 1),
        "noise_floor": (-128, 0), "ampdu_cnt": (0, 255), "channel": (1, 11),
        "secondary_channel": (0, 2), "local_timestamp": (0, 2**32 - 1),
        "ant": (0, 1), "sig_len": (0, 65535), "rx_format": (0, 1),
        "len": (2, MAX_CSI_BYTES), "first_word": (0, 1),
    }
    values = {key: _integer(fields[key], key, *limits) for key, limits in ranges.items()}
    if values["channel"] != config.channel:
        raise ParseError("Unexpected Wi-Fi channel")
    try:
        raw = json.loads(fields["data"])
    except (ValueError, RecursionError) as exc:
        raise ParseError("Invalid CSI JSON array") from exc
    if not isinstance(raw, list) or len(raw) != values["len"] or len(raw) % 2:
        raise ParseError("CSI byte length mismatch or odd length")
    if any(type(v) is not int or not -128 <= v <= 127 for v in raw):
        raise ParseError("Unsupported CSI sample: expected raw signed int8")
    invalid = bool(values["first_word"])
    if len(raw) - (4 if invalid else 0) < 2:
        raise ParseError("No usable CSI pairs after invalid first word")
    return {
        "schema_version": 1, "source_mode": "LIVE", "session_id": session_id,
        "received_at": received_at or utc_now(),
        "received_monotonic_s": time.monotonic() if now is None else now,
        "sequence": values["id"], "transmitter_mac": mac, "channel": values["channel"],
        "device_timestamp_us": values["local_timestamp"], "rssi_dbm": values["rssi"],
        "noise_floor_dbm": values["noise_floor"],
        **{key: values[key] for key in ("sig_mode", "mcs", "bandwidth", "ant")},
        "first_word_invalid": invalid, "csi_raw": raw, "magnitudes": magnitudes(raw, invalid),
        "quality": {"valid": True, "warnings": ["discarded_first_four_bytes"] if invalid else [],
                    "missing_packets_estimate": None, "profile": PROFILE},
    }


def validate_live_record(record: dict) -> dict:
    """Fail closed when a replay is not a valid recorded primary LIVE sample."""
    if not isinstance(record, dict) or record.get("source_mode") != "LIVE":
        raise ParseError("REPLAY accepts only recorded LIVE data, never TEST or REPLAY")
    if record.get("schema_version") != 1 or record.get("quality", {}).get("profile") != PROFILE or record.get("quality", {}).get("valid") is not True:
        raise ParseError("Unsupported or invalid recording profile")
    required = ("sequence", "channel", "device_timestamp_us", "rssi_dbm", "noise_floor_dbm", "sig_mode", "mcs", "bandwidth", "ant")
    if any(type(record.get(key)) is not int for key in required):
        raise ParseError("Invalid recorded integer metadata")
    raw = record.get("csi_raw")
    invalid = record.get("first_word_invalid")
    if type(invalid) is not bool or not isinstance(raw, list) or not 2 <= len(raw) <= MAX_CSI_BYTES or len(raw) % 2:
        raise ParseError("Invalid recorded CSI layout")
    if any(type(v) is not int or not -128 <= v <= 127 for v in raw) or len(raw) - (4 if invalid else 0) < 2:
        raise ParseError("Invalid recorded raw int8 CSI")
    if not isinstance(record.get("transmitter_mac"), str) or not MAC.fullmatch(record["transmitter_mac"]):
        raise ParseError("Invalid recorded MAC")
    limits = {"sequence": (0, 2**32-1), "device_timestamp_us": (0, 2**32-1), "channel": (1,11), "rssi_dbm": (-128,0), "noise_floor_dbm": (-128,0), "sig_mode": (0,1), "mcs": (0,31), "bandwidth": (0,1), "ant": (0,1)}
    if any(not lo <= record[key] <= hi for key, (lo, hi) in limits.items()):
        raise ParseError("Out-of-range recording metadata")
    clock = record.get("received_monotonic_s")
    if type(clock) not in (float, int) or not math.isfinite(clock) or clock < 0:
        raise ParseError("Invalid recorded monotonic time")
    try:
        dt = datetime.fromisoformat(record["received_at"])
        from uuid import UUID
        UUID(record["session_id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ParseError("Invalid recording provenance") from exc
    if dt.tzinfo is None:
        raise ParseError("Recording time must include timezone")
    expected = magnitudes(raw, invalid)
    supplied = record.get("magnitudes")
    if not isinstance(supplied, list) or len(supplied) != len(expected) or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v-e) > 1e-6 for v, e in zip(supplied, expected)):
        raise ParseError("Recorded magnitudes do not match raw CSI")
    return record
