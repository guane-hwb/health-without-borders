"""
Medical Coding Service abstraction layer.

Public API:
  - medical_llm_processor: the active MedicalCodingService singleton (selected by config).
  - MedicalCodingService: the Protocol that all backends implement.

Backward compatibility:
  The previous `from app.services.llm.service import medical_llm_processor` path
  is preserved via the re-export below.
"""

from app.services.llm.base import MedicalCodingService
from app.services.llm.factory import medical_llm_processor

__all__ = ["medical_llm_processor", "MedicalCodingService"]