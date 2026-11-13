"""
FHIR resource ids that are always valid, and bundles that reach the FHIR Store once.

Backend audit 2026-11-04:
- be-nov26-id-patient-fhir-invalido-bloquea-envio: ids were built from typed
  documents and codes ("1023 456 789"), and the FHIR Store rejected the whole
  bundle on every retry.
- be-nov26-reintento-duplica-rda-en-fhir: a retried or overlapping /sync sent
  the same RDA-Consulta again, with a new random identifier each time.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import Patient
from app.schemas.patient import PatientFullRecord
from app.services import fhir_service
from app.services.fhir import fhir_backend
from app.services.fhir.gcp import GCPHealthcareBackend
from app.services.fhir_service import (
    FHIR_ID_PATTERN,
    build_rda_consulta,
    build_rda_paciente,
    convert_to_fhir_rda,
    invalid_resource_ids,
    rda_consulta_bundle_id,
    rda_paciente_bundle_id,
)
from app.services.patient_service import claim_fhir_delivery, record_fhir_delivery
from tests.api.v1.test_fhir_delivery import CONSULTA, FakeStore, _kind, _payload, _sync
from tests.api.v1.test_fhir_service import _make_patient, _make_visit
from tests.api.v1.test_patients import VISIT_1

# ---------------------------------------------------------------------------
# Resource ids
# ---------------------------------------------------------------------------


def _hostile_patient() -> PatientFullRecord:
    """Documents and codes as people type them (G19, G21 of the audit)."""
    record = _make_patient(visit_override=_make_visit(with_payer=True)).model_dump(mode="json")
    record["patientInfo"]["identification"]["documentNumber"] = "VZ 987.6543"
    visit = record["medicalHistory"][0]
    visit["practitioner"]["documentNumber"] = "1023 456 789"
    visit["provider"]["repsCode"] = "5400 154001"
    visit["provider"]["locationSeatCode"] = "SINT/1122"
    visit["payer"]["code"] = "E" * 70
    return PatientFullRecord.model_validate(record)


def _resource_ids(bundle):
    return {e["resource"]["resourceType"]: e["resource"].get("id") for e in bundle["entry"]}


def test_resource_ids_are_valid_fhir_ids():
    patient = _hostile_patient()

    for bundle in (build_rda_paciente(patient), build_rda_consulta(patient, patient.medicalHistory[0])):
        ids = [e["resource"]["id"] for e in bundle["entry"] if "id" in e["resource"]]
        assert all(FHIR_ID_PATTERN.fullmatch(i) for i in ids), ids
        assert FHIR_ID_PATTERN.fullmatch(bundle["id"])
        assert invalid_resource_ids(bundle) == []


def test_the_typed_values_stay_in_the_identifiers():
    patient = _hostile_patient()
    bundle = build_rda_consulta(patient, patient.medicalHistory[0])
    resources = {e["resource"]["resourceType"] + str(i): e["resource"] for i, e in enumerate(bundle["entry"])}
    values = {
        ident.get("value")
        for r in resources.values()
        for ident in (r.get("identifier") or [])
    }

    assert {"VZ 987.6543", "1023 456 789", "5400 154001", "SINT/1122", "E" * 70} <= values


def test_the_same_person_or_place_gets_the_same_id_in_every_bundle():
    patient = _hostile_patient()
    paciente = _resource_ids(build_rda_paciente(patient))
    consulta = _resource_ids(build_rda_consulta(patient, patient.medicalHistory[0]))

    for kind in ("Patient", "Practitioner"):
        assert paciente[kind] == consulta[kind]


def test_references_point_at_the_new_ids():
    patient = _hostile_patient()
    bundle = build_rda_consulta(patient, patient.medicalHistory[0])
    urls = {e["fullUrl"] for e in bundle["entry"]}

    def references(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "reference":
                    yield value
                else:
                    yield from references(value)
        elif isinstance(node, list):
            for item in node:
                yield from references(item)

    refs = list(references(bundle))
    assert refs and all(ref in urls for ref in refs)


def test_an_invalid_id_is_reported_by_type_only(app_log):
    bad = {"entry": [{"resource": {"resourceType": "Practitioner", "id": "CC-1023 456 789"}},
                     {"resource": {"resourceType": "Patient", "id": "ok-1"}},
                     {"resource": {"resourceType": "Composition"}}]}

    assert invalid_resource_ids(bad) == ["Practitioner"]

    with patch.object(fhir_service, "build_rda_paciente", return_value=bad):
        convert_to_fhir_rda(_make_patient())

    assert "resource_types=['Practitioner']" in app_log.text
    assert "1023" not in app_log.text


# ---------------------------------------------------------------------------
# Bundle ids
# ---------------------------------------------------------------------------


def test_an_rda_consulta_has_the_same_id_every_time_it_is_built():
    patient = _make_patient(with_visit=True)
    visit = patient.medicalHistory[0]

    first, again = build_rda_consulta(patient, visit), build_rda_consulta(patient, visit)

    assert first["id"] == again["id"] == rda_consulta_bundle_id(visit.encounterIdentifier)
    assert first["identifier"]["value"] == first["id"]


def test_different_encounters_get_different_bundle_ids():
    assert rda_consulta_bundle_id("enc-a") != rda_consulta_bundle_id("enc-b")


def test_a_visit_without_identifier_still_gets_a_valid_bundle_id():
    patient = _make_patient(with_visit=True)
    visit = patient.medicalHistory[0].model_copy(update={"encounterIdentifier": None})

    assert FHIR_ID_PATTERN.fullmatch(build_rda_consulta(patient, visit)["id"])


def test_an_rda_paciente_id_follows_the_background():
    patient = _make_patient()

    same = build_rda_paciente(patient, "hash-1")["id"]
    assert same == build_rda_paciente(patient, "hash-1")["id"]
    assert same == rda_paciente_bundle_id(patient.patientId, "hash-1")
    assert same != build_rda_paciente(patient, "hash-2")["id"]


# ---------------------------------------------------------------------------
# Conditional create in the GCP backend
# ---------------------------------------------------------------------------


def _gcp():
    return GCPHealthcareBackend(project_id="p", dataset_id="d", fhir_store_id="s")


def _response(status, content=b'{"resourceType": "Bundle"}'):
    response = MagicMock(status_code=status, content=content)
    response.json.return_value = {"resourceType": "Bundle"}
    return response


BUNDLE = {"resourceType": "Bundle", "identifier": {"system": "https://x/bundle-identifier", "value": "b-1"}}


@patch("app.services.fhir.gcp.google.auth.default")
@patch("app.services.fhir.gcp.requests.Session.post")
@pytest.mark.parametrize("status", [200, 201])
def test_a_bundle_is_created_only_if_its_identifier_is_not_stored(mock_post, mock_auth, status):
    mock_auth.return_value = (MagicMock(token="t", valid=True), "p")
    mock_post.return_value = _response(status)

    result = _gcp().send_bundle(BUNDLE)

    assert result["status"] == "success"
    assert mock_post.call_args.kwargs["headers"]["If-None-Exist"] == (
        "identifier=https://x/bundle-identifier|b-1"
    )


@patch("app.services.fhir.gcp.google.auth.default")
@patch("app.services.fhir.gcp.requests.Session.post")
def test_a_bundle_already_stored_twice_counts_as_delivered(mock_post, mock_auth, app_log):
    mock_auth.return_value = (MagicMock(token="t", valid=True), "p")
    mock_post.return_value = _response(412)

    result = _gcp().send_bundle(BUNDLE)

    assert result == {"status": "success", "response": {}}
    assert "more than once" in app_log.text


@patch("app.services.fhir.gcp.google.auth.default")
@patch("app.services.fhir.gcp.requests.Session.post")
def test_a_match_without_body_is_still_a_success(mock_post, mock_auth):
    mock_auth.return_value = (MagicMock(token="t", valid=True), "p")
    mock_post.return_value = _response(200, content=b"")

    assert _gcp().send_bundle(BUNDLE) == {"status": "success", "response": {}}

    unreadable = _response(201)
    unreadable.json.side_effect = ValueError("not json")
    mock_post.return_value = unreadable
    assert _gcp().send_bundle(BUNDLE) == {"status": "success", "response": {}}


@patch("app.services.fhir.gcp.google.auth.default")
@patch("app.services.fhir.gcp.requests.Session.post")
def test_a_bundle_without_identifier_is_sent_without_condition(mock_post, mock_auth):
    mock_auth.return_value = (MagicMock(token="t", valid=True), "p")
    mock_post.return_value = _response(412)

    _gcp().send_bundle({"resourceType": "Bundle"})
    assert "If-None-Exist" not in mock_post.call_args.kwargs["headers"]
    _gcp().send_bundle({"resourceType": "Bundle", "identifier": {"value": "b-2"}})
    assert mock_post.call_args.kwargs["headers"]["If-None-Exist"] == "identifier=b-2"


# ---------------------------------------------------------------------------
# One /sync at a time sends a patient's bundles
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    fake = FakeStore()
    with patch.object(fhir_backend, "send_bundle", side_effect=fake.send_bundle):
        yield fake


def _stored_patient(client, db_session, store):
    response = _sync(client, _payload())
    assert response.status_code == 201, response.text
    return db_session.query(Patient).one()


def test_a_claim_is_taken_once_and_given_back(client, db_session, store):
    patient = _stored_patient(client, db_session, store)
    now = datetime.now(timezone.utc)

    state = claim_fhir_delivery(db_session, patient.id, now)
    assert state == (list(patient.synced_encounter_ids), patient.background_data_hash, True)
    assert claim_fhir_delivery(db_session, patient.id, now + timedelta(seconds=5)) is None

    record_fhir_delivery(db_session, patient.id, [], None, release_claim=True)
    db_session.refresh(patient)
    assert patient.fhir_delivery_claimed_until is None
    assert claim_fhir_delivery(db_session, patient.id, now) is not None


def test_a_claim_left_by_a_dead_request_expires(client, db_session, store):
    patient = _stored_patient(client, db_session, store)
    now = datetime.now(timezone.utc)
    claim_fhir_delivery(db_session, patient.id, now)

    assert claim_fhir_delivery(db_session, patient.id, now + timedelta(seconds=361)) is not None


def test_a_sync_that_finds_another_one_sending_does_not_send(client, db_session, store):
    patient = _stored_patient(client, db_session, store)
    patient.fhir_delivery_claimed_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.commit()
    sent_before = len(store.sent)

    response = _sync(client, _payload(medicalHistory=[VISIT_1]))

    assert response.status_code == 201
    assert response.json()["fhir_status"] == "in_progress"
    assert len(store.sent) == sent_before
    db_session.expire_all()
    assert VISIT_1["encounterIdentifier"] not in db_session.query(Patient).one().synced_encounter_ids


def test_the_next_sync_sends_once_the_claim_is_given_back(client, db_session, store):
    _stored_patient(client, db_session, store)

    response = _sync(client, _payload(medicalHistory=[VISIT_1]))

    assert response.json()["fhir_status"] == "success"
    assert [_kind(b) for b in store.bundles].count(CONSULTA) == 1
    db_session.expire_all()
    stored = db_session.query(Patient).one()
    assert stored.fhir_delivery_claimed_until is None
    assert VISIT_1["encounterIdentifier"] in stored.synced_encounter_ids


def test_bundles_are_decided_from_what_the_claim_reads(client, db_session, store):
    """Another request delivered the visit between this one's save and its claim."""
    patient = _stored_patient(client, db_session, store)
    delivered = (
        [VISIT_1["encounterIdentifier"]], patient.background_data_hash, True,
    )

    with patch("app.api.v1.endpoints.patients.claim_fhir_delivery", return_value=delivered):
        response = _sync(client, _payload(medicalHistory=[VISIT_1]))

    assert response.json()["fhir_status"] == "success"
    assert CONSULTA not in [_kind(b) for b in store.bundles]
