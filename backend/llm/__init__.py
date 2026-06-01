"""LLM provider plumbing (prompts-021D).

Default-off infrastructure for optional outbound LLM calls. Smart-mode
logic (prompt content, schema proposals) lives in prompts-021E and beyond.

Public surface:
    load_llm_config, save_llm_config, redact_config, validate_config,
    merge_write_only_key
    LLMClient, OpenAIClient, AnthropicClient, OllamaClient,
    OpenAICompatibleClient
    get_client, list_provider_names
    LLMError, LLMDisabledError, LLMConfigError,
    LLMTransportError, LLMProviderError

The module imports cleanly when ``enabled=false`` or the config file is
missing — only constructing a client (via ``get_client``) triggers
config validation.
"""
from __future__ import annotations

from backend.llm.config import (
    load_llm_config,
    merge_write_only_key,
    redact_config,
    save_llm_config,
    validate_config,
)
from backend.llm.errors import (
    LLMConfigError,
    LLMDisabledError,
    LLMError,
    LLMProviderError,
    LLMTransportError,
)
from backend.llm.client import (
    AnthropicClient,
    LLMClient,
    OllamaClient,
    OpenAIClient,
    OpenAICompatibleClient,
)
from backend.llm.registry import get_client, list_provider_names

__all__ = [
    "load_llm_config",
    "save_llm_config",
    "redact_config",
    "validate_config",
    "merge_write_only_key",
    "LLMClient",
    "OpenAIClient",
    "AnthropicClient",
    "OllamaClient",
    "OpenAICompatibleClient",
    "get_client",
    "list_provider_names",
    "LLMError",
    "LLMDisabledError",
    "LLMConfigError",
    "LLMTransportError",
    "LLMProviderError",
]
