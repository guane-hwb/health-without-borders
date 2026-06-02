"""
No-op Medical Coding Service.

For local development, unit tests, and environments without LLM access.
Returns deterministic fallback values without any network calls.

Uses R69 (appropiate fallback) instead of Z00.0/Z84.8 (false diagnoses).
"""

import logging
from typing import List, Optional

from app.schemas.patient import DiagnosisItem
from app.services.llm.base import (
    FALLBACK_ICD10_CODE,
    FALLBACK_ICD10_DESCRIPTION,
    MedicalCodingService,
)

logger = logging.getLogger(__name__)


class NoOpMedicalCodingService(MedicalCodingService):
    """An LLM service that returns fallback codes without calling any LLM."""

    def extract_diagnoses(
        self,
        history: Optional[str],
        physical: Optional[str],
        systems: Optional[str],
        plan: Optional[str],
    ) -> List[DiagnosisItem]:
        logger.info("NoOp LLM backend active — returning fallback diagnosis (R69).")
        return [
            DiagnosisItem(
                icd10Code=FALLBACK_ICD10_CODE,
                description=f"{FALLBACK_ICD10_DESCRIPTION} (LLM deshabilitado)",
            )
        ]

    def code_family_history_item(self, condition_description: str) -> dict:
        logger.info("NoOp LLM backend active — returning fallback code (R69).")
        return {
            "icd10Code": FALLBACK_ICD10_CODE,
            "icd11Code": None,
            "description": f"{condition_description} — {FALLBACK_ICD10_DESCRIPTION} (LLM deshabilitado)",
        }

    def code_chronic_condition(self, chronic_description: str) -> dict:
        logger.info("NoOp LLM backend active — returning fallback code (R69).")
        return {
            "icd10Code": FALLBACK_ICD10_CODE,
            "icd11Code": None,
            "description": f"{chronic_description} — {FALLBACK_ICD10_DESCRIPTION} (LLM deshabilitado)",
        }
