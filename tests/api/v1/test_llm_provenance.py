"""
AI output must be marked as such, must not overwrite clinical text, and the
LLM must only see what is new to the server.

Audit findings: x-v2-diagnosticos-ia-sin-revision-ni-procedencia,
x-v2-texto-clinico-reemplazado-por-descripcion-llm,
be-v2-ejemplos-few-shot-con-mapeos-erroneos,
be-v2-validacion-cie-debil-y-codigos-del-cliente-sin-validar (LLM side),
be-v2-llm-reinvocado-sobre-visitas-guardadas,
be-v2-restriccion-enfermeria-eludible, be-v2-vertex-endpoint-global-y-modelo-preview.
"""
import json
import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import Patient
from app.main import app
from app.schemas.patient import (
    ChronicConditionItem,
    CodeSource,
    DiagnosisItem,
    DiagnosisType,
    FamilyHistoryItem,
    FamilyRelationship,
)
from app.services.fhir_service import (
    _build_condition,
    _build_condition_statement,
    _build_family_member_history,
)
from app.services.llm import prompts
from app.services.terminology import normalize_icd10, terminology
from tests.api.v1.test_patients import (
    MOCK_PATIENT_PAYLOAD,
    MOCK_PATIENT_WITH_VISIT,
    MockNurse,
    MockUser,
)


class FakeLLM:
    """Counts calls and answers like a well-behaved coding service."""

    model_name = "fake-model"

    def __init__(self):
        self.diagnosis_calls = 0
        self.family_calls = 0
        self.chronic_calls = 0

    def extract_diagnoses(self, history, physical, systems, plan):
        self.diagnosis_calls += 1
        return [DiagnosisItem(icd10Code="J069", description="Infección respiratoria aguda")]

    def code_family_history_item(self, description):
        self.family_calls += 1
        return {"icd10Code": "E14", "icd11Code": "5A14",
                "description": "Diabetes mellitus, no especificada"}

    def code_chronic_condition(self, description):
        self.chronic_calls += 1
        return {"icd10Code": "R69", "icd11Code": None,
                "description": "Causas de morbilidad desconocidas y no especificadas"}


@pytest.fixture
def llm():
    fake = FakeLLM()
    with patch("app.api.v1.endpoints.patients.medical_llm_processor", fake):
        yield fake


def _visit_payload():
    """A visit as the app sends it: always without a diagnosis."""
    payload = deepcopy(MOCK_PATIENT_WITH_VISIT)
    payload["medicalHistory"][0]["diagnosis"] = []
    return payload


def _sync(client, payload, user=MockUser):
    app.dependency_overrides[get_current_user] = lambda: user()
    return client.post("/api/v1/patients/sync", json=payload)


def _stored(db_session, patient_id):
    return (
        db_session.query(Patient)
        .filter(Patient.frontend_patient_id == patient_id)
        .one()
        .full_record_json
    )


# ---------------------------------------------------------------------------
# Diagnoses: provenance and only-new visits
# ---------------------------------------------------------------------------


def test_ai_diagnosis_is_marked_with_its_provenance(client, db_session, llm):
    response = _sync(client, _visit_payload())

    assert response.status_code == 201, response.text
    diagnosis = _stored(db_session, "TEST-UNIT-002")["medicalHistory"][0]["diagnosis"][0]
    assert diagnosis["source"] == "ai_suggested"
    assert diagnosis["model"] == "fake-model"
    assert diagnosis["generatedAt"]


def test_clinician_diagnosis_on_a_new_visit_is_marked_as_such(client, db_session, llm):
    payload = deepcopy(MOCK_PATIENT_WITH_VISIT)
    payload["medicalHistory"][0]["diagnosis"] = [
        {"icd10Code": "J459", "description": "Asma"}
    ]

    assert _sync(client, payload).status_code == 201
    diagnosis = _stored(db_session, "TEST-UNIT-002")["medicalHistory"][0]["diagnosis"][0]
    assert diagnosis["source"] == "clinician"
    assert llm.diagnosis_calls == 0


def test_llm_not_called_for_already_stored_visits(client, db_session, llm):
    assert _sync(client, _visit_payload()).status_code == 201
    assert llm.diagnosis_calls == 1

    # The app never gets the server's diagnosis back, so it re-sends the visit
    # without one on every later sync.
    assert _sync(client, _visit_payload()).status_code == 201
    assert llm.diagnosis_calls == 1


# ---------------------------------------------------------------------------
# Background: text preserved, coding reused
# ---------------------------------------------------------------------------


