"""
Tests for the updated LLM fallback behavior.

Verifies that:
  - LLM errors produce R69 (honest fallback), not Z00.0/Z84.8 (false diagnoses).
  - Post-LLM validation rejects codes not in the Vulcano catalog.
  - Valid codes pass through unchanged.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.patient import DiagnosisItem
from app.services.llm.base import FALLBACK_ICD10_CODE
from app.services.llm.gemini import (
    GeminiMedicalCodingService,
    _make_fallback_diagnosis,
    _validate_and_fix_diagnosis,
    _validate_and_fix_dict,
)

# ============================================================================
# Fallback creation tests
# ============================================================================


class TestFallbackCreation:
    def test_fallback_diagnosis_uses_R69(self):
        diag = _make_fallback_diagnosis("test reason")
        assert diag.icd10Code == "R69"
        assert "test reason" in diag.description

    def test_fallback_is_never_Z00(self):
        """C1b: Z00.0 must NEVER appear as a fallback."""
        diag = _make_fallback_diagnosis("any reason")
        assert diag.icd10Code != "Z00.0"

    def test_fallback_is_never_Z84(self):
        """C1b: Z84.8 must NEVER appear as a fallback."""
        diag = _make_fallback_diagnosis("any reason")
        assert diag.icd10Code != "Z84.8"


# ============================================================================
# Post-LLM validation tests
# ============================================================================


class TestPostLLMValidation:
    """Tests for _validate_and_fix_diagnosis / _validate_and_fix_dict."""

    def test_valid_icd10_passes_through(self):
        """A code that exists in the catalog is accepted as-is."""
        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = True

            diag = DiagnosisItem(icd10Code="A09", icd11Code="1A40.Z", description="Diarrea")
            result = _validate_and_fix_diagnosis(diag)

            assert result.icd10Code == "A09"
            assert result.icd11Code == "1A40.Z"

    def test_invalid_icd10_replaced_with_R69(self):
        """A code NOT in the catalog is replaced with R69."""
        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = False

            diag = DiagnosisItem(icd10Code="Z00.129", description="Fake code")
            result = _validate_and_fix_diagnosis(diag)

            assert result.icd10Code == FALLBACK_ICD10_CODE
            assert "Z00.129" in result.description  # preserves the failed code for debugging

    def test_invalid_icd11_stripped_not_rejected(self):
        """Invalid ICD-11 is stripped (set to None), not the whole diagnosis."""
        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = False

            diag = DiagnosisItem(icd10Code="A09", icd11Code="INVALID", description="Diarrea")
            result = _validate_and_fix_diagnosis(diag)

            assert result.icd10Code == "A09"  # ICD-10 preserved
            assert result.icd11Code is None  # ICD-11 stripped

    def test_dict_validation_replaces_invalid_icd10(self):
        """Family history / chronic condition dict with invalid ICD-10 → R69."""
        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = False

            result = _validate_and_fix_dict({
                "icd10Code": "INVENTED",
                "icd11Code": None,
                "description": "Diabetes",
            })

            assert result["icd10Code"] == FALLBACK_ICD10_CODE
            assert "INVENTED" in result["description"]

    def test_dict_validation_passes_valid_code(self):
        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = True

            result = _validate_and_fix_dict({
                "icd10Code": "E11",
                "icd11Code": "5A11",
                "description": "Diabetes mellitus tipo 2",
            })

            assert result["icd10Code"] == "E11"
            assert result["icd11Code"] == "5A11"


# ============================================================================
# Integration: Gemini service error → R69 fallback
# ============================================================================


class TestGeminiErrorFallback:
    """Verify that Gemini errors produce R69, not Z00.0/Z84.8."""

    @pytest.fixture
    def gemini_service(self):
        with patch("app.services.llm.gemini.genai"):
            svc = GeminiMedicalCodingService(
                model_name="test-model", project_id="test"
            )
            return svc

    def test_extract_diagnoses_error_returns_R69(self, gemini_service):
        gemini_service._call = MagicMock(side_effect=RuntimeError("API down"))
        result = gemini_service.extract_diagnoses("h", "p", "s", "t")
        assert len(result) == 1
        assert result[0].icd10Code == "R69"
        assert result[0].icd10Code != "Z00.0"

    def test_code_family_history_error_returns_R69(self, gemini_service):
        gemini_service._call = MagicMock(side_effect=RuntimeError("API down"))
        result = gemini_service.code_family_history_item("Diabetes")
        assert result["icd10Code"] == "R69"
        assert result["icd10Code"] != "Z84.8"
        assert "Diabetes" in result["description"]

    def test_code_chronic_condition_error_returns_R69(self, gemini_service):
        gemini_service._call = MagicMock(side_effect=RuntimeError("API down"))
        result = gemini_service.code_chronic_condition("Hipertensión")
        assert result["icd10Code"] == "R69"
        assert result["icd10Code"] != "Z84.8"
        assert "Hipertensión" in result["description"]

    def test_extract_diagnoses_validates_codes(self, gemini_service):
        """Valid LLM response gets validated against catalog."""
        gemini_service._call = MagicMock(return_value=json.dumps([
            {"icd10Code": "A09", "icd11Code": None, "description": "Diarrea"},
        ]))
        with patch("app.services.llm.gemini.terminology") as mock_term:
            mock_term.validate_icd10.return_value = True
            mock_term.validate_icd11.return_value = True

            result = gemini_service.extract_diagnoses("h", "p", "s", "t")

            assert len(result) == 1
            assert result[0].icd10Code == "A09"
            mock_term.validate_icd10.assert_called_with("A09")
