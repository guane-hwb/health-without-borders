"""
Tests for ISO 3166-1 normalisation and the two country-valued FHIR extensions.

The RDA implementation guide binds both ``ExtensionPatientNationality`` and
``ExtensionCountryCode`` to a value set of ISO 3166-1 *numeric* codes with
``required`` strength. The app stores alpha-3. These tests pin the conversion
that reconciles the two at the bundle boundary.
"""

import logging
from datetime import date

import pytest

from app.schemas.patient import (
    Address,
    BiologicalSex,
    DocumentType,
    GuardianInfo,
    PatientFullRecord,
    PatientIdentification,
    PatientInfo,
)
from app.services import iso3166
from app.services.fhir_service import (
    EXT_COUNTRY_CODE,
    EXT_NATIONALITY,
    SYSTEM_COUNTRY,
    _build_patient_resource,
)

# ============================================================================
# iso3166.to_numeric
# ============================================================================


@pytest.mark.parametrize(
    ("alpha3", "numeric"),
    [
        ("COL", "170"),  # Colombia
        ("VEN", "862"),  # Venezuela
        ("ECU", "218"),  # Ecuador
        ("PER", "604"),  # Peru
        ("HTI", "332"),  # Haiti
        ("CUB", "192"),  # Cuba
    ],
)
def test_every_nationality_offered_by_the_app_maps_to_numeric(alpha3, numeric):
    """The registration form offers exactly these six. None may fall through."""
    assert iso3166.to_numeric(alpha3) == numeric


def test_alpha2_input_maps_to_numeric():
    assert iso3166.to_numeric("CO") == "170"


def test_numeric_input_passes_through_and_is_zero_padded():
    """A future migration to numeric storage must not require touching this."""
    assert iso3166.to_numeric("170") == "170"
    assert iso3166.to_numeric("4") == "004"


def test_input_is_case_insensitive_and_whitespace_tolerant():
    assert iso3166.to_numeric(" col ") == "170"
    assert iso3166.to_numeric("Ven") == "862"


@pytest.mark.parametrize("value", ["ZZZ", "XX", "", "   ", None, "1700", "999", "C"])
def test_unrecognised_input_yields_none(value):
    assert iso3166.to_numeric(value) is None


def test_display_is_the_iso_short_name():
    assert iso3166.display_for("170") == "Colombia"
    assert iso3166.display_for("862") == "Venezuela, Bolivarian Republic of"
    assert iso3166.display_for("999") is None


def test_the_table_is_internally_consistent():
    """Every alpha-3 resolves to a numeric that has a display."""
    for _alpha2, alpha3, numeric, name in iso3166._COUNTRIES:
        assert iso3166.to_numeric(alpha3) == numeric
        assert iso3166.display_for(numeric) == name
    assert len(iso3166._COUNTRIES) == 249


# ============================================================================
# Patient resource
# ============================================================================


def _record(*, nationality: str = "COL", country: str = "COL") -> PatientFullRecord:
    return PatientFullRecord(
        patientId="p-1",
        device_uid="04:TEST:UID",
        patientInfo=PatientInfo(
            identification=PatientIdentification(
                documentType=DocumentType.CC,
                documentNumber="1090123456",
            ),
            firstLastName="Ramírez",
            firstName="Ana",
            dob=date(1990, 5, 3),
            nationalityCode=nationality,
            nationalityName="Colombia",
            biologicalSex=BiologicalSex.F,
            address=Address(
                city="Cúcuta",
                state="Norte de Santander",
                country=country,
                countryName="Colombia",
            ),
        ),
        guardianInfo=GuardianInfo(name="", relationship="", phone=""),
    )


def _nationality_ext(resource):
    return next(
        (e for e in resource["extension"] if e["url"] == EXT_NATIONALITY), None
    )


def _country_ext(resource):
    address = resource["address"][0]
    if "_country" not in address:
        return None
    return address["_country"]["extension"][0]


def test_nationality_extension_emits_the_numeric_code():
    resource = _build_patient_resource(_record(nationality="COL"))

    coding = _nationality_ext(resource)["valueCoding"]
    assert coding["system"] == SYSTEM_COUNTRY
    assert coding["code"] == "170"
    assert coding["display"] == "Colombia"


def test_nationality_display_comes_from_iso_not_from_user_text():
    """`nationalityName` is free-form form input; the guide's code system wins."""
    record = _record(nationality="VEN")
    record.patientInfo.nationalityName = "Venezuela"

    coding = _nationality_ext(_build_patient_resource(record))["valueCoding"]

    assert coding["code"] == "862"
    assert coding["display"] == "Venezuela, Bolivarian Republic of"


def test_address_country_extension_emits_the_numeric_code():
    resource = _build_patient_resource(_record(country="COL"))

    extension = _country_ext(resource)
    assert extension["url"] == EXT_COUNTRY_CODE
    assert extension["valueCoding"] == {
        "system": SYSTEM_COUNTRY,
        "code": "170",
        "display": "Colombia",
    }


def test_numeric_input_is_accepted_unchanged_by_both_extensions():
    resource = _build_patient_resource(_record(nationality="862", country="170"))

    assert _nationality_ext(resource)["valueCoding"]["code"] == "862"
    assert _country_ext(resource)["valueCoding"]["code"] == "170"


def test_the_emitted_coding_carries_no_version_or_user_selected():
    """The guide pins `Coding.system`; nothing else belongs in there."""
    coding = _nationality_ext(_build_patient_resource(_record()))["valueCoding"]

    assert set(coding) == {"system", "code", "display"}


def test_unmappable_nationality_omits_the_extension_and_warns(caplog):
    """
    A bad country code must not block a clinical record from syncing, and must
    not produce a bundle that violates a required binding either.
    """
    logger = logging.getLogger("app.services.fhir_service")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.WARNING, logger="app.services.fhir_service")
    try:
        resource = _build_patient_resource(_record(nationality="XXX"))
    finally:
        logger.removeHandler(caplog.handler)

    assert _nationality_ext(resource) is None
    assert "patientInfo.nationalityCode" in caplog.text
    assert "XXX" in caplog.text


def test_unmappable_address_country_omits_the_extension(caplog):
    logger = logging.getLogger("app.services.fhir_service")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.WARNING, logger="app.services.fhir_service")
    try:
        resource = _build_patient_resource(_record(country="XXX"))
    finally:
        logger.removeHandler(caplog.handler)

    assert _country_ext(resource) is None
    # The human-readable country name survives; only the coded form is dropped.
    assert resource["address"][0]["country"] == "Colombia"
    assert "address.country" in caplog.text


def test_an_unmappable_nationality_still_produces_a_usable_patient():
    """Extensions may be empty; the rest of the resource must be intact."""
    resource = _build_patient_resource(_record(nationality="XXX", country="XXX"))

    assert resource["resourceType"] == "Patient"
    assert resource["extension"] == []
    assert resource["identifier"][0]["value"] == "1090123456"
