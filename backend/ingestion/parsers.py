"""
Parsers — format detection and parsing for JSON, NDJSON, CSV, and XML feeds.
Used by both local upload and remote fetch ingestion paths.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB hard limit

# Well-known envelope keys used by major TI/REST APIs to wrap a record list.
# Extended in prompts-015 for NVD (1.1 + 2.0) and other common feeds.
_ENVELOPE_KEYS: tuple[str, ...] = (
    "data", "results", "items", "entries", "objects", "indicators", "events",
    "vulnerabilities", "CVE_Items", "cves", "records", "feed", "value",
)

# Default flatten depth when no setting is available (used by tests).
_DEFAULT_FLATTEN_DEPTH = 5


def flatten_entry(
    obj: Any,
    max_depth: int = _DEFAULT_FLATTEN_DEPTH,
    sep: str = ".",
    _prefix: str = "",
    _depth: int = 0,
) -> dict[str, Any]:
    """Flatten a nested dict (TI feed record) into a single-level dict.

    Rules:
      - dict       -> recurse, joining keys with ``sep``.
      - list of primitives -> ", "-joined string.
      - list of dicts      -> first item flattened; ``<key>._count = N`` added.
      - depth cap reached  -> value serialised via JSON (or str fallback).
      - primitives -> passthrough.

    The function always returns a dict; the top-level ``obj`` must itself
    be a dict (any other type results in ``{}``).
    """
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in obj.items():
        key = f"{_prefix}{sep}{k}" if _prefix else str(k)
        if _depth >= max_depth:
            out[key] = _stringify_leaf(v)
            continue
        if isinstance(v, dict):
            out.update(flatten_entry(v, max_depth=max_depth, sep=sep, _prefix=key, _depth=_depth + 1))
        elif isinstance(v, list):
            if not v:
                out[key] = ""
            elif all(not isinstance(x, (dict, list)) for x in v):
                out[key] = ", ".join("" if x is None else str(x) for x in v)
            elif all(isinstance(x, dict) for x in v):
                nested = flatten_entry(v[0], max_depth=max_depth, sep=sep, _prefix=key, _depth=_depth + 1)
                out.update(nested)
                out[f"{key}{sep}_count"] = len(v)
            else:
                # Mixed list — stringify
                out[key] = _stringify_leaf(v)
        else:
            out[key] = v
    return out


def _stringify_leaf(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    try:
        import json as _json
        return _json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _flatten_all(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply flatten_entry to every dict in a list, using the configured depth."""
    try:
        from backend.config.loader import load_flatten_max_depth
        depth = load_flatten_max_depth()
    except Exception:
        depth = _DEFAULT_FLATTEN_DEPTH
    out: list[dict[str, Any]] = []
    for e in entries:
        if isinstance(e, dict):
            out.append(flatten_entry(e, max_depth=depth))
    return out


# Minimum number of records a dict must hold before it is considered a
# "map of records" (keyed-by-id collection) rather than a single object.
_RECORD_MAP_MIN_RECORDS = 2
# Fraction of records a key must appear in to count as a shared "core" key.
_RECORD_MAP_CORE_THRESHOLD = 0.60
# Minimum number of shared core keys required to treat the dict as homogeneous.
_RECORD_MAP_MIN_CORE_KEYS = 2


def _looks_like_record_map(payload: dict[str, Any]) -> bool:
    """Heuristic: is ``payload`` a dict keyed by id whose values are records?

    Detection is purely *structural* — no assumptions about the shape of the
    keys (no UUID/hex/numeric regexes). A dict is treated as a map of records
    when ALL of the following hold:

      - it has at least ``_RECORD_MAP_MIN_RECORDS`` entries;
      - every value is itself a dict;
      - at least ``_RECORD_MAP_MIN_CORE_KEYS`` keys are shared by ≥60% of the
        record dicts (structural homogeneity).

    This catches MISP-style manifests ``{uuid: {Orgc, Tag, info, date, ...}}``
    without hardcoding anything about the outer key format.
    """
    values = list(payload.values())
    if len(values) < _RECORD_MAP_MIN_RECORDS:
        return False
    if not all(isinstance(v, dict) for v in values):
        return False

    record_count = len(values)
    key_freq: dict[str, int] = {}
    for rec in values:
        for k in rec.keys():
            key_freq[k] = key_freq.get(k, 0) + 1

    threshold = record_count * _RECORD_MAP_CORE_THRESHOLD
    core_keys = [k for k, n in key_freq.items() if n >= threshold]
    return len(core_keys) >= _RECORD_MAP_MIN_CORE_KEYS


