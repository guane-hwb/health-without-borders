"""
Unit tests for app/services/fhir_service.py

Coverage targets (Codecov patch report):
  - Helpers: _fhir_datetime, _sex_to_fhir, _sex_to_gender_group,
             _allergy_category_display, _family_rel_display, _diag_type_display,
             _risk_factor_display, _zone_display, _doc_type_display,
             _modality_display, _service_group_display, _environment_display,
             _assign_fullurls_and_rewrite_refs, _patient_id
  - Resource builders: _build_patient_resource, _build_organization_ips,
             _build_organization_eapb, _build_practitioner, _build_location,
             _build_encounter, _build_condition, _build_condition_statement,
             _build_allergy_statement, _build_allergy_encounter,
             _build_family_member_history, _build_medication_statement,
             _build_medication_request, _build_risk_assessment,
             _build_occupation_obs, _build_attendance_allowance
  - Bundle builders: build_rda_paciente, build_rda_consulta
  - Public API: convert_to_fhir_rda (delta logic)

All tests are pure-Python — no DB, no HTTP client.
"""

from datetime import date, datetime

from app.schemas.patient import (
    Address,
    AllergyCategory,
    AllergyInfo,
    BackgroundHistory,
    BiologicalSex,
    CareEnvironment,
    CareModality,
    ChronicConditionItem,
    ClinicalEvaluation,
    DiagnosisItem,
    DiagnosisType,
    FamilyHistoryItem,
    FamilyRelationship,
    GuardianInfo,
    IncapacityInfo,
    IncapacityScope,
    MedicalHistoryItem,
    MedicationRequestItem,
    MedicationStatementItem,
    PatientFullRecord,
    PatientIdentification,
    PatientInfo,
    PayerInfo,
    PractitionerInfo,
    ProviderInfo,
    ResidenceZone,
    RiskFactor,
    RiskFactorType,
    ServiceGroup,
)
from app.services.fhir_service import (
    _allergy_category_display,
    _assign_fullurls_and_rewrite_refs,
    _build_allergy_encounter,
    _build_allergy_statement,
    _build_attendance_allowance,
    _build_condition,
    _build_condition_statement,
    _build_encounter,
    _build_family_member_history,
    _build_location,
    _build_medication_request,
    _build_medication_statement,
    _build_occupation_obs,
    _build_organization_eapb,
    _build_organization_ips,
    _build_patient_resource,
    _build_practitioner,
    _build_risk_assessment,
    _diag_type_display,
    _doc_type_display,
    _environment_display,
    _family_rel_display,
    _fhir_datetime,
    _modality_display,
    _patient_id,
    _risk_factor_display,
    _service_group_display,
    _sex_to_fhir,
    _sex_to_gender_group,
    _zone_display,
    build_rda_consulta,
    build_rda_paciente,
    convert_to_fhir_rda,
)

# ============================================================================
# FIXTURES — minimal valid patient record
# ============================================================================

def _make_patient(
    *,
    with_guardian: bool = True,
    with_background: bool = True,
    with_allergies: bool = True,
    with_visit: bool = False,
    visit_override: MedicalHistoryItem = None,
) -> PatientFullRecord:
    """Factory for a minimal but valid PatientFullRecord."""
    addr = Address(
        street="Calle 10 # 5-20",
        city="Cúcuta",
        cityCode="54001",
        state="Norte de Santander",
        zipCode="540001",
        country="170",
        countryName="Colombia",
        zone=ResidenceZone.URBANA,
    )
    pi = PatientInfo(
        identification=PatientIdentification(documentType="PT", documentNumber="VZ-9876543"),
        firstLastName="Rodríguez",
        secondLastName="Pérez",
        firstName="Santiago",
        secondName="Andrés",
        dob=date(2020, 1, 1),
        nationalityCode="862",
        nationalityName="Venezuela",
        biologicalSex=BiologicalSex.M,
        address=addr,
        bloodType="O+",
        weight=15.5,
        height=100.0,
    )
    guardian = GuardianInfo(
        name="María Pérez",
        relationship="Madre",
        phone="+573001234567",
        device_uid="GUARDIAN-UID-001",
    ) if with_guardian else GuardianInfo(name="", relationship="", phone="")

    background = BackgroundHistory(
        chronicConditions=[
            ChronicConditionItem(
                chronicDescription="Diabetes mellitus tipo 2",
                chronicCie10Code="E11",
                chronicCie11Code="5A11",
            )
        ],
        familyHistory=[
            FamilyHistoryItem(
                conditionDescription="Hipertensión",
                conditionCie10Code="I10",
                relationship=FamilyRelationship.PADRES,
            )
        ],
    ) if with_background else None

    allergies = [
        AllergyInfo(
            category=AllergyCategory.MEDICAMENTO,
            allergen="Penicilina",
            reaction="Habones",
            notes="Reacción leve",
        )
    ] if with_allergies else []

    visits = []
    if visit_override:
        visits = [visit_override]
    elif with_visit:
        visits = [_make_visit()]

    return PatientFullRecord(
        patientId="TEST-FHIR-001",
        device_uid="04:TEST:UID",
        patientInfo=pi,
        guardianInfo=guardian,
        backgroundHistory=background,
        allergies=allergies,
        medicalHistory=visits,
        vaccinationRecord=[],
    )


