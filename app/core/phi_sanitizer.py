"""
Protected Health Information (PHI) and Personally Identifiable Information (PII)
sanitization utilities for log output.

All modules that log patient-related data must use these functions to prevent
sensitive information from appearing in plaintext in log streams, files, or
monitoring systems (CloudWatch, Cloud Logging, etc.).

Usage:
    from app.core.phi_sanitizer import mask_id, mask_name, safe_patient_ref

    logger.info("Processing patient %s", safe_patient_ref(patient_id))
    logger.warning("Name mismatch for %s", mask_name("Juan Pérez"))
"""

import re
from typing import Optional


def mask_id(value: Optional[str]) -> str:
    """
    Mask an identifier (patient ID, document number, device UID, etc.)
    preserving only the first 3 and last 3 characters for traceability.

    Examples:
        mask_id("TEST-UNIT-001")  → "TES***001"
        mask_id("VZ-9876543")    → "VZ-***543"
        mask_id("AB")            → "***"
        mask_id(None)            → "unknown"
    """
    if not value:
        return "unknown"
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def mask_name(value: Optional[str]) -> str:
    """
    Mask a person's name, keeping only the first character of each word.

    Examples:
        mask_name("Juan Pérez")     → "J*** P***"
        mask_name("María")          → "M***"
        mask_name(None)             → "***"
    """
    if not value or not value.strip():
        return "***"
    parts = value.strip().split()
    return " ".join(f"{p[0]}***" if len(p) > 1 else p[0] for p in parts)


def safe_patient_ref(patient_id: Optional[str]) -> str:
    """
    Produce a safe log-friendly reference for a patient identifier.
    Alias for mask_id — exists for readability at call sites.
    """
    return mask_id(patient_id)


def safe_doc_ref(doc_type: Optional[str], doc_number: Optional[str]) -> str:
    """
    Produce a safe log-friendly reference for a document type + number pair.

    Examples:
        safe_doc_ref("CC", "1098765432")  → "CC:109***432"
        safe_doc_ref("PT", "VZ-123")      → "PT:***"
    """
    dtype = doc_type or "?"
    return f"{dtype}:{mask_id(doc_number)}"


def sanitize_error_body(body: Optional[str], max_length: int = 500) -> str:
    """
    Sanitize an HTTP error response body before logging. Strips patterns
    that commonly contain PHI (FHIR resource fragments, patient names,
    document numbers) and truncates to a safe length.

    This is intentionally aggressive — it's better to lose debugging detail
    in the logs than to leak clinical data.
    """
    if not body:
        return "<empty>"

    sanitized = body[:max_length]

    # Strip values that might contain names or identifiers inside FHIR
    # resource fragments.  Covers both scalar strings and JSON arrays:
    #   "family": "Pérez Rodríguez"      → "family": "[REDACTED]"
    #   "given": ["Juan Carlos"]          → "given": "[REDACTED]"
    sanitized = re.sub(
        r'"(family|given|text|display|value|name)":\s*"[^"]{4,}"',
        r'"\1": "[REDACTED]"',
        sanitized,
    )
    sanitized = re.sub(
        r'"(family|given|text|display|value|name)":\s*\[[^\]]{4,}\]',
        r'"\1": "[REDACTED]"',
        sanitized,
    )

    if len(body) > max_length:
        sanitized += f"... [truncated, {len(body)} chars total]"

    return sanitized