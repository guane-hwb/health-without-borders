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
    Servicio NLP clínico impulsado por Google Vertex AI.
    Maneja la conexión a Gemini para tareas de extracción estructurada.
    """
    def __init__(
        self,
        model_name: str,
        system_instruction: str,
    ) -> None:
        logger.info(f"Inicializando GenerativeProcessor con modelo: {model_name}")
        self.client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location='global',
        )
        self.model_name = model_name
        self.system_instruction = system_instruction
        
    def _get_safety_settings(self, threshold: types.HarmBlockThreshold) -> list[types.SafetySetting]:
        """
        Configura los filtros de seguridad. En entornos médicos, 
        suele ser necesario apagar/bajar los filtros para evitar que 
        términos anatómicos legítimos bloqueen la respuesta.
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
        Procesa las notas clínicas y extrae los códigos CIE-10/11 en formato JSON estructurado.
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
            
            # Pydantic parsea el JSON validando que cumpla la estructura
            diagnoses_dicts = json.loads(response.text)
            diagnoses = [DiagnosisItem(**d) for d in diagnoses_dicts]
            
            logger.info(f"Gemini extrajo {len(diagnoses)} diagnósticos exitosamente.")
            return diagnoses
            
        except Exception as e:
            logger.error(f"Error crítico extrayendo diagnósticos con Vertex AI: {str(e)}")
            # Fallback de emergencia seguro para que el proceso de sincronización no se rompa
            return [
                DiagnosisItem(
                    icd10Code="Z00.0", 
                    description="Examen médico general (Fallo en extracción IA)", 
                    is_ai_generated=True
                )
            ]


medical_llm_processor = GenerativeProcessor(
    model_name=settings.LLM_MODEL_NAME,
    system_instruction=SYSTEM_INSTRUCTION
)