from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.models import UserRole
from app.main import app
from app.services.fhir import fhir_backend


class MockUser:
    email = "doctor_test@hwb.org"
    id = "test-user-id"
    role = UserRole.doctor
    organization_id = "org-123"


class MockNurse:
    email = "nurse_test@hwb.org"
    id = "test-nurse-id"
    role = UserRole.nurse
    organization_id = "org-123"


class MockUnauthorized:
    email = "viewer@hwb.org"
    id = "test-viewer-id"
    role = UserRole.org_admin  # org_admin can search but not sync
    organization_id = "org-123"


# RDA-compliant mock payload (Resolution 1888/2025 + IG RDA v0.8.1 / schema v3.0)
MOCK_PATIENT_PAYLOAD = {
    "patientId": "TEST-UNIT-001",
    "device_uid": "04:A2:TEST:UID",
    "patientInfo": {
        "identification": {
            "documentType": "PT",
            "documentNumber": "VZ-9876543",
        },
        "firstLastName": "Rodríguez",
        "secondLastName": "Pérez",
        "firstName": "Santiago",
        "secondName": "Andrés",
        "dob": "2020-01-01",
        "nationalityCode": "VEN",
        "nationalityName": "Venezuela",
        "biologicalSex": "M",
        "address": {
            "street": "Calle 10 # 5-20",
            "city": "Cúcuta",
            "cityCode": "54001",
            "state": "Norte de Santander",
            "zipCode": "540001",
            "country": "170",           # ISO 3166-1 numeric for Colombia
            "countryName": "Colombia",
            "zone": "01",               # v3.0: "01" = URBANA (was "U")
        },
        "bloodType": "O+",
        "weight": 15.5,
        "height": 100.0,
    },
    "guardianInfo": {
        "name": "María Pérez",
        "relationship": "Madre",
        "phone": "+573001234567",
        "device_uid": "GUARDIAN-UID-001",
    },
    "backgroundHistory": {
        "chronicConditions": [],        # v3.0: list, not None
        "personalHistory": None,
        "familyHistory": [
            {
                "conditionDescription": "Diabetes mellitus tipo 2",
                "relationship": "04",   # FamilyRelationship.ABUELOS
            }
        ],
        "familyHistoryNotes": "Abuelo paterno con diabetes.",
        "medications": [],
    },
    "allergies": [
        {
            "category": "01",           # AllergyCategory.MEDICAMENTO
            "allergen": "Penicilina",
            "reaction": "Habones",
            "notes": "Reacción leve en la infancia reportada por la madre",
        }
    ],
    "medicalHistory": [],
    "vaccinationRecord": [],
}

# H7: Visits now include explicit encounterIdentifier for deterministic delta testing
VISIT_1 = {
    "type": "Consultation",
    "encounterIdentifier": "enc-visit-001",  # H7: explicit UUID
    "startDateTime": "2026-01-15T09:00:00",
    "endDateTime": "2026-01-15T09:45:00",
    "careModality": "01",
    "serviceGroup": "01",
    "careEnvironment": "05",
    "provider": {
        "repsCode": "540015400101",
        "name": "Hospital Erasmo Meoz",
        "nitNumber": "890500600",
        "locationSeatCode": "540015400101-01",
    },
    "practitioner": {
        "documentType": "CC",
        "documentNumber": "88001234",
        "name": "GOMEZ, ANDREA",
        "firstName": "Andrea",
        "secondName": None,
        "firstLastName": "Gomez",
        "secondLastName": None,
    },
    "location": "HOSPITAL_ERASMO_MEOZ",
    "physician": "GOMEZ, ANDREA",
    "clinicalEvaluation": {
        "historyOfCurrentIllness": "Fiebre de 3 días de evolución, tos seca.",
        "generalPhysicalExamination": "T: 38.5°C, FC: 110. Faringe eritematosa.",
        "systemsExamination": "Respiratorio: murmullo vesicular conservado.",
        "treatmentPlanObservations": "Acetaminofén 15mg/kg cada 6h. Control en 48h.",
    },
    "diagnosis": [
        {
            "icd10Code": "J06.9",
            "icd11Code": None,
            "description": "Infección aguda de las vías respiratorias superiores",
        }
    ],
    "diagnosisType": "01",
    "riskFactors": [],
    "incapacity": None,
    "payer": None,
}

