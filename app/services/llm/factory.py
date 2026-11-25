"""
Medical Coding Service Factory.

Returns the correct MedicalCodingService implementation based on settings.
To add a new backend (OpenAI, local Llama, etc.), import it here and add a
branch for its identifier.
"""

import logging

from app.core.config import settings
from app.services.llm import claude, gemini
from app.services.llm.base import MedicalCodingService
from app.services.llm.claude import ClaudeMedicalCodingService
from app.services.llm.gemini import GeminiMedicalCodingService
from app.services.llm.noop import NoOpMedicalCodingService

logger = logging.getLogger(__name__)


def get_llm_service() -> MedicalCodingService:
    """
    Instantiate and return the configured medical coding service.
    
    The backend is selected via the LLM_BACKEND environment variable:
      - "anthropic" → Claude through the Anthropic API (default)
      - "gemini"    → Google Vertex AI / Gemini
      - "noop"      → No-op fallback (no LLM calls)

    LLM_MODEL_NAME names the model; when it is not set, each backend uses its
    own default. Future contributors can add OpenAI or local model support by
    creating a new module in this package and adding a branch below.
    """
    backend_name = settings.LLM_BACKEND.lower()

    if backend_name == "anthropic":
        # Without a key every call would fail and every visit would get R69:
        # better that the service does not start.
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("LLM_BACKEND=anthropic needs ANTHROPIC_API_KEY.")
        logger.info("Using Claude (Anthropic API) as medical coding backend.")
        return ClaudeMedicalCodingService(
            model_name=settings.LLM_MODEL_NAME or claude.DEFAULT_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
        )

    if backend_name == "gemini":
        logger.info("Using Gemini (Vertex AI) as medical coding backend.")
        return GeminiMedicalCodingService(
            model_name=settings.LLM_MODEL_NAME or gemini.DEFAULT_MODEL,
            project_id=settings.GCP_PROJECT_ID,
        )

    if backend_name == "noop":
        logger.info("Using No-op medical coding backend.")
        return NoOpMedicalCodingService()

    raise ValueError(
        f"Unknown LLM_BACKEND: '{backend_name}'. "
        "Supported values: 'anthropic', 'gemini', 'noop'."
    )


# Module-level singleton
medical_llm_processor: MedicalCodingService = get_llm_service()