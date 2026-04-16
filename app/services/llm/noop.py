"""
No-op Medical Coding Service.

For local development, unit tests, and environments without LLM access.
Returns deterministic fallback values without any network calls.
"""

import logging
from typing import List, Optional

from app.schemas.patient import DiagnosisItem
from app.services.llm.base import MedicalCodingService

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
        logger.info("NoOp LLM backend active — returning fallback diagnosis.")
        return [
            DiagnosisItem(
                icd10Code="Z00.0",
                description="Examen médico general (LLM deshabilitado)",
            )
        ]

    def code_family_history_item(self, condition_description: str) -> dict:
        logger.info(
            f"NoOp LLM backend active — returning fallback code for '{condition_description}'."
        )
        return {
            "icd10Code": "Z84.8",
            "icd11Code": None,
            "description": f"{condition_description} (LLM deshabilitado)",
        }