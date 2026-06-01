"""Tests for the multi-format parser module."""
from __future__ import annotations

import json
import pytest


# ── detect_format ─────────────────────────────────────────────────────────────

def test_detect_json_object():
    from backend.ingestion.parsers import detect_format
    data = json.dumps({"indicator": "1.2.3.4"}).encode()
    assert detect_format(data) == "json"


def test_detect_json_array():
    from backend.ingestion.parsers import detect_format
    data = json.dumps([{"a": 1}, {"b": 2}]).encode()
    assert detect_format(data) == "json"


def test_detect_ndjson():
    from backend.ingestion.parsers import detect_format
    data = b'{"a":"1"}\n{"b":"2"}\n'
    assert detect_format(data) == "ndjson"


def test_detect_xml():
    from backend.ingestion.parsers import detect_format
    data = b'<?xml version="1.0"?><feed><entry><ip>1.2.3.4</ip></entry></feed>'
    assert detect_format(data) == "xml"


def test_detect_xml_no_declaration():
    from backend.ingestion.parsers import detect_format
    data = b'<feed><entry><ip>1.2.3.4</ip></entry></feed>'
    assert detect_format(data) == "xml"


def test_detect_csv():
    from backend.ingestion.parsers import detect_format
    data = b"indicator,type,severity\n1.2.3.4,ip,high\n5.6.7.8,ip,low\n"
    assert detect_format(data) == "csv"


def test_detect_rejects_oversized():
    from backend.ingestion.parsers import detect_format, MAX_FILE_SIZE
    with pytest.raises(ValueError, match="maximum allowed size"):
        detect_format(b"x" * (MAX_FILE_SIZE + 1))


def test_detect_rejects_non_utf8():
    from backend.ingestion.parsers import detect_format
    with pytest.raises(ValueError, match="UTF-8"):
        detect_format(b"\xff\xfe bad bytes here")


# ── parse_file — JSON ─────────────────────────────────────────────────────────

def test_parse_json_object():
    from backend.ingestion.parsers import parse_file
    data = json.dumps({"indicator": "evil.com"}).encode()
    fmt, rows = parse_file(data)
    assert fmt == "json"
    assert rows == [{"indicator": "evil.com"}]


def test_parse_json_array():
    from backend.ingestion.parsers import parse_file
    data = json.dumps([{"a": 1}, {"b": 2}]).encode()
    fmt, rows = parse_file(data)
    assert fmt == "json"
    assert len(rows) == 2


# ── parse_file — NDJSON ───────────────────────────────────────────────────────

def test_parse_ndjson_valid():
    from backend.ingestion.parsers import parse_file
    data = b'{"cidr":"1.10.16.0/20","rir":"apnic"}\n{"cidr":"2.20.0.0/16","rir":"arin"}\n'
    fmt, rows = parse_file(data)
    assert fmt == "ndjson"
    assert len(rows) == 2
    assert rows[0]["cidr"] == "1.10.16.0/20"


def test_parse_ndjson_blank_lines():
    from backend.ingestion.parsers import parse_file
    data = b'\n{"a":"1"}\n\n{"b":"2"}\n   \n'
    fmt, rows = parse_file(data)
    assert fmt == "ndjson"
    assert len(rows) == 2


def test_parse_ndjson_bad_line():
    from backend.ingestion.parsers import parse_file
    data = b'{"a":"1"}\nnot-json\n{"b":"2"}'
    with pytest.raises(ValueError, match="NDJSON"):
        parse_file(data, fmt="ndjson")


# ── parse_file — CSV ──────────────────────────────────────────────────────────

def test_parse_csv_basic():
    from backend.ingestion.parsers import parse_file
    data = b"indicator,type,severity\n1.2.3.4,ip,high\nevil.com,domain,medium\n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert len(rows) == 2
    assert rows[0]["indicator"] == "1.2.3.4"
    assert rows[1]["type"] == "domain"


def test_parse_csv_strips_whitespace():
    from backend.ingestion.parsers import parse_file
    data = b" indicator , type \n 1.2.3.4 , ip \n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert "indicator" in rows[0]


def test_parse_csv_no_data_rows():
    from backend.ingestion.parsers import parse_file
    with pytest.raises(ValueError, match="no data rows"):
        parse_file(b"indicator,type\n", fmt="csv")


# ── parse_file — delimiter sniffing (prompts-015) ──────────────────────────────

def test_parse_tsv_tab_delimited():
    """Tab-separated input must be parsed as TSV, not as a single CSV column."""
    from backend.ingestion.parsers import parse_file
    data = b"c2_ip\tprotocol\tport\n1.2.3.4\tHTTPS\t443\n5.6.7.8\tHTTP\t80\n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert len(rows) == 2
    assert rows[0]["c2_ip"] == "1.2.3.4"
    assert rows[0]["protocol"] == "HTTPS"
    assert rows[0]["port"] == "443"
    assert rows[1]["c2_ip"] == "5.6.7.8"


