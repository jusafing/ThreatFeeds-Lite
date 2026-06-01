"""
Smart-mode helpers (prompts-021E-1).

Pure, I/O-free building blocks for LLM-assisted mapping proposals:
  * sample_raw_entries      — pull a sanitised sample of raw entries from a source
  * discover_raw_field_names — union of keys, first-seen order
  * build_prompt            — produces (system_prompt, user_prompt)
  * parse_llm_response      — strict JSON extraction with code-fence stripping
  * validate_proposal       — closes the canonical set; drops unknowns
  * SmartModeError          — single error class for all of the above

Security: raw values are control-char-stripped, truncated to 80 chars, and
JSON-encoded before reaching the prompt body. The LLM response is validated
against the closed yaml-derived canonical set; unknown canonicals are
silently dropped. The operator's manual approval is the final defence.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

from backend.config.loader import get_configured_field_names, load_fields
from backend.db.manager import query_entries
from backend.normalizer.db import query_normalized

logger = logging.getLogger(__name__)

_MAX_VALUE_CHARS = 80
# prompts-061: lowered 50→10. Combined with the field-centric prompt
# (build_prompt) this keeps the LLM payload small enough for external gateways
# that reject oversized requests with HTTP 400. The route default
# (routes_smart._DEFAULT_SAMPLE_SIZE) re-exports this value.
_DEFAULT_SAMPLE_SIZE = 10
_MAX_SAMPLE_SIZE = 100
_SKIP_TOKEN = "__skip__"

# ── prompts-061/063: field-centric prompt ───────────────────────────────────
# The prompt is built around FIELD NAMES (the primary signal for where a field
# belongs in the canonical schema). Example values are sent only for fields
# whose nature can't be inferred from the name, and only within a byte budget.
#
# prompts-063: the structural-signature collapse (UUID/hex/numeric → "*") was
# removed. It existed only to tame a MISP manifest that ingested as ONE giant
# row of "<event-uuid>.Orgc.name"-style keys; that root cause is now fixed in
# ingestion (parsers.extract_entries splits a map-of-records into one row per
# record), so each record flattens to a few dozen normal field names. The LLM
# now sees and maps the real field names directly — no expand-back step.
_MAX_EXAMPLES_PER_FIELD = 3
# Byte budget for the OPTIONAL example-values section. Field names and the
# canonical schema are ALWAYS emitted in full (guaranteed LLM context); only
# inline example values are capped.
_SAMPLE_BUDGET_CHARS = 20000
# Safety ceiling on the number of distinct field names sent. Far above any
# realistic per-record schema; bounds pathological sources.
_MAX_PROMPT_FIELDS = 2000

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

# Strip C0 control chars except \t (0x09), \n (0x0A), \r (0x0D). Helps reduce
# prompt-injection noise before JSON-encoding (which already escapes these,
# but stripping cuts visible junk in audit records too).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SmartModeError(Exception):
    """Raised on any unrecoverable smart-mode validation failure."""


# ── Sampling ────────────────────────────────────────────────────────────────


def _sanitise_value(value: Any) -> Any:
    """Apply the prompt-injection mitigation chain to a single value.

    Order: control-char strip → 80-char truncate → JSON-encode for non-scalars.
    Returns either a plain string or the original numeric/bool/None scalar
    (caller json.dumps()es the whole sample row so quoting is consistent).
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = _CONTROL_CHAR_RE.sub("", value)
        if len(cleaned) > _MAX_VALUE_CHARS:
            cleaned = cleaned[: _MAX_VALUE_CHARS - 1] + "…"
        return cleaned
    # list/dict/anything else → JSON-encode then truncate
    try:
        encoded = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        encoded = str(value)
    encoded = _CONTROL_CHAR_RE.sub("", encoded)
    if len(encoded) > _MAX_VALUE_CHARS:
        encoded = encoded[: _MAX_VALUE_CHARS - 1] + "…"
    return encoded


