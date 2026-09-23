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

import json
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


# JSON keys whose string values may carry names, identifiers, dates or free
# clinical text in FHIR payloads and OperationOutcome diagnostics.
_PHI_KEYS = (
    "family|given|text|display|value|name|birthDate|diagnostics|line|city|"
    "district|postalCode|div|expression|location"
)


def _summarize_operation_outcome(body: str) -> Optional[str]:
    """Reduce a FHIR OperationOutcome to its issue severities and codes."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("resourceType") != "OperationOutcome":
        return None
    issues = parsed.get("issue") or []
    summary = ", ".join(
        f"{issue.get('severity', '?')}/{issue.get('code', '?')}"
        for issue in issues
        if isinstance(issue, dict)
    )
    return f"OperationOutcome issues=[{summary}]"


def sanitize_error_body(body: Optional[str], max_length: int = 500) -> str:
    """
    Sanitize an HTTP error response body before logging. Strips patterns
    that commonly contain PHI (FHIR resource fragments, patient names,
    document numbers) and truncates to a safe length.

    A FHIR ``OperationOutcome`` is reduced to its issue severities and codes:
    its ``diagnostics`` text routinely quotes the offending values. Anything
    else is redacted over the WHOLE body before truncating, so a value cut in
    half by the truncation cannot slip past the patterns.

    This is intentionally aggressive — it's better to lose debugging detail
    in the logs than to leak clinical data.
    """
    if not body:
        return "<empty>"

    outcome = _summarize_operation_outcome(body)
    if outcome is not None:
        return outcome[:max_length]

    # Strip values that might contain names or identifiers inside FHIR
    # resource fragments.  Covers both scalar strings and JSON arrays:
    #   "family": "Pérez Rodríguez"      → "family": "[REDACTED]"
    #   "given": ["Juan Carlos"]          → "given": "[REDACTED]"
    sanitized = re.sub(
        rf'"({_PHI_KEYS})":\s*"[^"]{{4,}}"',
        r'"\1": "[REDACTED]"',
        body,
    )
    sanitized = re.sub(
        rf'"({_PHI_KEYS})":\s*\[[^\]]{{4,}}\]',
        r'"\1": "[REDACTED]"',
        sanitized,
    )

    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + f"... [truncated, {len(body)} chars total]"

    return sanitized