def test_parse_csv_semicolon_delimited():
    from backend.ingestion.parsers import parse_file
    data = b"indicator;type;severity\n1.2.3.4;ip;high\nevil.com;domain;medium\n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert len(rows) == 2
    assert rows[0]["indicator"] == "1.2.3.4"
    assert rows[1]["type"] == "domain"


def test_parse_csv_pipe_delimited():
    from backend.ingestion.parsers import parse_file
    data = b"indicator|type|severity\n1.2.3.4|ip|high\n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert rows[0]["indicator"] == "1.2.3.4"
    assert rows[0]["severity"] == "high"


def test_parse_csv_first_line_is_header_verbatim():
    """A '#'-prefixed first line is the header verbatim (no comment stripping)."""
    from backend.ingestion.parsers import parse_file
    data = b"# c2_ip,first_seen,port\n1.2.3.4,2020-01-01,443\n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert len(rows) == 1
    # The '#' is part of the first column name, not stripped.
    assert "# c2_ip" in rows[0]
    assert rows[0]["# c2_ip"] == "1.2.3.4"


# ── flatten_entry (prompts-015) ────────────────────────────────────────────────

def test_flatten_entry_nested_dict():
    from backend.ingestion.parsers import flatten_entry
    obj = {"cve": {"id": "CVE-2024-1", "desc": {"lang": "en", "value": "x"}}}
    out = flatten_entry(obj, max_depth=5)
    assert out["cve.id"] == "CVE-2024-1"
    assert out["cve.desc.lang"] == "en"
    assert out["cve.desc.value"] == "x"


def test_flatten_entry_list_of_primitives_joined():
    from backend.ingestion.parsers import flatten_entry
    obj = {"tags": ["malware", "apt", "phish"]}
    out = flatten_entry(obj)
    assert out["tags"] == "malware, apt, phish"


def test_flatten_entry_list_of_dicts_first_plus_count():
    from backend.ingestion.parsers import flatten_entry
    obj = {"references": [
        {"url": "https://a.example", "tag": "vendor"},
        {"url": "https://b.example", "tag": "exploit"},
        {"url": "https://c.example", "tag": "blog"},
    ]}
    out = flatten_entry(obj)
    assert out["references.url"] == "https://a.example"
    assert out["references.tag"] == "vendor"
    assert out["references._count"] == 3


def test_flatten_entry_depth_cap_truncates_to_string():
    from backend.ingestion.parsers import flatten_entry
    obj = {"a": {"b": {"c": {"d": "deep"}}}}
    out = flatten_entry(obj, max_depth=2)
    # depth cap kicks in before reaching 'd'
    assert "a.b" in out or "a.b.c" in out
    # The deepest reachable value is stringified, not a dict
    for v in out.values():
        assert not isinstance(v, dict)


def test_extract_entries_vulnerabilities_envelope():
    """NVD 2.0 shape: {'vulnerabilities': [{'cve': {...}}, ...]}"""
    from backend.ingestion.parsers import extract_entries
    payload = {"vulnerabilities": [
        {"cve": {"id": "CVE-1"}},
        {"cve": {"id": "CVE-2"}},
    ]}
    rows = extract_entries(payload)
    assert len(rows) == 2
    assert rows[0]["cve"]["id"] == "CVE-1"


def test_extract_entries_cve_items_envelope():
    """NVD 1.1 legacy shape: {'CVE_Items': [...]}"""
    from backend.ingestion.parsers import extract_entries
    payload = {"CVE_Items": [{"foo": "bar"}, {"baz": "qux"}]}
    rows = extract_entries(payload)
    assert len(rows) == 2
    assert rows[0]["foo"] == "bar"


def test_parse_json_nvd_shape_flattens():
    """End-to-end: NVD-shaped JSON yields multiple flattened rows, not [object Object]."""
    from backend.ingestion.parsers import parse_file
    payload = json.dumps({
        "vulnerabilities": [
            {"cve": {"id": "CVE-2024-1", "metrics": {"score": 9.8}}},
            {"cve": {"id": "CVE-2024-2", "metrics": {"score": 5.0}}},
        ]
    }).encode()
    fmt, rows = parse_file(payload)
    assert fmt == "json"
    assert len(rows) == 2
    assert rows[0]["cve.id"] == "CVE-2024-1"
    assert rows[0]["cve.metrics.score"] == 9.8
    assert rows[1]["cve.id"] == "CVE-2024-2"


# ── parse_file — XML ──────────────────────────────────────────────────────────

def test_parse_xml_basic():
    from backend.ingestion.parsers import parse_file
    data = b"""<?xml version="1.0"?>
<feed>
  <entry><indicator>1.2.3.4</indicator><severity>high</severity></entry>
  <entry><indicator>evil.com</indicator><severity>medium</severity></entry>
</feed>"""
    fmt, rows = parse_file(data)
    assert fmt == "xml"
    assert len(rows) == 2
    assert rows[0]["indicator"] == "1.2.3.4"
    assert rows[1]["severity"] == "medium"


