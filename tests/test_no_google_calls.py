"""
The test suite never reaches Google, whatever the developer's .env says.

On 2026-11-12 two local runs of the suite sent 404 synthetic bundles to the
pilot's FHIR Store: .env selected the real backends and the libraries used the
machine's application-default credentials. tests/conftest.py now forces the
no-op backends and refuses any request to a Google host.
"""
import asyncio

import httpx
import pytest
import requests

from app.core.config import settings
from app.services.fhir import fhir_backend
from app.services.fhir.noop import NoOpFHIRBackend
from app.services.llm import medical_llm_processor
from app.services.llm.noop import NoOpMedicalCodingService
from tests.conftest import BLOCKED_GOOGLE_CALLS, _is_google


def test_the_application_runs_with_the_no_op_backends():
    assert settings.FHIR_BACKEND == "noop"
    assert settings.LLM_BACKEND == "noop"
    assert isinstance(fhir_backend, NoOpFHIRBackend)
    assert isinstance(medical_llm_processor, NoOpMedicalCodingService)


@pytest.mark.parametrize(
    "url, google",
    [
        ("https://healthcare.googleapis.com/v1/projects/p/fhir/Bundle", True),
        ("https://oauth2.googleapis.com/token", True),
        ("https://us-central1-aiplatform.googleapis.com/v1/x", True),
        ("http://metadata.google.internal/computeMetadata/v1/", True),
        ("http://169.254.169.254/computeMetadata/v1/", True),
        ("http://testserver/api/v1/patients/sync", False),
        ("https://notgoogleapis.com/x", False),
    ],
)
def test_google_hosts_are_recognised(url, google):
    assert _is_google(url) is google


def _refused(call) -> list[str]:
    with pytest.raises(Exception, match="Tests must not call Google"):
        call()
    hosts = list(BLOCKED_GOOGLE_CALLS)
    BLOCKED_GOOGLE_CALLS.clear()  # handled here: do not fail this test
    return hosts


def test_requests_to_google_are_refused_and_recorded():
    assert _refused(
        lambda: requests.post("https://healthcare.googleapis.com/v1/x", timeout=1)
    ) == ["healthcare.googleapis.com"]


def test_httpx_requests_to_google_are_refused_and_recorded():
    assert _refused(
        lambda: httpx.post("https://aiplatform.googleapis.com/v1/x", timeout=1)
    ) == ["aiplatform.googleapis.com"]

    async def call():
        async with httpx.AsyncClient() as client:
            await client.post("https://aiplatform.googleapis.com/v1/y", timeout=1)

    assert _refused(lambda: asyncio.run(call())) == ["aiplatform.googleapis.com"]
