"""
/stats/overview on PostgreSQL with real tokens (audit poc16).
"""
from copy import deepcopy

from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, VISIT_1


def test_statistics_credit_the_organization_that_did_the_work(api, staff):
    doc_a, doc_b = staff["tokens"]["doc_a"], staff["tokens"]["doc_b"]
    assert api.post("/api/v1/patients/sync", headers=doc_a["headers"],
                    json=deepcopy(MOCK_PATIENT_PAYLOAD)).status_code == 201
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    visit = deepcopy(VISIT_1)
    visit["startDateTime"] = "2026-09-10T10:00:00-05:00"
    payload["medicalHistory"] = [visit]
    payload["vaccinationRecord"] = [{
        "vaccinationId": "VAC-B", "date": "2026-09-10", "vaccineName": "Triple viral",
        "vaccineCode": "03", "dose": 1, "administratedBy": "Enfermera",
        "administratedAt": "Albergue", "status": "completed",
    }]
    assert api.post("/api/v1/patients/sync", headers=doc_b["headers"], json=payload).status_code == 201

    def totals(org_key):
        response = api.get(
            "/api/v1/stats/overview",
            params={"organization_id": staff["ids"][org_key], "date_from": "2026-09-01",
                    "date_to": "2026-09-30"},
            headers=staff["tokens"]["sa"]["headers"],
        )
        assert response.status_code == 200, response.text
        t = response.json()["totals"]
        return t["patients"], t["vaccine_doses"], t["encounters"]

    assert totals("org_a") == (1, 0, 0)
    assert totals("org_b") == (0, 1, 1)
