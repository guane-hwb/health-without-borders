"""
Unit tests for app/core/phi_sanitizer.py
"""


from app.core.phi_sanitizer import (
    mask_id,
    mask_name,
    safe_doc_ref,
    safe_patient_ref,
    sanitize_error_body,
)

# ============================================================================
# mask_id
# ============================================================================

class TestMaskId:
    def test_none_returns_unknown(self):
        assert mask_id(None) == "unknown"

    def test_empty_string_returns_unknown(self):
        assert mask_id("") == "unknown"

    def test_short_value_fully_masked(self):
        assert mask_id("AB") == "***"
        assert mask_id("ABCDEF") == "***"

    def test_seven_chars_shows_first_and_last_three(self):
        assert mask_id("ABCDEFG") == "ABC***EFG"

    def test_uuid_style_id(self):
        result = mask_id("550e8400-e29b-41d4-a716-446655440000")
        assert result.startswith("550")
        assert result.endswith("000")
        assert "***" in result

    def test_document_number(self):
        result = mask_id("1098765432")
        assert result == "109***432"

    def test_original_value_not_present(self):
        """The full original value must never appear in the output."""
        original = "VZ-9876543"
        result = mask_id(original)
        assert original not in result


# ============================================================================
# mask_name
# ============================================================================

class TestMaskName:
    def test_none_returns_stars(self):
        assert mask_name(None) == "***"

    def test_empty_string_returns_stars(self):
        assert mask_name("") == "***"
        assert mask_name("   ") == "***"

    def test_single_word(self):
        assert mask_name("María") == "M***"

    def test_two_words(self):
        assert mask_name("Juan Pérez") == "J*** P***"

    def test_three_words(self):
        assert mask_name("Ana María López") == "A*** M*** L***"

    def test_single_char_word(self):
        assert mask_name("A") == "A"

    def test_original_name_not_present(self):
        original = "Santiago Rodríguez"
        result = mask_name(original)
        assert "Santiago" not in result
        assert "Rodríguez" not in result


# ============================================================================
# safe_patient_ref
# ============================================================================

class TestSafePatientRef:
    def test_delegates_to_mask_id(self):
        assert safe_patient_ref("TEST-UNIT-001") == mask_id("TEST-UNIT-001")

    def test_none(self):
        assert safe_patient_ref(None) == "unknown"


# ============================================================================
# safe_doc_ref
# ============================================================================

class TestSafeDocRef:
    def test_normal_document(self):
        result = safe_doc_ref("CC", "1098765432")
        assert result == "CC:109***432"

    def test_short_document(self):
        result = safe_doc_ref("PT", "VZ-12")
        assert result == "PT:***"

    def test_none_type(self):
        result = safe_doc_ref(None, None)
        assert result == "?:unknown"


# ============================================================================
# sanitize_error_body
# ============================================================================

class TestSanitizeErrorBody:
    def test_none_returns_empty_marker(self):
        assert sanitize_error_body(None) == "<empty>"

    def test_empty_string_returns_empty_marker(self):
        assert sanitize_error_body("") == "<empty>"

    def test_short_body_passes_through(self):
        body = '{"status": "error", "code": 400}'
        assert sanitize_error_body(body) == body

    def test_truncates_long_body(self):
        body = "x" * 1000
        result = sanitize_error_body(body, max_length=100)
        assert len(result) < 200
        assert "truncated" in result
        assert "1000 chars total" in result

    def test_redacts_family_name(self):
        body = '{"family": "Pérez Rodríguez", "given": ["Juan"]}'
        result = sanitize_error_body(body)
        assert "Pérez" not in result
        assert "[REDACTED]" in result

    def test_redacts_display_field(self):
        body = '{"display": "Diabetes mellitus tipo 2"}'
        result = sanitize_error_body(body)
        assert "Diabetes" not in result

    def test_redacts_value_field(self):
        body = '{"value": "1098765432"}'
        result = sanitize_error_body(body)
        assert "1098765432" not in result

    def test_preserves_short_values(self):
        """Values under 4 chars are likely codes, not PHI — should be kept."""
        body = '{"value": "M"}'
        result = sanitize_error_body(body)
        assert '"M"' in result

    def test_redacts_text_field(self):
        body = '{"text": "Paciente con antecedentes de hipertensión arterial severa"}'
        result = sanitize_error_body(body)
        assert "hipertensión" not in result

    def test_redacts_name_field(self):
        body = '{"name": "Hospital Erasmo Meoz de Cúcuta"}'
        result = sanitize_error_body(body)
        assert "Erasmo" not in result
