"""
Post-LLM validation shared by every medical coding backend.

Every ICD-10/11 code an LLM returns is checked against the Vulcano IHCE
terminology catalog before it is accepted; an invalid ICD-10 code becomes the
honest R69 fallback, an invalid ICD-11 code is dropped. Clinical text is never
logged, only the reason for a fallback.
"""

import logging

from app.schemas.patient import DiagnosisItem
from app.services.llm.base import (
    FALLBACK_ICD10_CODE,
    fallback_coding,
    fallback_diagnosis,
)
from app.services.terminology import terminology

logger = logging.getLogger(__name__)


def make_fallback_diagnosis(reason: str) -> DiagnosisItem:
    """
    Honest fallback diagnosis (R69). The reason goes to the log, not into the
    clinical description a professional will read.
    """
    logger.warning("Diagnosis extraction fell back to %s: %s", FALLBACK_ICD10_CODE, reason)
    return fallback_diagnosis()


def make_fallback_dict(reason: str) -> dict:
    """Honest fallback (R69) for family history / chronic conditions."""
    logger.warning("Background coding fell back to %s: %s", FALLBACK_ICD10_CODE, reason)
    return fallback_coding()


def validate_and_fix_diagnosis(diag: DiagnosisItem) -> DiagnosisItem:
    """
    Validate ICD-10/11 codes against the catalog.
    ICD-10 is normalised ('J45.9' → 'J459') and a category with subcategories
    becomes its unspecified one ('J45' → 'J459'), the same rule the prompt
    gives; if it is still invalid, the whole diagnosis becomes R69. If ICD-11
    is invalid, strip it (ICD-11 is optional).
    """
    icd10 = terminology.terminal_icd10(diag.icd10Code)
    if icd10 is None:
        return make_fallback_diagnosis("LLM ICD-10 code not in the catalog")
    diag.icd10Code = icd10

    # ICD-11 is optional — if invalid, just strip it
    if diag.icd11Code and not terminology.validate_icd11(diag.icd11Code):
        logger.warning(
            "ICD-11 code '%s' not found in Vulcano catalog — stripping",
            diag.icd11Code,
        )
        diag.icd11Code = None

    return diag


def validate_and_fix_dict(result: dict) -> dict:
    """Validate ICD codes in a family history / chronic condition dict."""
    icd10 = terminology.terminal_icd10(result.get("icd10Code"))
    if icd10 is None:
        return make_fallback_dict("LLM ICD-10 code not in the catalog")
    result["icd10Code"] = icd10

    icd11 = result.get("icd11Code")
    if icd11 and not terminology.validate_icd11(icd11):
        logger.warning("ICD-11 code '%s' not in catalog — stripping", icd11)
        result["icd11Code"] = None

    return result
