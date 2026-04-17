"""
FHIR Store Backend Factory.

Returns the correct FHIRStoreBackend implementation based on settings.
To add a new backend, import it here and add a branch for its identifier.
"""

import logging

from app.core.config import settings
from app.services.fhir.base import FHIRStoreBackend
from app.services.fhir.gcp import GCPHealthcareBackend
from app.services.fhir.noop import NoOpFHIRBackend

logger = logging.getLogger(__name__)


def get_fhir_backend() -> FHIRStoreBackend:
    """
    Instantiate and return the configured FHIR Store backend.
    
    The backend is selected via the FHIR_BACKEND environment variable:
      - "gcp"  → Google Cloud Healthcare API (default)
      - "noop" → No-op (development/testing)
    
    Future contributors can add implementations for Azure Health Data Services,
    HAPI FHIR, AWS HealthLake, etc. by creating a new module in this package
    and adding a branch below.
    """
    backend_name = settings.FHIR_BACKEND.lower()

    if backend_name == "gcp":
        logger.info("Using GCP Healthcare API as FHIR Store backend.")
        return GCPHealthcareBackend(
            project_id=settings.GCP_PROJECT_ID,
            dataset_id=settings.GCP_DATASET_ID,
            fhir_store_id=settings.GCP_FHIR_STORE_ID,
            location=settings.GCP_LOCATION,
        )

    if backend_name == "noop":
        logger.info("Using No-op FHIR Store backend (bundles will be discarded).")
        return NoOpFHIRBackend()

    raise ValueError(
        f"Unknown FHIR_BACKEND: '{backend_name}'. "
        "Supported values: 'gcp', 'noop'."
    )


# Module-level singleton
fhir_backend: FHIRStoreBackend = get_fhir_backend()