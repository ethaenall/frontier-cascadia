"""Deterministic SIMULATION/TEST CSV25 fixtures. Not radio or hardware evidence.

This module has no I/O, ports, clock reads, randomness, credentials, or network.
The READY banner intentionally exercises the production wire-profile gate.
The caller MUST retain TEST provenance; CSV25 itself cannot encode source mode.
"""
from __future__ import annotations

import csv
import io
import math
import re

__all__ = ["READY_BANNER", "SYNTHETIC_PREAMBLE", "csv25_line"]

READY_BANNER = (
    b"# CASCADIA_CSI role=receiver profile=esp32-s3-raw-int8-v1 state=READY\n"
)
SYNTHETIC_PREAMBLE = (
    b"# profile source_mode=TEST SIMULATION=1 hardware=NONE "
    b"synthetic_fixture=csv25 not_RF_model=1\n"
)
_MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
_UINT32_MASK = (1 << 32) - 1
_SCALAR_COUNT = 128  # 64 imaginary,real pairs; compact synthetic fixture only.


def _clip_int8(value: float) -> int:
    """Round to nearest (ties to even), then saturate to signed int8."""
    if not math.isfinite(value):
        raise ValueError("sample must be finite")
    return max(-128, min(127, round(value)))


def _uint32(value: int, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer, not a bool or float")
    return value & _UINT32_MASK


def csv25_line(
    sequence: int,
    device_timestamp_us: int,
    *,
    motion: bool = False,
    sample_time: float = 0.0,
    transmitter_mac: str = "02:ca:5c:ad:1a:01",
    channel: int = 6,
    first_word_invalid: bool = False,
) -> bytes:
    """Return one ASCII CSV25 line with LF, generated only from these inputs.

    Both integer counters wrap modulo 2**32, including negative integers.
    Quiet samples have an exactly stable shape. ``motion`` enables a deliberately
    large sinusoidal shape disturbance; it does not represent a person or RF.
    ``sample_time`` is caller-controlled synthetic seconds, never a wall clock.
    The first-word flag replaces four scalars with obvious bounded sentinels;
    the remaining shape is unchanged and the wire still contains all 128 bytes.
    """
    seq = _uint32(sequence, "sequence")
    timestamp = _uint32(device_timestamp_us, "device_timestamp_us")
    if type(motion) is not bool or type(first_word_invalid) is not bool:
        raise TypeError("motion and first_word_invalid must be booleans")
    if type(sample_time) not in (int, float):
        raise TypeError("sample_time must be a finite number")
    try:
        finite_time = float(sample_time)
    except OverflowError as exc:
        raise ValueError("sample_time must fit a finite float") from exc
    if not math.isfinite(finite_time):
        raise ValueError("sample_time must be finite")
    if not isinstance(transmitter_mac, str) or not _MAC.fullmatch(transmitter_mac):
        raise ValueError("transmitter_mac must have six hexadecimal octets")
    if type(channel) is not int or not 1 <= channel <= 11:
        raise ValueError("channel must be an integer from 1 through 11")

    # Bounded phase handles even very large finite caller timestamps safely.
    synthetic_phase = math.remainder(finite_time, math.tau)
    raw: list[int] = []
    for index in range(_SCALAR_COUNT // 2):
        angle = math.tau * index / (_SCALAR_COUNT // 2)
        amplitude = 45 + 9 * math.cos(3 * angle) + 4 * math.sin(7 * angle)
        if motion:
            amplitude += (19 * math.sin(5 * angle + 2 * synthetic_phase)
                          + 8 * math.cos(2 * angle - synthetic_phase))
        phase = 0.35 + 0.4 * angle
        # Espressif CSI order is imaginary first, then real.
        raw.extend((_clip_int8(amplitude * math.sin(phase)),
                    _clip_int8(amplitude * math.cos(phase))))
    if first_word_invalid:
        raw[:4] = [127, -128, 127, -128]

    # Metadata is fixed test scaffolding, not measured radio state. In particular
    # RSSI stays fixed through disturbance, proving this is not RSSI motion data.
    row = [
        "CSI_DATA", seq, transmitter_mac.lower(), -45, 11, 1, 0, 0, 1, 0,
        0, 0, 0, 0, -95, 0, channel, 0, timestamp, 0, 47, 1,
        len(raw), int(first_word_invalid), "[" + ",".join(map(str, raw)) + "]",
    ]
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(row)
    return output.getvalue().encode("ascii")
