"""Tests for the population-weighted scoring helpers (021E-4)."""
from __future__ import annotations

from backend.normalizer.smart import (
    conflicts_with_existing,
    raw_field_population,
    score_proposal,
)


# ── raw_field_population ────────────────────────────────────────────────────


def test_population_counts_non_empty_values():
    samples = [
        {"a": "x", "b": "y"},
        {"a": "z", "c": ""},  # empty string does NOT count
        {"a": "q", "b": None},  # None does NOT count
        {"d": []},  # empty list does NOT count
        {"d": [1]},  # non-empty list counts
    ]
    pop = raw_field_population(samples)
    assert pop == {"a": 3, "b": 1, "d": 1}


def test_population_handles_empty_samples():
    assert raw_field_population([]) == {}
    assert raw_field_population([{}, {}]) == {}


# ── score_proposal ──────────────────────────────────────────────────────────


def test_score_proposal_returns_zero_when_population_empty():
    before, after, delta = score_proposal({}, {"a": "indicator"}, {})
    assert (before, after, delta) == (0.0, 0.0, 0.0)


def test_score_proposal_simple_addition():
    # population: a=10, b=5, c=1 → total 16
    pop = {"a": 10, "b": 5, "c": 1}
    # existing maps b only → coverage = 5/16 = 0.3125
    # proposed adds a and c → after = 16/16 = 1.0
    before, after, delta = score_proposal(
        {"b": "x"}, {"a": "y", "c": "z"}, pop,
    )
    assert round(before, 4) == 0.3125
    assert after == 1.0
    assert round(delta, 4) == 0.6875


def test_score_proposal_existing_wins_overlay():
    """Proposed keys already in existing must NOT inflate coverage_after."""
    pop = {"a": 10, "b": 5}
    # Existing already maps both → coverage 1.0
    # Proposed re-maps a (no effect under existing-wins) → delta should be 0
    before, after, delta = score_proposal(
        {"a": "x", "b": "y"}, {"a": "different"}, pop,
    )
    assert before == 1.0
    assert after == 1.0
    assert delta == 0.0


def test_score_proposal_unmapped_proposed_key_does_not_count():
    """A proposed field not in the population (rare but possible) is 0."""
    pop = {"a": 10}
    before, after, delta = score_proposal({}, {"a": "x", "ghost": "y"}, pop)
    # 'ghost' contributes 0; 'a' contributes 10/10 = 1.0
    assert before == 0.0
    assert after == 1.0
    assert delta == 1.0


def test_score_proposal_threshold_just_above_and_below():
    """Threshold boundary cases — auto-apply decision relies on these."""
    pop = {"a": 100, "b": 5}  # total 105
    # Adding b alone: 5/105 ≈ 0.0476 (just below 5% threshold)
    before, after, delta = score_proposal({}, {"b": "x"}, pop)
    assert 0.04 < delta < 0.05

    # Adding a alone: 100/105 ≈ 0.9524 (well above)
    before, after, delta = score_proposal({}, {"a": "x"}, pop)
    assert delta > 0.5


# ── conflicts_with_existing ─────────────────────────────────────────────────


def test_conflicts_returns_intersecting_keys_sorted():
    existing = {"foo": "x", "bar": "y", "baz": "z"}
    proposed = {"bar": "Y", "qux": "w", "foo": "X"}
    assert conflicts_with_existing(proposed, existing) == ["bar", "foo"]


def test_conflicts_empty_when_disjoint():
    assert conflicts_with_existing({"a": "x"}, {"b": "y"}) == []


def test_conflicts_treats_same_value_as_conflict():
    """021E-4 user decision: any key in manual_mappings is a conflict,
    regardless of whether the canonical value matches."""
    assert conflicts_with_existing({"a": "same"}, {"a": "same"}) == ["a"]
