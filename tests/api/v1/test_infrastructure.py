"""
Tests for application wiring that the endpoint tests bypass.

Covers:
- Backend factories (FHIR Store, LLM) and their no-op implementations
- Database URI strategies and the get_db session dependency
- Application startup in app.main (NFC config guard, CORS, health check)
"""
import runpy
import warnings
from unittest.mock import MagicMock, patch

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.db import session as db_session_module
from app.db.session import get_database_uri, get_db
from app.services.fhir.factory import get_fhir_backend
from app.services.fhir.noop import NoOpFHIRBackend
from app.services.llm.base import FALLBACK_ICD10_CODE
from app.services.llm.factory import get_llm_service
from app.services.llm.noop import NoOpMedicalCodingService

# ---------------------------------------------------------------------------
# FHIR Store backend factory
# ---------------------------------------------------------------------------


class TestFhirBackendFactory:
    def test_gcp_backend_uses_gcp_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "FHIR_BACKEND", "GCP")
        monkeypatch.setattr(settings, "GCP_PROJECT_ID", "hwb-project")
        monkeypatch.setattr(settings, "GCP_DATASET_ID", "hwb-dataset")
        monkeypatch.setattr(settings, "GCP_FHIR_STORE_ID", "hwb-store")
        monkeypatch.setattr(settings, "GCP_LOCATION", "us-central1")

        with patch("app.services.fhir.factory.GCPHealthcareBackend") as gcp_cls:
            backend = get_fhir_backend()

        assert backend is gcp_cls.return_value
        gcp_cls.assert_called_once_with(
            project_id="hwb-project",
            dataset_id="hwb-dataset",
            fhir_store_id="hwb-store",
            location="us-central1",
        )

    def test_noop_backend(self, monkeypatch):
        monkeypatch.setattr(settings, "FHIR_BACKEND", "noop")
        assert isinstance(get_fhir_backend(), NoOpFHIRBackend)

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "FHIR_BACKEND", "azure")
        with pytest.raises(ValueError, match="Unknown FHIR_BACKEND: 'azure'"):
            get_fhir_backend()


class TestNoOpFhirBackend:
    def test_send_bundle_is_skipped(self):
        bundle = {"resourceType": "Bundle", "entry": [{}, {}]}
        result = NoOpFHIRBackend().send_bundle(bundle)
        assert result == {"status": "skipped", "reason": "No-op FHIR backend configured"}

    def test_send_bundle_without_entries(self):
        assert NoOpFHIRBackend().send_bundle({})["status"] == "skipped"


# ---------------------------------------------------------------------------
# LLM backend factory
# ---------------------------------------------------------------------------


class TestLlmBackendFactory:
    def test_gemini_backend_uses_model_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "Gemini")
        monkeypatch.setattr(settings, "LLM_MODEL_NAME", "gemini-test")
        monkeypatch.setattr(settings, "GCP_PROJECT_ID", "hwb-project")

        with patch("app.services.llm.factory.GeminiMedicalCodingService") as gemini_cls:
            service = get_llm_service()

        assert service is gemini_cls.return_value
        gemini_cls.assert_called_once_with(model_name="gemini-test", project_id="hwb-project")

    def test_noop_backend(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "noop")
        assert isinstance(get_llm_service(), NoOpMedicalCodingService)

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "openai")
        with pytest.raises(ValueError, match="Unknown LLM_BACKEND: 'openai'"):
            get_llm_service()


class TestNoOpMedicalCoding:
    def test_extract_diagnoses_returns_fallback(self):
        result = NoOpMedicalCodingService().extract_diagnoses(
            history="Fiebre", physical=None, systems=None, plan=None
        )
        assert len(result) == 1
        assert result[0].icd10Code == FALLBACK_ICD10_CODE
        assert "LLM deshabilitado" in result[0].description

    def test_code_family_history_returns_fallback(self):
        result = NoOpMedicalCodingService().code_family_history_item("Hipertensión")
        assert result["icd10Code"] == FALLBACK_ICD10_CODE
        assert result["icd11Code"] is None
        assert result["description"].startswith("Hipertensión — ")


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------


