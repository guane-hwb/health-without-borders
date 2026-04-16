"""
Google Vertex AI (Gemini) implementation of MedicalCodingService.

This is the ONLY file in the LLM package that imports google-genai.
All other code depends on the abstract MedicalCodingService Protocol.
"""

import json
import logging
from typing import List, Optional

from google import genai
from google.genai import types

from app.schemas.patient import DiagnosisItem
from app.services.llm.base import MedicalCodingService
from app.services.llm.prompts import (
    SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION_FAMILY_HISTORY,
    build_clinical_prompt,
    build_family_history_prompt,
)
from app.services.llm.schemas import (
    DIAGNOSIS_RESPONSE_SCHEMA,
    FAMILY_HISTORY_RESPONSE_SCHEMA,
)

logger = logging.getLogger(__name__)


class GeminiMedicalCodingService(MedicalCodingService):
    """
    Clinical NLP service powered by Google Vertex AI (Gemini).
    
    Handles two medical coding tasks:
      1. extract_diagnoses() — from clinical evaluation notes → List[DiagnosisItem]
      2. code_family_history_item() — from condition description → ICD-10/11 codes
    """

    def __init__(self, model_name: str, project_id: Optional[str]) -> None:
        logger.info(f"Initializing GeminiMedicalCodingService with model: {model_name}")
        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location="global",
        )
        self.model_name = model_name

    def _get_safety_settings(
        self, threshold: types.HarmBlockThreshold
    ) -> list[types.SafetySetting]:
        """
        Lowered safety filters for medical context — prevents legitimate
        anatomical/clinical terms from being flagged as inappropriate.
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
            logger.info(f"Gemini extracted {len(diagnoses)} diagnoses.")
            return diagnoses

        except Exception as e:
            logger.error(f"Error extracting diagnoses with Gemini: {str(e)}")
            return [
                DiagnosisItem(
                    icd10Code="Z00.0",
                    description="Examen médico general (Fallo en extracción IA)",
                )
            ]

    def code_family_history_item(self, condition_description: str) -> dict:
        prompt = build_family_history_prompt(condition_description)
        config = self._build_config(
            SYSTEM_INSTRUCTION_FAMILY_HISTORY, FAMILY_HISTORY_RESPONSE_SCHEMA
        )

        try:
            raw = self._call(prompt, config)
            result = json.loads(raw)
            logger.info(
                f"Gemini coded family history: "
                f"'{condition_description}' -> {result.get('icd10Code')}"
            )
            return result

        except Exception as e:
            logger.error(f"Error coding family history with Gemini: {str(e)}")
            return {
                "icd10Code": "Z84.8",
                "icd11Code": None,
                "description": f"{condition_description} (Fallo en codificación IA)",
            }