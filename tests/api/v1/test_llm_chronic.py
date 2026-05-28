"""
Unit tests for:
  - app/services/llm/gemini.py  → GeminiMedicalCodingService.code_chronic_condition
  - app/services/llm/prompts.py → build_chronic_condition_prompt
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.llm.gemini import GeminiMedicalCodingService
from app.services.llm.prompts import build_chronic_condition_prompt

# ============================================================================
# PROMPTS — build_chronic_condition_prompt
# ============================================================================

class TestBuildChronicConditionPrompt:
    def test_contains_condition_description(self):
        prompt = build_chronic_condition_prompt("Diabetes mellitus tipo 2")
        assert "Diabetes mellitus tipo 2" in prompt

    def test_is_non_empty_string(self):
        prompt = build_chronic_condition_prompt("Hipertensión arterial")
        assert isinstance(prompt, str)
        assert len(prompt) > 10

    def test_different_inputs_produce_different_prompts(self):
        p1 = build_chronic_condition_prompt("Diabetes")
        p2 = build_chronic_condition_prompt("Asma")
        assert p1 != p2


# ============================================================================
# GEMINI — GeminiMedicalCodingService.code_chronic_condition
# ============================================================================

@pytest.fixture
def gemini_service():
    """
    Returns a GeminiMedicalCodingService with the Google genai client fully
    mocked so no network calls are made.
    """
    with patch("app.services.llm.gemini.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        service = GeminiMedicalCodingService(
            model_name="gemini-2.0-flash-001",
            project_id="test-project",
        )
        service._mock_client = mock_client  # expose for per-test control
        yield service


class TestCodeChronicConditionHappyPath:
    def test_returns_dict_with_icd_codes(self, gemini_service):
        """Happy path: Gemini returns a valid JSON response."""
        expected = {
            "icd10Code": "E11",
            "icd11Code": "5A11",
            "description": "Diabetes mellitus tipo 2",
        }
        gemini_service._call = MagicMock(return_value=json.dumps(expected))

        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = True
            result = gemini_service.code_chronic_condition("Diabetes mellitus tipo 2")

        assert result["icd10Code"] == "E11"
        assert result["icd11Code"] == "5A11"
        assert result["description"] == "Diabetes mellitus tipo 2"

    def test_call_uses_correct_prompt_and_config(self, gemini_service):
        """_call is invoked once with a non-empty prompt."""
        gemini_service._call = MagicMock(return_value=json.dumps({
            "icd10Code": "I10", "icd11Code": "BA00.Z",
            "description": "Hipertensión esencial (primaria)",
        }))

        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = True
            gemini_service.code_chronic_condition("Hipertensión")

        gemini_service._call.assert_called_once()
        prompt_arg = gemini_service._call.call_args[0][0]
        assert "Hipertensión" in prompt_arg

    def test_null_icd11_code_is_preserved(self, gemini_service):
        """icd11Code=null in the response must not be replaced or omitted."""
        gemini_service._call = MagicMock(return_value=json.dumps({
            "icd10Code": "J45.9",
            "icd11Code": None,
            "description": "Asma, no especificada",
        }))

        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = True
            result = gemini_service.code_chronic_condition("Asma")

        assert result["icd10Code"] == "J45.9"
        assert result["icd11Code"] is None


class TestCodeChronicConditionErrorFallback:
    def test_json_parse_error_returns_fallback(self, gemini_service):
        """If the model returns invalid JSON, a safe fallback dict is returned (R69)."""
        gemini_service._call = MagicMock(return_value="NOT_VALID_JSON")

        result = gemini_service.code_chronic_condition("Glaucoma")

        assert result["icd10Code"] == "R69"
        assert result["icd11Code"] is None
        assert "Glaucoma" in result["description"]
        assert "Fallo en codificación IA" in result["description"]

    def test_network_error_returns_fallback(self, gemini_service):
        """If _call raises an exception, the fallback dict is returned (R69)."""
        gemini_service._call = MagicMock(side_effect=RuntimeError("Vertex AI down"))

        result = gemini_service.code_chronic_condition("Epilepsia")

        assert result["icd10Code"] == "R69"
        assert result["icd11Code"] is None
        assert "Epilepsia" in result["description"]

    def test_fallback_does_not_raise(self, gemini_service):
        """Error path must never propagate exceptions to the caller."""
        gemini_service._call = MagicMock(side_effect=Exception("Unexpected"))

        try:
            result = gemini_service.code_chronic_condition("Condición desconocida")
        except Exception:
            pytest.fail("code_chronic_condition raised an exception instead of returning fallback")

        assert isinstance(result, dict)
        assert result["icd10Code"] == "R69"
