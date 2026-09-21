"""
Medical Coding Service Protocol.

This module defines the abstract contract that any LLM-powered medical
coding implementation must satisfy. By coding against this Protocol instead
of a specific LLM provider (Vertex AI, OpenAI, Anthropic, local models),
the application remains vendor-neutral.

To add a new LLM provider, create a new module next to this file that
implements the MedicalCodingService Protocol, then register it in factory.py.

FALLBACK STRATEGY:
  When the LLM fails or returns an invalid code, the service returns a
  clinically appropriate fallback instead of fabricating a false diagnosis:
  - Diagnoses: R69 ("Causas de morbilidad desconocidas y no especificadas")
  - Family history / chronic conditions: R69 with the original description preserved
  All fallback codes use verificationStatus="provisional" to signal they need review.
  The bundle IS generated and sent — the fallback never blocks transmission.
"""

from typing import List, Optional, Protocol, runtime_checkable

from app.schemas.patient import DiagnosisItem

# Honest fallback code — "Unknown and unspecified causes of morbidity"
# Unlike Z00.0 (general exam) or Z84.8 (family history), R69 does not
# fabricate a clinical meaning. It signals "we couldn't determine the code".
FALLBACK_ICD10_CODE = "R69"
FALLBACK_ICD10_DESCRIPTION = "Causas de morbilidad desconocidas y no especificadas"


@runtime_checkable
class MedicalCodingService(Protocol):
    """
    Contract for LLM-powered medical coding services.

    Any implementation MUST:
      - Return deterministic, structured output matching the schemas.
      - Never raise exceptions — failures are returned as fallback values.
      - Handle its own authentication and API errors internally.
      - Use FALLBACK_ICD10_CODE (R69) on error, NOT invented diagnoses.

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

        Returns:
            List of DiagnosisItem.
            On error, returns a single DiagnosisItem with code R69 (provisional).
        """
        ...

    def code_family_history_item(self, condition_description: str) -> dict:
        """
        Map a free-text family history condition description to ICD-10/11 codes.

        Returns:
            Dict with icd10Code, icd11Code (nullable), description.
            On error, returns a fallback dict with icd10Code=R69.
        """
        ...

    def code_chronic_condition(self, chronic_description: str) -> dict:
        """
        Map a free-text chronic condition description to ICD-10/11 codes.

        Returns:
            Dict with icd10Code, icd11Code (nullable), description.
            On error, returns a fallback dict with icd10Code=R69.
        """
        ...
