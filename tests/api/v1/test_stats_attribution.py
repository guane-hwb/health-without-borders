"""
Statistics attribute each act to the organization that performed it.

Audit findings: be-v2-estadisticas-por-org-atribuyen-mal-la-atencion (poc16),
be-v2-vaccinecode-sin-validacion (grouping).
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.api.deps import get_current_user
from app.core.security import get_password_hash
from app.db.models import Organization, Patient, User, UserRole
from app.main import app
from app.services.stats_service import _vaccine_code_key, build_overview
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, VISIT_1

NOW = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)


def _vaccine(vaccination_id, code="03", day="2026-09-10"):
    return {"vaccinationId": vaccination_id, "date": day, "vaccineName": "Triple viral",
            "vaccineCode": code, "dose": 1, "administratedBy": "Enfermera",
            "administratedAt": "Albergue", "status": "completed"}


def _visit(encounter_id, start="2026-09-10T10:00:00"):
    visit = deepcopy(VISIT_1)
    visit.update(encounterIdentifier=encounter_id, startDateTime=start)
    return visit


@pytest.fixture
def orgs(client, db_session):
    people = {}
    for key, name in (("a", "Org A"), ("b", "Org B")):
        org = Organization(name=name, is_active=True)
        db_session.add(org)
        db_session.commit()
        user = User(email=f"doc@{key}.org", full_name="Persona Sintetica",
                    hashed_password=get_password_hash("password123"), role=UserRole.doctor,
                    is_active=True, organization_id=org.id)
        db_session.add(user)
        db_session.commit()
        people[key] = {"org": org.id, "user": user.id}
    return people


def _sync_as(client, db_session, user_id, **top):
    user = db_session.query(User).filter(User.id == user_id).one()
    app.dependency_overrides[get_current_user] = lambda: user
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    response = client.post("/api/v1/patients/sync", json=payload)
    assert response.status_code == 201, response.text
    return response


def _stats(db_session, org_id=None):
    return build_overview(db_session, organization_id=org_id,
                          date_from=datetime(2026, 9, 1).date(), now=NOW)


def test_acts_count_for_the_organization_that_performed_them(client, db_session, orgs):
    """poc16: B vaccinated and saw a child A registered; A got the credit."""
    _sync_as(client, db_session, orgs["a"]["user"])
    _sync_as(client, db_session, orgs["b"]["user"],
             vaccinationRecord=[_vaccine("VAC-B")], medicalHistory=[_visit("ENC-B")])

    a, b = _stats(db_session, orgs["a"]["org"]), _stats(db_session, orgs["b"]["org"])

    assert (a.totals.patients, a.totals.vaccine_doses, a.totals.encounters) == (1, 0, 0)
    assert (b.totals.patients, b.totals.vaccine_doses, b.totals.encounters) == (0, 1, 1)
    everyone = _stats(db_session)
    assert (everyone.totals.patients, everyone.totals.vaccine_doses, everyone.totals.encounters) == (1, 1, 1)


def test_sync_stamps_only_new_items_with_the_authenticated_caller(client, db_session, orgs):
    _sync_as(client, db_session, orgs["a"]["user"], vaccinationRecord=[_vaccine("VAC-A")])
    forged = _vaccine("VAC-B")
    forged["recordedByOrganizationId"] = orgs["a"]["org"]  # a client cannot claim it
    _sync_as(client, db_session, orgs["b"]["user"],
             vaccinationRecord=[_vaccine("VAC-A"), forged])

    vaccines = {
        v["vaccinationId"]: v
        for v in db_session.query(Patient).one().full_record_json["vaccinationRecord"]
    }
    assert vaccines["VAC-A"]["recordedByOrganizationId"] == orgs["a"]["org"]
    assert vaccines["VAC-B"]["recordedByOrganizationId"] == orgs["b"]["org"]
    assert vaccines["VAC-B"]["recordedByUserId"] == orgs["b"]["user"]
    assert vaccines["VAC-B"]["recordedAt"]


def test_legacy_acts_without_attribution_count_for_the_patients_organization(
    client, db_session, orgs
):
    _sync_as(client, db_session, orgs["a"]["user"])
    patient = db_session.query(Patient).one()
    record = deepcopy(patient.full_record_json)
    record["vaccinationRecord"] = [_vaccine("VAC-OLD")]  # stored before the stamp existed
    record["medicalHistory"] = [_visit("ENC-OLD")]
    patient.full_record_json = record
    db_session.commit()

    a, b = _stats(db_session, orgs["a"]["org"]), _stats(db_session, orgs["b"]["org"])

    assert (a.totals.vaccine_doses, a.totals.encounters) == (1, 1)
    assert (b.totals.vaccine_doses, b.totals.encounters) == (0, 0)


@pytest.mark.parametrize(
    ("raw", "key"), [("03", "03"), ("3", "03"), (" 03 ", "03"), ("110", "110"), ("mmr", "MMR")]
)
def test_vaccine_codes_are_grouped(raw, key):
    assert _vaccine_code_key(raw) == key


def test_grouped_codes_count_as_one_vaccine(client, db_session, orgs):
    _sync_as(client, db_session, orgs["a"]["user"], vaccinationRecord=[
        _vaccine("V1", code="03"), _vaccine("V2", code="3"), _vaccine("V3", code=" 03 "),
    ])

    vaccines = _stats(db_session, orgs["a"]["org"]).vaccines

    assert [(v.code, v.count) for v in vaccines] == [("03", 3)]
