"""
Clinical NLP service powered by Google Vertex AI (Gemini).

Provides two medical coding capabilities:
  1. extract_diagnoses()       — From clinical evaluation notes → List[DiagnosisItem]
  2. code_family_history_item() — From condition description → ICD-10/11 codes
"""

import json
import logging
from typing import List, Optional
from google import genai
from google.genai import types

from app.schemas.patient import DiagnosisItem
from app.services.llm.prompts import (
    SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION_FAMILY_HISTORY,
    build_clinical_prompt,
    build_family_history_prompt,
)
from app.services.llm.schemas import DIAGNOSIS_RESPONSE_SCHEMA, FAMILY_HISTORY_RESPONSE_SCHEMA
from app.core.config import settings

logger = logging.getLogger(__name__)


class GenerativeProcessor:
    """
    Clinical NLP service powered by Google Vertex AI.
    Handles Gemini connection for structured extraction tasks.
    """

    def __init__(self, model_name: str) -> None:
        logger.info(f"Initializing GenerativeProcessor with model: {model_name}")
        self.client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location="global",
        )
        self.model_name = model_name

    def _get_safety_settings(
        self, threshold: types.HarmBlockThreshold
    ) -> list[types.SafetySetting]:
        """
        Configures safety filters. In medical environments,
        it is usually necessary to lower the filters to prevent
        legitimate anatomical terms from blocking the response.
        """
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
        """Builds a Gemini generation config with structured JSON output."""
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

    def _call_gemini(self, prompt: str, config: types.GenerateContentConfig) -> str:
        """Calls Gemini and returns the raw text response."""
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

    # ---------------------------------------------------------------
    # 1. DIAGNOSIS EXTRACTION (from clinical evaluation notes)
    # ---------------------------------------------------------------

    def extract_diagnoses(
        self,
        history: Optional[str],
        physical: Optional[str],
        systems: Optional[str],
        plan: Optional[str],
    ) -> List[DiagnosisItem]:
        """
        Processes clinical notes and extracts ICD-10/11 coded diagnoses.

        The LLM analyzes the four clinical evaluation fields and returns
        one or more diagnoses with:
          - icd10Code (WHO standard)
          - icd11Code (WHO standard, null if uncertain)
          - description (in Spanish)

        The diagnosis type (impresión/confirmado) is NOT determined by the LLM;
        it is set by the physician at the encounter level (MedicalHistoryItem.diagnosisType).
        """
        prompt = build_clinical_prompt(history, physical, systems, plan)
        config = self._build_config(SYSTEM_INSTRUCTION, DIAGNOSIS_RESPONSE_SCHEMA)

        try:
            raw = self._call_gemini(prompt, config)
            diagnoses_dicts = json.loads(raw)
            diagnoses = [DiagnosisItem(**d) for d in diagnoses_dicts]

            logger.info(f"Gemini successfully extracted {len(diagnoses)} diagnoses.")
            return diagnoses

        except Exception as e:
            logger.error(f"Error extracting diagnoses with Vertex AI: {str(e)}")
            return [
                DiagnosisItem(
                    icd10Code="Z00.0",
                    description="Examen médico general (Fallo en extracción IA)",
                )
            ]

    # ---------------------------------------------------------------
    # 2. FAMILY HISTORY ICD CODING (from condition description)
    # ---------------------------------------------------------------

    def code_family_history_item(self, condition_description: str) -> dict:
        """
        Maps a single family history condition description to ICD-10/11 codes.

        Input:  "Glaucoma"  (free-text from the patient/frontend)
        Output: {"icd10Code": "H40.9", "icd11Code": "9A61.Z", "description": "Glaucoma, no especificado"}

        Returns a dict with icd10Code, icd11Code (nullable), and description.
        The caller is responsible for merging these into the FamilyHistoryItem.
        """
        prompt = build_family_history_prompt(condition_description)
        config = self._build_config(
            SYSTEM_INSTRUCTION_FAMILY_HISTORY, FAMILY_HISTORY_RESPONSE_SCHEMA
        )

        try:
            raw = self._call_gemini(prompt, config)
            result = json.loads(raw)

            logger.info(
                f"Gemini coded family history condition: "
                f"'{condition_description}' -> {result.get('icd10Code')}"
            )
            return result

        except Exception as e:
            logger.error(
                f"Error coding family history with Vertex AI: {str(e)}"
            )
            # Fallback: return the description as-is with a generic code
            return {
                "icd10Code": "Z84.8",
                "icd11Code": None,
                "description": f"{condition_description} (Fallo en codificación IA)",
            }


# Module-level singleton
medical_llm_processor = GenerativeProcessor(
    model_name=settings.LLM_MODEL_NAME,
)