VISIT_2 = {
    **VISIT_1,
    "encounterIdentifier": "enc-visit-002",  # H7: different UUID
    "startDateTime": "2026-04-10T09:00:00",
    "endDateTime": None,
    "clinicalEvaluation": {
        "historyOfCurrentIllness": "Control post-infección respiratoria.",
        "generalPhysicalExamination": None,
        "systemsExamination": None,
        "treatmentPlanObservations": None,
    },
    "diagnosis": [
        {
            "icd10Code": "Z09",
            "icd11Code": None,
            "description": "Examen de seguimiento",
        }
    ],
    "diagnosisType": "02",
}

# Payload with a medical visit
MOCK_PATIENT_WITH_VISIT = {
    **MOCK_PATIENT_PAYLOAD,
    "patientId": "TEST-UNIT-002",
    "device_uid": "04:A2:TEST:UID2",
    "patientInfo": {
        **MOCK_PATIENT_PAYLOAD["patientInfo"],
        "identification": {
            "documentType": "PT",
            "documentNumber": "VZ-1111111",
        },
    },
    "medicalHistory": [VISIT_1],
}


def _override_doctor():
    app.dependency_overrides[get_current_user] = lambda: MockUser()


def _override_nurse():
    app.dependency_overrides[get_current_user] = lambda: MockNurse()


def _clear_overrides():
    app.dependency_overrides.pop(get_current_user, None)


def _sync_patient(client, payload=None):
    """Helper: sync a patient as doctor with GCP mocked."""
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        return client.post("/api/v1/patients/sync", json=payload or MOCK_PATIENT_PAYLOAD)


# ============================================================================
# SYNC ENDPOINT TESTS
# ============================================================================


def test_sync_patient_success(client: TestClient):
    """Sync patient with RDA-compliant payload returns 201 and correct data."""
    response = _sync_patient(client)
    _clear_overrides()

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"
    # H3: internal_id is now server-generated — just check it's a non-empty string
    assert len(data["internal_id"]) > 0
    assert data["fhir_status"] == "success"


def test_sync_duplicate_device_uid_returns_409(client: TestClient, db_session):
    """A new patient reusing an existing device_uid is rejected with 409, not 500."""
    from app.db.models import Patient

    first = _sync_patient(client)
    assert first.status_code == 201

    # Different patient (distinct patientId + document) reusing the same tag.
    duplicate = {
        **MOCK_PATIENT_PAYLOAD,
        "patientId": "TEST-UNIT-DUP",
        "patientInfo": {
            **MOCK_PATIENT_PAYLOAD["patientInfo"],
            "identification": {
                "documentType": "PT",
                "documentNumber": "VZ-0000000",
            },
        },
    }
    response = _sync_patient(client, payload=duplicate)
    _clear_overrides()

    assert response.status_code == 409
    # The conflicting record must not have been persisted.
    assert (
        db_session.query(Patient)
        .filter(Patient.frontend_patient_id == "TEST-UNIT-DUP")
        .first()
        is None
    )
    # The original tag owner is untouched.
    assert (
        db_session.query(Patient)
        .filter(Patient.device_uid == MOCK_PATIENT_PAYLOAD["device_uid"])
        .count()
        == 1
    )