def _sanitise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with every value passed through _sanitise_value
    and housekeeping keys removed.

    prompts-056: every non-housekeeping key is retained even when its value
    is ``None``/empty in this particular row. Previously empty-valued keys
    were dropped, so a field that was sparse/optional across the small sample
    never reached ``discover_raw_field_names`` and was silently omitted from
    the LLM prompt — yielding proposals with very few fields. Keeping the key
    (with its empty value) guarantees every field seen in any sampled row is
    offered to the LLM for mapping.
    """
    skip_keys = {"id", "ingested_at", "ingest_mode", "dedup_key", "normalized", "raw", "extra"}
    return {
        k: _sanitise_value(v)
        for k, v in row.items()
        if k not in skip_keys
    }


async def sample_raw_entries(
    source_name: str,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
) -> list[dict[str, Any]]:
    """Sample up to ``sample_size`` rows from ``source_name``.

    Strategy:
      1. Pull un-normalized rows first (most interesting for smart-mode).
      2. If empty, fall back to normalized rows (still useful for mapping audit).
      3. If still empty, raise :class:`SmartModeError`.

    Each returned row is sanitised: values stripped of control chars,
    truncated to 80 chars, non-scalars JSON-encoded.
    """
    if sample_size <= 0:
        raise SmartModeError("sample_size must be > 0")
    capped = min(sample_size, _MAX_SAMPLE_SIZE)

    rows = await query_entries(
        source_name=source_name, limit=capped, filters={"normalized": 0}
    )
    if not rows:
        rows = await query_entries(source_name=source_name, limit=capped)
    if not rows:
        # Last resort — normalized DB. May still be empty.
        rows = await query_normalized(source_name=source_name, limit=capped)
    if not rows:
        raise SmartModeError(
            f"source {source_name!r} has no entries to sample"
        )
    return [_sanitise_row(r) for r in rows]


async def sample_consolidated_entries(
    sources: list[str],
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Sample up to ``sample_size`` rows from EACH source and combine them.

    prompts-032: consolidated (multi-feed) proposals. ``sample_size`` is
    applied *per source* (reusing :func:`sample_raw_entries`) so every
    selected feed is represented in the combined sample, then the rows are
    concatenated. The caller unions the raw field names across the combined
    sample to build a single global prompt.

    Sources that have no entries to sample are skipped rather than aborting
    the whole job, so a single empty feed in a multi-feed request is not
    fatal. Returns ``(combined_samples, contributing_sources)`` where
    ``contributing_sources`` lists only the feeds that yielded rows
    (preserving input order).

    Raises :class:`SmartModeError` when ``sources`` is empty or when NONE of
    the selected sources yield any rows.
    """
    if not sources:
        raise SmartModeError("no sources provided")
    combined: list[dict[str, Any]] = []
    contributing: list[str] = []
    for src in sources:
        try:
            rows = await sample_raw_entries(src, sample_size=sample_size)
        except SmartModeError:
            logger.info(
                "smart-mode consolidated: source %r has no entries; skipping",
                src,
            )
            continue
        combined.extend(rows)
        contributing.append(src)
    if not combined:
        raise SmartModeError(
            "none of the selected sources have entries to sample: "
            + ", ".join(repr(s) for s in sources)
        )
    return combined, contributing


