"""
Medical Coding Service Protocol.

This module defines the abstract contract that any LLM-powered medical
coding implementation must satisfy. By coding against this Protocol instead
of a specific LLM provider (Vertex AI, OpenAI, Anthropic, local models),
the application remains vendor-neutral.

To add a new LLM provider, create a new module next to this file that
implements the MedicalCodingService Protocol, then register it in factory.py.
"""

from typing import List, Optional, Protocol, runtime_checkable

from app.schemas.patient import DiagnosisItem


@runtime_checkable
class MedicalCodingService(Protocol):
    """
    Contract for LLM-powered medical coding services.
    
    Any implementation MUST:
      - Return deterministic, structured output matching the schemas.
      - Never raise exceptions — failures are returned as fallback values.
      - Handle its own authentication and API errors internally.
    
    The implementation MUST NOT:
      - Expose vendor-specific types or exceptions in its public API.
      - Modify the input data.
    """

    def extract_diagnoses(
        self,
        history: Optional[str],
        physical: Optional[str],
        systems: Optional[str],
        plan: Optional[str],
    ) -> List[DiagnosisItem]:
        """
        Analyze free-text clinical evaluation notes and return ICD-10/11 coded diagnoses.
        
        Args:
            history: History of current illness (motivo de consulta, enfermedad actual).
            physical: General physical examination findings.
            systems: Systems examination (revisión por sistemas).
            plan: Treatment plan and observations.
        
        Returns:
            List of DiagnosisItem, one per distinct diagnosis inferred from the notes.
            On error, returns a single fallback DiagnosisItem (Z00.0).
        """
        ...

    def code_family_history_item(self, condition_description: str) -> dict:
        """
        Map a free-text family history condition description to ICD-10/11 codes.
        
        Args:
            condition_description: A patient-reported condition like "Diabetes" or "Glaucoma".
        
        Returns:
            Dict with keys:
              - icd10Code (str): WHO ICD-10 code.
              - icd11Code (Optional[str]): WHO ICD-11 MMS code, or None if uncertain.
              - description (str): Official medical description in Spanish.
            On error, returns a fallback dict with icd10Code=Z84.8.
        """
        ...