"""
/sync against PostgreSQL: the behaviours SQLite cannot show (column lengths,
foreign keys, row locks, connections held while waiting on the LLM / FHIR).

Audit PoCs: poc01, poc05, poc08, poc15, poc20.
"""
import threading
import time
from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, VISIT_1

UID = MOCK_PATIENT_PAYLOAD["device_uid"]
GUARDIAN = {"X-Guardian-Device-UID": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]}


def _payload(**top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    return payload


def _sync(api, tokens, payload):
    return api.post("/api/v1/patients/sync", headers=tokens["headers"], json=payload)


def _scan(api, tokens, uid=UID):
    return api.get(f"/api/v1/patients/scan/{uid}", headers={**tokens["headers"], **GUARDIAN})


def test_other_nationality_fits_the_column(api, staff, db):
    """poc01: 'OTHER' did not fit VARCHAR(3) and the patient never synced."""
    doc = staff["tokens"]["doc_a"]
    payload = _payload()
    payload["patientInfo"]["nationalityCode"] = "OTHER"

    assert _sync(api, doc, payload).status_code == 201

    stored = db.execute(text("SELECT nationality_code FROM patients")).scalar_one()
    assert stored == "UNK"


def test_concurrent_syncs_keep_every_visit(api, staff):
    """poc15: without the row lock, concurrent syncs overwrote each other."""
    doc = staff["tokens"]["doc_a"]
    assert _sync(api, doc, _payload()).status_code == 201
    base = _scan(api, doc).json()
    barrier = threading.Barrier(10)
    codes = []

    def device(i):
        payload = deepcopy(base)
        visit = deepcopy(VISIT_1)
        visit["encounterIdentifier"] = f"CONC-{i}"
        visit["startDateTime"] = f"2026-09-21T{10 + i}:00:00"
        payload["medicalHistory"] = [visit]
        payload.pop("recordVersion", None)
        from app.main import app

        client = TestClient(app)
        barrier.wait()
        codes.append(client.post("/api/v1/patients/sync", headers=doc["headers"], json=payload).status_code)

    threads = [threading.Thread(target=device, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(codes) == {201}
    visits = _scan(api, doc).json()["medicalHistory"]
    assert sorted(v["encounterIdentifier"] for v in visits) == [f"CONC-{i}" for i in range(10)]


def test_stale_offline_copy_keeps_allergy_and_bracelet(api, staff):
    """poc05 on PostgreSQL, through the retired-UID signal."""
    doc = staff["tokens"]["doc_a"]
    old_copy = _payload(allergies=[])
    assert _sync(api, doc, old_copy).status_code == 201
    replaced = _payload(
        device_uid="UID-PULSERA-NUEVA", retiredDeviceReason="lost",
        allergies=[{"category": "01", "allergen": "Penicilina"}],
    )
    assert _sync(api, doc, replaced).status_code == 201

    old_copy["medicalHistory"] = [deepcopy(VISIT_1)]
    response = _sync(api, doc, old_copy)

    assert response.json()["conflicts"] == ["stale_payload_retired_device_uid"]
    record = _scan(api, doc, "UID-PULSERA-NUEVA").json()
    assert [a["allergen"] for a in record["allergies"]] == ["Penicilina"]
    assert len(record["medicalHistory"]) == 1
    assert _scan(api, doc).status_code == 410


def test_base_version_protects_a_stale_copy(api, staff):
    doc = staff["tokens"]["doc_a"]
    version = _sync(api, doc, _payload()).json()["record_version"]
    _sync(api, doc, _payload(allergies=[{"category": "01", "allergen": "Mani"}], baseVersion=version))

    response = _sync(api, doc, _payload(allergies=[], baseVersion=version))

    assert response.json()["conflicts"] == ["stale_payload_base_version"]
    assert [a["allergen"] for a in _scan(api, doc).json()["allergies"]] == ["Mani"]


def test_deleting_a_user_with_audit_rows_is_a_409(api, staff):
    """poc08: the foreign keys from the access log made it a 500."""
    doc, admin = staff["tokens"]["doc_a"], staff["tokens"]["admin_a"]
    assert _sync(api, doc, _payload()).status_code == 201  # writes an access-log row

    response = api.delete(f"/api/v1/users/{staff['ids']['doc_a']}", headers=admin["headers"])

    assert response.status_code == 409
    assert "Deactivate the account instead" in response.json()["detail"]


def test_deleting_an_organization_whose_user_sent_telemetry_is_a_409(api, staff):
    """poc08: NFC telemetry references the user; the cascade used to 500."""
    telemetry = api.post(
        "/api/v1/patients/nfc-key-versions", headers=staff["tokens"]["doc_b"]["headers"],
        json={"entries": [{"device_uid": "04:AA", "key_version": 0,
                           "observed_at": "2026-09-25T10:00:00+00:00"}]},
    )
    assert telemetry.status_code == 202

    response = api.delete(
        f"/api/v1/organizations/{staff['ids']['org_b']}", headers=staff["tokens"]["sa"]["headers"]
    )

    assert response.status_code == 409
    assert "Deactivate the organization instead" in response.json()["detail"]


def test_no_connection_is_held_while_waiting_on_the_llm_or_fhir(api, staff, db, monkeypatch):
    """poc20: every slow /sync held a pooled connection 'idle in transaction'."""
    from app.api.v1.endpoints import patients as endpoints
    from app.services.fhir import fhir_backend

    real_llm = endpoints.medical_llm_processor

    class SlowLLM:
        model_name = "slow-fake"

        def extract_diagnoses(self, **kwargs):
            time.sleep(2)
            return real_llm.extract_diagnoses(**kwargs)

        def code_family_history_item(self, text):
            return real_llm.code_family_history_item(text)

        def code_chronic_condition(self, text):
            return real_llm.code_chronic_condition(text)

    def slow_send(bundle):
        time.sleep(1)
        return {"status": "success"}

    monkeypatch.setattr(endpoints, "medical_llm_processor", SlowLLM())
    monkeypatch.setattr(fhir_backend, "send_bundle", slow_send)
    doc = staff["tokens"]["doc_a"]
    database = db.execute(text("SELECT current_database()")).scalar_one()
    db.commit()

    def device(i):
        payload = _payload(patientId=f"PID-{i}", device_uid=f"UID-{i}")
        payload["patientInfo"]["identification"]["documentNumber"] = f"DOC-{i}"
        visit = deepcopy(VISIT_1)
        visit["diagnosis"] = []
        payload["medicalHistory"] = [visit]
        from app.main import app

        TestClient(app).post("/api/v1/patients/sync", headers=doc["headers"], json=payload)

    threads = [threading.Thread(target=device, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    samples = []
    for _ in range(6):
        time.sleep(0.5)
        samples.append(db.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = :db "
            "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
        ), {"db": database}).scalar_one())
        db.commit()
    for thread in threads:
        thread.join()

    assert samples == [0] * 6