class TestDatabaseUri:
    @pytest.fixture(autouse=True)
    def db_credentials(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_USER", "hwb")
        monkeypatch.setattr(settings, "DB_PASS", "secret")
        monkeypatch.setattr(settings, "DB_NAME", "hwb_db")
        monkeypatch.setattr(settings, "INSTANCE_CONNECTION_NAME", None)
        monkeypatch.setattr(settings, "DATABASE_URL", None)

    def test_cloud_sql_socket_takes_priority(self, monkeypatch):
        monkeypatch.setattr(settings, "INSTANCE_CONNECTION_NAME", "proj:region:inst")
        monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://ignored")

        assert get_database_uri() == (
            "postgresql+psycopg2://hwb:secret@/hwb_db?host=/cloudsql/proj:region:inst"
        )

    def test_explicit_database_url(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///./local.db")
        assert get_database_uri() == "sqlite:///./local.db"

    def test_localhost_fallback(self):
        assert get_database_uri() == "postgresql://hwb:secret@localhost:5432/hwb_db"


class TestGetDb:
    def test_yields_session_and_closes_it(self, monkeypatch):
        session_factory = MagicMock()
        monkeypatch.setattr(db_session_module, "SessionLocal", session_factory)

        dependency = get_db()
        db = next(dependency)
        assert db is session_factory.return_value
        db.close.assert_not_called()

        dependency.close()
        db.close.assert_called_once()

    def test_closes_session_when_request_fails(self, monkeypatch):
        session_factory = MagicMock()
        monkeypatch.setattr(db_session_module, "SessionLocal", session_factory)

        dependency = get_db()
        db = next(dependency)
        with pytest.raises(RuntimeError):
            dependency.throw(RuntimeError("request failed"))
        db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Application startup (app.main)
#
# app.main is executed in a fresh namespace with runpy so these tests never
# replace the shared `app` object the rest of the suite runs against.
# ---------------------------------------------------------------------------


def _run_main() -> dict:
    with warnings.catch_warnings():
        # Expected: app.main is already imported and we deliberately run a copy.
        warnings.filterwarnings("ignore", message="'app.main' found in sys.modules")
        return runpy.run_module("app.main")


class TestApplicationStartup:
    def test_invalid_nfc_configuration_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(
            Settings,
            "nfc_keyring_errors",
            lambda self: ["NFC_MASTER_KEY is not valid base64", "NFC_KEK is too short"],
        )
        with pytest.raises(RuntimeError) as exc_info:
            _run_main()

        message = str(exc_info.value)
        assert message.startswith("Invalid NFC key configuration:")
        assert "  - NFC_MASTER_KEY is not valid base64\n  - NFC_KEK is too short" in message

    def test_no_cors_middleware_without_origins(self, monkeypatch):
        monkeypatch.setattr(settings, "BACKEND_CORS_ORIGINS", " , ")
        app = _run_main()["app"]
        assert not any(m.cls is CORSMiddleware for m in app.user_middleware)

    def test_cors_allows_configured_origins(self, monkeypatch):
        monkeypatch.setattr(
            settings, "BACKEND_CORS_ORIGINS", " https://app.hwb.org , https://admin.hwb.org,"
        )
        app = _run_main()["app"]

        cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
        assert cors.kwargs["allow_origins"] == ["https://app.hwb.org", "https://admin.hwb.org"]

        # No `with`: the lifespan (NFC keyring preparation) is not needed here.
        client = TestClient(app)
        preflight = client.options(
            "/health-check",
            headers={
                "Origin": "https://admin.hwb.org",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://admin.hwb.org"

        rejected = client.options(
            "/health-check",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in rejected.headers

    def test_health_check(self, client: TestClient):
        response = client.get("/health-check")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
