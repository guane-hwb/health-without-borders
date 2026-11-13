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
    MedicalCodingService,
    fallback_coding,
    fallback_diagnosis,
)

logger = logging.getLogger(__name__)


class NoOpMedicalCodingService(MedicalCodingService):
    """An LLM service that returns fallback codes without calling any LLM."""

    model_name = "noop"

    def extract_diagnoses(
        self,
        history: Optional[str],
        physical: Optional[str],
        systems: Optional[str],
        plan: Optional[str],
    ) -> List[DiagnosisItem]:
        logger.info("NoOp LLM backend active — returning fallback diagnosis (R69).")
        return [fallback_diagnosis()]

    def code_family_history_item(self, condition_description: str) -> dict:
        logger.info("NoOp LLM backend active — returning fallback code (R69).")
        return fallback_coding()

    def code_chronic_condition(self, chronic_description: str) -> dict:
        logger.info("NoOp LLM backend active — returning fallback code (R69).")
        return fallback_coding()