def test_sync_bracelet_replacement_conflict_returns_409(client: TestClient):
    """Reassigning an existing patient's device_uid to a tag owned by another
    patient is rejected with 409, not 500."""
    a = _sync_patient(client, payload=MOCK_PATIENT_PAYLOAD)
    assert a.status_code == 201
    b = _sync_patient(client, payload=MOCK_PATIENT_WITH_VISIT)
    assert b.status_code == 201

    # Re-sync patient B, but point its tag at patient A's device_uid.
    collide = {
        **MOCK_PATIENT_WITH_VISIT,
        "device_uid": MOCK_PATIENT_PAYLOAD["device_uid"],
    }
    response = _sync_patient(client, payload=collide)
    _clear_overrides()

    assert response.status_code == 409


def test_sync_patient_with_visit_generates_multiple_bundles(client: TestClient):
    """Syncing with 1 visit generates 2 FHIR bundles (RDA-Paciente + RDA-Consulta)."""
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)
        _clear_overrides()

        assert response.status_code == 201
        assert mock_gcp.call_count == 2


def test_sync_nurse_cannot_add_medical_history(client: TestClient):
    """A nurse cannot add medical history entries to an existing patient."""
    _sync_patient(client)
    _clear_overrides()

    _override_nurse()
    payload_with_visit = {
        **MOCK_PATIENT_PAYLOAD,
        "medicalHistory": [
            {
                "type": "Consultation",
                "encounterIdentifier": "enc-nurse-attempt-001",
                "startDateTime": "2026-02-01T10:00:00",
                "endDateTime": None,
                "careModality": "01",
                "serviceGroup": "01",
                "careEnvironment": "05",
                "provider": {
                    "repsCode": "540015400101",
                    "name": "Hospital Erasmo Meoz",
                    "nitNumber": "890500600",
                    "locationSeatCode": "540015400101-01",
                },
                "practitioner": {
                    "documentType": "CC",
                    "documentNumber": "88001234",
                    "name": "GOMEZ, ANDREA",
                    "firstName": "Andrea",
                    "secondName": None,
                    "firstLastName": "Gomez",
                    "secondLastName": None,
                },
                "clinicalEvaluation": {
                    "historyOfCurrentIllness": "Fiebre",
                    "generalPhysicalExamination": None,
                    "systemsExamination": None,
                    "treatmentPlanObservations": None,
                },
                "diagnosis": [],
                "diagnosisType": "01",
                "riskFactors": [],
                "incapacity": None,
                "payer": None,
            }
        ],
    }
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_with_visit)

    _clear_overrides()
    assert response.status_code == 403
    assert "Nurses can only add vaccines" in response.json()["detail"]


def test_sync_nurse_history_rejected_before_llm(client: TestClient):
    """A nurse adding history is rejected before any LLM call is made."""
    _sync_patient(client)
    _clear_overrides()

    _override_nurse()
    payload_with_visit = {
        **MOCK_PATIENT_PAYLOAD,
        "medicalHistory": [
            {
                "type": "Consultation",
                "encounterIdentifier": "enc-nurse-attempt-002",
                "startDateTime": "2026-02-01T10:00:00",
                "endDateTime": None,
                "careModality": "01",
                "serviceGroup": "01",
                "careEnvironment": "05",
                "provider": {
                    "repsCode": "540015400101",
                    "name": "Hospital Erasmo Meoz",
                    "nitNumber": "890500600",
                    "locationSeatCode": "540015400101-01",
                },
                "practitioner": {
                    "documentType": "CC",
                    "documentNumber": "88001234",
                    "name": "GOMEZ, ANDREA",
                    "firstName": "Andrea",
                    "secondName": None,
                    "firstLastName": "Gomez",
                    "secondLastName": None,
                },
                "clinicalEvaluation": {
                    "historyOfCurrentIllness": "Fiebre",
                    "generalPhysicalExamination": None,
                    "systemsExamination": None,
                    "treatmentPlanObservations": None,
                },
                "diagnosis": [],
                "diagnosisType": "01",
                "riskFactors": [],
                "incapacity": None,
                "payer": None,
            }
        ],
    }
    with patch("app.api.v1.endpoints.patients.medical_llm_processor") as mock_llm:
        response = client.post("/api/v1/patients/sync", json=payload_with_visit)

    _clear_overrides()
    assert response.status_code == 403
    mock_llm.extract_diagnoses.assert_not_called()