def test_parse_xml_attributes():
    from backend.ingestion.parsers import parse_file
    data = b'<feed><entry ip="1.2.3.4" severity="high"/></feed>'
    fmt, rows = parse_file(data)
    assert fmt == "xml"
    assert rows[0]["ip"] == "1.2.3.4"


def test_parse_xml_invalid():
    from backend.ingestion.parsers import parse_file
    with pytest.raises(ValueError, match="valid XML"):
        parse_file(b"<not closed", fmt="xml")


def test_parse_xml_empty():
    from backend.ingestion.parsers import parse_file
    with pytest.raises(ValueError, match="no parseable entries"):
        parse_file(b"<feed></feed>", fmt="xml")


# ── extract_entries ───────────────────────────────────────────────────────────


def test_extract_entries_envelope_data():
    from backend.ingestion.parsers import extract_entries
    payload = {"meta": {"v": 1}, "data": [{"a": 1}, {"a": 2}]}
    assert extract_entries(payload) == [{"a": 1}, {"a": 2}]


def test_extract_entries_envelope_results():
    from backend.ingestion.parsers import extract_entries
    payload = {"count": 3, "results": [{"x": 1}, {"x": 2}, {"x": 3}]}
    assert extract_entries(payload) == [{"x": 1}, {"x": 2}, {"x": 3}]


def test_extract_entries_single_list_dict_value_auto_detect():
    """Unknown envelope key with a single list[dict] value is auto-detected."""
    from backend.ingestion.parsers import extract_entries
    payload = {"misc_threats": [{"i": "1.1.1.1"}, {"i": "2.2.2.2"}]}
    assert extract_entries(payload) == [{"i": "1.1.1.1"}, {"i": "2.2.2.2"}]


def test_extract_entries_true_single_object_preserved():
    """A bare object payload (no list values) is still treated as one entry."""
    from backend.ingestion.parsers import extract_entries
    payload = {"indicator": "1.2.3.4", "severity": "high"}
    assert extract_entries(payload) == [payload]


def test_extract_entries_top_level_list_passthrough():
    from backend.ingestion.parsers import extract_entries
    payload = [{"a": 1}, {"a": 2}]
    assert extract_entries(payload) == payload


# ── extract_entries: map-of-records (prompts-063) ──────────────────────────────


def test_extract_entries_map_of_records_misp_manifest_splits():
    """A MISP-style manifest keyed by event UUID is split into one row per
    record (root-cause fix for the 1-giant-row ingestion bug)."""
    from backend.ingestion.parsers import extract_entries
    u1 = "3f2a1b4c-5d6e-7081-92a3-b4c5d6e7f809"
    u2 = "9988aabb-ccdd-eeff-0011-223344556677"
    u3 = "11112222-3333-4444-5555-666677778888"
    payload = {
        u1: {"Orgc": {"name": "OrgA"}, "info": "evt1", "date": "2026-01-01"},
        u2: {"Orgc": {"name": "OrgB"}, "info": "evt2", "date": "2026-01-02"},
        u3: {"Orgc": {"name": "OrgC"}, "info": "evt3", "date": "2026-01-03"},
    }
    rows = extract_entries(payload)
    assert len(rows) == 3
    assert {r["info"] for r in rows} == {"evt1", "evt2", "evt3"}


def test_extract_entries_map_of_records_requires_homogeneity():
    """Two value-dicts sharing no common core keys are NOT a record map; the
    payload is preserved as a single object (Step-5 fallback)."""
    from backend.ingestion.parsers import extract_entries
    payload = {
        "alpha": {"foo": 1, "bar": 2},
        "beta": {"baz": 3, "qux": 4},
    }
    assert extract_entries(payload) == [payload]


def test_extract_entries_map_of_records_needs_min_two_records():
    """A single keyed record dict is below the record-map threshold and is kept
    as one entry (a genuine single-object payload)."""
    from backend.ingestion.parsers import extract_entries
    payload = {"only": {"Orgc": {"name": "OrgA"}, "info": "evt1", "date": "x"}}
    assert extract_entries(payload) == [payload]


def test_extract_entries_map_of_records_non_dict_values_not_split():
    """Scalar values mean this is an ordinary single object, not a record map."""
    from backend.ingestion.parsers import extract_entries
    payload = {"indicator": "1.2.3.4", "severity": "high", "score": 9}
    assert extract_entries(payload) == [payload]


def test_extract_entries_envelope_takes_precedence_over_record_map():
    """A well-known envelope key wins even if the dict could look like a map."""
    from backend.ingestion.parsers import extract_entries
    payload = {"data": [{"a": 1}, {"a": 2}], "meta": {"k": 1}}
    assert extract_entries(payload) == [{"a": 1}, {"a": 2}]
