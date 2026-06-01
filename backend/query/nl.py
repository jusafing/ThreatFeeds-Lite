"""Natural-language DB query (prompts-064).

Pure, I/O-light building blocks that turn an operator's natural-language
question into a *constrained structured filter* and execute it against the
local DB via the existing parameterized query layer.

Design (ADR-0023):
  * The LLM NEVER emits SQL. It returns a JSON object restricted to a closed
    set of filter keys (:data:`STRUCTURED_FILTER_KEYS`). Values are validated
    and then passed to :func:`backend.db.manager.query_entries` /
    :func:`backend.normalizer.db.query_normalized`, which bind every value via
    ``?`` placeholders. This preserves the parameterized-value invariant and
    leaves no SQL-injection surface.
  * ``source`` is validated against the known-source set before use, which also
    closes the unsanitized ``_db_path`` path-traversal concern for this surface.
  * ``limit`` is clamped to ``1..MAX_LIMIT``.

The robust JSON extraction (code-fence / Harmony / comment stripping, tolerant
load) is reused from :mod:`backend.normalizer.smart`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.db.manager import query_entries
from backend.normalizer.db import query_normalized
from backend.normalizer.smart import parse_llm_response

logger = logging.getLogger(__name__)

# Closed set of keys the LLM may emit. Anything else is dropped in validation.
#   dataset             — which table to query ("raw" | "normalized")
#   source              — restrict to one feed (validated against known sources)
#   search              — free-text LIKE term across indexed columns
#   limit               — max rows (clamped to 1..MAX_LIMIT)
#   <column filters>    — exact-match equality on a whitelisted column
STRUCTURED_FILTER_KEYS: frozenset[str] = frozenset({
    "dataset", "source", "search", "limit",
    "severity", "indicator_type", "threat_type", "cve_id", "actor", "country",
})

# Column-equality filter keys (everything in the whitelist except the four
# control keys). These map onto real columns of the entries schema and onto
# canonical normalized columns when present.
COLUMN_FILTER_KEYS: frozenset[str] = STRUCTURED_FILTER_KEYS - {
    "dataset", "source", "search", "limit",
}

VALID_DATASETS: frozenset[str] = frozenset({"raw", "normalized"})
DEFAULT_DATASET = "normalized"

MAX_LIMIT = 2000
DEFAULT_LIMIT = 200

# When normalized column filters are present we must fetch a wide page and
# post-filter in Python (query_normalized has no column-filter support), then
# truncate. This caps that wide fetch.
_NORMALIZED_FETCH_CAP = MAX_LIMIT


class NLQueryError(Exception):
    """Raised on an unrecoverable NL-query parse/validation failure."""


@dataclass
class StructuredQuery:
    """A validated, safe-to-execute query distilled from the LLM answer."""
    dataset: str = DEFAULT_DATASET
    source: str | None = None
    search: str | None = None
    limit: int = DEFAULT_LIMIT
    column_filters: dict[str, str] = field(default_factory=dict)

    def as_interpreted_filter(self) -> dict[str, Any]:
        """Render the query as a plain dict for the API response (transparency)."""
        out: dict[str, Any] = {"dataset": self.dataset, "limit": self.limit}
        if self.source:
            out["source"] = self.source
        if self.search:
            out["search"] = self.search
        out.update(self.column_filters)
        return out


# ── Prompt building ─────────────────────────────────────────────────────────


def build_nl_prompt(
    question: str,
    *,
    default_dataset: str,
    known_sources: list[str],
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for the NL→filter translation.

    The model is asked for a single JSON object restricted to the closed key
    set. It must NOT emit SQL, prose, or invent keys/values.
    """
    sources_block = (
        "\n".join(f"  - {s}" for s in known_sources) if known_sources else "(none)"
    )
    column_keys = ", ".join(sorted(COLUMN_FILTER_KEYS))

    system_prompt = (
        "You are a Threat Intelligence query assistant. Translate the user's "
        "natural-language question into a single JSON filter object and nothing "
        "else. You MUST respond with one JSON object only — no prose, no "
        "markdown, no SQL. "
        "Allowed keys (use only these; omit any you cannot fill): "
        '"dataset" (one of "raw" | "normalized"), "source" (one of the listed '
        'feed names), "search" (free-text keywords), "limit" (integer), and the '
        f"exact-match column filters: {column_keys}. "
        "Do NOT invent keys or feed names. Put product names, package names, "
        'and other free-text concepts into "search".'
    )

    user_prompt = (
        f"Question: {question}\n\n"
        f"Default dataset if unspecified: {default_dataset}\n\n"
        f"Known feeds (use exact names for \"source\"; omit \"source\" to search "
        f"all):\n{sources_block}\n\n"
        f"Return a JSON object using only the allowed keys."
    )
    return system_prompt, user_prompt