def extract_entries(payload: Any) -> list[dict[str, Any]]:
    """
    Coerce a parsed JSON payload into a list of entry dicts.

    Resolution order:
      1. list                       → return as-is
      2. dict containing a well-known envelope key whose value is list[dict]
                                    → return that nested list (log INFO)
      3. dict containing exactly one value that is list[dict]
                                    → return that nested list (log INFO)
      4. dict that is a homogeneous map of records (keyed by id)
                                    → return list(payload.values()) (log INFO)
      5. dict                       → return [payload]  (single-object payload)
      6. anything else              → []
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    # Step 2 — well-known envelope keys
    for key in _ENVELOPE_KEYS:
        val = payload.get(key)
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            logger.info("Envelope key '%s' detected → %d entries", key, len(val))
            return val

    # Step 3 — single list[dict] value
    list_dict_values = [
        v for v in payload.values()
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
    ]
    if len(list_dict_values) == 1:
        logger.info(
            "Auto-detected single list[dict] value → %d entries",
            len(list_dict_values[0]),
        )
        return list_dict_values[0]

    # Step 4 — homogeneous map of records keyed by id (e.g. MISP manifest)
    if _looks_like_record_map(payload):
        records = list(payload.values())
        logger.info(
            "Auto-detected map of records (%d keyed entries) → splitting into rows",
            len(records),
        )
        return records

    # Step 5 — fallback
    return [payload]


def detect_format(raw_bytes: bytes) -> str:
    """
    Detect the structured format of raw bytes.
    Returns one of: 'json', 'ndjson', 'csv', 'xml'.

    Detection order:
    1. Try json.loads — standard JSON object or array → 'json'
    2. Try line-by-line JSON (NDJSON) → 'ndjson'
    3. Check for XML declaration or root element tag → 'xml'
    4. Fall back to CSV
    """
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise ValueError(
            f"File exceeds maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8: {exc}") from exc

    stripped = text.lstrip()

    # ── Standard JSON ──────────────────────────────────────────────────────
    try:
        payload = json.loads(text)
        if isinstance(payload, (dict, list)):
            return "json"
    except json.JSONDecodeError:
        pass

    # ── NDJSON ────────────────────────────────────────────────────────────
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        try:
            for line in lines[:5]:  # probe first 5 lines only
                json.loads(line)
            return "ndjson"
        except json.JSONDecodeError:
            pass

    # ── XML ───────────────────────────────────────────────────────────────
    if stripped.startswith("<?xml") or stripped.startswith("<"):
        return "xml"

    # ── CSV (default) ─────────────────────────────────────────────────────
    return "csv"


def parse_file(raw_bytes: bytes, fmt: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """
    Parse raw bytes into a list of dicts.
    Returns (detected_format, list_of_dicts).
    If fmt is provided it overrides detection.
    Raises ValueError on invalid input.
    """
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise ValueError(
            f"File exceeds maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8: {exc}") from exc

    detected = fmt or detect_format(raw_bytes)

    if detected == "json":
        return detected, _parse_json(text)
    if detected == "ndjson":
        return detected, _parse_ndjson(text)
    if detected == "csv":
        return detected, _parse_csv(text)
    if detected == "xml":
        return detected, _parse_xml(text)

    raise ValueError(f"Unsupported format: {detected!r}")


# ── Format-specific parsers ────────────────────────────────────────────────────


def _parse_json(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"File is not valid JSON: {exc}") from exc

    entries = extract_entries(payload)
    if not entries:
        raise ValueError("JSON must be an object or array of objects")
    return _flatten_all(entries)


def _parse_ndjson(text: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("File is empty")
    result: list[dict[str, Any]] = []
    for i, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"File is not valid JSON or NDJSON: line {i}: {exc}"
            ) from exc
        result.append(obj)
    return _flatten_all(result)


def _sniff_delimiter(text: str) -> str:
    """Sniff the delimiter of a CSV-like text.

    Strategy:
      1. Take a sample (~8 KB) from the first non-blank line onward.
      2. If TAB occurs more than 3x as often as the next-most-frequent
         candidate among (',', ';', '|'), force TAB.
      3. Otherwise let csv.Sniffer pick among ',\\t;|'.
      4. Fall back to ',' on any failure.
    """
    # Strip leading blank lines, then take ~8 KB of sample.
    lines = text.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip():
            start = i
            break
    sample = "\n".join(lines[start:start + 50])[:8192]
    if not sample:
        return ","

    # TSV tiebreaker
    counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
    tab = counts["\t"]
    other_max = max(counts[","], counts[";"], counts["|"])
    if tab > 0 and tab > 3 * other_max:
        logger.info("CSV delimiter sniffed: TAB (tiebreaker)")
        return "\t"

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        logger.info("CSV delimiter sniffed: %r", dialect.delimiter)
        return dialect.delimiter
    except csv.Error:
        logger.info("CSV delimiter sniff failed, falling back to ','")
        return ","


def _parse_csv(text: str) -> list[dict[str, Any]]:
    """
    Parse CSV/TSV text. The first non-blank line is the header verbatim.

    The delimiter is auto-detected among (',', '\\t', ';', '|') with a
    TAB tiebreaker — see _sniff_delimiter. The first non-blank line is
    treated as the header verbatim (no comment stripping).
    """
    delimiter = _sniff_delimiter(text)
    # Trim any leading blank lines so the header is the actual first row.
    lines = text.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip():
            start = i
            break
    cleaned_text = "\n".join(lines[start:])

    reader = csv.DictReader(io.StringIO(cleaned_text), delimiter=delimiter)
    if reader.fieldnames is None:
        raise ValueError("CSV file has no header row")
    rows: list[dict[str, Any]] = []
    for row in reader:
        # Strip whitespace from keys and values; skip fully empty rows
        cleaned = {k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items() if k}
        if any(v for v in cleaned.values()):
            rows.append(cleaned)
    if not rows:
        raise ValueError("CSV file contains no data rows")
    return rows


def _parse_xml(text: str) -> list[dict[str, Any]]:
    """
    Parse XML into a list of dicts. Each direct child of the root element
    becomes one dict entry; its own child elements become key-value pairs.
    Attributes of child elements are also included.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"File is not valid XML: {exc}") from exc

    rows: list[dict[str, Any]] = []
    for child in root:
        entry: dict[str, Any] = {}
        # Include child attributes
        entry.update(child.attrib)
        # Include child sub-elements as key-value pairs
        for sub in child:
            tag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag  # strip namespace
            entry[tag] = sub.text.strip() if sub.text else ""
        # If the child element itself has text and no sub-elements, use its tag
        if not list(child) and child.text and child.text.strip():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            entry[tag] = child.text.strip()
        if entry:
            rows.append(entry)

    if not rows:
        raise ValueError("XML file contains no parseable entries")
    return _flatten_all(rows)