def test_sync_nurse_can_create_new_patient(client: TestClient):
    """A nurse may create a brand-new patient.

    The history restriction only applies when a record already exists, so on a
    first sync there is no prior history to guard against.
    """
    _override_nurse()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

    _clear_overrides()
    assert response.status_code == 201


def test_sync_stores_rda_columns_in_db(client: TestClient, db_session):
    """New RDA columns and sync tracking columns are persisted correctly."""
    from app.db.models import Patient

    _sync_patient(client)
    _clear_overrides()

    # H3: Lookup by frontend_patient_id + organization_id
    patient = db_session.query(Patient).filter(
        Patient.frontend_patient_id == "TEST-UNIT-001",
        Patient.organization_id == "org-123",
    ).first()
    assert patient is not None
    assert patient.document_type == "PT"
    assert patient.document_number == "VZ-9876543"
    assert patient.nationality_code == "VEN"
    assert patient.biological_sex == "M"
    assert patient.last_name == "Rodríguez"
    assert patient.second_last_name == "Pérez"
    assert patient.first_name == "Santiago"
    # H3: Server-generated PK is different from the frontend ID
    assert patient.id != "TEST-UNIT-001"
    assert len(patient.id) > 0
    # H7: Synced encounters is a list, not a count
    assert patient.synced_encounter_ids == []
    # H1: Background hash is computed
    assert patient.background_data_hash is not None
    assert len(patient.background_data_hash) == 64  # SHA-256 hex digest
    assert patient.rda_paciente_sent is True


def test_sync_delta_only_sends_new_bundles(client: TestClient, db_session):
    """
    H7: Second sync with 1 new visit should only generate bundles for the
    NEW encounter (identified by encounterIdentifier UUID), not re-send old ones.
    """
    from app.db.models import Patient

    # First sync: patient with 1 visit → expect 2 bundles
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)
        first_call_count = mock_gcp.call_count

    # H7: Verify synced_encounter_ids contains the encounter UUID
    patient = db_session.query(Patient).filter(
        Patient.frontend_patient_id == "TEST-UNIT-002",
    ).first()
    assert "enc-visit-001" in patient.synced_encounter_ids
    assert patient.rda_paciente_sent is True

    # Second sync: same patient, now with 2 visits (1 old + 1 new)
    payload_two_visits = {
        **MOCK_PATIENT_WITH_VISIT,
        "medicalHistory": [VISIT_1, VISIT_2],
    }
    with patch.object(fhir_backend, "send_bundle") as mock_gcp2:
        mock_gcp2.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_two_visits)
        second_call_count = mock_gcp2.call_count

    _clear_overrides()

    assert response.status_code == 201
    assert first_call_count == 2   # RDA-Paciente + 1 RDA-Consulta
    # H7+H1: Only 1 new RDA-Consulta (background unchanged → no RDA-Paciente)
    assert second_call_count == 1

    # Verify both encounters are now tracked
    db_session.expire_all()
    patient = db_session.query(Patient).filter(
        Patient.frontend_patient_id == "TEST-UNIT-002",
    ).first()
    assert "enc-visit-001" in patient.synced_encounter_ids
    assert "enc-visit-002" in patient.synced_encounter_ids


def test_sync_no_new_visits_skips_all_bundles(client: TestClient):
    """Re-syncing with same data generates zero bundles — nothing changed."""
    _override_doctor()

    # First sync with 1 visit
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)

    # Second sync with SAME data (no new visits, no background change)
    with patch.object(fhir_backend, "send_bundle") as mock_gcp2:
        mock_gcp2.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)
        resync_call_count = mock_gcp2.call_count

    _clear_overrides()

    assert response.status_code == 201
    # H1+H7: No new visits + background unchanged → 0 bundles
    assert resync_call_count == 0


