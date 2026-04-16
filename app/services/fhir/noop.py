"""
No-op FHIR Store Backend.

Useful for local development, unit tests, and environments where no real FHIR
Store is configured. All calls return "skipped" without making any network requests.
"""

import logging

from app.services.fhir.base import FHIRBundleSendResult, FHIRStoreBackend

logger = logging.getLogger(__name__)


class NoOpFHIRBackend(FHIRStoreBackend):
    """A FHIR backend that accepts bundles but does nothing with them."""

    def send_bundle(self, bundle: dict) -> FHIRBundleSendResult:
        logger.info(
            "NoOp FHIR backend active — bundle discarded "
            f"(resourceType={bundle.get('resourceType')}, entries={len(bundle.get('entry', []))})"
        )
        return {"status": "skipped", "reason": "No-op FHIR backend configured"}