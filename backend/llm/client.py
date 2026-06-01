"""LLM HTTP clients (prompts-021D).

Stdlib transport via :mod:`urllib.request` wrapped in
``asyncio.to_thread`` at the call site. No new runtime dependencies.

All clients accept an injectable ``transport`` callable for testing;
the default uses :func:`_http_request` which talks to the live network.

Security notes:
    * ``skip_tls_verify=True`` builds an UNVERIFIED SSL context PER
      REQUEST (never global) and emits a WARNING log including the
      provider name. API keys are never logged.
    * Retries: 5xx is retried up to ``max_retries`` with exponential
      backoff; 4xx fails immediately with :class:`LLMProviderError`.
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable

from backend.llm.errors import LLMProviderError, LLMTransportError

logger = logging.getLogger(__name__)


# ── Defensive parse helper (prompts-025) ────────────────────────────────────


def _parse_json_or_raise(
    *,
    provider_name: str,
    body: bytes,
    status: int | None,
    where: str,
) -> Any:
    """Decode *body* as JSON or raise :class:`LLMProviderError`.

    prompts-023 added a defensive parse to ``OpenAIClient.complete`` so a
    200-OK with a non-OpenAI body would surface as a typed error instead
    of an uncaught ``KeyError`` (which the Test runner re-raised as
    HTTP 500). prompts-025 extends the same guarantee to every other
    decode site (every ``list_models`` and the Ollama / Anthropic
    ``complete`` paths). Without it, an upstream returning HTTP 200 with
    an empty body, HTML, or any other non-JSON payload crashes the Test
    route with ``json.JSONDecodeError`` and the operator sees a generic
    500 with no actionable detail.

    *where* is a short label (e.g. ``"list_models"``) appended to the
    error message so the Test Details transcript names the call site.
    """
    body_str = body.decode("utf-8", errors="replace") if body else ""
    try:
        return json.loads(body_str)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LLMProviderError(
            f"provider {provider_name!r} returned non-JSON body on "
            f"{where} ({type(exc).__name__})",
            status=status,
            body=body_str,
        ) from exc


# ── OpenAI-compatible model-list discovery (prompts-029) ────────────────────


def _candidate_model_list_urls(base_url: str) -> list[str]:
    """Ordered, de-duplicated candidate model-catalog URLs for an
    OpenAI-compatible server.

    prompts-029: ``complete`` always posts to ``{base_url}/chat/completions``,
    which works for OpenWebUI (``base_url=…/api``). But the OpenAI-shaped
    model catalog frequently does NOT live at ``{base_url}/models`` for
    such servers — OpenWebUI exposes it under ``…/api/v1/models`` (or the
    host-root ``/v1/models`` / ``/openai/models`` passthrough). OpenAI
    proper and ``…/v1`` compatibles resolve on the first candidate, so the
    common case is a single GET; only a path-split server falls through to
    the alternates.

    *base_url* is assumed already ``rstrip('/')``-ed (the client does this
    in ``__init__``).
    """
    base = base_url.rstrip("/")
    candidates = [
        f"{base}/models",        # OpenAI proper + …/v1 compatibles (common case)
        f"{base}/v1/models",     # non-versioned base (e.g. OpenWebUI …/api)
        f"{base}/openai/models",  # OpenWebUI explicit OpenAI passthrough
    ]
    # Host-root mounts: a server may expose the OpenAI API at the origin
    # root regardless of the chat-completions prefix.
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme and parsed.netloc:
        root = f"{parsed.scheme}://{parsed.netloc}"
        candidates.append(f"{root}/v1/models")
        candidates.append(f"{root}/models")
    # De-duplicate, preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _extract_openai_model_ids(data: Any) -> list[str] | None:
    """Return model ids from an OpenAI-shaped ``{"data": [{"id": …}]}``
    payload, or ``None`` if the shape does not match (so the caller can
    try the next candidate URL instead of treating it as a hard error)."""
    if not isinstance(data, dict):
        return None
    entries = data.get("data")
    if not isinstance(entries, list):
        return None
    return [
        m.get("id", "")
        for m in entries
        if isinstance(m, dict) and m.get("id")
    ]


def _recover_json_object_from_reasoning(text: str) -> str | None:
    """Best-effort recovery of a final JSON answer embedded in a reasoning blob.

    prompts-037 Phase 3: some reasoning-model gateways (notably the Ollama-backed
    gpt-oss gateway behind provider ``cdt2``) place the completed answer in
    ``message.reasoning_content`` and leave ``message.content`` empty. The answer
    object is emitted at the END of the reasoning text, frequently followed by
    trailing prose (e.g. "Thus produce JSON object exactly. Let's output.").

    We scan for all TOP-LEVEL balanced ``{...}`` spans — respecting JSON string
    literals and escapes so braces inside quoted strings (or prose) do not
    unbalance the scan — and return the LAST span that parses as a non-empty
    JSON object. Returns ``None`` when nothing parseable is found, in which case
    the caller raises the usual empty-content diagnostic.
    """
    if not text:
        return None
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append((start, i + 1))
    for s, e in reversed(spans):
        blob = text[s:e]
        try:
            parsed = json.loads(blob, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed:
            return blob
    return None


def _extract_openai_content(
    data: Any, *, provider_name: str, status: int | None, body_str: str,
) -> str:
    """Return ``choices[0].message.content`` from an OpenAI-shaped response.

    prompts-035 (#2.5): the assistant text lives in the standard envelope
    field ``message.content``. When that is empty/missing we DON'T silently
    return ``""`` — instead we raise :class:`LLMProviderError` with a
    deterministic diagnostic built from the other STANDARD envelope fields the
    server already provides:

      * ``choices[0].finish_reason == "length"`` → the output-token budget was
        exhausted (truncation). This is the gpt-oss empty-content failure
        (#7/9/12); the fix is to raise ``smart_mode.llm_max_tokens``.
      * ``message.reasoning_content`` / ``message.reasoning`` (de-facto field
        used by reasoning-model gateways: vLLM, DeepSeek, SGLang, some
        OpenWebUI builds) present but ``content`` empty → the model spent its
        budget thinking and never emitted a final answer.
      * ``finish_reason == "content_filter"`` → blocked upstream.

    A non-empty ``content`` is returned verbatim (contract unchanged).
    """
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMProviderError(
            f"provider {provider_name!r} returned non-OpenAI response shape "
            f"({type(exc).__name__})",
            status=status,
            body=body_str,
        ) from exc

    content = message.get("content") if isinstance(message, dict) else None
    if content:
        return content

    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    reasoning = None
    if isinstance(message, dict):
        reasoning = message.get("reasoning_content") or message.get("reasoning")

    # prompts-037 Phase 3: some reasoning-model gateways (e.g. the Ollama-backed
    # gpt-oss gateway) emit the COMPLETED answer into ``reasoning_content`` and
    # leave ``content`` empty (finish_reason=stop). The answer is a JSON object
    # at the END of the reasoning text. Recover it rather than discarding usable
    # output. The downstream validator re-parses and drops any non-canonical
    # keys, so a false-positive recovery cannot corrupt a mapping.
    if reasoning:
        recovered = _recover_json_object_from_reasoning(reasoning)
        if recovered is not None:
            logger.info(
                "llm.recovered provider=%s: content empty but a JSON answer was "
                "recovered from reasoning_content (%d chars, finish_reason=%s)",
                provider_name, len(recovered), finish_reason,
            )
            return recovered

    if finish_reason == "length":
        detail = (
            "the output-token budget was exhausted (finish_reason=length) — "
            "raise smart_mode.llm_max_tokens"
        )
    elif reasoning:
        suffix = f", finish_reason={finish_reason}" if finish_reason else ""
        detail = (
            f"the model emitted reasoning but no final answer (empty content{suffix})"
            " — raise smart_mode.llm_max_tokens or limit the model's reasoning"
        )
    elif finish_reason == "content_filter":
        detail = "the response was blocked upstream (finish_reason=content_filter)"
    else:
        suffix = f" (finish_reason={finish_reason})" if finish_reason else ""
        detail = (
            f"the model returned no content{suffix} — this often means the "
            "output-token budget was exhausted by reasoning or the request "
            "timed out"
        )
    raise LLMProviderError(
        f"provider {provider_name!r} returned empty content: {detail}",
        status=status,
        body=body_str,
    )


# ── Redaction helpers (prompts-022) ─────────────────────────────────────────

# Header names treated as bearing secrets; matched case-insensitively. The
# whole value is replaced with the literal "***" so log lines and Test
# Details transcripts never expose an API key.
_SENSITIVE_HEADERS = frozenset({"authorization", "x-api-key", "api-key"})

# Cap how much of a request/response body we ever emit at DEBUG or surface
# in the Test Details transcript. 8 KiB is plenty for diagnosing LLM wire
# issues without flooding the log file or the modal.
BODY_LOG_LIMIT = 8192

# prompts-037: separate, larger cap for the raw request/response captured onto
# the smart-mapping proposal row (``llm_request_raw`` / ``llm_response_json``).
# A consolidated mapping response over 60+ raw fields can exceed the 8 KiB log
# cap; 64 KiB keeps the full envelope inspectable while still bounding
# proposals.db growth. Truncation is marked by ``_truncate_body``.
EXCHANGE_CAPTURE_LIMIT = 65536


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced by '***'.

    Match is case-insensitive on the *header name*; values are never
    inspected (we don't try to be clever about JWTs or similar inside
    other headers — only the well-known auth headers are masked).
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() in _SENSITIVE_HEADERS:
            out[k] = "***"
        else:
            out[k] = v
    return out


def _truncate_body(data: bytes | str | None, limit: int = BODY_LOG_LIMIT) -> str:
    """Coerce bytes/str/None to a length-capped str for logging or transcripts.

    Bytes are decoded as UTF-8 with replacement. ``None`` becomes ``""``.
    A body longer than *limit* is suffixed with ``"…[truncated N more]"``
    so it's obvious the value was clipped.
    """
    if data is None:
        return ""
    if isinstance(data, bytes):
        s = data.decode("utf-8", errors="replace")
    else:
        s = str(data)
    if len(s) <= limit:
        return s
    extra = len(s) - limit
    return s[:limit] + f"…[truncated {extra} more]"


def format_request_raw(record: dict[str, Any] | None) -> str:
    """Render a captured ``_send`` exchange record as a raw HTTP request blob.

    prompts-037: persisted to the proposal row's ``llm_request_raw`` and shown
    in the error card. Headers are already redacted by the caller
    (``_redact_headers``), so no API key reaches this string. Shape::

        POST https://host/v1/chat/completions
        Content-Type: application/json
        Authorization: ***

        {"model": …, "messages": […], …}
    """
    if not record:
        return ""
    headers = record.get("headers_redacted") or {}
    header_lines = "\n".join(f"{k}: {v}" for k, v in headers.items())
    method = record.get("method", "") or ""
    url = record.get("url", "") or ""
    body = record.get("request_body", "") or ""
    parts = [f"{method} {url}".strip()]
    if header_lines:
        parts.append(header_lines)
    request_line_block = "\n".join(parts)
    return f"{request_line_block}\n\n{body}".rstrip()


def format_response_json(status: int | None, body: str | None) -> str:
    """Render the full raw HTTP response (status line + verbatim body).

    prompts-037: persisted to the proposal row's ``llm_response_json``. ``body``
    is the WHOLE HTTP response envelope, not just ``choices[0].message.content``.
    """
    status_line = f"HTTP {status}" if status is not None else "HTTP (no response)"
    return f"{status_line}\n\n{body or ''}".rstrip()


# ── Transport ───────────────────────────────────────────────────────────────

Transport = Callable[..., tuple[int, dict[str, str], bytes]]


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    skip_tls_verify: bool,
    max_retries: int,
    provider_name: str = "unknown",
) -> tuple[int, dict[str, str], bytes]:
    """Send an HTTP request via stdlib urllib.

    Returns ``(status, headers, body)``. Retries on 5xx and transport errors
    up to ``max_retries`` with exponential backoff (0.5s, 1s, 2s, …).
    Raises :class:`LLMTransportError` on terminal network failure and
    :class:`LLMProviderError` on terminal 4xx/5xx.
    """
    ctx: ssl.SSLContext | None = None
    if url.startswith("https://"):
        if skip_tls_verify:
            logger.warning(
                "LLM provider=%s: skip_tls_verify=True — TLS certificate "
                "verification DISABLED for this request",
                provider_name,
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx = ssl.create_default_context()

    last_exc: Exception | None = None
    last_status: int | None = None
    last_body: bytes = b""
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = resp.getcode()
                raw_headers = resp.headers
                if hasattr(raw_headers, "items"):
                    resp_headers = dict(raw_headers.items())
                else:
                    resp_headers = dict(raw_headers or [])
                resp_body = resp.read()
                return status, resp_headers, resp_body
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            try:
                last_body = exc.read()
            except Exception:
                last_body = b""
            if 500 <= exc.code < 600 and attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise LLMProviderError(
                f"provider {provider_name!r} returned HTTP {exc.code}",
                status=exc.code,
                body=last_body.decode("utf-8", errors="replace"),
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise LLMTransportError(
                f"provider {provider_name!r} transport failure: {exc!s}"
            ) from exc
    # Defensive — loop exits via return or raise.
    raise LLMTransportError(
        f"provider {provider_name!r} exhausted retries (status={last_status})"
    ) from last_exc


# ── Base class ──────────────────────────────────────────────────────────────


class LLMClient(ABC):
    """Abstract LLM client. Concrete subclasses implement provider wire shape."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        skip_tls_verify: bool = False,
        extra_body: dict[str, Any] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.skip_tls_verify = bool(skip_tls_verify)
        # prompts-035 (#2b): optional, config-driven request-body additions
        # merged into the OpenAI-compatible /chat/completions payload. Used to
        # pass reasoning-model controls (e.g. reasoning_effort,
        # chat_template_kwargs) WITHOUT hardcoding any vendor param. Never
        # overrides core keys (model/messages/stream). Empty/absent by default,
        # so non-reasoning providers are unaffected.
        self.extra_body: dict[str, Any] = dict(extra_body) if extra_body else {}
        self._transport: Transport = transport or _http_request
        # prompts-037: the last HTTP exchange captured by ``_send`` (method,
        # url, redacted headers, request body, status, full response body,
        # duration, error). Read by the smart-mapping runner after ``complete``
        # to persist the raw request + full response JSON on the proposal row.
        # Distinct from ``_tap`` (test_runner) so the prompts-035 ``context=``
        # log field is unaffected. ``None`` until the first call.
        self._last_exchange: dict[str, Any] | None = None

    def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        step: str | None = None,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Send an HTTP request via the configured transport.

        prompts-022: this is the single chokepoint for every LLM HTTP
        call (every concrete client routes through here). It always
        emits an INFO summary line; full request + response bodies are
        emitted at DEBUG, with sensitive headers redacted and bodies
        truncated to ``BODY_LOG_LIMIT``. API keys are NEVER written to
        the log at any level.

        If ``self._tap`` is set (used by ``test_runner``), the same
        per-call record is also forwarded to the tap as a dict so the
        Test Details modal transcript stays in sync with the log file.
        ``step`` is an optional purpose label ('list_models' / 'complete')
        set by each call site. prompts-035 (#4): it is now also emitted on
        the INFO ``llm.request`` / ``llm.response`` lines as ``purpose=`` so
        an operator can correlate provider-side traffic by request type
        (e.g. distinguishing discovery ``list_models`` from ``complete``
        calls). A ``context=`` field — ``test`` when a tap is installed (a
        Test/Discover probe) vs ``direct`` (a smart-mapping job or other
        direct call) — is emitted too, so the two-step Test can be told
        apart from smart-job completions WITHOUT changing any gating.
        """
        purpose = step or "unknown"
        context = "test" if getattr(self, "_tap", None) is not None else "direct"
        redacted = _redact_headers(headers)
        debug_enabled = logger.isEnabledFor(logging.DEBUG)
        request_body_str: str | None = None
        if debug_enabled:
            request_body_str = _truncate_body(body)
            logger.debug(
                "llm.request.body provider=%s method=%s url=%s headers=%s body=%s",
                self.name, method, url, redacted, request_body_str,
            )
        logger.info(
            "llm.request provider=%s purpose=%s context=%s method=%s url=%s",
            self.name, purpose, context, method, url,
        )

        started = time.monotonic()
        status: int | None = None
        resp_headers: dict[str, str] = {}
        resp_body: bytes = b""
        err: Exception | None = None
        effective_timeout = self.timeout_seconds if timeout is None else float(timeout)
        try:
            status, resp_headers, resp_body = self._transport(
                method,
                url,
                headers=headers,
                body=body,
                timeout=effective_timeout,
                skip_tls_verify=self.skip_tls_verify,
                max_retries=self.max_retries,
                provider_name=self.name,
            )
            return status, resp_headers, resp_body
        except Exception as exc:  # noqa: BLE001 — surfaced below + re-raised
            err = exc
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            if err is None:
                logger.info(
                    "llm.response provider=%s purpose=%s status=%d duration_ms=%d bytes=%d",
                    self.name, purpose, status or 0, duration_ms, len(resp_body),
                )
                if debug_enabled:
                    logger.debug(
                        "llm.response.body provider=%s status=%d body=%s",
                        self.name, status or 0, _truncate_body(resp_body),
                    )
            else:
                # Errors carry their own structured info inside the message
                # (LLMProviderError includes status + body; LLMTransportError
                # includes the underlying transport message). Log at WARNING
                # so failed calls stand out without DEBUG noise.
                logger.warning(
                    "llm.response provider=%s purpose=%s duration_ms=%d error=%s: %s",
                    self.name, purpose, duration_ms, type(err).__name__, err,
                )

            # prompts-037: capture the full exchange for proposal persistence,
            # regardless of log level or tap. Request body is truncated from the
            # ORIGINAL bytes (not the 8 KiB debug string) so the 64 KiB cap is
            # honoured. On HTTP 4xx/5xx the transport raised before resp_body was
            # populated here; the response body lives on the exception and the
            # runner sources it from there. Headers are already redacted.
            self._last_exchange = {
                "method": method,
                "url": url,
                "headers_redacted": redacted,
                "request_body": _truncate_body(body, EXCHANGE_CAPTURE_LIMIT),
                "status_code": status,
                "response_body": _truncate_body(resp_body, EXCHANGE_CAPTURE_LIMIT),
                "duration_ms": duration_ms,
                "error": None if err is None else f"{type(err).__name__}: {err}",
            }

            # Tap (test_runner) gets the full record — including bodies —
            # regardless of log level so the Test Details modal can always
            # render the transcript.
            tap = getattr(self, "_tap", None)
            if tap is not None:
                tap({
                    "step": step,
                    "method": method,
                    "url": url,
                    "headers_redacted": redacted,
                    "request_body": request_body_str
                        if request_body_str is not None
                        else _truncate_body(body),
                    "status_code": status,
                    "response_body": _truncate_body(resp_body),
                    "duration_ms": duration_ms,
                    "error": None if err is None else f"{type(err).__name__}: {err}",
                })

    def last_exchange_raw(
        self, error: Exception | None = None
    ) -> tuple[str, str]:
        """Return ``(llm_request_raw, llm_response_json)`` for the last ``_send``.

        prompts-037: consumed by the smart-mapping runner to persist the raw
        request + full HTTP response envelope on the proposal row.

        The response body is normally taken from the captured sink
        (``_last_exchange``). On an :class:`LLMProviderError` carrying a body
        (an HTTP 4xx/5xx, where the transport raised before ``_send`` populated
        ``resp_body``), the status + body are sourced from the exception
        instead. Transport errors leave the response body empty (no HTTP
        response was received) but the request is still returned.
        """
        record = self._last_exchange
        request_raw = format_request_raw(record)
        status = record.get("status_code") if record else None
        body = record.get("response_body") if record else ""
        if isinstance(error, LLMProviderError):
            if error.status is not None:
                status = error.status
            if error.body:
                body = _truncate_body(error.body, EXCHANGE_CAPTURE_LIMIT)
        response_json = format_response_json(status, body)
        return request_raw, response_json

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float | None = None,
        model: str | None = None,
    ) -> str:
        ...

    def list_models(self) -> list[str] | None:
        """Return available model names, or None if the provider has no
        well-known list endpoint. Used for token-free smoke tests."""
        return None


# ── OpenAI ──────────────────────────────────────────────────────────────────


class OpenAIClient(LLMClient):
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float | None = None,
        model: str | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # prompts-023: send "stream": False explicitly. The OpenAI default
        # is non-streaming, but a few compatible servers (and reverse
        # proxies fronting them, e.g. OpenWebUI behind nginx) switch to
        # chunked when the field is absent. Matches what OllamaClient
        # already does for the same reason.
        payload = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        # prompts-035 (#2b): merge optional config-driven reasoning/extra body
        # params. setdefault → core keys above always win; extra_body only adds
        # NEW keys (e.g. reasoning_effort). No-op when extra_body is empty.
        for key, value in self.extra_body.items():
            payload.setdefault(key, value)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            # prompts-023: be explicit about wanting JSON back. Some
            # strict proxies in front of compatible servers return HTML
            # when Accept is missing.
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        status, _, resp = self._send(
            "POST", f"{self.base_url}/chat/completions", headers=headers, body=body,
            timeout=timeout, step="complete",
        )
        body_str = resp.decode("utf-8", errors="replace")
        # prompts-023: defensive parse. Previously a server that returned
        # 200 OK with a non-OpenAI body (e.g. ``{"detail": "..."}`` from
        # a misconfigured OpenWebUI route) would raise an uncaught
        # KeyError here, which surfaced as a 500 from the test runner
        # with no useful operator-facing message. Now we raise the typed
        # LLMProviderError carrying the offending body so the Test
        # transcript shows what the server actually said.
        try:
            data = json.loads(body_str)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"provider {self.name!r} returned non-OpenAI response shape "
                f"({type(exc).__name__})",
                status=status,
                body=body_str,
            ) from exc
        # prompts-035 (#2.5): extract via the standard envelope; empty content
        # raises a deterministic finish_reason/reasoning diagnostic.
        return _extract_openai_content(
            data, provider_name=self.name, status=status, body_str=body_str,
        )

    def list_models(self) -> list[str] | None:
        headers = {
            # prompts-023: explicit Accept (same rationale as complete()).
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        status, _, resp = self._send(
            "GET", f"{self.base_url}/models", headers=headers, body=None, step="list_models",
        )
        # prompts-025: defensive parse — see _parse_json_or_raise. A 200
        # OK with an empty / HTML / otherwise-non-JSON body used to crash
        # the Test route with json.JSONDecodeError → HTTP 500.
        data = _parse_json_or_raise(
            provider_name=self.name, body=resp, status=status, where="list_models",
        )
        try:
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except (AttributeError, TypeError) as exc:
            raise LLMProviderError(
                f"provider {self.name!r} returned non-OpenAI list_models shape "
                f"({type(exc).__name__})",
                status=status,
                body=resp.decode("utf-8", errors="replace"),
            ) from exc


# ── Anthropic ───────────────────────────────────────────────────────────────


class AnthropicClient(LLMClient):
    _ANTHROPIC_VERSION = "2023-06-01"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float | None = None,
        model: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
        }
        status, _, resp = self._send(
            "POST", f"{self.base_url}/v1/messages", headers=headers, body=body,
            timeout=timeout, step="complete",
        )
        # prompts-025: defensive parse (see _parse_json_or_raise).
        data = _parse_json_or_raise(
            provider_name=self.name, body=resp, status=status, where="complete",
        )
        # Anthropic returns content as a list of blocks.
        try:
            blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except (AttributeError, TypeError) as exc:
            raise LLMProviderError(
                f"provider {self.name!r} returned non-Anthropic response shape "
                f"({type(exc).__name__})",
                status=status,
                body=resp.decode("utf-8", errors="replace"),
            ) from exc

    def list_models(self) -> list[str] | None:
        # Anthropic has no stable public list-models endpoint at a fixed
        # shape; smoke tests fall back to complete().
        return None


# ── Ollama ──────────────────────────────────────────────────────────────────


class OllamaClient(LLMClient):
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float | None = None,
        model: str | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        status, _, resp = self._send(
            "POST", f"{self.base_url}/api/chat", headers=headers, body=body,
            timeout=timeout, step="complete",
        )
        # prompts-025: defensive parse.
        data = _parse_json_or_raise(
            provider_name=self.name, body=resp, status=status, where="complete",
        )
        try:
            return data.get("message", {}).get("content", "")
        except (AttributeError, TypeError) as exc:
            raise LLMProviderError(
                f"provider {self.name!r} returned non-Ollama response shape "
                f"({type(exc).__name__})",
                status=status,
                body=resp.decode("utf-8", errors="replace"),
            ) from exc

    def list_models(self) -> list[str] | None:
        status, _, resp = self._send(
            "GET", f"{self.base_url}/api/tags", headers={}, body=None, step="list_models",
        )
        # prompts-025: defensive parse.
        data = _parse_json_or_raise(
            provider_name=self.name, body=resp, status=status, where="list_models",
        )
        try:
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except (AttributeError, TypeError) as exc:
            raise LLMProviderError(
                f"provider {self.name!r} returned non-Ollama list_models shape "
                f"({type(exc).__name__})",
                status=status,
                body=resp.decode("utf-8", errors="replace"),
            ) from exc


# ── OpenAI-compatible (Together, Groq, vLLM, LM Studio, …) ──────────────────


class OpenAICompatibleClient(OpenAIClient):
    """Same wire shape as OpenAI but arbitrary ``base_url``.

    Sends ``Authorization: Bearer`` only when ``api_key`` is non-empty
    so it works with auth-less local servers (vLLM, LM Studio, …).
    """

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float | None = None,
        model: str | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # prompts-023: see OpenAIClient.complete — explicit stream:false +
        # Accept + defensive parse.
        payload = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        # prompts-035 (#2b): merge optional config-driven reasoning/extra body
        # params. setdefault → core keys above always win; extra_body only adds
        # NEW keys (e.g. reasoning_effort). No-op when extra_body is empty.
        for key, value in self.extra_body.items():
            payload.setdefault(key, value)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        status, _, resp = self._send(
            "POST", f"{self.base_url}/chat/completions", headers=headers, body=body,
            timeout=timeout, step="complete",
        )
        body_str = resp.decode("utf-8", errors="replace")
        try:
            data = json.loads(body_str)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"provider {self.name!r} returned non-OpenAI response shape "
                f"({type(exc).__name__})",
                status=status,
                body=body_str,
            ) from exc
        # prompts-035 (#2.5): standard-envelope extraction with a deterministic
        # finish_reason/reasoning diagnostic when content is empty.
        return _extract_openai_content(
            data, provider_name=self.name, status=status, body_str=body_str,
        )

    def list_models(self) -> list[str] | None:
        """Discover the model catalog, probing candidate URLs in order.

        prompts-029: ``complete`` posts to ``{base_url}/chat/completions``
        (works for OpenWebUI ``base_url=…/api``), but the OpenAI-shaped
        catalog often is NOT at ``{base_url}/models`` for such servers.
        We try :func:`_candidate_model_list_urls` in order and return the
        first OpenAI-shaped JSON payload. The common case (OpenAI proper /
        ``…/v1`` compatibles) succeeds on the first candidate — a single
        GET.

        Failure semantics, preserving the prompts-025 intent:
          * A 200 OK with a non-JSON body or a wrong (non-OpenAI) JSON
            shape from a candidate is recorded and the next candidate is
            tried — it does NOT short-circuit, because a path-split server
            answers the wrong route with HTML/native JSON.
          * If a candidate returns a non-empty OpenAI-shaped list, return
            it immediately.
          * If a candidate returns an *empty* OpenAI-shaped list, remember
            it as a valid-but-empty result and keep probing other
            candidates (a different route may publish models); fall back to
            the empty list only if nothing else is found.
          * Transport / HTTP errors (endpoint absent, 4xx/5xx) are
            recorded and the next candidate is tried.
          * If every candidate failed AND at least one returned a 2xx with
            an unusable body (misconfiguration), raise a single
            :class:`LLMProviderError` naming every attempted URL. If every
            candidate failed only with transport/HTTP errors (no server
            ever answered a usable 2xx), return ``None`` so the caller
            falls back to the free-text model path.
        """
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        candidates = _candidate_model_list_urls(self.base_url)
        attempted: list[str] = []
        saw_2xx_bad_body = False
        last_status: int | None = None
        last_body: str = ""
        empty_ok: list[str] | None = None

        for url in candidates:
            attempted.append(url)
            try:
                status, _, resp = self._send(
                    "GET", url, headers=headers, body=None, step="list_models",
                )
            except (LLMProviderError, LLMTransportError):
                # Endpoint absent / transport error — try the next candidate.
                continue
            last_status = status
            body_str = resp.decode("utf-8", errors="replace") if resp else ""
            last_body = body_str
            try:
                data = json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                # 2xx with a non-JSON body (e.g. HTML from a path-split
                # route). Misconfiguration signal — remember and continue.
                saw_2xx_bad_body = True
                continue
            ids = _extract_openai_model_ids(data)
            if ids is None:
                # 2xx JSON but not OpenAI-shaped (e.g. OpenWebUI native).
                saw_2xx_bad_body = True
                continue
            if ids:
                return ids
            # Valid OpenAI shape but empty — keep the first empty result and
            # keep probing in case another route actually lists models.
            if empty_ok is None:
                empty_ok = ids

        if empty_ok is not None:
            return empty_ok

        if saw_2xx_bad_body:
            raise LLMProviderError(
                f"provider {self.name!r} returned no OpenAI-shaped model "
                f"catalog on list_models; tried {len(attempted)} candidate "
                f"URL(s)",
                status=last_status,
                body=last_body,
                attempted_urls=attempted,
            )
        # Every candidate failed with a transport/HTTP error — the server
        # genuinely exposes no catalog. Caller falls back to free-text.
        return None