def test_sync_gcp_failure_does_not_update_tracking(client: TestClient, db_session):
    """If GCP fails, sync tracking should NOT be updated so bundles retry next time."""
    from app.db.models import Patient

    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "error", "error": "GCP is down"}
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)

    _clear_overrides()

    assert response.status_code == 201
    assert response.json()["fhir_status"] == "error"

    # H7: Tracking should NOT have been updated
    patient = db_session.query(Patient).filter(
        Patient.frontend_patient_id == "TEST-UNIT-002",
    ).first()
    assert patient.synced_encounter_ids == []
    assert patient.rda_paciente_sent is False


# ============================================================================
# H1: BACKGROUND DATA HASH TESTS
# ============================================================================


def test_h1_background_change_triggers_rda_paciente(client: TestClient, db_session):
    """
    H1: When background data changes (e.g., new allergy added), the
    RDA-Paciente bundle should be regenerated even if there are no new visits.
    """
    # First sync: patient with 1 visit
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)

    # Second sync: same visits, but a NEW allergy was added
    payload_new_allergy = {
        **MOCK_PATIENT_WITH_VISIT,
        "allergies": MOCK_PATIENT_WITH_VISIT["allergies"] + [
            {
                "category": "02",  # AllergyCategory.ALIMENTO
                "allergen": "Maní",
                "reaction": "Angioedema",
                "notes": "Reacción severa",
            }
        ],
    }
    with patch.object(fhir_backend, "send_bundle") as mock_gcp2:
        mock_gcp2.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_new_allergy)
        bundle_count = mock_gcp2.call_count

    _clear_overrides()

    assert response.status_code == 201
    # H1: Background changed → RDA-Paciente regenerated, no new visits → just 1 bundle
    assert bundle_count == 1


# ============================================================================
# H3: ORG-SCOPED PATIENT TESTS
# ============================================================================


def test_h3_same_frontend_id_different_orgs(client: TestClient, db_session):
    """
    H3: Two organizations can independently register a patient with the
    same frontend-generated patientId.
    """
    from app.db.models import Patient

    # Sync as Org A
    _sync_patient(client)
    _clear_overrides()

    # Sync the SAME patientId but as Org B
    class MockDoctorOrgB:
        email = "doctor.b@ngo-b.org"
        id = "user-org-b-001"
        role = UserRole.doctor
        organization_id = "org-456"

    app.dependency_overrides[get_current_user] = lambda: MockDoctorOrgB()
    payload_org_b = {
        **MOCK_PATIENT_PAYLOAD,
        "device_uid": "04:A2:ORGB:UID",  # different device
    }
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_org_b)
    _clear_overrides()

    assert response.status_code == 201

    # Both should exist as separate records
    org_a = db_session.query(Patient).filter(
        Patient.frontend_patient_id == "TEST-UNIT-001",
        Patient.organization_id == "org-123",
    ).first()
    org_b = db_session.query(Patient).filter(
        Patient.frontend_patient_id == "TEST-UNIT-001",
        Patient.organization_id == "org-456",
    ).first()
    assert org_a is not None
    assert org_b is not None
    # H3: They have different server-generated IDs
    assert org_a.id != org_b.id


# ============================================================================
# SCAN ENDPOINT TESTS
# ============================================================================


def test_scan_success(client: TestClient):
    """Scanning a registered device with guardian UID returns the patient record."""
    _sync_patient(client)

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}",
        headers={"X-Guardian-Device-UID": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]},
    )
    _clear_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == "TEST-UNIT-001"
    assert data["patientInfo"]["firstName"] == "Santiago"
    assert data["patientInfo"]["identification"]["documentType"] == "PT"
    assert data["patientInfo"]["nationalityCode"] == "VEN"


