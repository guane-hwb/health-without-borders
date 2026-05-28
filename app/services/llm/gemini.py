"""
Gemini-powered Medical Coding Service (Google Vertex AI).
Key features:
  - Post-LLM validation: every ICD-10/11 code is validated against
    the Vulcano IHCE terminology catalog before being accepted.
  - Honest fallback: on LLM error, returns R69 ("unknown morbidity")
    instead of fabricating a false diagnosis like Z00.0.
  - PHI sanitization: clinical text is NOT logged (C3 remediation).
"""

import json
import logging
from typing import List, Optional

from google import genai
from google.genai import types

from app.schemas.patient import DiagnosisItem
from app.services.llm.base import FALLBACK_ICD10_CODE, FALLBACK_ICD10_DESCRIPTION
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
from app.services.terminology import terminology

logger = logging.getLogger(__name__)


def _make_fallback_diagnosis(reason: str) -> DiagnosisItem:
    """Create an honest fallback diagnosis (R69) with the failure reason."""
    return DiagnosisItem(
        icd10Code=FALLBACK_ICD10_CODE,
        description=f"{FALLBACK_ICD10_DESCRIPTION} ({reason})",
    )


def _make_fallback_dict(description: str, reason: str) -> dict:
    """Create an honest fallback dict (R69) for family history / chronic conditions."""
    return {
        "icd10Code": FALLBACK_ICD10_CODE,
        "icd11Code": None,
        "description": f"{description} — {FALLBACK_ICD10_DESCRIPTION} ({reason})",
    }


def _validate_and_fix_diagnosis(diag: DiagnosisItem) -> DiagnosisItem:
    """
    Validate ICD-10/11 codes against the Vulcano catalog.
    If ICD-10 is invalid, replace the whole diagnosis with R69.
    If ICD-11 is invalid, strip it (ICD-11 is optional).
    """
    if not terminology.validate_icd10(diag.icd10Code):
        logger.warning(
            "ICD-10 code '%s' not found in Vulcano catalog — replacing with %s",
            diag.icd10Code, FALLBACK_ICD10_CODE,
        )
        return _make_fallback_diagnosis(
            f"Código LLM '{diag.icd10Code}' no válido en catálogo MinSalud"
        )

    # ICD-11 is optional — if invalid, just strip it
    if diag.icd11Code and not terminology.validate_icd11(diag.icd11Code):
        logger.warning(
            "ICD-11 code '%s' not found in Vulcano catalog — stripping",
            diag.icd11Code,
        )
        diag.icd11Code = None

    return diag


def _validate_and_fix_dict(result: dict) -> dict:
    """Validate ICD codes in a family history / chronic condition dict."""
    icd10 = result.get("icd10Code", "")
    if not terminology.validate_icd10(icd10):
        logger.warning(
            "ICD-10 code '%s' not found in Vulcano catalog — replacing with %s",
            icd10, FALLBACK_ICD10_CODE,
        )
        result["icd10Code"] = FALLBACK_ICD10_CODE
        result["description"] = (
            f"{result.get('description', '')} "
            f"(Código LLM '{icd10}' no válido en catálogo MinSalud)"
        )

    icd11 = result.get("icd11Code")
    if icd11 and not terminology.validate_icd11(icd11):
        logger.warning("ICD-11 code '%s' not in catalog — stripping", icd11)
        result["icd11Code"] = None

    return result


class GeminiMedicalCodingService:
    """
    Google Vertex AI Gemini implementation of MedicalCodingService.
    All ICD codes are validated against the Vulcano IHCE terminology catalog.
    """

    def __init__(self, model_name: str, project_id: Optional[str] = None) -> None:
        self.client = genai.Client(
            vertexai=True, project=project_id, location="global"
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
        return response.text

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
            validated = [_validate_and_fix_diagnosis(d) for d in diagnoses]
            return validated

        except Exception as e:
            logger.error("Error extracting diagnoses with Gemini: %s", type(e).__name__)
            return [_make_fallback_diagnosis("Fallo en extracción IA")]

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
            return _validate_and_fix_dict(result)

        except Exception as e:
            logger.error("Error coding family history with Gemini: %s", type(e).__name__)
            return _make_fallback_dict(condition_description, "Fallo en codificación IA")

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
            return _validate_and_fix_dict(result)

        except Exception as e:
            logger.error("Error coding chronic condition with Gemini: %s", type(e).__name__)
            return _make_fallback_dict(chronic_description, "Fallo en codificación IA")