def _make_visit(
    *,
    with_diagnosis: bool = True,
    with_provider: bool = True,
    with_practitioner: bool = True,
    with_payer: bool = False,
    with_risk_factors: bool = False,
    with_prescriptions: bool = False,
    with_occupation: bool = False,
    with_incapacity: bool = False,
    with_discharge: bool = False,
    with_entry_route: bool = False,
) -> MedicalHistoryItem:
    provider = ProviderInfo(
        repsCode="540015400101",
        name="Hospital Erasmo Meoz",
        nitNumber="890500600",
        locationSeatCode="540015400101-01",
    ) if with_provider else None

    practitioner = PractitionerInfo(
        documentType="CC",
        documentNumber="88001234",
        name="GOMEZ, ANDREA",
        firstName="Andrea",
        firstLastName="Gomez",
    ) if with_practitioner else None

    diagnosis = [
        DiagnosisItem(icd10Code="J06.9", icd11Code="CA0Z",
                      description="Infección aguda de las vías respiratorias superiores")
    ] if with_diagnosis else []

    risk_factors = [RiskFactor(type=RiskFactorType.BIOLOGICOS, name="Exposición a virus")] \
        if with_risk_factors else []

    prescriptions = [
        MedicationRequestItem(
            medicationName="Acetaminofén",
            dciCode="N02BE01",
            dosage="500mg cada 8h",
            frequency="cada 8 horas",
            duration="5 días",
            route="oral",
        )
    ] if with_prescriptions else []

    payer = PayerInfo(code="EPS001", name="EPS Test") if with_payer else None
    incapacity = IncapacityInfo(scope=IncapacityScope.NUEVA, days=3) if with_incapacity else None

    return MedicalHistoryItem(
        type="Consultation",
        startDateTime=datetime(2026, 1, 15, 9, 0, 0),
        endDateTime=datetime(2026, 1, 15, 9, 45, 0),
        careModality=CareModality.INTRAMURAL,
        serviceGroup=ServiceGroup.CONSULTA_EXTERNA,
        careEnvironment=CareEnvironment.INSTITUCIONAL,
        provider=provider,
        practitioner=practitioner,
        clinicalEvaluation=ClinicalEvaluation(
            historyOfCurrentIllness="Fiebre de 3 días",
            generalPhysicalExamination="T: 38.5°C",
            systemsExamination="Respiratorio normal",
            treatmentPlanObservations="Reposo y antitérmicos",
        ),
        diagnosis=diagnosis,
        diagnosisType=DiagnosisType.IMPRESION_DIAGNOSTICA,
        riskFactors=risk_factors,
        prescriptions=prescriptions,
        payer=payer,
        incapacity=incapacity,
        occupation="2211" if with_occupation else None,
        occupationDescription="Médico general" if with_occupation else None,
        dischargeDisposition="04" if with_discharge else None,
        entryRoute="01" if with_entry_route else None,
    )


# ============================================================================
# HELPER FUNCTION TESTS
# ============================================================================

