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
