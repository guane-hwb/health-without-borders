"""
Claude backend (Anthropic API) of the medical coding service.

No call leaves the test: the Anthropic client is a mock. What is checked is
the request the service builds, how it reads each kind of answer, and that
every answer it cannot use becomes the R69 fallback.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from anthropic.types.beta import BetaTextBlock

from app.core.config import settings
from app.schemas.patient import CodeSource, DiagnosisItem
from app.services.llm import claude
from app.services.llm.base import FALLBACK_ICD10_CODE, mark_ai_diagnoses
from app.services.llm.claude import ClaudeMedicalCodingService
from app.services.llm.factory import get_llm_service
from app.services.llm.schemas import (
    CHRONIC_CONDITION_OUTPUT_SCHEMA,
    DIAGNOSIS_OUTPUT_SCHEMA,
    FAMILY_HISTORY_OUTPUT_SCHEMA,
    json_output_schema,
)

MODEL = "claude-opus-5-5"


def _answer(payload, *, model=MODEL, stop_reason="end_turn", stop_details=None, text=True):
    """A Messages API answer: a thinking block, then the JSON text."""
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text:
        content.append(BetaTextBlock(type="text", text=json.dumps(payload)))
    return SimpleNamespace(
        content=content, model=model, stop_reason=stop_reason, stop_details=stop_details
    )


@pytest.fixture
def service():
    with patch("app.services.llm.claude.anthropic.Anthropic"):
        svc = ClaudeMedicalCodingService(MODEL, api_key="sk-test")
    return svc


@pytest.fixture
def catalog():
    """Every code the model answers is in the catalog."""
    with patch("app.services.llm.validation.terminology") as mock_term:
        mock_term.terminal_icd10.side_effect = lambda code: code
        mock_term.validate_icd11.return_value = True
        yield mock_term


# ============================================================================
# Schemas
# ============================================================================


class TestJsonOutputSchema:
    def test_types_are_lower_case_and_objects_closed(self):
        schema = json_output_schema(
            {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}, "required": ["a"]}
        )
        assert schema == {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        }

    def test_nullable_becomes_a_null_type(self):
        schema = json_output_schema({"type": "STRING", "nullable": True, "description": "d"})
        assert schema == {"type": ["string", "null"], "description": "d"}

    def test_array_items_are_converted(self):
        schema = json_output_schema({"type": "ARRAY", "items": {"type": "OBJECT", "properties": {}}})
        assert schema["items"]["additionalProperties"] is False

    def test_diagnoses_go_under_one_key(self):
        diagnoses = DIAGNOSIS_OUTPUT_SCHEMA["properties"]["diagnoses"]
        assert DIAGNOSIS_OUTPUT_SCHEMA["required"] == ["diagnoses"]
        assert diagnoses["type"] == "array"
        assert diagnoses["items"]["properties"]["icd11Code"]["type"] == ["string", "null"]

    @pytest.mark.parametrize(
        "schema",
        [DIAGNOSIS_OUTPUT_SCHEMA, FAMILY_HISTORY_OUTPUT_SCHEMA, CHRONIC_CONDITION_OUTPUT_SCHEMA],
    )
    def test_every_object_is_closed(self, schema):
        def objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    yield node
                for value in node.values():
                    yield from objects(value)

        assert all(obj["additionalProperties"] is False for obj in objects(schema))


# ============================================================================
# The client and the request
# ============================================================================


class TestRequest:
    def test_client_has_the_timeout_and_no_retries(self):
        with patch("app.services.llm.claude.anthropic.Anthropic") as client_cls:
            svc = ClaudeMedicalCodingService(MODEL, api_key="sk-test")

        client_cls.assert_called_once_with(
            api_key="sk-test", timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0
        )
        assert svc.model_name == MODEL

    def test_request_asks_for_the_schema_with_fallbacks(self, service, catalog):
        create = service.client.beta.messages.create
        create.return_value = _answer({"icd10Code": "E11", "description": "Diabetes"})

        service.code_chronic_condition("Diabetes")

        kwargs = create.call_args.kwargs
        assert kwargs["model"] == MODEL
        assert kwargs["max_tokens"] == claude.MAX_TOKENS
        assert kwargs["messages"][0]["role"] == "user"
        assert kwargs["output_config"] == {
            "effort": settings.LLM_EFFORT,
            "format": {"type": "json_schema", "schema": CHRONIC_CONDITION_OUTPUT_SCHEMA},
        }
        assert kwargs["betas"] == [claude.FALLBACK_BETA]
        assert kwargs["fallbacks"] == "default"
        # Sampling parameters are rejected by this model.
        assert "temperature" not in kwargs


# ============================================================================
# extract_diagnoses
# ============================================================================


class TestExtractDiagnoses:
    def test_answer_is_validated_and_names_the_model(self, service, catalog):
        service.client.beta.messages.create.return_value = _answer(
            {"diagnoses": [{"icd10Code": "J459", "icd11Code": "CA23", "description": "Asma"}]}
        )

        diagnoses = service.extract_diagnoses("tos", None, None, None)

        assert [d.icd10Code for d in diagnoses] == ["J459"]
        assert diagnoses[0].icd11Code == "CA23"
        assert diagnoses[0].model == MODEL

    def test_fallback_model_is_named_and_logged(self, service, catalog, app_log):
        service.client.beta.messages.create.return_value = _answer(
            {"diagnoses": [{"icd10Code": "J459", "description": "Asma"}]},
            model="claude-opus-4-8",
        )

        diagnoses = service.extract_diagnoses("tos", None, None, None)

        assert diagnoses[0].model == "claude-opus-4-8"
        assert "fallback model claude-opus-4-8" in app_log.text

    def test_refusal_gives_R69(self, service, app_log):
        service.client.beta.messages.create.return_value = _answer(
            None, stop_reason="refusal", stop_details=SimpleNamespace(category="bio"), text=False
        )

        diagnoses = service.extract_diagnoses("tos", None, None, None)

        assert [d.icd10Code for d in diagnoses] == [FALLBACK_ICD10_CODE]
        assert diagnoses[0].source == CodeSource.AI_FALLBACK
        assert "stop_reason=refusal category=bio" in app_log.text

    def test_cut_answer_gives_R69(self, service):
        service.client.beta.messages.create.return_value = _answer(
            {"diagnoses": []}, stop_reason="max_tokens"
        )
        assert service.extract_diagnoses("tos", None, None, None)[0].icd10Code == FALLBACK_ICD10_CODE

    def test_answer_without_text_gives_R69(self, service):
        service.client.beta.messages.create.return_value = _answer(None, text=False)
        assert service.extract_diagnoses("tos", None, None, None)[0].icd10Code == FALLBACK_ICD10_CODE

    def test_api_error_gives_R69_and_logs_no_clinical_text(self, service, app_log):
        service.client.beta.messages.create.side_effect = RuntimeError("tos persistente")

        diagnoses = service.extract_diagnoses("tos persistente", None, None, None)

        assert diagnoses[0].icd10Code == FALLBACK_ICD10_CODE
        assert "RuntimeError" in app_log.text
        assert "tos persistente" not in app_log.text


# ============================================================================
# code_family_history_item / code_chronic_condition
# ============================================================================


class TestBackgroundCoding:
    def test_family_history_is_validated(self, service, catalog):
        service.client.beta.messages.create.return_value = _answer(
            {"icd10Code": "C509", "icd11Code": None, "description": "Cáncer de mama"}
        )
        result = service.code_family_history_item("Cáncer de mama")
        assert result["icd10Code"] == "C509"
        assert "fallback" not in result

    def test_family_history_error_gives_R69(self, service):
        service.client.beta.messages.create.side_effect = RuntimeError("boom")
        result = service.code_family_history_item("Cáncer de mama")
        assert result["icd10Code"] == FALLBACK_ICD10_CODE
        assert result["fallback"] is True

    def test_chronic_condition_is_validated(self, service, catalog):
        service.client.beta.messages.create.return_value = _answer(
            {"icd10Code": "E11", "icd11Code": "5A11", "description": "Diabetes tipo 2"}
        )
        assert service.code_chronic_condition("Diabetes")["icd10Code"] == "E11"

    def test_chronic_condition_error_gives_R69(self, service):
        service.client.beta.messages.create.side_effect = RuntimeError("boom")
        assert service.code_chronic_condition("Diabetes")["fallback"] is True


# ============================================================================
# Provenance and the factory
# ============================================================================


def test_provenance_keeps_the_model_that_answered():
    answered = DiagnosisItem(icd10Code="J459", description="Asma", model="claude-opus-4-8")
    unnamed = DiagnosisItem(icd10Code="E11", description="Diabetes")

    marked = mark_ai_diagnoses([answered, unnamed], MODEL)

    assert [d.model for d in marked] == ["claude-opus-4-8", MODEL]


class TestFactory:
    def test_anthropic_backend_uses_the_default_model(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "Anthropic")
        monkeypatch.setattr(settings, "LLM_MODEL_NAME", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")

        with patch("app.services.llm.factory.ClaudeMedicalCodingService") as claude_cls:
            service = get_llm_service()

        assert service is claude_cls.return_value
        claude_cls.assert_called_once_with(model_name=claude.DEFAULT_MODEL, api_key="sk-test")

    def test_anthropic_backend_uses_the_configured_model(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "anthropic")
        monkeypatch.setattr(settings, "LLM_MODEL_NAME", "claude-sonnet-5-5")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")

        with patch("app.services.llm.factory.ClaudeMedicalCodingService") as claude_cls:
            get_llm_service()

        claude_cls.assert_called_once_with(model_name="claude-sonnet-5-5", api_key="sk-test")

    def test_anthropic_backend_without_key_does_not_start(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "anthropic")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            get_llm_service()

    def test_gemini_backend_without_model_uses_its_default(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_BACKEND", "gemini")
        monkeypatch.setattr(settings, "LLM_MODEL_NAME", None)

        with patch("app.services.llm.factory.GeminiMedicalCodingService") as gemini_cls:
            get_llm_service()

        assert gemini_cls.call_args.kwargs["model_name"] == "gemini-3.8-flash"