def _with_background():
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["backgroundHistory"]["familyHistory"] = [
        {"conditionDescription": "Diabetes", "relationship": "04"}
    ]
    payload["backgroundHistory"]["chronicConditions"] = [
        {"chronicDescription": "Asma leve"}
    ]
    return payload


def test_original_description_is_preserved(client, db_session, llm):
    """poc: 'Diabetes' used to become 'Diabetes mellitus tipo 2' in the record."""
    assert _sync(client, _with_background()).status_code == 201

    background = _stored(db_session, "TEST-UNIT-001")["backgroundHistory"]
    family = background["familyHistory"][0]
    assert family["conditionDescription"] == "Diabetes"
    assert family["conditionCie10Code"] == "E14"
    assert family["conditionCodedDisplay"] == "Diabetes mellitus, no especificada"
    assert family["codingSource"] == "ai_suggested"
    chronic = background["chronicConditions"][0]
    assert chronic["chronicDescription"] == "Asma leve"
    assert chronic["codingSource"] == "ai_fallback"
    assert "Código LLM" not in json.dumps(background)


def test_background_coding_is_reused_instead_of_asking_again(client, db_session, llm):
    assert _sync(client, _with_background()).status_code == 201
    assert (llm.family_calls, llm.chronic_calls) == (1, 1)

    # Same text, different spacing/case, still uncoded on the device.
    payload = _with_background()
    payload["backgroundHistory"]["familyHistory"][0]["conditionDescription"] = " diabetes "
    assert _sync(client, payload).status_code == 201

    assert (llm.family_calls, llm.chronic_calls) == (1, 1)
    family = _stored(db_session, "TEST-UNIT-001")["backgroundHistory"]["familyHistory"][0]
    assert family["conditionCie10Code"] == "E14"
    assert family["codingSource"] == "ai_suggested"


def test_legacy_coded_item_without_source_is_reused(client, db_session, llm):
    payload = _with_background()
    payload["backgroundHistory"]["familyHistory"][0]["conditionCie10Code"] = "E14"
    assert _sync(client, payload).status_code == 201

    assert _sync(client, _with_background()).status_code == 201
    family = _stored(db_session, "TEST-UNIT-001")["backgroundHistory"]["familyHistory"][0]
    assert family["conditionCie10Code"] == "E14"
    assert family["codingSource"] is None
    assert llm.family_calls == 0


# ---------------------------------------------------------------------------
# Nurse rule (poc02)
# ---------------------------------------------------------------------------


def test_nurse_cannot_add_visit_by_sending_only_the_new_one(client, db_session, llm):
    assert _sync(client, deepcopy(MOCK_PATIENT_WITH_VISIT)).status_code == 201

    payload = deepcopy(MOCK_PATIENT_WITH_VISIT)
    new_visit = deepcopy(payload["medicalHistory"][0])
    new_visit["encounterIdentifier"] = "VISITA-ENFERMERA-1"
    payload["medicalHistory"] = [new_visit]

    response = _sync(client, payload, user=MockNurse)

    assert response.status_code == 403
    assert len(_stored(db_session, "TEST-UNIT-002")["medicalHistory"]) == 1


def test_nurse_cannot_create_patient_with_visits(client, db_session, llm):
    response = _sync(client, deepcopy(MOCK_PATIENT_WITH_VISIT), user=MockNurse)

    assert response.status_code == 403
    assert db_session.query(Patient).count() == 0
    assert llm.diagnosis_calls == 0


def test_nurse_can_resend_known_visits_with_a_new_vaccine(client, db_session, llm):
    assert _sync(client, deepcopy(MOCK_PATIENT_WITH_VISIT)).status_code == 201
    payload = deepcopy(MOCK_PATIENT_WITH_VISIT)
    payload["vaccinationRecord"] = [{
        "vaccinationId": "VAC-1", "date": "2026-09-01", "vaccineName": "Triple viral",
        "vaccineCode": "03", "dose": 1, "administratedBy": "Enfermera",
        "administratedAt": "Albergue", "status": "completed",
    }]

    assert _sync(client, payload, user=MockNurse).status_code == 201


# ---------------------------------------------------------------------------
# FHIR
# ---------------------------------------------------------------------------


def _verification(resource):
    return resource["verificationStatus"]["coding"][0]["code"]


