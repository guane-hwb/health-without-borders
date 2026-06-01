"""
Tests for app.services.terminology — ICD-10/11 code validation.

Tests cover both modes:
  - Full catalog (>5000 codes): exact match validation
  - Fragment (<5000 codes): format-based validation
"""

import json
from unittest.mock import patch

import pytest

from app.services.terminology import TerminologyService


@pytest.fixture
def full_icd10_file(tmp_path):
    """Simulate a full ICD-10 catalog (>5000 codes)."""
    codes = {f"A{i:03d}": f"Disease {i}" for i in range(6000)}
    codes["B86"] = "Escabiosis"
    codes["E441"] = "Desnutrición proteicoenergética leve"
    codes["R69"] = "Causas de morbilidad desconocidas y no especificadas"
    codes["A099"] = "Gastroenteritis y colitis de origen no especificado"
    path = tmp_path / "icd10_codes.json"
    path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def fragment_icd10_file(tmp_path):
    """Simulate a Vulcano fragment (~395 codes)."""
    codes = {
        "A099": "Gastroenteritis y colitis de origen no especificado",
        "Z001": "Control de salud de rutina del niño",
    }
    path = tmp_path / "icd10_codes.json"
    path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def full_icd11_file(tmp_path):
    codes = {f"1A{i:02d}": f"Disease {i}" for i in range(1500)}
    codes["CA0Z"] = "Trastornos respiratorios"
    path = tmp_path / "icd11_codes.json"
    path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def fragment_icd11_file(tmp_path):
    codes = {"CA0Z": "Trastornos respiratorios"}
    path = tmp_path / "icd11_codes.json"
    path.write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    return path


class TestFullCatalogValidation:
    """When full catalog is loaded, validation is exact match."""

    @pytest.fixture(autouse=True)
    def setup(self, full_icd10_file, full_icd11_file):
        self.svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", full_icd10_file),
            patch("app.services.terminology.ICD11_FILE", full_icd11_file),
        ):
            self.svc.load()

    def test_detects_full_catalog(self):
        assert self.svc.has_full_icd10 is True
        assert self.svc.icd10_count > 5000

    def test_valid_code_in_catalog(self):
        assert self.svc.validate_icd10("B86") is True
        assert self.svc.validate_icd10("R69") is True
        assert self.svc.validate_icd10("A099") is True

    def test_invalid_code_not_in_catalog(self):
        """Exact match rejects codes not in the full catalog."""
        assert self.svc.validate_icd10("ZZZZZ") is False
        assert self.svc.validate_icd10("X999") is False

    def test_empty_and_none(self):
        assert self.svc.validate_icd10("") is False
        assert self.svc.validate_icd10(None) is False

    def test_case_insensitive(self):
        assert self.svc.validate_icd10("b86") is True
        assert self.svc.validate_icd10("r69") is True

    def test_display_name(self):
        assert self.svc.get_icd10_display("B86") == "Escabiosis"
        assert self.svc.get_icd10_display("X999") is None


class TestFragmentValidation:
    """When only a fragment is loaded, validation falls back to format check."""

    @pytest.fixture(autouse=True)
    def setup(self, fragment_icd10_file, fragment_icd11_file):
        self.svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", fragment_icd10_file),
            patch("app.services.terminology.ICD11_FILE", fragment_icd11_file),
        ):
            self.svc.load()

    def test_detects_fragment(self):
        assert self.svc.has_full_icd10 is False
        assert self.svc.icd10_count < 5000

    def test_valid_format_accepted(self):
        """B86 is valid WHO format even though it's not in the 395-code fragment."""
        assert self.svc.validate_icd10("B86") is True
        assert self.svc.validate_icd10("E441") is True
        assert self.svc.validate_icd10("R69") is True

    def test_invalid_format_rejected(self):
        assert self.svc.validate_icd10("ZZZZZ") is False
        assert self.svc.validate_icd10("123") is False
        assert self.svc.validate_icd10("A09.9") is False  # Dot not allowed

    def test_display_from_fragment(self):
        assert self.svc.get_icd10_display("A099") is not None
        assert self.svc.get_icd10_display("B86") is None  # Not in fragment


class TestICD11:
    """ICD-11 is optional — None/empty always passes."""

    @pytest.fixture(autouse=True)
    def setup(self, fragment_icd10_file, fragment_icd11_file):
        self.svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", fragment_icd10_file),
            patch("app.services.terminology.ICD11_FILE", fragment_icd11_file),
        ):
            self.svc.load()

    def test_null_passes(self):
        assert self.svc.validate_icd11(None) is True
        assert self.svc.validate_icd11("") is True

    def test_valid_format(self):
        assert self.svc.validate_icd11("1A40.Z") is True
        assert self.svc.validate_icd11("5A11") is True
        assert self.svc.validate_icd11("5B50.0") is True

    def test_invalid_format(self):
        assert self.svc.validate_icd11("not-a-code") is False


class TestNoFilesLoaded:
    def test_format_validation_still_works(self, tmp_path):
        svc = TerminologyService()
        with (
            patch("app.services.terminology.ICD10_FILE", tmp_path / "nope.json"),
            patch("app.services.terminology.ICD11_FILE", tmp_path / "nope.json"),
        ):
            svc.load()

        assert svc.validate_icd10("A099") is True   # Valid format
        assert svc.validate_icd10("ZZZZZ") is False  # Invalid format
        assert svc.icd10_count == 0