def discover_raw_field_names(samples: list[dict[str, Any]]) -> list[str]:
    """Union of keys across samples, preserving first-seen order."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in samples:
        for key in row.keys():
            if key not in seen_set:
                seen.append(key)
                seen_set.add(key)
    return seen


# ── Prompt building ─────────────────────────────────────────────────────────


def _canonical_field_names(field_scope: str = "all") -> list[str]:
    """Closed canonical set, sourced from feed-fields.yaml.

    prompts-032 Phase E: ``field_scope`` controls which canonical names are
    offered to the LLM (and used to close the validated proposal):
      * ``'all'`` (default, per-source path) — every core + custom field,
        regardless of its ``enabled`` flag.
      * ``'configured'`` (consolidated opt-in) — only ENABLED core + custom
        fields, via :func:`backend.config.loader.get_configured_field_names`.
    """
    if field_scope == "configured":
        return get_configured_field_names()
    data = load_fields() or {}
    names: list[str] = []
    seen: set[str] = set()
    for group in ("core_fields", "custom_fields"):
        for field in data.get(group, []) or []:
            name = (field or {}).get("name")
            if name and name not in seen:
                names.append(name)
                seen.add(name)
    return names


def _norm_token(value: str) -> str:
    """Lowercase and strip non-alphanumerics for loose name comparison."""
    return _NON_ALNUM_RE.sub("", value.lower())


def _is_self_describing(
    field_name: str, canonical_norms: set[str]
) -> bool:
    """True when a field's leaf name already matches a canonical name.

    Such fields need no example values — the name alone tells the LLM where the
    field belongs — so we spend the example budget only on ambiguous fields.
    """
    leaf = _norm_token(field_name.split(".")[-1])
    return bool(leaf) and leaf in canonical_norms


def _example_values_for(
    field_name: str,
    samples: list[dict[str, Any]],
    limit: int,
) -> list[str]:
    """Collect up to ``limit`` distinct non-empty example values for a field.

    Scans the field across the sample rows. Values are already
    sanitised/truncated by :func:`_sanitise_row`.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in samples:
        if field_name not in row:
            continue
        value = row[field_name]
        if value is None or value == "" or value == [] or value == {}:
            continue
        rendered = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, default=str)
        )
        if rendered in seen_set:
            continue
        seen.append(rendered)
        seen_set.add(rendered)
        if len(seen) >= limit:
            return seen
    return seen


def _field_section(
    samples: list[dict[str, Any]],
    raw_fields: list[str],
    canonical_fields: list[str],
) -> str:
    """Build the field-centric raw-field block.

    Every field NAME is always emitted so the LLM is asked to decide every
    field (guaranteed context). Example values are added only to fields whose
    name is not self-describing, and only while within
    :data:`_SAMPLE_BUDGET_CHARS`. The field count is capped at
    :data:`_MAX_PROMPT_FIELDS` as a last-resort size guard.
    """
    canonical_norms = {_norm_token(c) for c in canonical_fields}

    truncated = len(raw_fields) > _MAX_PROMPT_FIELDS
    emitted = raw_fields[:_MAX_PROMPT_FIELDS] if truncated else raw_fields

    lines: list[str] = []
    budget = _SAMPLE_BUDGET_CHARS
    for field in emitted:
        if budget > 0 and not _is_self_describing(field, canonical_norms):
            examples = _example_values_for(
                field, samples, _MAX_EXAMPLES_PER_FIELD
            )
            if examples:
                rendered = ", ".join(
                    json.dumps(e, ensure_ascii=False) for e in examples
                )
                suffix = f"  e.g. {rendered}"
                if len(suffix) <= budget:
                    budget -= len(suffix)
                    lines.append(f"  - {field}{suffix}")
                    continue
        lines.append(f"  - {field}")

    block = "\n".join(lines) if lines else "(none)"
    if truncated:
        block += (
            f"\n  … (field list truncated to the first {_MAX_PROMPT_FIELDS} "
            f"of {len(raw_fields)} distinct fields to fit the request size)"
        )
    return block


