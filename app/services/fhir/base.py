"""
FHIR Store Backend Protocol.

This module defines the abstract contract that any FHIR Store implementation
must satisfy. By coding against this Protocol instead of a concrete backend
(GCP Healthcare API, Azure Health Data Services, HAPI FHIR, etc.),
the application remains vendor-neutral and deployable on any infrastructure.

To add a new backend (e.g. Azure, HAPI), create a new module next to
this file that implements the FHIRStoreBackend Protocol, then register it
in factory.py.
"""

from typing import Protocol, runtime_checkable

from typing_extensions import TypedDict


class FHIRBundleSendResult(TypedDict, total=False):
    """Standardized response from any FHIR Store backend."""
    status: str           # "success" | "error" | "skipped"
    reason: str           # Present when status == "skipped"
    error: str            # Present when status == "error"
    response: dict        # Raw backend response (present when status == "success")


@runtime_checkable
class FHIRStoreBackend(Protocol):
    """
    Contract for FHIR Store implementations.
    
    A backend is responsible for transmitting a single FHIR Bundle (as a dict)
    to its configured FHIR Store and returning a standardized result.
    
    The backend MUST:
      - Handle its own authentication (API keys, service accounts, OAuth).
      - Return a status of "success", "error", or "skipped".
      - Never raise exceptions — all failures are reported via the return value.
    
    The backend MUST NOT:
      - Generate or modify FHIR bundles (that's the FHIRBundleBuilder's job).
      - Depend on application-specific data structures.
    """

    def send_bundle(self, bundle: dict) -> FHIRBundleSendResult:
        """
        Transmit a FHIR Bundle to the configured FHIR Store.
        
        Args:
            bundle: A valid FHIR R4 Bundle resource as a Python dict.
        
        Returns:
            FHIRBundleSendResult with status and either response or error details.
        """
        ...