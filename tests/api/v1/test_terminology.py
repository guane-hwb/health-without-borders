"""
Tests for app.services.terminology — ICD-10/11 code validation.
"""

import json
from unittest.mock import patch

import pytest

from app.services.terminology import TerminologyService


@pytest.fixture
def sample_icd10_file(tmp_path):
    """Create a temporary ICD-10 codes file."""
    codes = {
        "A09": "Diarrea y gastroenteritis de presunto origen infeccioso",
        "E11": "Diabetes mellitus tipo 2",
        "I10": "Hipertensión esencial (primaria)",
        "J06.9": "Infección aguda de las vías respiratorias superiores, no especificada",
        "R69": "Causas de morbilidad desconocidas y no especificadas",
        "Z00.0": "Examen médico general",
    }
    path = tmp_path / "icd10_codes.json"
    path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def sample_icd11_file(tmp_path):
    """Create a temporary ICD-11 codes file."""
    codes = {
        "1A40.Z": "Gastroenteritis y colitis de origen no especificado",
        "5A11": "Diabetes mellitus tipo 2",
        "BA00.Z": "Hipertensión esencial (primaria)",
    }
    path = tmp_path / "icd11_codes.json"
    path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    return path


class TestTerminologyService:

    def test_load_and_validate_icd10(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.is_loaded
        assert svc.icd10_count == 6
        assert svc.icd11_count == 3

    def test_valid_icd10_code(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.validate_icd10("A09") is True
        assert svc.validate_icd10("E11") is True
        assert svc.validate_icd10("R69") is True

    def test_invalid_icd10_code(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.validate_icd10("ZZZZZ") is False
        assert svc.validate_icd10("Z00.129") is False  # US ICD-10-CM, not WHO
        assert svc.validate_icd10("") is False
        assert svc.validate_icd10(None) is False

    def test_icd10_case_insensitive(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.validate_icd10("a09") is True
        assert svc.validate_icd10("e11") is True

    def test_valid_icd11_code(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.validate_icd11("5A11") is True
        assert svc.validate_icd11("BA00.Z") is True

    def test_invalid_icd11_code(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.validate_icd11("XXXXX") is False

    def test_null_icd11_is_valid(self, sample_icd10_file, sample_icd11_file):
        """ICD-11 is optional — null/None should always pass."""
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.validate_icd11(None) is True
        assert svc.validate_icd11("") is True

    def test_get_display(self, sample_icd10_file, sample_icd11_file):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", sample_icd10_file),
            patch("app.services.terminology.ICD11_FILE", sample_icd11_file),
        ):
            svc.load()

        assert svc.get_icd10_display("A09") is not None
        assert "Diarrea" in svc.get_icd10_display("A09")
        assert svc.get_icd10_display("ZZZZZ") is None

    def test_graceful_degradation_missing_file(self, tmp_path):
        """If catalog files don't exist, validation accepts all codes."""
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", tmp_path / "nonexistent.json"),
            patch("app.services.terminology.ICD11_FILE", tmp_path / "nonexistent.json"),
        ):
            svc.load()

        # With no catalog loaded, all codes pass (graceful degradation)
        assert svc.validate_icd10("ANYTHING") is True
        assert svc.validate_icd11("ANYTHING") is True
        assert svc.icd10_count == 0
