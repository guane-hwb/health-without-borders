"""
LLM coding in /sync: a bounded budget, honest provenance and catalog codes.

Backend audit 2026-11-04:
- be-nov26-sync-llamadas-llm-sin-tope (PoC-3): one /sync made 1400 calls in a row.
- be-nov26-llm-invocado-con-notas-vacias: a visit without notes still called the
  LLM, and any R69 was recorded as a fallback.
- be-nov26-codigos-cie-sin-contrastar-con-catalogo: codes sent by a client
  reached the RDA unchecked, and its display was the LLM's wording.
- be-nov26-prompt-contradice-el-catalogo: the prompts and the validation applied
  different rules.
"""
import threading
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import Settings, settings
from app.schemas.patient import DiagnosisItem
from app.services.llm import prompts
from app.services.llm.base import FALLBACK_ICD10_CODE, ai_code_source, fallback_coding
from app.services.llm.gemini import _validate_and_fix_diagnosis, _validate_and_fix_dict
from app.services.llm.schemas import (
    CHRONIC_CONDITION_RESPONSE_SCHEMA,
    DIAGNOSIS_RESPONSE_SCHEMA,
    FAMILY_HISTORY_RESPONSE_SCHEMA,
)
from app.services.terminology import TerminologyService, terminology
from tests.api.v1.test_llm_provenance import (
    FakeLLM,
    _stored,
    _sync,
    _visit_payload,
    _with_background,
)

REPO = Path(__file__).resolve().parents[3]


class CountingLLM(FakeLLM):
    """FakeLLM that also measures how many calls run at the same time."""

    def __init__(self, delay=0.0, answer="J069"):
        super().__init__()
        self.delay = delay
        self.answer = answer
        self.running = 0
        self.max_running = 0
        self.lock = threading.Lock()

    def extract_diagnoses(self, history, physical, systems, plan):
        with self.lock:
            self.diagnosis_calls += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        time.sleep(self.delay)
        with self.lock:
            self.running -= 1
        return [DiagnosisItem(icd10Code=self.answer, description="Respuesta del modelo")]

    def code_chronic_condition(self, description):
        self.chronic_calls += 1
        return {"icd10Code": "J459", "icd11Code": None, "description": "Asma"}


@pytest.fixture
def counting_llm():
    fake = CountingLLM()
    with patch("app.api.v1.endpoints.patients.medical_llm_processor", fake):
        yield fake


def _many_visits(n):
    payload = _visit_payload()
    template = payload["medicalHistory"][0]
    visits = []
    for i in range(n):
        visit = deepcopy(template)
        visit["encounterIdentifier"] = f"enc-budget-{i:03d}"
        visit["startDateTime"] = f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:00:00"
        visit["endDateTime"] = None
        visits.append(visit)
    payload["medicalHistory"] = visits
    return payload


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_sync_caps_llm_calls(client, db_session, counting_llm):
    response = _sync(client, _many_visits(100))

    assert response.status_code == 201, response.text
    assert counting_llm.diagnosis_calls == settings.LLM_MAX_CALLS_PER_SYNC == 20
    visits = _stored(db_session, "TEST-UNIT-002")["medicalHistory"]
    sources = [v["diagnosis"][0]["source"] for v in visits]
    assert sources.count("ai_suggested") == 20
    assert sources.count("ai_fallback") == 80
    left = [v["diagnosis"][0] for v in visits if v["diagnosis"][0]["source"] == "ai_fallback"]
    assert {d["icd10Code"] for d in left} == {FALLBACK_ICD10_CODE}
    assert all(d.get("model") is None for d in left)  # no model stands behind them


def test_calls_run_in_parallel_but_bounded(client, counting_llm, monkeypatch):
    counting_llm.delay = 0.05

    assert _sync(client, _many_visits(12)).status_code == 201

    assert counting_llm.diagnosis_calls == 12
    assert 1 < counting_llm.max_running <= settings.LLM_MAX_CONCURRENT_CALLS


def test_no_call_starts_once_the_time_budget_is_spent(client, db_session, counting_llm, monkeypatch):
    monkeypatch.setattr(settings, "LLM_SYNC_BUDGET_SECONDS", 0)

    assert _sync(client, _many_visits(3)).status_code == 201

    assert counting_llm.diagnosis_calls == 0
    visits = _stored(db_session, "TEST-UNIT-002")["medicalHistory"]
    assert [v["diagnosis"][0]["source"] for v in visits] == ["ai_fallback"] * 3