class TestFhirDatetime:
    def test_none_returns_utc_now(self):
        result = _fhir_datetime(None)
        assert result.endswith("Z")

    def test_datetime_object(self):
        dt = datetime(2026, 1, 15, 9, 0, 0)
        result = _fhir_datetime(dt)
        assert "2026-01-15" in result
        assert result.endswith("Z")

    def test_date_object(self):
        d = date(2020, 1, 1)
        result = _fhir_datetime(d)
        assert result == "2020-01-01"

    def test_string_passthrough(self):
        result = _fhir_datetime("2026-01-15T09:00:00Z")
        assert result == "2026-01-15T09:00:00Z"


class TestSexHelpers:
    def test_sex_to_fhir_male(self):
        assert _sex_to_fhir(BiologicalSex.M) == "male"

    def test_sex_to_fhir_female(self):
        assert _sex_to_fhir(BiologicalSex.F) == "female"

    def test_sex_to_fhir_indeterminate(self):
        assert _sex_to_fhir(BiologicalSex.I) == "other"

    def test_sex_to_gender_group_male(self):
        gg = _sex_to_gender_group(BiologicalSex.M)
        assert gg.value == "01"

    def test_sex_to_gender_group_female(self):
        gg = _sex_to_gender_group(BiologicalSex.F)
        assert gg.value == "02"

    def test_sex_to_gender_group_indeterminate(self):
        gg = _sex_to_gender_group(BiologicalSex.I)
        assert gg.value == "03"


class TestDisplayHelpers:
    def test_allergy_category_display_known(self):
        assert _allergy_category_display(AllergyCategory.MEDICAMENTO) == "Medicamento"

    def test_allergy_category_display_unknown_fallback(self):
        # Enum only has known values, test the fallback via patching value
        from unittest.mock import MagicMock
        fake = MagicMock()
        fake.value = "99"
        assert _allergy_category_display(fake) == "Otra"

    def test_family_rel_display(self):
        assert _family_rel_display(FamilyRelationship.ABUELOS) == "Abuelos"
        assert _family_rel_display(FamilyRelationship.PADRES) == "Padres"

    def test_diag_type_display(self):
        assert _diag_type_display(DiagnosisType.IMPRESION_DIAGNOSTICA) == "Impresión diagnóstica"
        assert _diag_type_display(DiagnosisType.CONFIRMADO_NUEVO) == "Confirmado Nuevo"

    def test_risk_factor_display(self):
        assert _risk_factor_display(RiskFactorType.BIOLOGICOS) == "Biológicos"
        assert _risk_factor_display(RiskFactorType.QUIMICOS) == "Químicos"

    def test_zone_display(self):
        assert _zone_display("01") == "Urbana"
        assert _zone_display("02") == "Rural"
        assert _zone_display("99") == "Urbana"  # fallback

    def test_doc_type_display_known(self):
        assert _doc_type_display("CC") == "Cédula ciudadanía"
        assert _doc_type_display("PT") == "Permiso Temporal de Permanencia"

    def test_doc_type_display_unknown_passthrough(self):
        assert _doc_type_display("XYZ") == "XYZ"

    def test_modality_display(self):
        assert _modality_display("01") == "Intramural"
        assert _modality_display("99") == ""  # unknown

    def test_service_group_display(self):
        assert _service_group_display("01") == "Consulta externa"
        assert _service_group_display("99") == ""

    def test_environment_display(self):
        assert _environment_display("05") == "Institucional"
        assert _environment_display("99") == ""


class TestPatientId:
    def test_patient_id_format(self):
        patient = _make_patient()
        assert _patient_id(patient) == "PT-VZ-9876543"


class TestAssignFullUrls:
    def test_rewrites_hash_references(self):
        """#id references are rewritten to urn:uuid after post-processing."""
        bundle = {
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "patient-1",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "cond-1",
                        "subject": {"reference": "#patient-1"},
                    }
                },
            ]
        }
        result = _assign_fullurls_and_rewrite_refs(bundle)

        # Every entry should now have a fullUrl
        for entry in result["entry"]:
            assert entry["fullUrl"].startswith("urn:uuid:")

        # The #patient-1 reference should be rewritten to the matching urn:uuid
        patient_urn = result["entry"][0]["fullUrl"]
        cond_ref = result["entry"][1]["resource"]["subject"]["reference"]
        assert cond_ref == patient_urn

    def test_entry_without_id_still_gets_full_url(self):
        bundle = {"entry": [{"resource": {"resourceType": "Bundle"}}]}
        result = _assign_fullurls_and_rewrite_refs(bundle)
        assert result["entry"][0]["fullUrl"].startswith("urn:uuid:")


