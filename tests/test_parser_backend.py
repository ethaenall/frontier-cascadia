import csv
import io
import json
from uuid import uuid4

import pytest

from threshold.parser import COLUMNS, MAX_LINE_BYTES, ParseError, ParserConfig, parse_line


def wire(raw=None, **changes):
    raw = raw if raw is not None else [3, 4] * 64
    row = dict(zip(COLUMNS, ["CSI_DATA", "1", "02:ca:5c:ad:1a:01", "-45", "11", "1", "0", "0", "1", "0", "0", "0", "0", "0", "-95", "0", "6", "0", "100000", "0", "128", "1", str(len(raw)), "0", json.dumps(raw)]))
    row.update({key: str(value) for key, value in changes.items()})
    output = io.StringIO()
    csv.writer(output).writerow([row[key] for key in COLUMNS])
    return output.getvalue()


def parse(line):
    return parse_line(line, ParserConfig(), str(uuid4()), now=100.0)


def test_raw_signed_imaginary_real_and_first_four_invalid():
    record = parse(wire([127, -128, 5, 12, 3, 4, -8, 15], first_word=1))
    assert record["magnitudes"] == [5.0, 17.0]
    assert record["csi_raw"][:4] == [127, -128, 5, 12]
    assert record["first_word_invalid"] is True
    assert record["source_mode"] == "LIVE"
    assert record["received_monotonic_s"] == 100.0


@pytest.mark.parametrize("line", ["", ",".join(COLUMNS), "I (1234) wifi: ready", "ESP-ROM:esp32s3", "# CASCADIA_CSI role=receiver profile=esp32-s3-raw-int8-v1 state=READY", "# STATS received=2"])
def test_known_logs_ignored(line):
    assert parse(line) is None


@pytest.mark.parametrize("changes", [
    {"mac": "02:ca:5c:ad:1a:02"}, {"channel": 11}, {"channel": 14},
    {"id": -1}, {"id": 2**32}, {"local_timestamp": 2**32},
    {"sig_mode": 2}, {"rx_format": 2}, {"bandwidth": 2}, {"first_word": 2},
    {"len": 126}, {"rssi": "-45.2"}, {"noise_floor": 1},
    {"data": "[3,4,3]", "len": 3}, {"data": "[128,4]", "len": 2},
    {"data": "[-129,4]", "len": 2}, {"data": "[true,4]", "len": 2},
    {"data": "[3.0,4]", "len": 2}, {"data": "{}", "len": 2},
    {"data": "[3,4,5,6]", "len": 4, "first_word": 1},
])
def test_rejects_unsupported_or_malformed_fields(changes):
    with pytest.raises(ParseError):
        parse(wire(**changes))


@pytest.mark.parametrize("line", ["CSI_DATA,1,broken", "random noise", b"\xff", "x" * (MAX_LINE_BYTES + 1), 'CSI_DATA,"unclosed'])
def test_malformed_serial_is_not_data(line):
    with pytest.raises(ParseError):
        parse(line)


def test_extra_missing_columns_and_gain_compensated_samples_rejected():
    with pytest.raises(ParseError):
        parse(wire().strip() + ",extra")
    with pytest.raises(ParseError):
        parse(wire([300, -512] * 64))


def test_mac_is_normalized_and_unsigned_wrap_allowed():
    record = parse(wire(mac="02:CA:5C:AD:1A:01", id=2**32-1, local_timestamp=2**32-1))
    assert record["transmitter_mac"] == "02:ca:5c:ad:1a:01"
    assert record["sequence"] == 2**32-1