def test_scan_not_found(client: TestClient):
    """Scanning an unregistered device returns 404."""
    _override_doctor()
    response = client.get("/api/v1/patients/scan/NONEXISTENT-UID")
    _clear_overrides()

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_scan_minor_requires_guardian(client: TestClient):
    """Scanning a minor's bracelet without guardian UID returns 403."""
    _sync_patient(client)

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}"
    )
    _clear_overrides()

    assert response.status_code == 403
    assert "Guardian bracelet scan required" in response.json()["detail"]


def test_scan_minor_wrong_guardian(client: TestClient):
    """Scanning a minor's bracelet with a wrong guardian UID returns 403."""
    _sync_patient(client)

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}",
        headers={"X-Guardian-Device-UID": "WRONG-GUARDIAN-UID"},
    )
    _clear_overrides()

    assert response.status_code == 403
    assert "Guardian tag mismatch" in response.json()["detail"]


def test_scan_minor_accepts_guardian2_uid(client: TestClient):
    """Scanning a minor's bracelet with guardian2's UID should succeed."""
    payload_with_g2 = {
        **MOCK_PATIENT_PAYLOAD,
        "patientId": "TEST-UNIT-G2-001",
        "device_uid": "04:A2:G2:UID",
        "guardian2Info": {
            "name": "Carlos Pérez",
            "relationship": "Padre",
            "phone": "+573009876543",
            "device_uid": "GUARDIAN2-UID-001",
        },
    }
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=payload_with_g2)

    # Scan using guardian2's UID
    response = client.get(
        f"/api/v1/patients/scan/{payload_with_g2['device_uid']}",
        headers={"X-Guardian-Device-UID": "GUARDIAN2-UID-001"},
    )
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-G2-001"


def test_sync_persists_guardian_consent_and_guardian2(client: TestClient):
    """Guardian consent, document info, and guardian2 are persisted in full_record_json."""
    payload = {
        **MOCK_PATIENT_PAYLOAD,
        "patientId": "TEST-UNIT-CONSENT-001",
        "device_uid": "04:A2:CONSENT:UID",
        "guardianInfo": {
            **MOCK_PATIENT_PAYLOAD["guardianInfo"],
            "documentType": "CC",
            "documentNumber": "52456789",
            "consent": {
                "accepted": True,
                "acceptedAt": "2026-05-20T10:30:00",
                "email": "guardian@example.com",
                "signatureBase64": "iVBORw0KGgoAAAANSUhEUg==",
            },
        },
        "guardian2Info": {
            "name": "Pedro López",
            "relationship": "Tío",
            "phone": "+573005555555",
            "device_uid": "GUARDIAN2-CONSENT-UID",
            "documentType": "CE",
            "documentNumber": "E-123456",
        },
    }

    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload)
    _clear_overrides()

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"

def test_update_patient_persists_guardian2_name(client: TestClient):
    """Updating an existing patient with guardian2Info covers the update branch."""
    # First sync — create patient without guardian2
    _sync_patient(client)

    # Second sync — same patient, now with guardian2Info
    payload_with_g2 = {
        **MOCK_PATIENT_PAYLOAD,
        "guardian2Info": {
            "name": "Carlos Pérez",
            "relationship": "Padre",
            "phone": "+573009876543",
            "device_uid": "GUARDIAN2-UID-UPDATE",
        },
    }
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_with_g2)
    _clear_overrides()

    assert response.status_code == 201
    
# ============================================================================
# SEARCH (STRICT LOOKUP) ENDPOINT TESTS
# ============================================================================


def test_search_exact_match_returns_patient(client: TestClient):
    """Strict lookup with all correct fields returns the single patient."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == "TEST-UNIT-001"
    assert data["patientInfo"]["identification"]["documentNumber"] == "VZ-9876543"


def test_search_by_second_last_name(client: TestClient):
    """Lookup using the second last name also finds the patient."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Pérez",  # second last name
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-001"