# ============================================================================
# RESOURCE BUILDER TESTS
# ============================================================================

class TestBuildPatientResource:
    def test_basic_structure(self):
        patient = _make_patient()
        r = _build_patient_resource(patient)
        assert r["resourceType"] == "Patient"
        assert r["id"] == "PT-VZ-9876543"
        assert r["active"] is True
        assert r["gender"] == "male"
        assert r["birthDate"] == "2020-01-01"
        assert r["deceasedBoolean"] is False

    def test_name_includes_both_surnames(self):
        patient = _make_patient()
        r = _build_patient_resource(patient)
        assert "Rodríguez Pérez" in r["name"][0]["family"]

    def test_address_zone_extension(self):
        patient = _make_patient()
        r = _build_patient_resource(patient)
        addr = r["address"][0]
        assert addr["city"] == "Cúcuta"
        zone_ext = addr["extension"][0]
        assert zone_ext["valueCoding"]["code"] == "01"  # URBANA

    def test_gender_group_extension(self):
        patient = _make_patient()
        r = _build_patient_resource(patient)
        gg_ext = r["_gender"]["extension"][0]
        assert gg_ext["valueCoding"]["code"] == "01"  # Hombre

    def test_guardian_contact_included(self):
        patient = _make_patient(with_guardian=True)
        r = _build_patient_resource(patient)
        assert "contact" in r
        assert r["contact"][0]["name"]["text"] == "María Pérez"

    def test_no_street_omits_line(self):
        patient = _make_patient()
        patient.patientInfo.address.street = None
        r = _build_patient_resource(patient)
        assert "line" not in r["address"][0]


class TestBuildOrganizationIps:
    def test_returns_none_if_no_provider(self):
        assert _build_organization_ips(None) is None

    def test_basic_structure(self):
        provider = ProviderInfo(repsCode="540015400101", name="Hospital Meoz", nitNumber="890500600")
        r = _build_organization_ips(provider)
        assert r["resourceType"] == "Organization"
        assert r["id"] == "540015400101"
        # Two identifiers: NIT + CodigoPrestador
        assert len(r["identifier"]) == 2

    def test_unknown_nit_fallback(self):
        provider = ProviderInfo(repsCode="540015400101", name="Hospital Meoz")
        r = _build_organization_ips(provider)
        assert r["identifier"][0]["value"] == "Desconocido"


class TestBuildOrganizationEapb:
    def test_returns_none_if_no_payer(self):
        assert _build_organization_eapb(None) is None

    def test_returns_none_if_no_code(self):
        assert _build_organization_eapb(PayerInfo(name="EPS Test")) is None

    def test_basic_structure(self):
        r = _build_organization_eapb(PayerInfo(code="EPS001", name="EPS Test"))
        assert r["resourceType"] == "Organization"
        assert r["id"] == "EPS001"
        assert r["name"] == "EPS Test"


class TestBuildPractitioner:
    def test_returns_none_if_no_pract(self):
        assert _build_practitioner(None, "pract-id") is None

    def test_with_desglose_names(self):
        pract = PractitionerInfo(
            documentType="CC", documentNumber="88001234", name="GOMEZ, ANDREA",
            firstName="Andrea", firstLastName="Gomez", secondLastName="López",
        )
        r = _build_practitioner(pract, "CC-88001234")
        assert r["resourceType"] == "Practitioner"
        name = r["name"][0]
        assert name["family"] == "Gomez López"
        family_exts = name["_family"]["extension"]
        assert any(e["url"].endswith("FathersFamilyName") for e in family_exts)
        assert any(e["url"].endswith("MothersFamilyName") for e in family_exts)

    def test_without_desglose_falls_back_to_full_name(self):
        pract = PractitionerInfo(
            documentType="CC", documentNumber="88001234", name="Gomez Andrea",
        )
        r = _build_practitioner(pract, "CC-88001234")
        assert r["name"][0]["family"] == "Andrea"