# ── Parsing & validation ─────────────────────────────────────────────────────


def parse_nl_filter(text: str) -> dict[str, str]:
    """Parse the raw LLM text into a ``{key: value}`` dict.

    Reuses the tolerant extraction from smart-mode (code-fence / Harmony /
    comment stripping, tolerant JSON load). Raises :class:`NLQueryError` on a
    structural failure.
    """
    try:
        return parse_llm_response(text)
    except Exception as exc:  # SmartModeError and anything unexpected
        raise NLQueryError(f"could not parse LLM filter response: {exc!s}") from exc


def validate_nl_filter(
    raw: dict[str, Any],
    *,
    default_dataset: str = DEFAULT_DATASET,
    known_sources: list[str] | None = None,
) -> StructuredQuery:
    """Reduce the raw LLM dict to a safe :class:`StructuredQuery`.

    Silent drops (a partial-but-safe query still runs):
      * unknown keys                              → dropped
      * ``dataset`` not in {raw, normalized}      → default
      * ``source`` not in ``known_sources``       → dropped (query all feeds)
      * ``limit`` out of range / non-int          → clamped to 1..MAX_LIMIT
      * empty/blank string values                 → dropped
    """
    known = set(known_sources or [])
    sq = StructuredQuery(
        dataset=default_dataset if default_dataset in VALID_DATASETS else DEFAULT_DATASET,
        limit=DEFAULT_LIMIT,
    )

    for key, value in raw.items():
        if key not in STRUCTURED_FILTER_KEYS:
            continue
        if key == "dataset":
            ds = str(value).strip().lower()
            if ds in VALID_DATASETS:
                sq.dataset = ds
            continue
        if key == "limit":
            sq.limit = _coerce_limit(value)
            continue
        text = _coerce_text(value)
        if not text:
            continue
        if key == "source":
            if text in known:
                sq.source = text
            else:
                logger.info("NL query: dropping unknown source %r", text)
            continue
        if key == "search":
            sq.search = text
            continue
        # remaining whitelist keys are exact-match column filters
        sq.column_filters[key] = text

    return sq


def _coerce_limit(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if n < 1:
        return 1
    if n > MAX_LIMIT:
        return MAX_LIMIT
    return n


def _coerce_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


# ── Execution ────────────────────────────────────────────────────────────────


async def execute_structured_query(sq: StructuredQuery) -> list[dict[str, Any]]:
    """Run a validated query against the raw or normalized store.

    Raw: column filters map directly onto ``query_entries(filters=...)``.
    Normalized: ``query_normalized`` has no column-filter support, so when
    column filters are present we fetch a wide page (``source``/``search`` in
    SQL) and apply the equality filters in-process before truncating to
    ``limit``. All value binding stays parameterized inside the query layer.
    """
    if sq.dataset == "raw":
        return await query_entries(
            source_name=sq.source,
            limit=sq.limit,
            search=sq.search,
            filters=dict(sq.column_filters) or None,
        )

    # normalized
    if not sq.column_filters:
        return await query_normalized(
            source_name=sq.source, limit=sq.limit, search=sq.search,
        )

    rows = await query_normalized(
        source_name=sq.source, limit=_NORMALIZED_FETCH_CAP, search=sq.search,
    )
    filtered = _post_filter(rows, sq.column_filters)
    return filtered[: sq.limit]


def _post_filter(
    rows: list[dict[str, Any]], column_filters: dict[str, str]
) -> list[dict[str, Any]]:
    """Apply case-insensitive exact-match column filters in-process.

    A filter whose column is absent from EVERY row is ignored (the normalized
    schema may not carry that canonical field) rather than emptying the result.
    """
    available = {k for row in rows for k in row.keys()}
    active = {k: v for k, v in column_filters.items() if k in available}
    if not active:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        if all(
            str(row.get(col, "")).strip().lower() == val.strip().lower()
            for col, val in active.items()
        ):
            out.append(row)
    return out
