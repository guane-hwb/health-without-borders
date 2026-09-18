"""
Unit tests for the identity text standardization helpers.

These functions decide whether a clinician typing a name by hand reaches the
right child's record, so each rule is pinned directly instead of only through
the ``/patients/search`` endpoint.
"""

import pytest

from app.core.text_normalizer import (
    normalize_document_number,
    normalize_text,
    strip_accents,
    text_matches,
    tokenize,
)

# ============================================================================
# NORMALIZATION
# ============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pérez", "Perez"),
        ("Muñoz", "Munoz"),
        ("Andrés Güell", "Andres Guell"),
        ("Rodriguez", "Rodriguez"),  # nothing to strip
    ],
)
def test_strip_accents_maps_letters_to_their_base_form(raw, expected):
    assert strip_accents(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Andrés Guerrero", "andres guerrero"),
        ("ANDRES GUERRERO", "andres guerrero"),
        ("  Andrés   GUERRERO ", "andres guerrero"),
        ("María\tPérez\n", "maria perez"),
    ],
)
def test_normalize_text_folds_accents_case_and_spacing(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize("empty", [None, "", "   ", "\t\n"])
def test_normalize_text_returns_empty_string_for_unusable_input(empty):
    assert normalize_text(empty) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VZ-9876543", "vz9876543"),
        ("vz 987.6543", "vz9876543"),
        ("VZ/9876543", "vz9876543"),
        ("VZ_9876543", "vz9876543"),
        ("  9876543  ", "9876543"),
    ],
)
def test_normalize_document_number_drops_optional_separators(raw, expected):
    assert normalize_document_number(raw) == expected


def test_normalize_document_number_handles_missing_value():
    assert normalize_document_number(None) == ""


def test_tokenize_splits_a_name_into_normalized_words():
    assert tokenize("  Andrés   FELIPE ") == ["andres", "felipe"]


def test_tokenize_treats_compound_name_joiners_as_word_boundaries():
    assert tokenize("Jean-Pierre") == ["jean", "pierre"]
    assert tokenize("D'Angelo") == ["d", "angelo"]


def test_tokenize_returns_empty_list_for_missing_value():
    assert tokenize(None) == []


# ============================================================================
# PARTIAL MATCHING
# ============================================================================


def test_match_ignores_accents_and_case():
    """The case this whole module exists for: same person, different typing."""
    assert text_matches("andres guerrero", "Andrés", "Guerrero") is True
    assert text_matches("ANDRÉS GUERRERO", "andres", "guerrero") is True


def test_match_accepts_a_subset_of_the_stored_name():
    """One surname is enough when the patient has two."""
    assert text_matches("Guerrero", "Guerrero", "Duque") is True
    assert text_matches("Duque", "Guerrero", "Duque") is True


def test_match_accepts_both_surnames_in_any_order():
    assert text_matches("Guerrero Duque", "Guerrero", "Duque") is True
    assert text_matches("Duque Guerrero", "Guerrero", "Duque") is True


def test_match_accepts_a_truncated_word():
    assert text_matches("Guerr", "Guerrero", "Duque") is True


def test_match_accepts_either_part_of_a_compound_name():
    """Hyphens and apostrophes are word boundaries, not letters."""
    assert text_matches("Pierre", "Jean-Pierre", None) is True
    assert text_matches("Angelo", "D'Angelo", None) is True


def test_match_rejects_a_word_the_patient_does_not_have():
    assert text_matches("Pedro González", "María", "Pérez") is False


def test_match_rejects_a_different_name():
    assert text_matches("Carlos", "Santiago", None) is False


def test_match_rejects_a_word_the_stored_name_only_contains_mid_token():
    """Matching is anchored at word starts, so "rrero" is not "Guerrero"."""
    assert text_matches("rrero", "Guerrero", "Duque") is False


@pytest.mark.parametrize(
    ("query", "candidates"),
    [
        (None, ("Guerrero",)),
        ("", ("Guerrero",)),
        ("   ", ("Guerrero",)),
        ("Guerrero", (None,)),
        ("Guerrero", (None, None)),
        ("Guerrero", ()),
    ],
)
def test_match_is_never_true_when_either_side_is_empty(query, candidates):
    """An unusable comparison must deny, never wave a lookup through."""
    assert text_matches(query, *candidates) is False