class TestBuildLocation:
    def test_returns_none_if_no_provider(self):
        assert _build_location(None) is None

    def test_uses_location_seat_code(self):
        provider = ProviderInfo(repsCode="540015400101", name="Hospital Meoz",
                                locationSeatCode="540015400101-01")
        r = _build_location(provider)
        assert r["id"] == "540015400101-01"
        assert r["name"] == "Hospital Meoz"

    def test_defaults_seat_code_when_absent(self):
        provider = ProviderInfo(repsCode="540015400101", name="Hospital Meoz")
        r = _build_location(provider)
        assert r["id"] == "540015400101-01"  # defaults to repsCode-01


class TestBuildEncounter:
    def test_basic_structure(self):
        visit = _make_visit()
        r = _build_encounter(visit, "PT-VZ-001", "540015400101", "CC-88001234",
                              "540015400101-01", "Encounter-0")
        assert r["resourceType"] == "Encounter"
        assert r["status"] == "finished"
        assert r["class"]["code"] == "AMB"

    def test_end_datetime_included(self):
        visit = _make_visit()
        r = _build_encounter(visit, "PT-VZ-001", None, None, None, "Encounter-0")
        assert "end" in r["period"]

    def test_discharge_and_entry_route_extensions(self):
        visit = _make_visit(with_discharge=True, with_entry_route=True)
        r = _build_encounter(visit, "PT-VZ-001", None, None, None, "Encounter-0")
        ext_urls = [e["url"] for e in r.get("extension", [])]
        assert any("Discharge" in u for u in ext_urls)
        assert any("EntryRoute" in u for u in ext_urls)

    def test_no_practitioner_uses_physician_display(self):
        visit = _make_visit(with_practitioner=False)
        visit.physician = "Dr. Fallback"
        r = _build_encounter(visit, "PT-VZ-001", None, None, None, "Encounter-0")
        assert r["participant"][0]["individual"]["display"] == "Dr. Fallback"


class TestBuildCondition:
    def test_basic_condition(self):
        diag = DiagnosisItem(icd10Code="J06.9", description="Infección respiratoria")
        r = _build_condition(diag, "PT-VZ-001", "Condition-0")
        assert r["resourceType"] == "Condition"
        assert r["code"]["coding"][0]["code"] == "J06.9"

    def test_verification_status_has_system(self):
        diag = DiagnosisItem(icd10Code="J06.9", description="Infección respiratoria")
        r = _build_condition(diag, "PT-VZ-001", "Condition-0")
        assert (
            r["verificationStatus"]["coding"][0]["system"]
            == "http://terminology.hl7.org/CodeSystem/condition-ver-status"
        )

    def test_includes_icd11_when_present(self):
        diag = DiagnosisItem(icd10Code="J06.9", icd11Code="CA0Z", description="Infección")
        r = _build_condition(diag, "PT-VZ-001", "Condition-0")
        codes = [c["system"] for c in r["code"]["coding"]]
        assert "http://hl7.org/fhir/sid/icd-11" in codes


class TestBuildConditionStatement:
    def test_with_cie10_code(self):
        cond = ChronicConditionItem(chronicDescription="Diabetes", chronicCie10Code="E11",
                                    chronicCie11Code="5A11")
        r = _build_condition_statement(cond, "PT-VZ-001", "Condition-0")
        assert r["code"]["coding"][0]["code"] == "E11"
        # ICD-11 also present
        assert r["code"]["coding"][1]["code"] == "5A11"

    def test_without_cie10_uses_text(self):
        cond = ChronicConditionItem(chronicDescription="Condición desconocida")
        r = _build_condition_statement(cond, "PT-VZ-001", "Condition-0")
        assert r["code"]["text"] == "Condición desconocida"

    def test_verification_status_has_system(self):
        cond = ChronicConditionItem(chronicDescription="Diabetes", chronicCie10Code="E11")
        r = _build_condition_statement(cond, "PT-VZ-001", "Condition-0")
        assert (
            r["verificationStatus"]["coding"][0]["system"]
            == "http://terminology.hl7.org/CodeSystem/condition-ver-status"
        )


