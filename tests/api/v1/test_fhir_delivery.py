"""
What reaches the FHIR Store, and how deliveries are tracked.

Audit findings: be-v2-seguimiento-fhir-incoherente (poc07, poc07b),
be-v2-rda-construido-desde-payload (poc22).
"""
from copy import deepcopy
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.schemas.patient import PatientFullRecord
from app.services.fhir import fhir_backend
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, VISIT_1, VISIT_2, MockUser

PACIENTE = "102089-0"
CONSULTA = "51845-6"


def _kind(bundle) -> str:
    composition = bundle["entry"][0]["resource"]
    return composition["type"]["coding"][0]["code"]


class FakeStore:
    """Records every bundle; fails the kinds listed in ``failing``."""

    def __init__(self):
        self.failing: set[str] = set()
        self.sent: list[tuple[str, str]] = []
        self.bundles: list[dict] = []

    def send_bundle(self, bundle):
        kind = _kind(bundle)
        ok = kind not in self.failing
        self.sent.append(("OK" if ok else "FAIL", kind))
        self.bundles.append(bundle)
        return {"status": "success"} if ok else {"status": "error", "error": "503"}


@pytest.fixture
def store():
    fake = FakeStore()
    with patch.object(fhir_backend, "send_bundle", side_effect=fake.send_bundle):
        yield fake


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


def _payload(**top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    return payload


def test_background_change_resent_after_fhir_failure(client, db_session, store):
    """poc07: the hash was stored before the upload, so a failed RDA-Paciente
    carrying a new allergy was never retried."""
    assert _sync(client, _payload(allergies=[])).status_code == 201

    with_allergy = _payload(allergies=[{"category": "01", "allergen": "Penicilina"}])
    store.failing = {PACIENTE}
    assert _sync(client, with_allergy).json()["fhir_status"] == "error"

    store.failing = set()
    store.sent.clear()
    store.bundles.clear()
    assert _sync(client, with_allergy).json()["fhir_status"] == "success"

    assert store.sent == [("OK", PACIENTE)]
    allergies = [
        e["resource"] for e in store.bundles[0]["entry"]
        if e["resource"]["resourceType"] == "AllergyIntolerance"
    ]
    assert [a["code"]["text"] for a in allergies] == ["Penicilina"]


def test_partial_fhir_failure_does_not_resend_accepted_bundles(client, db_session, store):
    """poc07b: with one bundle failing, every accepted one was sent (and stored
    in the FHIR Store) again on the retry."""
    payload = _payload(medicalHistory=[deepcopy(VISIT_1), deepcopy(VISIT_2)])
    store.failing = {CONSULTA}
    _sync(client, payload)
    assert store.sent == [("OK", PACIENTE), ("FAIL", CONSULTA), ("FAIL", CONSULTA)]

    store.failing = set()
    store.sent.clear()
    _sync(client, payload)

    assert store.sent == [("OK", CONSULTA), ("OK", CONSULTA)]
    patient = db_session.query(Patient).one()
    assert sorted(patient.synced_encounter_ids) == ["enc-visit-001", "enc-visit-002"]
    assert patient.rda_paciente_sent is True

    store.sent.clear()
    _sync(client, payload)
    assert store.sent == []


def test_rda_uses_stored_identity(client, db_session, store):
    """poc22: identity changes the server refuses still reached the RDA."""
    assert _sync(client, _payload()).status_code == 201
    changed = _payload(medicalHistory=[deepcopy(VISIT_1)])
    changed["patientInfo"]["firstName"] = "NOMBRE-CAMBIADO"
    changed["patientInfo"]["dob"] = "2001-01-01"

    store.bundles.clear()
    assert _sync(client, changed).status_code == 201

    patients = [
        e["resource"]
        for bundle in store.bundles
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Patient"
    ]
    assert patients
    for resource in patients:
        assert resource["birthDate"] == "2020-01-01"
        assert "NOMBRE-CAMBIADO" not in str(resource["name"])


def test_unreadable_stored_record_falls_back_to_the_payload(client, db_session, store):
    try:
        PatientFullRecord.model_validate({})
    except ValidationError as exc:
        invalid = exc

    with patch(
        "app.api.v1.endpoints.patients.PatientFullRecord.model_validate",
        side_effect=invalid,
    ):
        response = _sync(client, _payload())

    assert response.status_code == 201
    assert store.sent == [("OK", PACIENTE)]
