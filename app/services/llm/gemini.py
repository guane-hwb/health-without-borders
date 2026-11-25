"""
Gemini-powered Medical Coding Service (Google Vertex AI).
Key features:
  - Post-LLM validation: every ICD-10/11 code is validated against
    the Vulcano IHCE terminology catalog before being accepted.
  - Honest fallback: on LLM error, returns R69 ("unknown morbidity")
    instead of fabricating a false diagnosis like Z00.0.
  - PHI sanitization: clinical text is NOT logged.
"""

import json
import logging
from typing import List, Optional

from google import genai
from google.genai import types

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
    CHRONIC_CONDITION_RESPONSE_SCHEMA,
    DIAGNOSIS_RESPONSE_SCHEMA,
    FAMILY_HISTORY_RESPONSE_SCHEMA,
)
from app.services.llm.validation import (
    make_fallback_diagnosis,
    make_fallback_dict,
    validate_and_fix_diagnosis,
    validate_and_fix_dict,
)

logger = logging.getLogger(__name__)

# The model the Gemini backend uses when LLM_MODEL_NAME is not set.
DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiMedicalCodingService:
    """
    Google Vertex AI Gemini implementation of MedicalCodingService.
    All ICD codes are validated against the Vulcano IHCE terminology catalog.
    """

    def __init__(self, model_name: str, project_id: Optional[str] = None) -> None:
        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location=settings.LLM_LOCATION,
            # A timeout surfaces as an exception, which every caller already
            # turns into the R69 fallback.
            http_options=types.HttpOptions(timeout=int(settings.LLM_TIMEOUT_SECONDS * 1000)),
        )
        self.model_name = model_name
        logger.info(f"Gemini Medical Coding Service initialized: {model_name}")

    @staticmethod
    def _get_safety_settings(threshold):
        return [
            types.SafetySetting(category=category, threshold=threshold)
            for category in [
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            ]
        ]

    def _build_config(
        self, system_instruction: str, response_schema: dict
    ) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            temperature=0.0,
            top_p=0.95,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=response_schema,
            system_instruction=[types.Part.from_text(text=system_instruction)],
            safety_settings=self._get_safety_settings(
                types.HarmBlockThreshold.BLOCK_NONE
            ),
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
        )

    def _call(self, prompt: str, config: types.GenerateContentConfig) -> str:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[
                types.Content(
                    role="user", parts=[types.Part.from_text(text=prompt)]
                )
            ],
            config=config,
        )
        # No text (a blocked answer) fails json.loads like a malformed one.
        return response.text or ""

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
        config = self._build_config(SYSTEM_INSTRUCTION, DIAGNOSIS_RESPONSE_SCHEMA)

        try:
            raw = self._call(prompt, config)
            diagnoses_dicts = json.loads(raw)
            diagnoses = [DiagnosisItem(**d) for d in diagnoses_dicts]
            logger.info("Gemini extracted %d diagnoses.", len(diagnoses))

            # Post-LLM validation against Vulcano catalog
            validated = [validate_and_fix_diagnosis(d) for d in diagnoses]
            return validated

        except Exception as e:
            logger.error("Error extracting diagnoses with Gemini: %s", type(e).__name__)
            return [make_fallback_diagnosis("Gemini call failed")]

    # -----------------------------------------------------------------
    # code_family_history_item
    # -----------------------------------------------------------------

    def code_family_history_item(self, condition_description: str) -> dict:
        prompt = build_family_history_prompt(condition_description)
        config = self._build_config(
            SYSTEM_INSTRUCTION_FAMILY_HISTORY, FAMILY_HISTORY_RESPONSE_SCHEMA
        )

        try:
            raw = self._call(prompt, config)
            result = json.loads(raw)
            logger.info(
                "Gemini coded family history — icd10=%s",
                result.get("icd10Code"),
            )
            return validate_and_fix_dict(result)

        except Exception as e:
            logger.error("Error coding family history with Gemini: %s", type(e).__name__)
            return make_fallback_dict("Gemini call failed")

    # -----------------------------------------------------------------
    # code_chronic_condition
    # -----------------------------------------------------------------

    def code_chronic_condition(self, chronic_description: str) -> dict:
        prompt = build_chronic_condition_prompt(chronic_description)
        config = self._build_config(
            SYSTEM_INSTRUCTION_CHRONIC_CONDITION, CHRONIC_CONDITION_RESPONSE_SCHEMA
        )

        try:
            raw = self._call(prompt, config)
            result = json.loads(raw)
            logger.info(
                "Gemini coded chronic condition — icd10=%s",
                result.get("icd10Code"),
            )
            return validate_and_fix_dict(result)

        except Exception as e:
            logger.error("Error coding chronic condition with Gemini: %s", type(e).__name__)
            return make_fallback_dict("Gemini call failed")