class TestBuildAllergyStatement:
    def test_basic_structure(self):
        allergy = AllergyInfo(category=AllergyCategory.MEDICAMENTO, allergen="Penicilina",
                               reaction="Habones", notes="Nota")
        r = _build_allergy_statement(allergy, "PT-VZ-001", "Allergy-0")
        assert r["resourceType"] == "AllergyIntolerance"
        assert r["code"]["text"] == "Penicilina"
        assert "reaction" in r
        assert "note" in r

    def test_status_codings_have_systems(self):
        allergy = AllergyInfo(category=AllergyCategory.MEDICAMENTO, allergen="Penicilina")
        r = _build_allergy_statement(allergy, "PT-VZ-001", "Allergy-0")
        assert (
            r["clinicalStatus"]["coding"][0]["system"]
            == "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
        )
        assert (
            r["verificationStatus"]["coding"][0]["system"]
            == "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification"
        )

    def test_encounter_allergy_adds_encounter_ref(self):
        allergy = AllergyInfo(category=AllergyCategory.ALIMENTO, allergen="Maní")
        r = _build_allergy_encounter(allergy, "PT-VZ-001", "Encounter-0", "Allergy-0")
        assert "encounter" in r
        assert "verificationStatus" not in r


class TestBuildFamilyMemberHistory:
    def test_basic_structure(self):
        fh = FamilyHistoryItem(conditionDescription="Hipertensión",
                                conditionCie10Code="I10",
                                relationship=FamilyRelationship.PADRES)
        r = _build_family_member_history(fh, "PT-VZ-001", "FMH-0")
        assert r["resourceType"] == "FamilyMemberHistory"
        assert r["condition"][0]["code"]["coding"][0]["code"] == "I10"

    def test_with_icd11_code(self):
        fh = FamilyHistoryItem(conditionDescription="Diabetes",
                                conditionCie10Code="E11",
                                conditionCie11Code="5A11",
                                relationship=FamilyRelationship.ABUELOS)
        r = _build_family_member_history(fh, "PT-VZ-001", "FMH-0")
        systems = [c["system"] for c in r["condition"][0]["code"]["coding"]]
        assert "http://hl7.org/fhir/sid/icd-11" in systems


class TestBuildMedicationStatement:
    def test_with_dci_code(self):
        med = MedicationStatementItem(medicationName="Metformina", dciCode="A10BA02",
                                       dosage="500mg con comidas")
        r = _build_medication_statement(med, "PT-VZ-001", "MedStmt-0")
        assert r["resourceType"] == "MedicationStatement"
        assert r["medicationCodeableConcept"]["coding"][0]["code"] == "A10BA02"
        assert r["dosage"][0]["text"] == "500mg con comidas"

    def test_without_dci_uses_text(self):
        med = MedicationStatementItem(medicationName="Medicamento genérico")
        r = _build_medication_statement(med, "PT-VZ-001", "MedStmt-0")
        assert r["medicationCodeableConcept"]["text"] == "Medicamento genérico"


class TestBuildMedicationRequest:
    def test_basic_structure(self):
        rx = MedicationRequestItem(
            medicationName="Acetaminofén", dciCode="N02BE01", iumCode="12345",
            dosage="500mg", frequency="cada 8h", duration="5 días", route="oral",
            notes="Con comida",
        )
        r = _build_medication_request(rx, "PT-VZ-001", "Encounter-0", "CC-88001234",
                                       "MedReq-0", "2026-01-15T09:00:00Z")
        assert r["resourceType"] == "MedicationRequest"
        assert r["requester"]["reference"].startswith("urn:uuid:") or \
               r["requester"]["reference"] == "#CC-88001234"
        assert "dosageInstruction" in r
        assert r["note"][0]["text"] == "Con comida"

    def test_without_pract_no_requester(self):
        rx = MedicationRequestItem(medicationName="Ibuprofeno")
        r = _build_medication_request(rx, "PT-VZ-001", "Encounter-0", None,
                                       "MedReq-0", "2026-01-15T09:00:00Z")
        assert "requester" not in r


class TestBuildRiskAssessment:
    def test_basic_structure(self):
        rf = RiskFactor(type=RiskFactorType.BIOLOGICOS, name="Exposición a virus")
        r = _build_risk_assessment(rf, "PT-VZ-001", "Encounter-0", "RA-0")
        assert r["resourceType"] == "RiskAssessment"
        assert r["status"] == "registered"
        assert r["code"]["text"] == "Exposición a virus"