@pytest.mark.parametrize(
    ("source", "diagnosis_type", "expected"),
    [
        (CodeSource.AI_SUGGESTED, DiagnosisType.CONFIRMADO_NUEVO, "provisional"),
        (CodeSource.AI_FALLBACK, DiagnosisType.CONFIRMADO_NUEVO, "provisional"),
        (None, DiagnosisType.CONFIRMADO_NUEVO, "provisional"),
        (CodeSource.CLINICIAN, DiagnosisType.IMPRESION_DIAGNOSTICA, "provisional"),
        (CodeSource.CLINICIAN, DiagnosisType.CONFIRMADO_NUEVO, "confirmed"),
        (CodeSource.CLINICIAN, DiagnosisType.CONFIRMADO_REPETIDO, "confirmed"),
    ],
)
def test_only_clinician_confirmed_diagnoses_are_confirmed(source, diagnosis_type, expected):
    diagnosis = DiagnosisItem(icd10Code="J069", description="IRA", source=source)

    resource = _build_condition(diagnosis, "Patient-1", "Condition-1", diagnosis_type)

    assert _verification(resource) == expected


def test_default_diagnosis_type_is_an_impression():
    diagnosis = DiagnosisItem(icd10Code="J069", description="IRA", source=CodeSource.CLINICIAN)

    assert _verification(_build_condition(diagnosis, "Patient-1", "Condition-1")) == "provisional"


def test_chronic_condition_keeps_original_text_next_to_the_code():
    item = ChronicConditionItem(
        chronicDescription="Diabetes", chronicCie10Code="E14",
        chronicCodedDisplay="Diabetes mellitus, no especificada",
    )

    code = _build_condition_statement(item, "Patient-1", "Condition-1")["code"]

    assert code["text"] == "Diabetes"
    assert code["coding"][0]["display"] == "Diabetes mellitus, no especificada"


def test_family_history_without_code_has_no_empty_coding():
    item = FamilyHistoryItem(conditionDescription="Glaucoma", relationship=FamilyRelationship("04"))

    code = _build_family_member_history(item, "Patient-1", "FMH-1")["condition"][0]["code"]

    assert code == {"text": "Glaucoma"}


def test_family_history_with_code_keeps_text_and_display():
    item = FamilyHistoryItem(
        conditionDescription="Glaucoma", relationship=FamilyRelationship("04"),
        conditionCie10Code="H409", conditionCie11Code="9C61.Z",
    )

    code = _build_family_member_history(item, "Patient-1", "FMH-1")["condition"][0]["code"]

    assert code["text"] == "Glaucoma"
    assert code["coding"][0] == {"system": code["coding"][0]["system"], "code": "H409",
                                 "display": "Glaucoma"}
    assert code["coding"][1]["code"] == "9C61.Z"


# ---------------------------------------------------------------------------
# Terminology and prompts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["I", "XXII", "A00-A09"])
def test_icd10_chapters_and_blocks_are_rejected(code):
    assert terminology.validate_icd10(code) is False


@pytest.mark.parametrize("code", ["01", "XN74M"])
def test_icd11_chapters_and_extension_codes_are_rejected(code):
    assert terminology.validate_icd11(code) is False


def test_dotted_icd10_codes_are_normalised():
    assert normalize_icd10(" j45.9 ") == "J459"
    assert terminology.validate_icd10("J45.9") is True
    assert terminology.get_icd10_display("J45.9") == terminology.get_icd10_display("J459")


def test_prompt_examples_match_catalog():
    """Every code pair taught to the LLM must exist, and must not be the
    conjunctiva-for-glaucoma kind of mismatch the audit found."""
    catalog10 = json.loads(Path("app/data/icd10_codes.json").read_text())
    catalog11 = json.loads(Path("app/data/icd11_codes.json").read_text())
    text = "\n".join(
        [prompts.SYSTEM_INSTRUCTION, prompts.SYSTEM_INSTRUCTION_FAMILY_HISTORY,
         prompts.SYSTEM_INSTRUCTION_CHRONIC_CONDITION]
    )
    pairs = re.findall(r'"icd10Code": "(\w+)", "icd11Code": (null|"[\w.]+")', text)
    assert len(pairs) >= 10

    for icd10, icd11 in pairs:
        assert icd10 in catalog10, icd10
        if icd11 != "null":
            assert icd11.strip('"') in catalog11, icd11
    assert '"Glaucoma"' in text and '"9A61.Z"' not in text
    assert '"icd10Code": "E14", "icd11Code": "5A14"' in text


def test_gemini_client_uses_configured_location(monkeypatch):
    from app.services.llm import gemini

    monkeypatch.setattr(settings, "LLM_LOCATION", "us-central1")
    with patch.object(gemini.genai, "Client", MagicMock()) as client_cls:
        gemini.GeminiMedicalCodingService("model", project_id="p")

    assert client_cls.call_args.kwargs["location"] == "us-central1"
