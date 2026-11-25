"""
Claude-powered Medical Coding Service (Anthropic API).

Same contract as the Gemini backend:
  - Structured output: each task answers a JSON schema (app/services/llm/schemas.py).
  - Post-LLM validation: every ICD-10/11 code is checked against the Vulcano
    IHCE catalog before it is accepted.
  - Honest fallback: an error, a timeout, a refusal or a cut answer gives R69,
    never an invented diagnosis.
  - PHI sanitization: clinical text is NOT logged.
"""

import json
import logging
from typing import List, Optional, Tuple

import anthropic
from anthropic.types.beta import BetaTextBlock

from app.core.config import settings
from app.schemas.patient import DiagnosisItem
from app.services.llm.prompts import (
    SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION_CHRONIC_CONDITION,
    SYSTEM_INSTRUCTION_FAMILY_HISTORY,
    build_chronic_condition_prompt,
    build_clinical_prompt,
    build_family_history_prompt,
)
from app.services.llm.schemas import (
    CHRONIC_CONDITION_OUTPUT_SCHEMA,
    DIAGNOSIS_OUTPUT_SCHEMA,
    FAMILY_HISTORY_OUTPUT_SCHEMA,
)
from app.services.llm.validation import (
    make_fallback_diagnosis,
    make_fallback_dict,
    validate_and_fix_diagnosis,
    validate_and_fix_dict,
)

logger = logging.getLogger(__name__)

# The model the Claude backend uses when LLM_MODEL_NAME is not set.
DEFAULT_MODEL = "claude-sonnet-5-5"

# Adaptive thinking is on by default and counts against max_tokens; the JSON
# answers themselves are short. A cut answer (stop_reason "max_tokens") falls back to R69.
MAX_TOKENS = 16000

# When a safety classifier declines a request, the API runs it again on a
# fallback model chosen by the refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class UnusableAnswer(Exception):
    """The model answered, but not with a JSON text the service can use."""


class ClaudeMedicalCodingService:
    """
    Anthropic Claude implementation of MedicalCodingService.
    All ICD codes are validated against the Vulcano IHCE terminology catalog.
    """

    def __init__(self, model_name: str, api_key: str) -> None:
        self.client = anthropic.Anthropic(
            api_key=api_key,
            # A timeout surfaces as an exception, which every caller already
            # turns into the R69 fallback.
            timeout=settings.LLM_TIMEOUT_SECONDS,
            # A retry would take one call past LLM_TIMEOUT_SECONDS and past the
            # /sync budget. A failed call gives R69 now, and a later /sync codes
            # the background items again.
            max_retries=0,
        )
        self.model_name = model_name
        logger.info("Claude Medical Coding Service initialized: %s", model_name)

    def _call(self, system: str, prompt: str, schema: dict) -> Tuple[dict, str]:
        """
        One coding request. Returns the parsed JSON answer and the model that
        gave it, which is a fallback model when the configured one declined.
        """
        response = self.client.beta.messages.create(
            model=self.model_name,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": settings.LLM_EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        # "refusal" (every model in the chain declined) and "max_tokens" (cut
        # answer) carry no JSON that matches the schema.
        if response.stop_reason != "end_turn":
            category = response.stop_details.category if response.stop_details else None
            raise UnusableAnswer(f"stop_reason={response.stop_reason} category={category}")
        text = next((b.text for b in response.content if isinstance(b, BetaTextBlock)), None)
        if text is None:
            raise UnusableAnswer("no text block")
        if response.model != self.model_name:
            logger.warning("Claude coding answered by fallback model %s", response.model)
        return json.loads(text), response.model

    @staticmethod
    def _failure(error: Exception) -> str:
        """What the log says about a failed call: never the clinical text."""
        if isinstance(error, UnusableAnswer):
            return f"{type(error).__name__} ({error})"
        return type(error).__name__

    # -----------------------------------------------------------------
    # extract_diagnoses
    # -----------------------------------------------------------------

    def extract_diagnoses(
        self,
        history: Optional[str],
        physical: Optional[str],
        systems: Optional[str],
        plan: Optional[str],
    ) -> List[DiagnosisItem]:
        prompt = build_clinical_prompt(history, physical, systems, plan)

        try:
            result, answered_by = self._call(SYSTEM_INSTRUCTION, prompt, DIAGNOSIS_OUTPUT_SCHEMA)
            diagnoses = [DiagnosisItem(**d) for d in result["diagnoses"]]
            logger.info("Claude extracted %d diagnoses.", len(diagnoses))

            # Post-LLM validation against Vulcano catalog
            validated = [validate_and_fix_diagnosis(d) for d in diagnoses]
            # Provenance names the model that answered, a fallback one included.
            for diagnosis in validated:
                diagnosis.model = answered_by
            return validated

        except Exception as e:
            logger.error("Error extracting diagnoses with Claude: %s", self._failure(e))
            return [make_fallback_diagnosis("Claude call failed")]

    # -----------------------------------------------------------------
    # code_family_history_item
    # -----------------------------------------------------------------

    def code_family_history_item(self, condition_description: str) -> dict:
        prompt = build_family_history_prompt(condition_description)

        try:
            result, _ = self._call(
                SYSTEM_INSTRUCTION_FAMILY_HISTORY, prompt, FAMILY_HISTORY_OUTPUT_SCHEMA
            )
            logger.info("Claude coded family history — icd10=%s", result.get("icd10Code"))
            return validate_and_fix_dict(result)

        except Exception as e:
            logger.error("Error coding family history with Claude: %s", self._failure(e))
            return make_fallback_dict("Claude call failed")

    # -----------------------------------------------------------------
    # code_chronic_condition
    # -----------------------------------------------------------------

    def code_chronic_condition(self, chronic_description: str) -> dict:
        prompt = build_chronic_condition_prompt(chronic_description)

        try:
            result, _ = self._call(
                SYSTEM_INSTRUCTION_CHRONIC_CONDITION, prompt, CHRONIC_CONDITION_OUTPUT_SCHEMA
            )
            logger.info("Claude coded chronic condition — icd10=%s", result.get("icd10Code"))
            return validate_and_fix_dict(result)

        except Exception as e:
            logger.error("Error coding chronic condition with Claude: %s", self._failure(e))
            return make_fallback_dict("Claude call failed")