class TestBuildOccupationObs:
    def test_with_display(self):
        r = _build_occupation_obs("2211", "Médico general", "PT-VZ-001", "Obs-0")
        assert r["resourceType"] == "Observation"
        assert r["valueCodeableConcept"]["coding"][0]["code"] == "2211"
        assert r["valueCodeableConcept"]["coding"][0]["display"] == "Médico general"

    def test_without_display(self):
        r = _build_occupation_obs("2211", None, "PT-VZ-001", "Obs-0")
        assert "display" not in r["valueCodeableConcept"]["coding"][0]


class TestBuildAttendanceAllowance:
    def test_basic_incapacity(self):
        incap = IncapacityInfo(scope=IncapacityScope.NUEVA, days=3)
        r = _build_attendance_allowance(incap, "PT-VZ-001", "Encounter-0", "Obs-0")
        assert r["resourceType"] == "Observation"
        assert len(r["component"]) == 1
        assert r["component"][0]["valueCodeableConcept"]["coding"][0]["code"] == "01"

    def test_with_maternity_leave_adds_component(self):
        incap = IncapacityInfo(scope=IncapacityScope.PRORROGA, days=5, maternityLeaveDays=98)
        r = _build_attendance_allowance(incap, "PT-VZ-001", "Encounter-0", "Obs-0")
        assert len(r["component"]) == 2
        assert r["component"][1]["valueQuantity"]["value"] == 98


# ============================================================================
# BUNDLE BUILDER TESTS
# ============================================================================

class TestBuildRdaPaciente:
    def test_bundle_structure(self):
        patient = _make_patient()
        bundle = build_rda_paciente(patient)
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "document"
        assert bundle["language"] == "es-CO"

    def test_composition_is_first_entry(self):
        patient = _make_patient()
        bundle = build_rda_paciente(patient)
        first = bundle["entry"][0]["resource"]
        assert first["resourceType"] == "Composition"
        assert first["type"]["coding"][0]["code"] == "102089-0"

    def test_includes_patient_resource(self):
        patient = _make_patient()
        bundle = build_rda_paciente(patient)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Patient" in types

    def test_includes_chronic_condition(self):
        patient = _make_patient(with_background=True)
        bundle = build_rda_paciente(patient)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Condition" in types

    def test_includes_allergy(self):
        patient = _make_patient(with_allergies=True)
        bundle = build_rda_paciente(patient)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "AllergyIntolerance" in types

    def test_all_refs_rewritten_from_hash(self):
        """After build, no #id references should remain in the bundle."""
        patient = _make_patient(with_visit=True)
        bundle = build_rda_paciente(patient)
        import json
        bundle_str = json.dumps(bundle)
        assert "\"#" not in bundle_str

    def test_with_visit_adds_org_and_practitioner(self):
        patient = _make_patient(with_visit=True)
        bundle = build_rda_paciente(patient)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Organization" in types
        assert "Practitioner" in types

    def test_event_added_when_has_visit(self):
        patient = _make_patient(with_visit=True)
        bundle = build_rda_paciente(patient)
        comp = bundle["entry"][0]["resource"]
        assert "event" in comp


