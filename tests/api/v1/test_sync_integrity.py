"""
/sync integrity without a schema change.

Audit findings: x-v2-consultas-sin-id-se-duplican-en-cada-sync (poc06),
x-v2-sync-sin-control-de-versiones-pierde-datos-clinicos (poc05 + row lock),
be-v2-tarjeta-acudiente-reemplazable-con-solo-uid-pulsera (poc04),
x-v2-evidencia-consentimiento-mal-atribuida (poc09),
x-v2-409-sin-codigo-bloquea-registros.
"""
from copy import deepcopy

import pytest

from app.api.deps import get_current_user
from app.db.models import Patient, RetiredDeviceUid
from app.main import app
from app.schemas.patient import (
    DERIVED_ID_PREFIX,
    PatientFullRecord,
    derived_item_id,
)
from app.services.patient_service import (
    IdentityMismatchError,
    _find_patient_for_sync,
    create_or_update_patient,
)
from app.services.record_merger import (
    _same_guardian,
    _union_background,
    adopt_stored_item_ids,
    merge_patient_records,
)
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, VISIT_1, MockUser

GUARDIAN_HEADER = {"X-Guardian-Device-UID": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]}


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


def _stored(db_session):
    return db_session.query(Patient).one()


def _payload(**top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    return payload


def _vaccine(vaccination_id=None):
    vaccine = {
        "date": "2026-09-01", "vaccineName": "Triple viral", "vaccineCode": "03",
        "dose": 1, "administratedBy": "Enfermera", "administratedAt": "Albergue",
        "status": "completed",
    }
    if vaccination_id:
        vaccine["vaccinationId"] = vaccination_id
    return vaccine


# ---------------------------------------------------------------------------
# Deterministic ids (poc06)
# ---------------------------------------------------------------------------


def test_items_without_id_get_a_stable_derived_id():
    visit = {k: v for k, v in VISIT_1.items() if k != "encounterIdentifier"}
    payload = _payload(medicalHistory=[visit], vaccinationRecord=[_vaccine()])

    first = PatientFullRecord.model_validate(deepcopy(payload))
    second = PatientFullRecord.model_validate(deepcopy(payload))

    encounter_id = first.medicalHistory[0].encounterIdentifier
    assert encounter_id.startswith(DERIVED_ID_PREFIX)
    assert encounter_id == second.medicalHistory[0].encounterIdentifier
    assert encounter_id == derived_item_id(
        "medicalHistory", "TEST-UNIT-001", VISIT_1["startDateTime"]
    )
    assert first.vaccinationRecord[0].vaccinationId == second.vaccinationRecord[0].vaccinationId


def test_items_with_ids_and_odd_shapes_are_left_alone():
    payload = _payload(medicalHistory=[deepcopy(VISIT_1)], vaccinationRecord="not-a-list")
    before = deepcopy(payload)

    assert PatientFullRecord._derive_missing_item_ids(payload) == before
    assert PatientFullRecord._derive_missing_item_ids("raw") == "raw"


def test_edited_visit_without_id_is_not_duplicated(client, db_session):
    """poc06: 2, 3, 4 visits after three syncs of the same edited record."""
    assert _sync(client, _payload(medicalHistory=[deepcopy(VISIT_1)])).status_code == 201

    edited = {k: v for k, v in VISIT_1.items() if k not in ("encounterIdentifier", "diagnosis")}
    edited["clinicalEvaluation"] = {"historyOfCurrentIllness": "Nota corregida"}
    for _ in range(3):
        assert _sync(client, _payload(medicalHistory=[deepcopy(edited)])).status_code == 201

    visits = _stored(db_session).full_record_json["medicalHistory"]
    assert [v["encounterIdentifier"] for v in visits] == ["enc-visit-001"]


def test_new_visit_without_id_is_added_once(client, db_session):
    visit = {k: v for k, v in VISIT_1.items() if k != "encounterIdentifier"}
    for _ in range(3):
        assert _sync(client, _payload(medicalHistory=[deepcopy(visit)])).status_code == 201

    visits = _stored(db_session).full_record_json["medicalHistory"]
    assert len(visits) == 1
    assert visits[0]["encounterIdentifier"].startswith(DERIVED_ID_PREFIX)


def test_vaccine_without_id_adopts_the_stored_one(client, db_session):
    assert _sync(client, _payload(vaccinationRecord=[_vaccine("VAC-1")])).status_code == 201
    assert _sync(client, _payload(vaccinationRecord=[_vaccine()])).status_code == 201

    vaccines = _stored(db_session).full_record_json["vaccinationRecord"]
    assert [v["vaccinationId"] for v in vaccines] == ["VAC-1"]


def test_ambiguous_matches_keep_the_derived_id():
    stored = {
        "medicalHistory": [
            {"encounterIdentifier": "A", "startDateTime": VISIT_1["startDateTime"]},
            {"encounterIdentifier": "B", "startDateTime": VISIT_1["startDateTime"]},
            {"encounterIdentifier": "C", "startDateTime": "not-a-date"},
            {"startDateTime": VISIT_1["startDateTime"]},
        ],
        "vaccinationRecord": [{"vaccinationId": "V", "date": 20260901}, {"date": "2026-09-01"}],
    }
    visit = {k: v for k, v in VISIT_1.items() if k != "encounterIdentifier"}
    patient = PatientFullRecord.model_validate(
        _payload(medicalHistory=[visit], vaccinationRecord=[_vaccine()])
    )
    derived = patient.medicalHistory[0].encounterIdentifier

    adopt_stored_item_ids(patient, stored)
    adopt_stored_item_ids(patient, None)

    assert patient.medicalHistory[0].encounterIdentifier == derived
    assert patient.vaccinationRecord[0].vaccinationId.startswith(DERIVED_ID_PREFIX)


def test_bad_stored_dates_are_ignored():
    stored = {"vaccinationRecord": [{"vaccinationId": "V", "date": "31/12/2026"}]}
    patient = PatientFullRecord.model_validate(_payload(vaccinationRecord=[_vaccine()]))

    adopt_stored_item_ids(patient, stored)

    assert patient.vaccinationRecord[0].vaccinationId.startswith(DERIVED_ID_PREFIX)


# ---------------------------------------------------------------------------
# Stale offline copy (poc05)
# ---------------------------------------------------------------------------


def test_stale_payload_keeps_allergy_and_current_bracelet(client, db_session):
    """poc05: an old offline copy erased an allergy and re-activated the lost
    bracelet; now it only contributes its new visit."""
    old_copy = _payload(allergies=[])
    assert _sync(client, old_copy).status_code == 201

    # Device 2: the bracelet was lost and replaced, and an allergy recorded.
    replaced = _payload(
        device_uid="UID-PULSERA-NUEVA",
        retiredDeviceReason="lost",
        allergies=[{"category": "01", "allergen": "Penicilina"}],
    )
    assert _sync(client, replaced).status_code == 201

    # Device 1 comes back online with its old copy plus a new visit.
    old_copy["medicalHistory"] = [deepcopy(VISIT_1)]
    response = _sync(client, old_copy)

    assert response.status_code == 201
    assert response.json()["conflicts"] == ["stale_payload_retired_device_uid"]
    patient = _stored(db_session)
    assert patient.device_uid == "UID-PULSERA-NUEVA"
    record = patient.full_record_json
    assert record["device_uid"] == "UID-PULSERA-NUEVA"
    assert [a["allergen"] for a in record["allergies"]] == ["Penicilina"]
    assert [v["encounterIdentifier"] for v in record["medicalHistory"]] == ["enc-visit-001"]

    headers = {"X-Guardian-Device-UID": "GUARDIAN-UID-001"}
    assert client.get("/api/v1/patients/scan/UID-PULSERA-NUEVA", headers=headers).status_code == 200
    assert client.get("/api/v1/patients/scan/04:A2:TEST:UID", headers=headers).status_code == 410


def test_stale_guardian_card_does_not_come_back(client, db_session):
    assert _sync(client, _payload()).status_code == 201
    new_card = _payload()
    new_card["guardianInfo"]["device_uid"] = "GUARDIAN-UID-002"
    assert _sync(client, new_card).status_code == 201

    response = _sync(client, _payload())  # still carries GUARDIAN-UID-001

    assert response.json()["conflicts"] == ["stale_payload_retired_device_uid"]
    guardian = _stored(db_session).full_record_json["guardianInfo"]
    assert guardian["device_uid"] == "GUARDIAN-UID-002"
    retired = db_session.query(RetiredDeviceUid.device_uid).all()
    assert [row[0] for row in retired] == ["GUARDIAN-UID-001"]


def test_new_patient_on_a_retired_bracelet_is_refused(client, db_session):
    db_session.add(RetiredDeviceUid(
        device_uid="UID-PERDIDA", patient_id="otro-paciente", reason="lost", device_role="patient",
    ))
    db_session.commit()

    response = _sync(client, _payload(device_uid="UID-PERDIDA"))

    assert response.status_code == 409
    assert response.json()["code"] == "device_retired"
    assert db_session.query(Patient).count() == 0


def test_stale_merge_unites_background_lists():
    server = {
        "chronicConditions": [{"chronicDescription": "Asma"}],
        "familyHistory": [{"conditionDescription": "Diabetes", "relationship": "04"}],
        "medications": [{"medicationName": "Salbutamol"}],
        "personalHistory": "Prematuro",
        "familyHistoryNotes": None,
    }
    incoming = {
        "chronicConditions": [{"chronicDescription": " asma "}, {"chronicDescription": "Epilepsia"}],
        "familyHistory": [],
        "medications": ["not-a-dict"],
        "personalHistory": "",
        "familyHistoryNotes": "Abuela",
    }

    merged = _union_background(server, incoming)

    assert [c["chronicDescription"] for c in merged["chronicConditions"]] == ["Asma", "Epilepsia"]
    assert len(merged["familyHistory"]) == 1
    assert merged["medications"] == [{"medicationName": "Salbutamol"}]
    assert merged["personalHistory"] == "Prematuro"
    assert merged["familyHistoryNotes"] == "Abuela"
    assert _union_background(None, incoming) is incoming
    assert _union_background(server, None) is server


def test_row_lock_is_requested_for_the_upsert(db_session):
    """The lock is what serialises concurrent syncs on PostgreSQL (poc15);
    SQLite ignores FOR UPDATE, so only the query path is exercised here."""
    create_or_update_patient(db_session, PatientFullRecord.model_validate(_payload()), "org-1")

    found = _find_patient_for_sync(db_session, "TEST-UNIT-001", None, lock=True)
    by_tag = _find_patient_for_sync(db_session, "OTRO", "04:A2:TEST:UID", lock=True)

    assert found is not None and by_tag is not None and found.id == by_tag.id


# ---------------------------------------------------------------------------
# Tag-resolved syncs (poc04)
# ---------------------------------------------------------------------------


def _attacker_payload(**patient_info):
    payload = _payload(patientId="PID-ATACANTE")
    payload["patientInfo"]["identification"]["documentNumber"] = ""
    payload["patientInfo"].update(patient_info)
    payload["guardianInfo"] = {
        "name": "Falso Acudiente", "relationship": "Tio", "phone": "3000000000",
        "device_uid": "UID-TARJETA-ATACANTE",
    }
    return payload


def test_guardian_card_cannot_be_replaced_with_only_the_bracelet_uid(client, db_session):
    """poc04: an empty document let anyone who read the bracelet UID install
    their own card as the guardian's second factor."""
    assert _sync(client, _payload()).status_code == 201

    response = _sync(client, _attacker_payload())

    assert response.status_code == 409
    assert response.json()["code"] == "identity_mismatch"
    legit = client.get("/api/v1/patients/scan/04:A2:TEST:UID", headers=GUARDIAN_HEADER)
    assert legit.status_code == 200
    assert legit.json()["guardianInfo"]["name"] == "María Pérez"


def test_same_child_resolved_by_tag_adds_visits_but_keeps_guardians(client, db_session):
    assert _sync(client, _payload()).status_code == 201
    payload = _payload(patientId="PID-OTRO-DISPOSITIVO", medicalHistory=[deepcopy(VISIT_1)])
    payload["guardianInfo"] = _attacker_payload()["guardianInfo"]

    response = _sync(client, payload)

    assert response.status_code == 201
    assert response.json()["conflicts"] == ["guardians_not_changed_by_tag_resolved_sync"]
    record = _stored(db_session).full_record_json
    assert record["guardianInfo"]["device_uid"] == "GUARDIAN-UID-001"
    assert len(record["medicalHistory"]) == 1
    assert db_session.query(RetiredDeviceUid).count() == 0


def test_tag_resolved_sync_with_same_guardians_reports_nothing(client, db_session):
    assert _sync(client, _payload()).status_code == 201

    response = _sync(client, _payload(patientId="PID-OTRO-DISPOSITIVO"))

    assert response.status_code == 201
    assert response.json()["conflicts"] == []


@pytest.mark.parametrize(
    ("doc_type", "doc_number", "patient_info", "same"),
    [
        ("PT", "VZ-9876543", {}, True),
        ("PT", "VZ 9876543", {}, True),
        ("PT", "OTRO-123", {}, False),
        # A stored real document is never matched by an empty one, even with
        # the right birth date and names.
        ("PT", "", {}, False),
    ],
)
def test_identity_check_for_tag_resolved_syncs(db_session, doc_type, doc_number, patient_info, same):
    create_or_update_patient(db_session, PatientFullRecord.model_validate(_payload()), "org-1")
    _assert_identity_check(db_session, doc_type, doc_number, patient_info, same)


@pytest.mark.parametrize(
    ("doc_type", "doc_number", "patient_info", "same"),
    [
        ("MS", "LOCAL-2", {}, True),
        ("PT", "", {}, True),
        ("MS", "LOCAL-2", {"dob": "2019-01-01"}, False),
        ("MS", "LOCAL-2", {"firstName": "Otro"}, False),
    ],
)
def test_undocumented_patients_fall_back_to_birth_date_and_names(
    db_session, doc_type, doc_number, patient_info, same
):
    stored = _payload()
    stored["patientInfo"]["identification"] = {"documentType": "MS", "documentNumber": "LOCAL-1"}
    create_or_update_patient(db_session, PatientFullRecord.model_validate(stored), "org-1")
    _assert_identity_check(db_session, doc_type, doc_number, patient_info, same)


def _assert_identity_check(db_session, doc_type, doc_number, patient_info, same):
    payload = _payload(patientId="PID-OTRO-DISPOSITIVO")
    payload["patientInfo"]["identification"] = {"documentType": doc_type, "documentNumber": doc_number}
    payload["patientInfo"].update(patient_info)
    record = PatientFullRecord.model_validate(payload)

    if same:
        create_or_update_patient(db_session, record, "org-2")
    else:
        with pytest.raises(IdentityMismatchError):
            create_or_update_patient(db_session, record, "org-2")


# ---------------------------------------------------------------------------
# Consent evidence (poc09)
# ---------------------------------------------------------------------------


def _guardian(name, doc=None, uid=None, consent=None):
    guardian = {"name": name, "relationship": "Tia", "phone": "300"}
    if doc:
        guardian["documentNumber"] = doc
    if uid:
        guardian["device_uid"] = uid
    if consent:
        guardian["consent"] = consent
    return guardian


SIGNED = {"accepted": True, "acceptedAt": "2026-09-22T10:00:00", "signatureBase64": "FIRMA-UNO"}


def test_signature_not_carried_to_a_different_guardian():
    server = {"guardianInfo": _guardian("Acudiente Uno", doc="ACU-0001", consent=SIGNED)}
    inherited = {k: v for k, v in SIGNED.items() if k != "signatureBase64"}
    incoming = {"guardianInfo": _guardian("Acudiente Dos", doc="ACU-0002", consent=inherited)}

    merged = merge_patient_records(server, incoming)

    assert merged["guardianInfo"]["consent"] is None


def test_new_guardian_keeps_their_own_consent():
    server = {"guardianInfo": _guardian("Acudiente Uno", doc="ACU-0001", consent=SIGNED)}
    own = {"accepted": True, "acceptedAt": "2026-09-23T08:00:00"}
    incoming = {"guardianInfo": _guardian("Acudiente Dos", doc="ACU-0002", consent=own)}

    merged = merge_patient_records(server, incoming)

    assert merged["guardianInfo"]["consent"] == own


def test_same_guardian_resync_keeps_the_signature():
    server = {"guardianInfo": _guardian("Acudiente Uno", uid="CARD-1", consent=SIGNED)}
    incoming = {"guardianInfo": _guardian("Acudiente Uno", uid="CARD-1")}

    merged = merge_patient_records(server, incoming)

    assert merged["guardianInfo"]["consent"]["signatureBase64"] == "FIRMA-UNO"


@pytest.mark.parametrize(
    ("server", "incoming", "same"),
    [
        (_guardian("A", doc="1"), _guardian("B", doc="1"), True),
        (_guardian("A", doc="1"), _guardian("A", doc="2"), False),
        (_guardian("A", uid="C1"), _guardian("B", uid="C1"), True),
        (_guardian("A", uid="C1"), _guardian("A", uid="C2"), False),
        (_guardian("Ana María"), _guardian(" ana  maría "), True),
        (_guardian(""), _guardian(""), False),
    ],
)
def test_same_guardian_uses_the_strongest_shared_identifier(server, incoming, same):
    assert _same_guardian(server, incoming) is same
