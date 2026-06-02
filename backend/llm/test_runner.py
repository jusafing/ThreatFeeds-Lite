"""LLM provider 'Test Connection' orchestrator (prompts-022 step 3).

Public entry point: :func:`run_provider_test`. Runs both a ``list_models``
call AND a simple ``complete("ping")`` round-trip against the supplied
client, captures the full request/response transcript via the client's
``_tap`` hook (see :func:`backend.llm.client.LLMClient._send`), and
returns a structured result the HTTP layer can serialize verbatim.

Design notes:

* **Always run both steps** (user-locked decision in plan). Each step's
  outcome is reported independently in ``details[]``. The aggregated
  ``status`` is ``'ok'`` only when every *required* step succeeded.
* Anthropic has no public ``/models`` endpoint, so its ``list_models()``
  returns ``None``. In that case the runner records a synthetic
  ``list_models`` step with ``error='no public list endpoint (anthropic)'``
  and ``status_code=None`` and does NOT count it against the aggregate
  status — the dropdown gating moves to the frontend (free-text input).
* The tap-injection trick: ``LLMClient._send`` forwards a per-call dict
  to ``self._tap`` if set. Each step here installs a fresh tap closure
  that stamps the correct ``step`` label and appends to a local list,
  so the transcript order matches call order even though the concrete
  clients don't pass ``step=`` themselves.

The returned shape is the canonical wire payload for both
``POST /api/llm/providers/{name}/test`` (persisted provider) and
``POST /api/llm/providers/test`` (ephemeral) — see Step 4.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.llm.client import LLMClient
from backend.llm.errors import (
    LLMEmptyContentError,
    LLMProviderError,
    LLMTransportError,
)

logger = logging.getLogger(__name__)


# Truncate the 'sample' echoed back to the UI; the full response_body
# stays in the per-step transcript record.
_SAMPLE_LIMIT = 80
# Tiny prompt for the 'complete' smoke step. max_tokens=8 keeps cost
# minimal across providers; some refuse max_tokens=1 (e.g. Anthropic).
_PING_PROMPT = "ping"
_PING_SYSTEM = "You are a connectivity probe. Reply with the single word 'pong'."
# issue_local_02: the connectivity probe must mirror real proposal calls more
# closely. max_tokens=8 was so tight that reasoning models / OpenAI-compatible
# proxies (e.g. OpenWebUI) burned the whole budget on hidden reasoning and
# answered HTTP 200 with empty content (finish_reason=length) — a FALSE probe
# failure even though the provider was reachable and authenticated. A moderate
# budget removes most false-empties cheaply; the LLMEmptyContentError soft-pass
# below covers whatever remains.
_PING_MAX_TOKENS = 256


def _new_step_record(step: str) -> dict[str, Any]:
    """Skeleton transcript entry; populated by the tap or by error handlers."""
    return {
        "step": step,
        "method": None,
        "url": None,
        "headers_redacted": {},
        "request_body": None,
        "status_code": None,
        "response_body": "",
        "duration_ms": 0,
        "error": None,
        # prompts-061: non-blocking advisory. A step may succeed (error=None,
        # green badge) yet still carry a warning the operator should see — e.g.
        # an empty model catalog when the completion probe itself passed.
        "warning": None,
    }


def _install_tap(client: LLMClient, step: str, sink: list[dict[str, Any]]):
    """Install a tap that stamps every captured record with *step* and
    appends it to *sink*. Returns the previous tap so callers can restore."""
    previous = getattr(client, "_tap", None)

    def tap(record: dict[str, Any]) -> None:
        record = dict(record)
        record["step"] = step
        sink.append(record)

    client._tap = tap  # type: ignore[attr-defined]
    return previous


def _is_anthropic(client: LLMClient) -> bool:
    # Avoid a hard import cycle; identify by class name. AnthropicClient
    # lives in client.py so a name comparison is sufficient and stable.
    return type(client).__name__ == "AnthropicClient"


def run_discover_only(client: LLMClient) -> dict[str, Any]:
    """Run ONLY the list_models step (prompts-027 stage 2).

    Returns ``{"status", "details", "models"}`` — the canonical
    :func:`run_provider_test` payload minus the ``sample`` key. Used by
    the new ``POST /providers/discover`` routes when the operator wants
    to discover models *before* picking one to probe (Add Provider
    wizard stage 2, persisted-card "Discover Models" button).

    Same never-raises contract as :func:`run_provider_test`.
    """
    details: list[dict[str, Any]] = []
    models, overall_ok = _run_list_models_step(client, details)
    return {
        "status": "ok" if overall_ok else "error",
        "details": details,
        "models": models,
    }


def _run_list_models_step(
    client: LLMClient,
    details: list[dict[str, Any]],
    *,
    empty_is_error: bool = True,
) -> tuple[list[str] | None, bool]:
    """Execute the list_models step, append to *details*, return (models, ok).

    prompts-061: ``empty_is_error`` controls how an EMPTY (or ``None``) model
    catalog is treated when the call itself did not raise:

      * ``True`` (default, used by :func:`run_discover_only`) — an empty
        catalog is a hard failure: ``overall_ok`` flips to ``False`` and a red
        ``list_models`` record is recorded. Discovery's whole purpose is to
        populate a dropdown, so 0 models is a genuine failure there.
      * ``False`` (used by :func:`run_provider_test`) — an empty catalog is a
        NON-BLOCKING warning. ``overall_ok`` is left untouched and the record
        carries a ``warning`` (not an ``error``), so the step stays green and
        the completion probe remains the real gate. This fixes the regression
        where a reachable provider that publishes no ``/models`` list flipped
        the aggregate to "Probe failed" even though ``complete()`` succeeded.

    A genuine transport/provider error (an exception) is ALWAYS a hard failure
    regardless of ``empty_is_error``.
    """
    models: list[str] | None = None
    overall_ok = True
    if _is_anthropic(client):
        # Synthetic record: no wire call is made. The frontend handles
        # the Anthropic-specific free-text model input.
        rec = _new_step_record("list_models")
        rec["error"] = "no public list endpoint (anthropic)"
        details.append(rec)
        models = None
        logger.info(
            "llm.test step=list_models provider=%s skipped=anthropic",
            client.name,
        )
        # Anthropic is special: the missing endpoint does NOT fail the
        # aggregate for the full run_provider_test path (the frontend
        # supplies the model id by hand). For run_discover_only we
        # also keep overall_ok=True so the wizard can advance to the
        # free-text model input branch.
        return models, overall_ok

    step_sink: list[dict[str, Any]] = []
    prev_tap = _install_tap(client, "list_models", step_sink)
    try:
        models = client.list_models()
        # prompts-023: treat empty list the same as None — see the
        # original comment in run_provider_test for the rationale.
        if not models:
            empty_msg = (
                "client.list_models() returned None"
                if models is None
                else "list_models returned 0 models — "
                     "server reachable but no models published"
            )
            if empty_is_error:
                overall_ok = False
                if not step_sink:
                    rec = _new_step_record("list_models")
                    rec["error"] = empty_msg
                    step_sink.append(rec)
                elif models == [] and step_sink[-1].get("error") is None:
                    rec = _new_step_record("list_models")
                    rec["error"] = empty_msg
                    step_sink.append(rec)
            else:
                # prompts-061: non-blocking. Annotate the captured record (or
                # add a synthetic one) with a warning but leave overall_ok and
                # the step's error untouched so the badge stays green.
                if not step_sink:
                    rec = _new_step_record("list_models")
                    rec["warning"] = empty_msg
                    step_sink.append(rec)
                elif (
                    step_sink[-1].get("error") is None
                    and not step_sink[-1].get("warning")
                ):
                    step_sink[-1]["warning"] = empty_msg
        details.extend(step_sink)
    except (LLMProviderError, LLMTransportError) as exc:
        overall_ok = False
        if not step_sink:
            rec = _new_step_record("list_models")
            rec["error"] = f"{type(exc).__name__}: {exc}"
            step_sink.append(rec)
        details.extend(step_sink)
        logger.info(
            "llm.test step=list_models provider=%s error=%s",
            client.name, exc,
        )
    except Exception as exc:  # noqa: BLE001
        # prompts-025: belt-and-braces safety net.
        overall_ok = False
        if not step_sink:
            rec = _new_step_record("list_models")
            rec["error"] = f"unexpected {type(exc).__name__}: {exc}"
            step_sink.append(rec)
        else:
            step_sink[-1]["error"] = (
                f"unexpected {type(exc).__name__}: {exc}"
            )
        details.extend(step_sink)
        logger.warning(
            "llm.test step=list_models provider=%s unexpected_error=%s: %s",
            client.name, type(exc).__name__, exc,
        )
    finally:
        client._tap = prev_tap  # type: ignore[attr-defined]
    return models, overall_ok


def run_provider_test(client: LLMClient) -> dict[str, Any]:
    """Run the two-step provider test and return the canonical result dict.

    See module docstring for the shape. Never raises — every failure is
    captured in the transcript, and the aggregate ``status`` reflects
    whether the test as a whole passed.
    """
    details: list[dict[str, Any]] = []
    sample: str | None = None

    # ── Step A: list_models ────────────────────────────────────────────
    # prompts-027: extracted into a helper so POST /providers/discover
    # can reuse the step verbatim without also running the probe.
    # prompts-061: empty_is_error=False — for the full provider test an empty
    # model catalog is a non-blocking warning, not a failure. The completion
    # probe (Step B) is the real gate, so a reachable provider that publishes
    # no /models list still passes when complete() succeeds.
    models, overall_ok = _run_list_models_step(
        client, details, empty_is_error=False
    )

    # NOTE: legacy compatibility — the helper above preserves anthropic
    # synthetic-record semantics (overall_ok=True even though a
    # synthetic error string is recorded) so the full run_provider_test
    # still passes when 'complete' succeeds against an anthropic
    # client. See _run_list_models_step for the early-return branch.

    # ── Step B: complete (smoke prompt) ────────────────────────────────
    # prompts-024: skip when the client has no model. The wizard's first
    # Test runs before the operator has picked a model, so ``model`` is
    # an empty string at construction time. Every spec-compliant LLM
    # rejects POST /chat/completions with model="" as HTTP 400, which
    # used to fail the aggregate and hide the model picker behind a red
    # badge. The skip is reported in the transcript as a third
    # documented outcome (alongside "ok" and "error") so the operator
    # can re-run Test once they've picked a model from the list_models
    # result and get the canonical green/red verdict before Add LLM
    # enables.
    if not getattr(client, "model", ""):
        rec = _new_step_record("complete")
        rec["error"] = (
            "skipped: no model selected — pick a model from the "
            "list_models result and re-run Test to verify completion"
        )
        details.append(rec)
        logger.info(
            "llm.test step=complete provider=%s skipped=no-model",
            client.name,
        )
    else:
        step_sink_b: list[dict[str, Any]] = []
        prev_tap_b = _install_tap(client, "complete", step_sink_b)
        try:
            out = client.complete(
                _PING_PROMPT, system=_PING_SYSTEM, max_tokens=_PING_MAX_TOKENS
            )
            sample = (out or "")[:_SAMPLE_LIMIT]
            details.extend(step_sink_b)
            logger.info(
                "llm.test step=complete provider=%s ok=true sample_len=%d",
                client.name, len(sample),
            )
        except LLMEmptyContentError as exc:
            # issue_local_02 soft pass: the provider answered HTTP 200 but the
            # model produced no final text within the probe budget (reasoning
            # model / proxy spent the budget thinking, finish_reason=length).
            # That proves reachability AND authentication, so it must NOT fail
            # the connectivity test. Record a non-blocking warning on the step
            # and leave overall_ok untouched. Genuine provider/transport errors
            # (caught below) still fail the aggregate.
            if not step_sink_b:
                rec = _new_step_record("complete")
                step_sink_b.append(rec)
            warn = (
                "reachable and authenticated, but the model returned no answer "
                "within the probe's token budget"
            )
            if getattr(exc, "finish_reason", None):
                warn += f" (finish_reason={exc.finish_reason})"
            warn += " — this does not affect connectivity"
            step_sink_b[-1]["warning"] = warn
            sample = warn[:_SAMPLE_LIMIT]
            details.extend(step_sink_b)
            logger.info(
                "llm.test step=complete provider=%s ok=true soft_pass=empty_content "
                "finish_reason=%s",
                client.name, getattr(exc, "finish_reason", None),
            )
        except (LLMProviderError, LLMTransportError) as exc:
            overall_ok = False
            if not step_sink_b:
                rec = _new_step_record("complete")
                rec["error"] = f"{type(exc).__name__}: {exc}"
                step_sink_b.append(rec)
            details.extend(step_sink_b)
            logger.info(
                "llm.test step=complete provider=%s error=%s",
                client.name, exc,
            )
        except Exception as exc:  # noqa: BLE001
            # prompts-025: same belt-and-braces guarantee as the
            # list_models step — never let an unexpected exception
            # cross the route boundary as HTTP 500.
            overall_ok = False
            if not step_sink_b:
                rec = _new_step_record("complete")
                rec["error"] = f"unexpected {type(exc).__name__}: {exc}"
                step_sink_b.append(rec)
            else:
                step_sink_b[-1]["error"] = (
                    f"unexpected {type(exc).__name__}: {exc}"
                )
            details.extend(step_sink_b)
            logger.warning(
                "llm.test step=complete provider=%s unexpected_error=%s: %s",
                client.name, type(exc).__name__, exc,
            )
        finally:
            client._tap = prev_tap_b  # type: ignore[attr-defined]

    return {
        "status": "ok" if overall_ok else "error",
        "details": details,
        "models": models,
        "sample": sample,
    }
