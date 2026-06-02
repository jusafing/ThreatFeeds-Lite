"""LLM error hierarchy (prompts-021D).

LLMConfigError inherits ValueError so existing FastAPI 400-translation
patterns work without explicit catching.
"""
from __future__ import annotations


class LLMError(Exception):
    """Base for all LLM-related errors."""


class LLMDisabledError(LLMError):
    """Raised when an LLM operation is attempted with ``enabled=false``."""


class LLMConfigError(LLMError, ValueError):
    """Raised for malformed or invalid LLM configuration."""


class LLMTransportError(LLMError):
    """Network-level failure (timeout, DNS, refused, TLS, …)."""


class LLMProviderError(LLMError):
    """Provider returned an HTTP error (4xx/5xx after retries)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
        attempted_urls: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        # prompts-029: when model discovery probes several candidate
        # catalog URLs (OpenAI-compatible servers whose chat-completions
        # and model-list endpoints live under different prefixes — e.g.
        # OpenWebUI), the aggregate failure records every URL tried so the
        # operator sees exactly which endpoints were attempted.
        self.attempted_urls = attempted_urls


class LLMEmptyContentError(LLMProviderError):
    """Provider was reachable (HTTP 200) but returned empty completion content.

    issue_local_02: distinct from a genuine provider error. Reasoning models
    and OpenAI-compatible proxies (e.g. OpenWebUI) can answer HTTP 200 with an
    empty ``message.content`` because the output-token budget was spent on
    reasoning (``finish_reason=length``) or no final answer was emitted. The
    real smart-mapping path treats this as a failure (it needs the answer), but
    the connectivity probe should treat it as a *soft pass* — the provider is
    reachable and authenticated. Subclasses :class:`LLMProviderError` so every
    existing ``except LLMProviderError`` caller behaves exactly as before;
    only callers that explicitly want the soft-pass distinction catch this
    narrower type. Carries ``finish_reason`` for diagnostics.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        super().__init__(message, status=status, body=body)
        self.finish_reason = finish_reason
