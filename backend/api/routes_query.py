"""Query routes — natural-language DB query (prompts-064).

Exposes ``POST /api/query/nl``: an operator's natural-language question is
translated by the configured LLM into a *constrained structured filter*
(never raw SQL), validated against a closed whitelist, and executed against the
local raw/normalized store via the existing parameterized query layer.

Reader-gated: admins and the ``normal`` (viewer) role may call it; the
push-only ``sender`` role may not (see backend/main.py role allowlist).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db.manager import _get_all_sources
from backend.llm.errors import (
    LLMConfigError,
    LLMDisabledError,
    LLMProviderError,
    LLMTransportError,
)
from backend.llm.registry import get_client
from backend.normalizer.config import load_normalizer_config
from backend.query.nl import (
    DEFAULT_DATASET,
    VALID_DATASETS,
    NLQueryError,
    build_nl_prompt,
    execute_structured_query,
    parse_nl_filter,
    validate_nl_filter,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/query", tags=["query"])

# Output-token budget for the small JSON filter the model returns. Far smaller
# than the smart-mode mapping budget — the answer is a handful of keys.
_NL_MAX_TOKENS = 512


class NLQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Natural-language question")
    dataset: Optional[str] = Field(
        None, description='Override dataset ("raw" | "normalized")'
    )
    source: Optional[str] = Field(None, description="Restrict to a single feed")
    limit: Optional[int] = Field(None, ge=1, le=2000)


class NLQueryResponse(BaseModel):
    question: str
    dataset: str
    interpreted_filter: dict[str, Any]
    count: int
    results: list[dict[str, Any]]


def _resolve_provider_and_timeout() -> tuple[str | None, int]:
    """Provider name (None → configured default) and the LLM call timeout."""
    cfg = load_normalizer_config()
    smart = cfg.get("smart_mode") or {}
    provider = smart.get("provider") or None
    timeout = int(smart.get("llm_timeout_seconds") or 600)
    return provider, timeout


@router.post("/nl", response_model=NLQueryResponse)
async def nl_query(body: NLQueryRequest) -> NLQueryResponse:
    """Translate a natural-language question into a DB query and return rows."""
    default_dataset = DEFAULT_DATASET
    if body.dataset:
        ds = body.dataset.strip().lower()
        if ds not in VALID_DATASETS:
            raise HTTPException(
                status_code=422,
                detail=f"dataset must be one of {sorted(VALID_DATASETS)}",
            )
        default_dataset = ds

    provider_name, timeout = _resolve_provider_and_timeout()
    try:
        client = get_client(provider_name)
    except LLMDisabledError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"LLM is disabled — configure a provider to use NL query ({exc!s})",
        ) from exc
    except LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=f"LLM config error: {exc!s}") from exc

    known_sources = _get_all_sources()
    system_prompt, user_prompt = build_nl_prompt(
        body.question,
        default_dataset=default_dataset,
        known_sources=known_sources,
    )

    try:
        response_text = await asyncio.to_thread(
            client.complete,
            user_prompt,
            system=system_prompt,
            max_tokens=_NL_MAX_TOKENS,
            temperature=0.0,
            timeout=timeout,
        )
    except (LLMTransportError, LLMProviderError) as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc!s}") from exc

    try:
        raw_filter = parse_nl_filter(response_text)
    except NLQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    sq = validate_nl_filter(
        raw_filter,
        default_dataset=default_dataset,
        known_sources=known_sources,
    )
    # Explicit client overrides win over the LLM's choice.
    if body.dataset is not None:
        sq.dataset = default_dataset  # already validated against VALID_DATASETS
    if body.source is not None:
        sq.source = body.source if body.source in known_sources else None
    if body.limit is not None:
        sq.limit = body.limit

    results = await execute_structured_query(sq)
    return NLQueryResponse(
        question=body.question,
        dataset=sq.dataset,
        interpreted_filter=sq.as_interpreted_filter(),
        count=len(results),
        results=results,
    )
