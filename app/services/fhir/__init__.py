"""
FHIR Store abstraction layer.

Public API:
  - fhir_backend: the active FHIRStoreBackend singleton (selected by config).
  - FHIRStoreBackend: the Protocol that all backends implement.
"""

from app.services.fhir.base import FHIRBundleSendResult, FHIRStoreBackend
from app.services.fhir.factory import fhir_backend

__all__ = ["fhir_backend", "FHIRStoreBackend", "FHIRBundleSendResult"]