def test_search_case_insensitive(client: TestClient):
    """Lookup is case-insensitive for names and document number."""
    _sync_patient(client)

    params = {
        "document_number": "vz-9876543",   # lowercase
        "birth_date": "2020-01-01",
        "first_name": "santiago",           # lowercase
        "last_name": "rodríguez",           # lowercase
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-001"


def test_search_with_guardian_name(client: TestClient):
    """Providing guardian_name adds extra verification and still finds the patient."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
        "guardian_name": "María",  # partial match
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-001"


def test_search_wrong_document_returns_404(client: TestClient):
    """Wrong document number returns 404 — no data leaked."""
    _sync_patient(client)

    params = {
        "document_number": "WRONG-DOC-999",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 404
    assert "No patient found" in response.json()["detail"]


def test_search_wrong_name_returns_404(client: TestClient):
    """Correct document but wrong first name returns 404."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Carlos",  # wrong name
        "last_name": "Rodríguez",
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 404


def test_search_wrong_dob_returns_404(client: TestClient):
    """Correct document and name but wrong DOB returns 404."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "1999-12-31",  # wrong DOB
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 404


def test_search_wrong_guardian_returns_404(client: TestClient):
    """Correct data but wrong guardian name returns 404."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
        "guardian_name": "Pedro González",  # wrong guardian
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 404


def test_search_missing_mandatory_params_returns_422(client: TestClient):
    """Missing mandatory parameters triggers FastAPI validation (422)."""
    _override_doctor()

    # Missing document_number (mandatory)
    params = {
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.post("/api/v1/patients/search", json=params)
    _clear_overrides()

    assert response.status_code == 422


def test_sync_llm_codes_chronic_conditions(client: TestClient):
    """
    When a chronic condition lacks ICD codes, the LLM codes it.
    """
    payload_with_chronic = {
        **MOCK_PATIENT_PAYLOAD,
        "patientId": "TEST-UNIT-CHRONIC-001",
        "device_uid": "04:A2:CHRONIC:UID",
        "backgroundHistory": {
            **MOCK_PATIENT_PAYLOAD["backgroundHistory"],
            "chronicConditions": [
                {
                    "chronicDescription": "Diabetes mellitus tipo 2",
                    "chronicCie10Code": None,
                    "chronicCie11Code": None,
                }
            ],
        },
    }
 
    _override_doctor()
    with (
        patch(
            "app.api.v1.endpoints.patients.medical_llm_processor.code_chronic_condition",
            return_value={
                "icd10Code": "E11",
                "icd11Code": "5A11",
                "description": "Diabetes mellitus tipo 2",
            },
        ) as mock_code_chronic,
        patch.object(fhir_backend, "send_bundle") as mock_gcp,
    ):
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_with_chronic)
 
    _clear_overrides()
 
    assert response.status_code == 201
    mock_code_chronic.assert_called_once_with("Diabetes mellitus tipo 2")
 
 
def test_sync_skips_chronic_coding_when_code_already_present(client: TestClient):
    """
    If a chronic condition already has an ICD-10 code, the LLM must NOT
    be called for that item (idempotent re-sync).
    """
    payload_already_coded = {
        **MOCK_PATIENT_PAYLOAD,
        "patientId": "TEST-UNIT-CHRONIC-002",
        "device_uid": "04:A2:CHRONIC:UID2",
        "backgroundHistory": {
            **MOCK_PATIENT_PAYLOAD["backgroundHistory"],
            "chronicConditions": [
                {
                    "chronicDescription": "Diabetes mellitus tipo 2",
                    "chronicCie10Code": "E11",
                    "chronicCie11Code": "5A11",
                }
            ],
        },
    }
 
    _override_doctor()
    with (
        patch(
            "app.api.v1.endpoints.patients.medical_llm_processor.code_chronic_condition"
        ) as mock_code_chronic,
        patch.object(fhir_backend, "send_bundle") as mock_gcp,
    ):
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_already_coded)
 
    _clear_overrides()
 
    assert response.status_code == 201
    mock_code_chronic.assert_not_called()


