import json
import logging
from typing import List, Optional
from google import genai
from google.genai import types

from app.schemas.patient import DiagnosisItem
from app.services.llm.prompts import SYSTEM_INSTRUCTION, build_clinical_prompt
from app.services.llm.schemas import RESPONSE_SCHEMA
from app.core.config import settings

logger = logging.getLogger(__name__)

class GenerativeProcessor:
    """
    Clinical NLP service powered by Google Vertex AI.
    Handles Gemini connection for structured extraction tasks.
    """
    def __init__(
        self,
        model_name: str,
        system_instruction: str,
    ) -> None:
        logger.info(f"Initializing GenerativeProcessor with model: {model_name}")
        self.client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location='global',
        )
        self.model_name = model_name
        self.system_instruction = system_instruction
        
    def _get_safety_settings(self, threshold: types.HarmBlockThreshold) -> list[types.SafetySetting]:
        """
        Configures safety filters. In medical environments, 
        it is usually necessary to turn off/lower the filters to prevent 
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

    def extract_diagnoses(
        self, 
        history: Optional[str], 
        physical: Optional[str], 
        systems: Optional[str], 
        plan: Optional[str]
    ) -> List[DiagnosisItem]:
        """
        Processes clinical notes and extracts ICD-10/11 codes in structured JSON format.
        """
        prompt = build_clinical_prompt(history, physical, systems, plan)
        
        config = types.GenerateContentConfig(
            temperature=0.0,
            top_p=0.95,
            max_output_tokens = 65535,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            system_instruction=[types.Part.from_text(text=self.system_instruction)],
            safety_settings=self._get_safety_settings(types.HarmBlockThreshold.BLOCK_NONE),
            thinking_config=types.ThinkingConfig(thinking_budget=-1,),
        )
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                config=config
            )
            
            # Pydantic parses the JSON validating that it complies with the structure
            diagnoses_dicts = json.loads(response.text)
            diagnoses = [DiagnosisItem(**d) for d in diagnoses_dicts]
            
            logger.info(f"Gemini successfully extracted {len(diagnoses)} diagnoses.")
            return diagnoses
            
        except Exception as e:
            logger.error(f"Critical error extracting diagnoses with Vertex AI: {str(e)}")
            # Safe emergency fallback so the sync process doesn't break
            return [
                DiagnosisItem(
                    icd10Code="Z00.0", 
                    description="Examen médico general (Fallo en extracción IA)"
                )
            ]


medical_llm_processor = GenerativeProcessor(
    model_name=settings.LLM_MODEL_NAME,
    system_instruction=SYSTEM_INSTRUCTION
)