"""
Tests for UUID auto-assignment validators on MedicalHistoryItem and
VaccinationRecordItem, and for GCP error body sanitization.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.schemas.patient import MedicalHistoryItem, VaccinationRecordItem
from app.services.fhir.gcp import GCPHealthcareBackend

# ============================================================================
# MedicalHistoryItem — encounterIdentifier auto-generation
# ============================================================================

class TestMedicalHistoryItemValidator:
    def test_auto_assigns_uuid_when_missing(self):
        """encounterIdentifier is generated when the frontend omits it."""
        item = MedicalHistoryItem(
            startDateTime=datetime(2026, 4, 1, 10, 0),
        )
        assert item.encounterIdentifier is not None
        assert len(item.encounterIdentifier) == 36

    def test_auto_assigns_uuid_when_none(self):
        """encounterIdentifier is generated when explicitly set to None."""
        item = MedicalHistoryItem(
            startDateTime=datetime(2026, 4, 1, 10, 0),
            encounterIdentifier=None,
        )
        assert item.encounterIdentifier is not None
        assert len(item.encounterIdentifier) == 36

    def test_preserves_existing_uuid(self):
        """encounterIdentifier is kept when the frontend provides one."""
        item = MedicalHistoryItem(
            startDateTime=datetime(2026, 4, 1, 10, 0),
            encounterIdentifier="my-encounter-id",
        )
        assert item.encounterIdentifier == "my-encounter-id"

    def test_each_instance_gets_unique_uuid(self):
        """Two items created without IDs get different UUIDs."""
        a = MedicalHistoryItem(startDateTime=datetime(2026, 4, 1, 10, 0))
        b = MedicalHistoryItem(startDateTime=datetime(2026, 4, 1, 11, 0))
        assert a.encounterIdentifier != b.encounterIdentifier


# ============================================================================
# VaccinationRecordItem — vaccinationId auto-generation
# ============================================================================

class TestVaccinationRecordItemValidator:
    def test_auto_assigns_uuid_when_missing(self):
        """vaccinationId is generated when the frontend omits it."""
        item = VaccinationRecordItem(
            date="2026-03-15",
            vaccineName="BCG",
            vaccineCode="19",
            dose=1,
            administratedBy="Nurse A",
            administratedAt="Hospital Meoz",
            status="completed",
        )
        assert item.vaccinationId is not None
        assert len(item.vaccinationId) == 36

    def test_auto_assigns_uuid_when_none(self):
        """vaccinationId is generated when explicitly set to None."""
        item = VaccinationRecordItem(
            vaccinationId=None,
            date="2026-03-15",
            vaccineName="BCG",
            vaccineCode="19",
            dose=1,
            administratedBy="Nurse A",
            administratedAt="Hospital Meoz",
            status="completed",
        )
        assert item.vaccinationId is not None
        assert len(item.vaccinationId) == 36

    def test_preserves_existing_uuid(self):
        """vaccinationId is kept when the frontend provides one."""
        item = VaccinationRecordItem(
            vaccinationId="my-vax-id",
            date="2026-03-15",
            vaccineName="BCG",
            vaccineCode="19",
            dose=1,
            administratedBy="Nurse A",
            administratedAt="Hospital Meoz",
            status="completed",
        )
        assert item.vaccinationId == "my-vax-id"

    def test_each_instance_gets_unique_uuid(self):
        """Two items created without IDs get different UUIDs."""
        a = VaccinationRecordItem(
            date="2026-03-15", vaccineName="BCG", vaccineCode="19",
            dose=1, administratedBy="N", administratedAt="H", status="completed",
        )
        b = VaccinationRecordItem(
            date="2026-03-16", vaccineName="Polio", vaccineCode="02",
            dose=1, administratedBy="N", administratedAt="H", status="completed",
        )
        assert a.vaccinationId != b.vaccinationId


# ============================================================================
# GCPHealthcareBackend — error body sanitization
# ============================================================================

class TestGCPErrorSanitization:
    """Verify that GCP HTTP error responses don't leak PHI into logs."""

    def _make_backend(self) -> GCPHealthcareBackend:
        return GCPHealthcareBackend(
            project_id="test-project",
            dataset_id="test-dataset",
            fhir_store_id="test-store",
        )

    @patch("app.services.fhir.gcp.requests.post")
    @patch("app.services.fhir.gcp.google.auth.default")
    def test_http_error_body_is_sanitized(self, mock_auth, mock_post):
        """PHI in the GCP error response body is redacted before logging."""
        # Setup mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "fake-token"
        mock_auth.return_value = (mock_creds, "test-project")

        # Simulate a 400 error with PHI in the response body
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.text = '{"family": "Pérez Rodríguez", "given": ["Juan Carlos"]}'

        import requests as req_lib
        mock_post.side_effect = req_lib.exceptions.HTTPError(
            response=error_response
        )

        backend = self._make_backend()
        result = backend.send_bundle({"resourceType": "Bundle"})

        assert result["status"] == "error"
        # The error message must not contain the patient name
        assert "Pérez" not in result["error"]
        assert "Juan Carlos" not in result["error"]
        # But it should still contain useful debugging info
        assert "400" in result["error"]
        assert "REDACTED" in result["error"]

    @patch("app.services.fhir.gcp.requests.post")
    @patch("app.services.fhir.gcp.google.auth.default")
    def test_success_returns_response(self, mock_auth, mock_post):
        """Successful bundle submission returns the GCP response."""
        mock_creds = MagicMock()
        mock_creds.token = "fake-token"
        mock_auth.return_value = (mock_creds, "test-project")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"resourceType": "Bundle", "type": "transaction-response"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        backend = self._make_backend()
        result = backend.send_bundle({"resourceType": "Bundle"})

        assert result["status"] == "success"

    @patch("app.services.fhir.gcp.requests.post")
    @patch("app.services.fhir.gcp.google.auth.default")
    def test_unexpected_error_logs_only_class_name(self, mock_auth, mock_post):
        """Unexpected (non-HTTP) errors log only the exception class, not the message."""
        mock_creds = MagicMock()
        mock_creds.token = "fake-token"
        mock_auth.return_value = (mock_creds, "test-project")

        mock_post.side_effect = RuntimeError("secret internal detail")

        backend = self._make_backend()
        result = backend.send_bundle({"resourceType": "Bundle"})

        assert result["status"] == "error"
        assert "RuntimeError" in result["error"]
        assert "secret internal detail" not in result["error"]

    def test_unconfigured_backend_skips(self):
        """Backend with missing config returns skipped without errors."""
        backend = GCPHealthcareBackend(
            project_id=None, dataset_id=None, fhir_store_id=None
        )
        result = backend.send_bundle({"resourceType": "Bundle"})
        assert result["status"] == "skipped"