def test_a_background_item_left_by_the_budget_is_coded_by_a_later_sync(
    client, db_session, counting_llm, monkeypatch
):
    payload = _visit_payload()
    payload["backgroundHistory"] = _with_background()["backgroundHistory"]
    payload["backgroundHistory"]["familyHistory"] = []
    monkeypatch.setattr(settings, "LLM_MAX_CALLS_PER_SYNC", 1)

    assert _sync(client, payload).status_code == 201
    chronic = _stored(db_session, "TEST-UNIT-002")["backgroundHistory"]["chronicConditions"][0]
    assert (counting_llm.diagnosis_calls, counting_llm.chronic_calls) == (1, 0)
    assert chronic.get("chronicCie10Code") is None
    assert chronic["chronicDescription"] == "Asma leve"

    assert _sync(client, payload).status_code == 201  # the visit is stored now
    db_session.expire_all()
    chronic = _stored(db_session, "TEST-UNIT-002")["backgroundHistory"]["chronicConditions"][0]
    assert counting_llm.chronic_calls == 1
    assert chronic["chronicCie10Code"] == "J459"


def test_a_visit_without_clinical_notes_does_not_call_the_llm(client, db_session, counting_llm):
    payload = _visit_payload()
    payload["medicalHistory"][0]["clinicalEvaluation"] = {
        "historyOfCurrentIllness": "  ", "generalPhysicalExamination": "",
        "systemsExamination": None, "treatmentPlanObservations": "",
    }

    assert _sync(client, payload).status_code == 201

    assert counting_llm.diagnosis_calls == 0
    diagnosis = _stored(db_session, "TEST-UNIT-002")["medicalHistory"][0]["diagnosis"][0]
    assert (diagnosis["icd10Code"], diagnosis["source"]) == (FALLBACK_ICD10_CODE, "ai_fallback")


# ---------------------------------------------------------------------------
# Provenance: an R69 the model chose is a suggestion
# ---------------------------------------------------------------------------


def test_an_r69_returned_by_the_model_is_a_suggestion(client, db_session, counting_llm):
    counting_llm.answer = FALLBACK_ICD10_CODE

    assert _sync(client, _visit_payload()).status_code == 201

    diagnosis = _stored(db_session, "TEST-UNIT-002")["medicalHistory"][0]["diagnosis"][0]
    assert (diagnosis["icd10Code"], diagnosis["source"]) == ("R69", "ai_suggested")
    assert diagnosis["model"] == "fake-model"


def test_background_provenance_follows_the_fallback_flag():
    assert ai_code_source({"icd10Code": "R69"}) == "ai_suggested"
    assert ai_code_source(fallback_coding()) == "ai_fallback"


# ---------------------------------------------------------------------------
# Codes sent by the client
# ---------------------------------------------------------------------------


def test_a_client_diagnosis_outside_the_catalog_is_not_kept(client, db_session, counting_llm):
    payload = _visit_payload()
    payload["medicalHistory"][0]["diagnosis"] = [
        {"icd10Code": "Z00129", "description": "Código CM inventado"},
        {"icd10Code": "J45.9", "icd11Code": "NOPE.1", "description": "Asma"},
    ]

    assert _sync(client, payload).status_code == 201

    diagnoses = _stored(db_session, "TEST-UNIT-002")["medicalHistory"][0]["diagnosis"]
    assert [(d["icd10Code"], d["icd11Code"], d["source"]) for d in diagnoses] == [
        ("J45.9", None, "clinician")  # kept as sent; the RDA normalises it
    ]
    assert counting_llm.diagnosis_calls == 0


def test_a_visit_whose_client_codes_are_all_invalid_is_coded_by_the_llm(
    client, db_session, counting_llm
):
    payload = _visit_payload()
    payload["medicalHistory"][0]["diagnosis"] = [{"icd10Code": "XX99", "description": "?"}]

    assert _sync(client, payload).status_code == 201

    diagnosis = _stored(db_session, "TEST-UNIT-002")["medicalHistory"][0]["diagnosis"][0]
    assert (diagnosis["icd10Code"], diagnosis["source"]) == ("J069", "ai_suggested")