class TestBuildRdaConsulta:
    def test_bundle_structure(self):
        patient = _make_patient()
        visit = _make_visit()
        bundle = build_rda_consulta(patient, visit)
        assert bundle["resourceType"] == "Bundle"
        assert bundle["language"] == "es-CO"

    def test_composition_loinc_51845_6(self):
        patient = _make_patient()
        visit = _make_visit()
        bundle = build_rda_consulta(patient, visit)
        comp = bundle["entry"][0]["resource"]
        assert comp["type"]["coding"][0]["code"] == "51845-6"

    def test_includes_encounter(self):
        patient = _make_patient()
        visit = _make_visit()
        bundle = build_rda_consulta(patient, visit)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Encounter" in types

    def test_includes_condition_for_each_diagnosis(self):
        patient = _make_patient()
        visit = _make_visit(with_diagnosis=True)
        bundle = build_rda_consulta(patient, visit)
        conditions = [e for e in bundle["entry"]
                      if e["resource"]["resourceType"] == "Condition"]
        assert len(conditions) == 1

    def test_includes_payer_section(self):
        patient = _make_patient()
        visit = _make_visit(with_payer=True)
        bundle = build_rda_consulta(patient, visit)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Organization" in types

    def test_includes_risk_factor(self):
        patient = _make_patient()
        visit = _make_visit(with_risk_factors=True)
        bundle = build_rda_consulta(patient, visit)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "RiskAssessment" in types

    def test_includes_prescription(self):
        patient = _make_patient()
        visit = _make_visit(with_prescriptions=True)
        bundle = build_rda_consulta(patient, visit)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "MedicationRequest" in types

    def test_includes_occupation_observation(self):
        patient = _make_patient()
        visit = _make_visit(with_occupation=True)
        bundle = build_rda_consulta(patient, visit)
        obs = [e for e in bundle["entry"]
               if e["resource"]["resourceType"] == "Observation"]
        assert len(obs) >= 1

    def test_includes_incapacity_observation(self):
        patient = _make_patient()
        visit = _make_visit(with_incapacity=True)
        bundle = build_rda_consulta(patient, visit)
        obs = [e for e in bundle["entry"]
               if e["resource"]["resourceType"] == "Observation"]
        assert len(obs) >= 1

    def test_no_hash_references_remaining(self):
        import json
        patient = _make_patient(with_allergies=True)
        visit = _make_visit(with_diagnosis=True, with_risk_factors=True)
        bundle = build_rda_consulta(patient, visit)
        assert "\"#" not in json.dumps(bundle)

    def test_location_included_when_provider_present(self):
        patient = _make_patient()
        visit = _make_visit(with_provider=True)
        bundle = build_rda_consulta(patient, visit)
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        assert "Location" in types


# ============================================================================
# CONVERT_TO_FHIR_RDA — DELTA LOGIC TESTS
# ============================================================================

class TestConvertToFhirRda:
    def test_first_sync_no_visits_generates_one_bundle(self):
        patient = _make_patient(with_visit=False)
        bundles, new_ids = convert_to_fhir_rda(
            patient,
            synced_encounter_ids=[],
            rda_paciente_already_sent=False,
        )
        assert len(bundles) == 1
        comp = bundles[0]["entry"][0]["resource"]
        assert comp["type"]["coding"][0]["code"] == "102089-0"  # RDA-Paciente
        assert new_ids == []

    def test_first_sync_one_visit_generates_two_bundles(self):
        patient = _make_patient(with_visit=True)
        bundles, new_ids = convert_to_fhir_rda(
            patient,
            synced_encounter_ids=[],
            rda_paciente_already_sent=False,
        )
        assert len(bundles) == 2
        assert len(new_ids) == 1

    def test_no_new_visits_already_sent_generates_zero_bundles(self):
        patient = _make_patient(with_visit=True)
        # H7: The single visit's encounterIdentifier is now in the synced set
        enc_id = patient.medicalHistory[0].encounterIdentifier
        bundles, new_ids = convert_to_fhir_rda(
            patient,
            synced_encounter_ids=[enc_id],
            rda_paciente_already_sent=True,
        )
        assert len(bundles) == 0
        assert new_ids == []

    def test_new_visit_added_generates_one_consulta(self):
        """H7: Second sync with 1 new visit and unchanged background → only 1 RDA-Consulta."""
        visit1 = _make_visit()
        visit2 = _make_visit()
        visit2.startDateTime = datetime(2026, 4, 10, 9, 0, 0)
        patient = _make_patient(visit_override=None)
        patient.medicalHistory = [visit1, visit2]

        # Only visit1's encounter ID has been synced
        bundles, new_ids = convert_to_fhir_rda(
            patient,
            synced_encounter_ids=[visit1.encounterIdentifier],
            rda_paciente_already_sent=True,
            background_data_changed=False,
        )
        # H1+H7: background unchanged → no RDA-Paciente; 1 new visit → 1 RDA-Consulta
        assert len(bundles) == 1
        assert len(new_ids) == 1
        assert new_ids[0] == visit2.encounterIdentifier

    def test_background_change_regenerates_rda_paciente(self):
        """H1: Changed background triggers RDA-Paciente even without new visits."""
        patient = _make_patient(with_visit=True)
        enc_id = patient.medicalHistory[0].encounterIdentifier
        bundles, new_ids = convert_to_fhir_rda(
            patient,
            synced_encounter_ids=[enc_id],
            rda_paciente_already_sent=True,
            background_data_changed=True,  # H1: background data hash changed
        )
        assert len(bundles) == 1  # Only RDA-Paciente (no new visits)
        assert new_ids == []
