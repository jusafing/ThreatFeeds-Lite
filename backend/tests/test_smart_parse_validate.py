"""Tests for parse_llm_response + validate_proposal (021E-1)."""
from __future__ import annotations

import pytest

from backend.normalizer.smart import (
    SmartModeError,
    parse_llm_response,
    validate_proposal,
)


# ── parse_llm_response ──────────────────────────────────────────────────────


def test_parse_plain_json_object():
    out = parse_llm_response('{"a": "title"}')
    assert out == {"a": "title"}


def test_parse_strips_code_fence():
    text = '```json\n{"a": "title"}\n```'
    assert parse_llm_response(text) == {"a": "title"}


def test_parse_strips_prefix_prose():
    text = 'Sure, here you go:\n{"a": "title"}'
    assert parse_llm_response(text) == {"a": "title"}


def test_parse_empty_raises():
    with pytest.raises(SmartModeError, match="empty"):
        parse_llm_response("")


def test_parse_non_object_raises():
    with pytest.raises(SmartModeError):
        parse_llm_response("[1, 2, 3]")


def test_parse_invalid_json_raises():
    with pytest.raises(SmartModeError, match="not valid JSON"):
        parse_llm_response('{not json}')


def test_parse_no_braces_raises():
    with pytest.raises(SmartModeError, match="does not contain"):
        parse_llm_response("just prose")


def test_parse_coerces_non_string_values_to_string():
    out = parse_llm_response('{"a": 42, "b": null}')
    assert out == {"a": "42"}  # null dropped, 42 stringified


# ── parse_llm_response: prompts-034 tolerant recovery ───────────────────────
# Chat models emit JSON with defects that strict json.loads rejects even when
# the mapping is well-formed. Each recovery path below maps to a real failure
# observed in production ("all proposals fail").


def test_parse_recovers_unescaped_control_chars():
    # strict=False accepts a literal newline inside a string value
    # ("Invalid control character at line 2 col N" under strict parsing).
    text = '{"a": "ti\ntle"}'
    out = parse_llm_response(text)
    assert out == {"a": "ti\ntle"}


def test_parse_recovers_single_quoted_object():
    # Python-dict-style single quotes ("Expecting property name enclosed in
    # double quotes") recovered via ast.literal_eval.
    text = "{'a': 'title', 'b': 'description'}"
    out = parse_llm_response(text)
    assert out == {"a": "title", "b": "description"}


def test_parse_recovers_trailing_comma():
    # Trailing comma before the closing brace is stripped in the repair pass.
    text = '{"a": "title", "b": "description",}'
    out = parse_llm_response(text)
    assert out == {"a": "title", "b": "description"}


def test_parse_empty_message_mentions_token_budget():
    # Operator-facing diagnostic should hint at the most common root causes.
    with pytest.raises(SmartModeError, match="empty"):
        parse_llm_response("   \n  ")


def test_parse_truncated_json_hints_token_budget():
    # A malformed object (missing comma) surfaces the llm_max_tokens hint.
    with pytest.raises(SmartModeError, match="truncated|llm_max_tokens"):
        parse_llm_response('{"a": "title" "b": "desc"}')


# ── parse_llm_response: prompts-035 reasoning-model output ───────────────────
# gpt-oss:120b (a reasoning model) emits JSON wrapped in markdown fences with
# embedded JS comments, and interleaves OpenAI-"Harmony" channel control tokens.
# Each case below is distilled from a real failing response captured on the
# test server (see docs/agent-notes/prompts-035-diagnosis.md).


def test_parse_strips_js_block_comments():
    # proposal #10: fenced JSON whose body carried /* ... */ comments, which
    # strict json.loads rejects with "Expecting property name ... line 2 col 3".
    text = (
        "```json\n"
        "{\n"
        "  /* ---------- top-level fields ---------- */\n"
        '  "cve.id": "cve_id",\n'
        '  "cve.lastModified": "cve_last_modified"\n'
        "}\n"
        "```"
    )
    assert parse_llm_response(text) == {
        "cve.id": "cve_id",
        "cve.lastModified": "cve_last_modified",
    }


def test_parse_strips_js_line_comments():
    text = '{\n  "a": "title", // the headline\n  "b": "description"\n}'
    assert parse_llm_response(text) == {"a": "title", "b": "description"}


def test_parse_preserves_slashes_inside_string_values():
    # A // or /* inside a string value must NOT be treated as a comment.
    text = '{"a": "https://example.com/path", "b": "x/*y"}'
    assert parse_llm_response(text) == {
        "a": "https://example.com/path",
        "b": "x/*y",
    }


def test_parse_extracts_harmony_final_channel():
    # proposal #11 shape: analysis-channel reasoning noise followed by the
    # final channel carrying the actual JSON. We keep the final channel.
    text = (
        "<|channel|>analysis<|message|>We need to output a JSON object like "
        '{"role": "analysis"} but that draft is wrong.<|end|>'
        '<|channel|>final<|message|>{"a": "title", "b": "description"}'
    )
    assert parse_llm_response(text) == {"a": "title", "b": "description"}


def test_parse_strips_residual_harmony_tokens():
    # Channel tokens sprinkled around an otherwise-valid object are removed.
    text = '<|start|>{"a": "title"}<|return|>'
    assert parse_llm_response(text) == {"a": "title"}


# ── validate_proposal ───────────────────────────────────────────────────────


def test_validate_drops_unknown_raw_keys():
    out = validate_proposal({"unknown": "title"}, ["a"], ["title"])
    assert out == {}


def test_validate_drops_unknown_canonical_values():
    out = validate_proposal({"a": "made_up"}, ["a"], ["title"])
    assert out == {}


def test_validate_drops_skip_token():
    out = validate_proposal({"a": "__skip__"}, ["a"], ["title"])
    assert out == {}


def test_validate_keeps_valid_pairs():
    out = validate_proposal(
        {"a": "title", "b": "__skip__", "c": "indicator"},
        ["a", "b", "c"],
        ["title", "indicator"],
    )
    assert out == {"a": "title", "c": "indicator"}


def test_validate_empty_mapping_returns_empty():
    assert validate_proposal({}, ["a"], ["title"]) == {}