def build_prompt(
    source_name: str,
    samples: list[dict[str, Any]],
    raw_fields: list[str],
    canonical_fields: list[str],
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for the LLM call.

    prompts-061: the prompt is FIELD-CENTRIC. Field names are the primary
    signal for mapping; example values are attached only to fields whose name
    is ambiguous, within a byte budget. The canonical schema and the full
    field list always precede any example values, so the field definitions are
    guaranteed to reach the model.

    Hard constraints baked into the prompts:
      * respond with JSON only
      * map each field to exactly one canonical name OR the literal
        string ``"__skip__"``
      * do NOT invent new canonical names
    """
    canon_list = "\n".join(f"  - {c}" for c in canonical_fields)
    field_block = _field_section(samples, raw_fields, canonical_fields)

    system_prompt = (
        "You are a Threat Intelligence field-mapping assistant. "
        "Your job is to map raw feed field names to a fixed canonical schema. "
        "You MUST respond with a single JSON object and nothing else. "
        "The JSON object MUST map each raw field name to either one of the "
        "listed canonical names or the literal string \"__skip__\". "
        "You MUST NOT invent canonical names. "
        "You MUST NOT include any prose, explanation, or markdown."
    )

    user_prompt = (
        f"Source name: {source_name}\n\n"
        f"Canonical fields (closed set; use exact name or \"__skip__\"):\n"
        f"{canon_list}\n\n"
        f"Raw fields to map — map EACH field below to a canonical name or "
        f"\"__skip__\". The field name is the primary signal; some fields show "
        f"example values (\"e.g. …\") only where the name alone is ambiguous:\n"
        f"{field_block}\n\n"
        f"Return a JSON object whose keys are EXACTLY the field names listed "
        f"above and whose values are the corresponding canonical name or "
        f"\"__skip__\"."
    )
    return system_prompt, user_prompt


def build_consolidated_prompt(
    sources: list[str],
    samples: list[dict[str, Any]],
    raw_fields: list[str],
    canonical_fields: list[str],
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for a CONSOLIDATED request.

    prompts-032: like :func:`build_prompt` but the raw fields are the union
    seen across MULTIPLE feeds and the LLM produces ONE mapping dict that
    applies to all of them. Because normalization maps by raw-field name,
    one global ``{raw_field: canonical}`` dict works regardless of source.

    Same hard constraints as :func:`build_prompt` (JSON-only, closed
    canonical set, ``"__skip__"`` for no-map, no invented names).
    """
    label = ", ".join(sources) if sources else "(none)"
    canon_list = "\n".join(f"  - {c}" for c in canonical_fields)
    field_block = _field_section(samples, raw_fields, canonical_fields)

    system_prompt = (
        "You are a Threat Intelligence field-mapping assistant. "
        "Your job is to map raw feed field names to a fixed canonical schema. "
        "The raw fields below are the UNION of fields seen across MULTIPLE "
        "feeds; produce a single consolidated mapping that applies to all of "
        "them. "
        "You MUST respond with a single JSON object and nothing else. "
        "The JSON object MUST map each raw field name to either one of the "
        "listed canonical names or the literal string \"__skip__\". "
        "You MUST NOT invent canonical names. "
        "You MUST NOT include any prose, explanation, or markdown."
    )

    user_prompt = (
        f"Source feeds ({len(sources)}): {label}\n\n"
        f"Canonical fields (closed set; use exact name or \"__skip__\"):\n"
        f"{canon_list}\n\n"
        f"Raw fields to map (union across all feeds) — map EACH field below to "
        f"a canonical name or \"__skip__\". The field name is the primary "
        f"signal; some fields show example values (\"e.g. …\") only where the "
        f"name alone is ambiguous:\n"
        f"{field_block}\n\n"
        f"Return a JSON object whose keys are EXACTLY the field names listed "
        f"above and whose values are the corresponding canonical name or "
        f"\"__skip__\"."
    )
    return system_prompt, user_prompt


# ── Response parsing & validation ───────────────────────────────────────────


_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?|\n?```\s*$", re.MULTILINE)

# prompts-034: a trailing comma before a closing brace/bracket is the single
# most common JSON defect emitted by chat models. Stripped in the repair pass.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_code_fences(text: str) -> str:
    """Remove leading/trailing ``` and ```json fences if present."""
    return _FENCE_RE.sub("", text).strip()


# prompts-035: reasoning models (e.g. gpt-oss:120b) emit OpenAI-"Harmony"
# channel output — interleaved analysis/commentary/final segments delimited by
# ``<|channel|>`` / ``<|message|>`` control tokens. The real mapping, when
# present, lives in the LAST ``final`` channel; the analysis channel often
# contains stray braces that defeat first-{…last-} extraction.
_HARMONY_TOKEN_RE = re.compile(r"<\|[^>]*\|>")
_HARMONY_FINAL_RE = re.compile(r"final\s*<\|message\|>")


def _strip_harmony(text: str) -> str:
    """Reduce Harmony channel output to its final-channel payload.

    Best-effort and never raises. When no ``<|…|>`` tokens are present the text
    is returned unchanged, so non-reasoning providers are unaffected.
    """
    if "<|" not in text:
        return text
    finals = list(_HARMONY_FINAL_RE.finditer(text))
    if finals:
        text = text[finals[-1].end():]
    return _HARMONY_TOKEN_RE.sub("", text)


def _strip_json_comments(text: str) -> str:
    """Remove ``// …`` and ``/* … */`` comments that some models embed in JSON.

    prompts-035: a string-aware scanner so ``//`` or ``/*`` *inside* a string
    value (e.g. ``"https://…"``) is preserved. Handles both ``"`` and ``'``
    delimiters and backslash escapes. Best-effort: never raises.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    quote = ""
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                in_str = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            if nl < 0:
                break
            i = nl
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            close = text.find("*/", i + 2)
            if close < 0:
                break
            i = close + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_tolerant(blob: str) -> Any:
    """Parse ``blob`` as JSON, recovering from the common model defects.

    prompts-034: chat models routinely emit JSON that strict :func:`json.loads`
    rejects even when the mapping itself is well-formed. We attempt, in order:

      1. ``json.loads(strict=False)`` — accepts literal control characters
         (unescaped newlines/tabs) inside string values (sample error 3).
      2. the same after stripping trailing commas before ``}``/``]``.
      3. :func:`ast.literal_eval` — accepts single-quoted keys/values and
         Python ``True``/``False``/``None`` (sample error 2).

    Raises the ORIGINAL :class:`json.JSONDecodeError` if every attempt fails,
    so the caller can surface a precise diagnostic.
    """
    try:
        return json.loads(blob, strict=False)
    except json.JSONDecodeError as first_exc:
        # Attempt 2: drop trailing commas, retry tolerant.
        repaired = _TRAILING_COMMA_RE.sub(r"\1", blob)
        if repaired != blob:
            try:
                return json.loads(repaired, strict=False)
            except json.JSONDecodeError:
                pass
        # Attempt 3: Python-literal eval (single quotes / True/False/None).
        try:
            value = ast.literal_eval(blob)
        except (ValueError, SyntaxError):
            raise first_exc
        if isinstance(value, dict):
            return value
        raise first_exc


def parse_llm_response(text: str) -> dict[str, str]:
    """Parse the raw LLM text into a ``{raw_field: canonical_or_skip}`` dict.

    Accepts the response with or without code fences and tolerates the common
    chat-model JSON defects (see :func:`_loads_tolerant`). Raises
    :class:`SmartModeError` on any structural failure, with a message precise
    enough to diagnose the offending response (the full raw text is preserved
    separately on the proposal row's ``llm_response_raw``).
    """
    cleaned = _strip_code_fences(_strip_harmony(text or ""))
    if not cleaned:
        raise SmartModeError(
            "LLM response is empty (the model returned no content — this often "
            "means the output-token budget was exhausted by reasoning, or the "
            "request timed out)"
        )
    # Find the first '{' and the matching closing '}' — defensive against
    # providers that prepend stray prose despite the system prompt.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise SmartModeError(
            "LLM response does not contain a JSON object "
            f"(response starts with: {cleaned[:80]!r})"
        )
    # prompts-035: strip JS-style comments some models embed inside the object
    # (e.g. gpt-oss) before the tolerant JSON load.
    blob = _strip_json_comments(cleaned[start : end + 1])
    try:
        parsed = _loads_tolerant(blob)
    except json.JSONDecodeError as exc:
        raise SmartModeError(
            f"LLM response is not valid JSON: {exc!s} "
            "(the response may be truncated — consider raising "
            "smart_mode.llm_max_tokens)"
        ) from exc
    if not isinstance(parsed, dict):
        raise SmartModeError("LLM response JSON is not an object")
    # Coerce values to strings (some models emit numbers / nulls).
    result: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(k, str):
            continue
        if v is None:
            continue
        result[k] = str(v)
    return result


def validate_proposal(
    mapping: dict[str, str],
    raw_fields: list[str],
    canonical_fields: list[str],
) -> dict[str, str]:
    """Reduce ``mapping`` to the safe, applicable subset.

    Rules (silent drops, not exceptions, so a partial proposal still lands):
      * key not in raw_fields → drop
      * value == ``__skip__`` → drop (operator chose not to map)
      * value not in canonical_fields → drop
    """
    raw_set = set(raw_fields)
    canon_set = set(canonical_fields)
    cleaned: dict[str, str] = {}
    dropped_keys = 0
    dropped_values = 0
    skipped = 0
    for raw_field, canonical in mapping.items():
        if raw_field not in raw_set:
            dropped_keys += 1
            continue
        if canonical == _SKIP_TOKEN:
            skipped += 1
            continue
        if canonical not in canon_set:
            dropped_values += 1
            continue
        cleaned[raw_field] = canonical
    if dropped_keys or dropped_values or skipped:
        logger.info(
            "smart-mode validation: kept=%d dropped_unknown_keys=%d "
            "dropped_unknown_canonicals=%d explicit_skip=%d",
            len(cleaned), dropped_keys, dropped_values, skipped,
        )
    return cleaned


# ── Scoring (prompts-021E-4) ────────────────────────────────────────────────


def raw_field_population(samples: list[dict[str, Any]]) -> dict[str, int]:
    """Count how many sample rows contain each raw field with a non-empty value.

    A field is "populated" iff its value is not ``None`` and not the empty
    string. Lists/dicts count as populated when non-empty.

    Used to weight coverage so that a mapping for a field present in 95% of
    rows scores higher than one for a field present in 5%.
    """
    counts: dict[str, int] = {}
    for row in samples:
        for raw_field, value in row.items():
            if value is None:
                continue
            if isinstance(value, (str, list, dict)) and len(value) == 0:
                continue
            counts[raw_field] = counts.get(raw_field, 0) + 1
    return counts


def score_proposal(
    existing_mapping: dict[str, str],
    proposed_mapping: dict[str, str],
    raw_field_pop: dict[str, int],
) -> tuple[float, float, float]:
    """Return ``(coverage_before, coverage_after, coverage_delta)``.

    Coverage = sum(population[f] for f in mapped_raw_fields) /
               sum(population.values())

    ``coverage_after`` is computed on the union of existing + proposed
    mappings (the post-approval state assuming existing-wins overlay-merge —
    consistent with ``_merge_with_existing_wins`` in routes_smart.py).

    Returns (0.0, 0.0, 0.0) when the population is empty so callers can
    treat "no data" the same as "no improvement".
    """
    total = sum(raw_field_pop.values())
    if total == 0:
        return (0.0, 0.0, 0.0)

    def _coverage(mapped_fields: set[str]) -> float:
        return sum(raw_field_pop.get(f, 0) for f in mapped_fields) / total

    before_set = set(existing_mapping.keys())
    # Existing-wins overlay: proposed key only contributes if not already mapped.
    after_set = before_set | {k for k in proposed_mapping.keys() if k not in before_set}
    before = _coverage(before_set)
    after = _coverage(after_set)
    return (before, after, after - before)


def conflicts_with_existing(
    proposed_mapping: dict[str, str],
    existing_mapping: dict[str, str],
) -> list[str]:
    """Return raw_field keys in proposed that already exist in existing.

    Conflict definition (021E-4 user decision): ANY raw_field key already
    present in manual_mappings blocks auto-apply, regardless of whether the
    proposed canonical value matches the existing one. Rationale: never
    silently rewrite an operator's choice.

    Returns the (sorted, stable) list of conflicting raw_field names.
    """
    return sorted(set(proposed_mapping.keys()) & set(existing_mapping.keys()))