def test_an_invalid_background_code_from_the_client_is_recoded(client, db_session, counting_llm):
    payload = _with_background()
    payload["backgroundHistory"]["familyHistory"] = [
        {"conditionDescription": "Diabetes", "relationship": "04",
         "conditionCie10Code": "E1165", "codingSource": "clinician"},
        {"conditionDescription": "Hipertensión", "relationship": "02",
         "conditionCie10Code": "I10"},
    ]
    payload["backgroundHistory"]["chronicConditions"] = [
        {"chronicDescription": "Asma leve", "chronicCie10Code": "J45", "chronicCie11Code": "BAD"},
        {"chronicDescription": "Epilepsia", "chronicCie10Code": "G409"},
    ]

    assert _sync(client, payload).status_code == 201

    background = _stored(db_session, "TEST-UNIT-001")["backgroundHistory"]
    family = {f["conditionDescription"]: f for f in background["familyHistory"]}
    assert (family["Diabetes"]["conditionCie10Code"], family["Diabetes"]["codingSource"]) == (
        "E14", "ai_suggested"
    )
    assert family["Hipertensión"]["conditionCie10Code"] == "I10"
    chronic = {c["chronicDescription"]: c for c in background["chronicConditions"]}
    assert chronic["Asma leve"]["chronicCie10Code"] == "J459"  # recoded by the LLM
    assert chronic["Epilepsia"]["chronicCie10Code"] == "G409"
    assert (counting_llm.family_calls, counting_llm.chronic_calls) == (1, 1)


# ---------------------------------------------------------------------------
# One rule for the prompt and the validation
# ---------------------------------------------------------------------------


def test_a_category_with_subcategories_becomes_its_unspecified_one():
    assert terminology.terminal_icd10("E14") == "E149"
    assert terminology.terminal_icd10("j45.9") == "J459"
    assert terminology.terminal_icd10("Z00") == "Z00"  # no "9" subcategory
    assert terminology.terminal_icd10("E86") == "E86"  # no subcategories
    assert terminology.terminal_icd10("Z00129") is None  # ICD-10-CM
    assert terminology.terminal_icd10(None) is None


def test_without_the_full_catalog_codes_are_only_checked_for_shape():
    unloaded = TerminologyService()

    assert unloaded.terminal_icd10("E14") == "E14"
    assert unloaded.terminal_icd10("1234") is None


def test_llm_answers_are_completed_to_terminal_codes():
    diagnosis = _validate_and_fix_diagnosis(DiagnosisItem(icd10Code="J45", description="Asma"))
    assert diagnosis.icd10Code == "J459"
    assert _validate_and_fix_dict({"icd10Code": "E14", "icd11Code": None})["icd10Code"] == "E149"
    assert _validate_and_fix_dict({"icd10Code": "Z00129"})["fallback"] is True


def test_every_prompt_example_is_a_terminal_catalog_code():
    import re

    text = "\n".join([prompts.SYSTEM_INSTRUCTION, prompts.SYSTEM_INSTRUCTION_FAMILY_HISTORY,
                      prompts.SYSTEM_INSTRUCTION_CHRONIC_CONDITION])
    examples = re.findall(r'"icd10Code": "(\w+)"', text)

    assert len(examples) >= 10
    assert all(terminology.terminal_icd10(code) == code for code in examples), examples
    # Rule 4 used J209, a valid WHO code, as an example of a forbidden US code.
    assert "J209" not in text
    assert "'E11' not 'E11'" not in text


def test_icd11_is_nullable_in_every_response_schema():
    assert DIAGNOSIS_RESPONSE_SCHEMA["items"]["properties"]["icd11Code"]["nullable"] is True
    for schema in (FAMILY_HISTORY_RESPONSE_SCHEMA, CHRONIC_CONDITION_RESPONSE_SCHEMA):
        assert schema["properties"]["icd11Code"]["nullable"] is True


# ---------------------------------------------------------------------------
# Model and region
# ---------------------------------------------------------------------------


def test_a_ga_model_in_the_fhir_store_region():
    defaults = Settings.model_fields
    assert defaults["LLM_MODEL_NAME"].default == "gemini-3.8-flash"
    assert defaults["LLM_LOCATION"].default == "us-central1"
    deploy = (REPO / "cloudbuild.yaml").read_text()
    assert "LLM_MODEL_NAME=gemini-3.8-flash" in deploy
    assert "LLM_LOCATION=us-central1" in deploy


def test_background_items_without_text_are_not_coded(client, db_session, counting_llm):
    payload = _with_background()
    payload["backgroundHistory"]["familyHistory"] = [
        {"conditionDescription": "", "relationship": "04"}
    ]
    payload["backgroundHistory"]["chronicConditions"] = [{"chronicDescription": ""}]

    assert _sync(client, payload).status_code == 201

    assert (counting_llm.family_calls, counting_llm.chronic_calls) == (0, 0)