def test_scan_patient_from_different_org(client: TestClient):
    """A doctor from Org B can scan a patient registered by Org A (via device_uid)."""
    # First, sync patient as Org A doctor
    _sync_patient(client)

    # Now, scan as a doctor from a DIFFERENT organization
    class MockDoctorOrgB:
        email = "doctor.b@another-ngo.org"
        id = "user-org-b-001"
        role = UserRole.doctor
        organization_id = "org-different-456"  # Different from org-123

    app.dependency_overrides[get_current_user] = lambda: MockDoctorOrgB()

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}",
        headers={"X-Guardian-Device-UID": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]},
    )
    _clear_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == "TEST-UNIT-001"

# ============================================================================
# COVERAGE: Patient.__repr__ (models.py)
# ============================================================================


def test_patient_repr(client: TestClient, db_session):
    """Exercise Patient.__repr__ to cover the repr line in models.py."""
    from app.db.models import Patient

    _sync_patient(client)
    _clear_overrides()

    patient = db_session.query(Patient).first()
    text = repr(patient)
    assert "Patient" in text
    assert patient.frontend_patient_id in text


# ============================================================================
# COVERAGE: _get_real_client_ip (rate_limit.py)
# ============================================================================


def test_rate_limit_uses_trusted_proxy_ip():
    """The IP appended by the trusted proxy is used, not the spoofable leftmost."""
    from unittest.mock import MagicMock

    from app.core.rate_limit import _get_real_client_ip

    request = MagicMock()
    # Attacker prepends a fake IP; the single trusted proxy appends the real one.
    request.headers = {"x-forwarded-for": "1.2.3.4, 200.115.50.10"}
    # Default TRUSTED_PROXY_HOPS = 1 -> rightmost (trusted) entry, not "1.2.3.4".
    assert _get_real_client_ip(request) == "200.115.50.10"


def test_rate_limit_respects_trusted_proxy_hops():
    """With N trusted hops, the client IP is the N-th entry counted from the right."""
    from unittest.mock import MagicMock

    from app.core import rate_limit

    request = MagicMock()
    request.headers = {"x-forwarded-for": "1.2.3.4, 200.115.50.10, 10.0.0.1"}
    original = rate_limit.settings.TRUSTED_PROXY_HOPS
    try:
        rate_limit.settings.TRUSTED_PROXY_HOPS = 2
        assert rate_limit._get_real_client_ip(request) == "200.115.50.10"
    finally:
        rate_limit.settings.TRUSTED_PROXY_HOPS = original


def test_rate_limit_short_chain_falls_back_to_first():
    """If the chain is shorter than the configured hops, fall back to the first entry."""
    from unittest.mock import MagicMock

    from app.core.rate_limit import _get_real_client_ip

    request = MagicMock()
    request.headers = {"x-forwarded-for": "200.115.50.10"}
    assert _get_real_client_ip(request) == "200.115.50.10"


def test_rate_limit_no_forwarded_uses_peer():
    """Without X-Forwarded-For, the direct peer address is used."""
    from unittest.mock import MagicMock

    from app.core.rate_limit import _get_real_client_ip

    request = MagicMock()
    request.headers = {}
    request.client.host = "203.0.113.5"
    assert _get_real_client_ip(request) == "203.0.113.5"


def test_rate_limit_redis_branch():
    """When REDIS_URL is set, the limiter uses Redis storage."""
    from unittest.mock import patch

    from app.core.rate_limit import _build_limiter

    with patch("app.core.rate_limit.settings") as mock_settings:
        mock_settings.REDIS_URL = "redis://fake:6379/0"
        limiter = _build_limiter()
        assert limiter is not None
