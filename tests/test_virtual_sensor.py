"""Offline virtual-wire tests; no serial, PTY, hardware, calls, or network."""
import csv
import io
import json
import math
from uuid import UUID

import pytest

from scripts.virtual_sensor import READY_BANNER, SYNTHETIC_PREAMBLE, csv25_line, _clip_int8
from threshold.parser import COLUMNS, PROFILE, ParserConfig, parse_line

SESSION = str(UUID(int=1))
STAMP = "2026-09-12T00:00:00.000+00:00"


def fields(line):
    rows = list(csv.reader(io.StringIO(line.decode("ascii")), strict=True))
    assert len(rows) == 1
    assert len(rows[0]) == 25
    return dict(zip(COLUMNS, rows[0]))


def decode(line, config=None):
    return parse_line(line, config or ParserConfig(), SESSION, now=10.0, received_at=STAMP)


def test_exact_banners_and_explicit_simulation_preamble():
    assert READY_BANNER == b"# CASCADIA_CSI role=receiver profile=esp32-s3-raw-int8-v1 state=READY\n"
    assert SYNTHETIC_PREAMBLE.startswith(b"# profile ")
    assert all(word in SYNTHETIC_PREAMBLE for word in (b"SIMULATION", b"TEST", b"hardware=NONE", b"not_RF_model=1"))
    assert SYNTHETIC_PREAMBLE.endswith(b"\n")
    assert decode(READY_BANNER) is None
    assert decode(SYNTHETIC_PREAMBLE) is None


def test_exact_csv25_ascii_lf_quoted_array_and_valid_production_decode():
    line = csv25_line(12, 345_678)
    row = fields(line)
    assert line.endswith(b"]\"\n") and b"\r" not in line
    assert row["type"] == "CSI_DATA"
    assert row["id"] == "12" and row["local_timestamp"] == "345678"
    raw = json.loads(row["data"])
    assert len(raw) == int(row["len"]) == 128
    assert all(type(value) is int and -128 <= value <= 127 for value in raw)
    record = decode(line)
    assert record["sequence"] == 12
    assert record["device_timestamp_us"] == 345_678
    assert record["csi_raw"] == raw
    assert record["quality"]["profile"] == PROFILE
    assert record["transmitter_mac"] == "02:ca:5c:ad:1a:01"
    assert record["channel"] == 6
    assert record["magnitudes"] == [math.hypot(raw[i+1], raw[i]) for i in range(0,128,2)]
    # Parser mode is selected by transport, NOT inferred from fixture bytes.
    # The lead's simulation harness must relabel/preserve TEST provenance.
    assert record["source_mode"] == "LIVE"


def test_deterministic_quiet_baseline_and_time_varying_motion_shape_not_rssi():
    quiet = decode(csv25_line(1, 1))
    later_quiet = decode(csv25_line(2, 2, sample_time=1234.5))
    moving = decode(csv25_line(3, 3, motion=True, sample_time=1.5))
    later_moving = decode(csv25_line(4, 4, motion=True, sample_time=2.5))
    assert quiet["csi_raw"] == later_quiet["csi_raw"]
    assert moving["csi_raw"] != quiet["csi_raw"]
    assert moving["csi_raw"] != later_moving["csi_raw"]
    assert quiet["rssi_dbm"] == moving["rssi_dbm"] == -45
    differences = [abs(a-b) for a,b in zip(quiet["magnitudes"],moving["magnitudes"])]
    assert sum(differences)/len(differences) > 8
    assert csv25_line(9, 10, motion=True, sample_time=2.5) == csv25_line(9, 10, motion=True, sample_time=2.5)


def test_signed_imaginary_then_real_order_matches_declared_first_bin():
    raw = decode(csv25_line(1, 1))["csi_raw"]
    amplitude = 45 + 9
    assert raw[0] == round(amplitude * math.sin(0.35))
    assert raw[1] == round(amplitude * math.cos(0.35))
    assert raw[0] != raw[1]


def test_first_word_invalid_keeps_full_length_and_host_discards_exactly_four():
    ordinary = decode(csv25_line(1, 1))
    marked = decode(csv25_line(1, 1, first_word_invalid=True))
    assert marked["first_word_invalid"] is True
    assert len(marked["csi_raw"]) == 128
    assert marked["csi_raw"][:4] == [127, -128, 127, -128]
    assert marked["csi_raw"][4:] == ordinary["csi_raw"][4:]
    assert len(marked["magnitudes"]) == 62
    assert marked["magnitudes"] == ordinary["magnitudes"][2:]
    assert marked["quality"]["warnings"] == ["discarded_first_four_bytes"]


@pytest.mark.parametrize("value,expected", [(0,0),(2**32-1,2**32-1),(2**32,0),(2**32+5,5),(-1,2**32-1),(-2**32,0)])
def test_counter_and_device_timestamp_uint32_wrap(value, expected):
    record = decode(csv25_line(value, value))
    assert record["sequence"] == record["device_timestamp_us"] == expected


@pytest.mark.parametrize("value,expected", [(-1000,-128),(-128,-128),(-127.6,-128),(-2.5,-2),(0,0),(2.5,2),(126.8,127),(127,127),(128,127),(1000,127)])
def test_signed_int8_rounding_and_saturation(value, expected):
    assert _clip_int8(value) == expected
    assert type(_clip_int8(value)) is int


@pytest.mark.parametrize("sample_time", [-1e308, -1.5, 0, 1.5, 1e308])
def test_large_finite_motion_timestamps_remain_bounded(sample_time):
    raw = decode(csv25_line(1,1,motion=True,sample_time=sample_time))["csi_raw"]
    assert len(raw) == 128 and all(-128 <= value <= 127 for value in raw)


def test_configurable_mac_and_channel_are_explicit_and_canonical():
    config = ParserConfig(transmitter_mac="02:ca:5c:ad:1a:09", channel=11)
    record = decode(csv25_line(1,1,transmitter_mac="02:CA:5C:AD:1A:09",channel=11), config)
    assert record["transmitter_mac"] == config.transmitter_mac
    assert record["channel"] == 11


@pytest.mark.parametrize("kwargs", [
    {"sequence":True}, {"sequence":1.5}, {"device_timestamp_us":False},
    {"sample_time":float("inf")}, {"sample_time":float("nan")}, {"sample_time":True},
    {"sample_time":10**1000}, {"motion":1}, {"first_word_invalid":1},
    {"transmitter_mac":"bad"}, {"channel":0}, {"channel":12}, {"channel":True},
])
def test_invalid_inputs_fail_closed(kwargs):
    arguments = {"sequence":1,"device_timestamp_us":2,**kwargs}
    with pytest.raises((TypeError, ValueError)):
        csv25_line(**arguments)


@pytest.mark.parametrize("value", [float("inf"),float("-inf"),float("nan")])
def test_clipping_does_not_hide_nonfinite_samples(value):
    with pytest.raises(ValueError):
        _clip_int8(value)
