"""
Text standardization utilities for identity matching.

Patient identities are typed by hand, on mobile keyboards, in border-area
clinics and by staff who may never have seen the original registration. The
same child is therefore registered as "Andrés Guerrero" in one organization and
typed as "andres guerrero" in the next. Comparing those strings literally makes
the record unreachable, which defeats the continuity of care the platform
exists to guarantee.

These helpers reduce free-text identity fields to a canonical form
(accent-free, case-free, single-spaced) and expose a partial matching rule used
by the strict patient lookup (``/patients/search``).

Usage:
    from app.core.text_normalizer import (
        normalize_document_number,
        normalize_text,
        text_matches,
    )

    normalize_text("  Andrés   GUERRERO ")      → "andres guerrero"
    normalize_document_number("vz-987.654")     → "vz987654"
    text_matches("andres guerrero", "Andrés Felipe", "Guerrero Duque") → True
"""

import re
import unicodedata
from typing import Optional

_WHITESPACE_RE = re.compile(r"\s+")

# Characters that join two words inside a single name ("Jean-Pierre", "D'Angelo")
# and are treated as word boundaries when a name is split for matching.
_NAME_WORD_BREAK_RE = re.compile(r"[-'’]")

# Separators that may or may not be typed inside an identity document number
# ("VZ-9876543" / "vz 9876543" / "VZ.9876543" are the same document).
# ``find_patient_strict`` mirrors this exact tuple in SQL, so both sides of the
# comparison are normalized identically — do not diverge them.
DOCUMENT_SEPARATORS = ("-", " ", ".", "/", "_")


def strip_accents(value: str) -> str:
    """
    Remove diacritical marks, mapping each letter to its base form.

    Spanish names carry accents and tildes inconsistently across registrations,
    so "Pérez"/"Perez" and "Muñoz"/"Munoz" must compare equal.

    Examples:
        strip_accents("Pérez")  → "Perez"
        strip_accents("Muñoz")  → "Munoz"
    """
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(value: Optional[str]) -> str:
    """
    Canonical form of a free-text identity field: accent-free, case-free and
    single-spaced.

    Returns "" for None or blank input so callers can treat "absent" and
    "unusable" alike.

    Examples:
        normalize_text("  Andrés   GUERRERO ")  → "andres guerrero"
        normalize_text(None)                    → ""
    """
    if not value:
        return ""
    folded = strip_accents(value.casefold())
    return _WHITESPACE_RE.sub(" ", folded).strip()


def normalize_document_number(value: Optional[str]) -> str:
    """
    Canonical form of an identity document number — normalized text with the
    optional separators in ``DOCUMENT_SEPARATORS`` removed.

    Examples:
        normalize_document_number("VZ-9876543")  → "vz9876543"
        normalize_document_number("vz 987.6543") → "vz9876543"
    """
    normalized = normalize_text(value)
    for separator in DOCUMENT_SEPARATORS:
        normalized = normalized.replace(separator, "")
    return normalized


def tokenize(value: Optional[str]) -> list[str]:
    """
    Split a name into its normalized words.

    Hyphens and apostrophes count as word boundaries, so a compound name is
    reachable by either of its parts.

    Examples:
        tokenize("Andrés Felipe")  → ["andres", "felipe"]
        tokenize("Jean-Pierre")    → ["jean", "pierre"]
        tokenize(None)             → []
    """
    spaced = _NAME_WORD_BREAK_RE.sub(" ", normalize_text(value))
    return [token for token in spaced.split(" ") if token]


def text_matches(query: Optional[str], *candidates: Optional[str]) -> bool:
    """
    Partial, accent- and case-insensitive match of ``query`` against the name
    parts in ``candidates``.

    ``candidates`` are the stored fragments of one logical name (e.g. first and
    second last name), which is why they are compared as a single pool: the
    caller does not know how many parts the person has, nor in which field the
    clinician typed them.

    A match requires every word of the query to be the start of some stored
    word, in any order. So "guerrero", "duque" and "duque guerrero" all match
    ("Guerrero", "Duque"), and a truncated "guerr" matches too.

    Matching is anchored at word starts rather than being a free substring
    search: "rrero" is not a way of typing "Guerrero", and an anchored rule is
    predictable enough to explain to the staff who depend on it. Every query
    word must also be accounted for, so extra words the patient does not have
    ("Pedro González" against "María Pérez") fail. Returns False when either
    side is empty — an unusable comparison is never a match.
    """
    query_tokens = tokenize(query)
    candidate_tokens = [token for candidate in candidates for token in tokenize(candidate)]
    if not query_tokens or not candidate_tokens:
        return False

    return all(
        any(stored.startswith(wanted) for stored in candidate_tokens)
        for wanted in query_tokens
    )
