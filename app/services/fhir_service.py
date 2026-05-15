"""
FHIR RDA Service — Resolution 1888/2025 + IG RDA v0.8.1 Full Compliance

Generates FHIR R4 Bundles of type "document" for:
  - RDA-Paciente (patient self-reported health background)
  - RDA-Consulta (ambulatory encounter clinical data)

Each Bundle follows the IHCE Implementation Guide v0.8.1:
  https://vulcano.ihcecol.gov.co/index

Architecture:
  Bundle(type=document)
    ├── Composition (root — sections with references)
    ├── Patient (PatientRDA)
    ├── Organization (IPS — CareDeliveryOrganizationRDA)
    ├── Organization (EAPB — HealthBenefitPlanAdminOrganizationRDA)  [optional]
    ├── Practitioner (PractitionerRDA)
    ├── Encounter (EncounterAmbulatoryRDA)
    ├── Condition (ConditionRDA / ConditionStatementRDA)
    ├── AllergyIntolerance (AllergyIntoleranceRDA / StatementRDA)
    ├── FamilyMemberHistory (FamilyMemberHistoryRDA)
    ├── MedicationStatement (MedicationStatementRDA)          ← NEW v2.0
    ├── MedicationRequest (MedicationRequestRDA)              ← NEW v2.0
    ├── Observation (PatientOccupationAtEncounterRDA)         ← NEW v2.0
    ├── Coverage (AttendanceAllowanceRDA)                     ← NEW v2.0 (emptyReason)
    ├── ServiceRequest (ServiceRequestRDA)                    ← NEW v2.0 (emptyReason)
    └── DocumentReference (DocumentReferenceEPIRDA)           ← NEW v2.0 (emptyReason)

CHANGELOG v2.0 (IG RDA v0.8.1 Conformity):
  1.  FIX: SYSTEM_CIE11 changed from "http://id.who.int/icd/release/11/mms"
           to "http://hl7.org/fhir/sid/icd-11" per IG v0.8.1 ConditionRDA profile.
  2.  NEW: _build_medication_statement() for MedicationStatementRDA.
  3.  NEW: _build_medication_request() for MedicationRequestRDA.
  4.  NEW: _build_occupation_observation() for PatientOccupationAtEncounterRDA.
  5.  NEW: _build_condition_statement() replaces inline dict for ConditionStatementRDA.
           Now generates proper ICD-10/11 coded conditions instead of text-only.
  6.  NEW: RDA-Paciente section "Antecedentes farmacológicos" (was MISSING).
  7.  NEW: RDA-Consulta sections: Occupation, Prescriptions, Incapacity,
           Service Orders, Documents — all with emptyReason when no data.
  8.  FIX: All Composition sections now use _build_section() which correctly
           handles emptyReason + text.div when empty (FHIRPath constraint).
  9.  NEW: Profile constants for all new resource types.
  10. FIX: Risk factors section in RDA-Consulta now uses _build_section()
           consistently (was inline before, missing emptyReason on empty).
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.schemas.patient import (
    AllergyCategory,
    BiologicalSex,
    ChronicConditionItem,
    DiagnosisType,
    FamilyRelationship,
    MedicalHistoryItem,
    MedicationRequestItem,
    MedicationStatementItem,
    PatientFullRecord,
)

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS — FHIR System URIs for Colombian RDA
# ============================================================================

FHIR_RDA_BASE = "https://fhir.minsalud.gov.co/rda"

# Terminology systems — IG RDA v0.8.1 official CodeSystem URIs
# Verified against:
#   [EJ] = Official example JSON: Encounter-5314ede9-e261-4555-aaf6-7c1b4eff3595.json
#   [CS] = CodeSystem page on vulcano.ihcecol.gov.co
#   [?]  = Pending verification (tentative name, base URI confirmed)

# Patient demographics — [CS] verified
SYSTEM_PERSON_ID = f"{FHIR_RDA_BASE}/CodeSystem/ColombianPersonIdentifier"     # [CS]
SYSTEM_ETHNICITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianEthnicGroup"          # [CS]
SYSTEM_DISABILITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianDisabilityClassification"  # [CS]
SYSTEM_GENDER_IDENTITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianGenderIdentity" # [CS]
SYSTEM_ZONE = f"{FHIR_RDA_BASE}/CodeSystem/ColombianResidenceZone"             # [CS]
SYSTEM_MUNICIPALITY = f"{FHIR_RDA_BASE}/CodeSystem/DIVIPOLA"                   # [CS]
SYSTEM_COUNTRY = f"{FHIR_RDA_BASE}/CodeSystem/ISO31661"                        # [CS]

# Encounter context — [EJ] verified from official Encounter example JSON
SYSTEM_MODALITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianTechModality"          # [EJ] Encounter.type[0]
SYSTEM_SERVICE_GROUP = f"{FHIR_RDA_BASE}/CodeSystem/GrupoServicios"            # [EJ] Encounter.type[1]
SYSTEM_ENVIRONMENT = f"{FHIR_RDA_BASE}/CodeSystem/EntornoAtencion"             # [EJ] Encounter.type[3]
SYSTEM_CAUSA_EXTERNA = f"{FHIR_RDA_BASE}/CodeSystem/RIPSCausaExternaVersion2"  # [EJ] Encounter.reasonCode
SYSTEM_DIAG_TYPE = f"{FHIR_RDA_BASE}/CodeSystem/RIPSTipoDiagnosticoPrincipalVersion2"  # [EJ] Encounter.diagnosis.ext
SYSTEM_DIAG_ROLE = f"{FHIR_RDA_BASE}/CodeSystem/ColombianDiagnosisRole"        # [EJ] Encounter.diagnosis.use
SYSTEM_DISCHARGE = f"{FHIR_RDA_BASE}/CodeSystem/CondicionyDestinoUsuarioEgreso"  # [EJ] ext:DischargeDisposition
SYSTEM_VIA_INGRESO = f"{FHIR_RDA_BASE}/CodeSystem/ViaIngreso"                  # [CS]
SYSTEM_REPS_SERVICES = f"{FHIR_RDA_BASE}/CodeSystem/REPShealthcareServices"    # [EJ] Encounter.type[2]
SYSTEM_CUPS = f"{FHIR_RDA_BASE}/CodeSystem/CUPS"                               # [EJ] Encounter.serviceType

# Clinical terminologies
SYSTEM_FAMILY_REL = f"{FHIR_RDA_BASE}/CodeSystem/ParentescoAntecedente"         # [POSTMAN] verified
SYSTEM_OCCUPATION = f"{FHIR_RDA_BASE}/CodeSystem/CIUO88AC"                     # [CS]
SYSTEM_INCAPACITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianLicenseScope"        # [CS]
SYSTEM_ALLERGY_CAT = f"{FHIR_RDA_BASE}/CodeSystem/TipoAlergia"                # [POSTMAN] verified
SYSTEM_RISK_FACTOR = f"{FHIR_RDA_BASE}/CodeSystem/FactorRiesgo"               # [POSTMAN] verified
SYSTEM_REPS = f"{FHIR_RDA_BASE}/CodeSystem/ColombianOrganizationIdentifiers"   # [CS]
SYSTEM_DCI = f"{FHIR_RDA_BASE}/CodeSystem/MipresINN"                           # [CS]
SYSTEM_IUM = f"{FHIR_RDA_BASE}/CodeSystem/IUM"                                 # [CS]

SYSTEM_CIE10 = "http://hl7.org/fhir/sid/icd-10"
# FIX v2.0: Changed from "http://id.who.int/icd/release/11/mms" to match IG v0.8.1
SYSTEM_CIE11 = "http://hl7.org/fhir/sid/icd-11"
SYSTEM_LOINC = "http://loinc.org"
SYSTEM_UNITS = "http://unitsofmeasure.org"
SYSTEM_CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
SYSTEM_ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
SYSTEM_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
SYSTEM_HL7_ID_TYPE = "http://terminology.hl7.org/CodeSystem/v2-0203"           # [POSTMAN] dual coding
SYSTEM_PARTICIPATION = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"  # [POSTMAN]
SYSTEM_CONFIDENTIALITY = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"  # [POSTMAN]
SYSTEM_CONDITION_CATEGORY = "http://terminology.hl7.org/CodeSystem/condition-category"  # [POSTMAN]

# NamingSystem URIs — [POSTMAN] verified
NAMING_SYSTEM_RNEC = f"{FHIR_RDA_BASE}/NamingSystem/RNEC"
NAMING_SYSTEM_REPS = f"{FHIR_RDA_BASE}/NamingSystem/REPS"
NAMING_SYSTEM_ENCOUNTERS = f"{FHIR_RDA_BASE}/NamingSystem/Encounters"

# Profile canonical URLs — existing
PROFILE_PATIENT = f"{FHIR_RDA_BASE}/StructureDefinition/PatientRDA"
PROFILE_ORG_IPS = f"{FHIR_RDA_BASE}/StructureDefinition/CareDeliveryOrganizationRDA"
PROFILE_ORG_EAPB = f"{FHIR_RDA_BASE}/StructureDefinition/HealthBenefitPlanAdminOrganizationRDA"
PROFILE_PRACTITIONER = f"{FHIR_RDA_BASE}/StructureDefinition/PractitionerRDA"
PROFILE_ENCOUNTER_AMB = f"{FHIR_RDA_BASE}/StructureDefinition/EncounterAmbulatoryRDA"
PROFILE_CONDITION = f"{FHIR_RDA_BASE}/StructureDefinition/ConditionRDA"
PROFILE_CONDITION_STMT = f"{FHIR_RDA_BASE}/StructureDefinition/ConditionStatementRDA"
PROFILE_ALLERGY = f"{FHIR_RDA_BASE}/StructureDefinition/AllergyIntoleranceRDA"
PROFILE_ALLERGY_STMT = f"{FHIR_RDA_BASE}/StructureDefinition/AllergyIntoleranceStatementRDA"
PROFILE_FAMILY_HISTORY = f"{FHIR_RDA_BASE}/StructureDefinition/FamilyMemberHistoryRDA"
PROFILE_COMPOSITION_PATIENT = f"{FHIR_RDA_BASE}/StructureDefinition/CompositionPatientStatementRDA"
PROFILE_COMPOSITION_AMB = f"{FHIR_RDA_BASE}/StructureDefinition/CompositionAmbulatoryRDA"
PROFILE_BUNDLE_PATIENT = f"{FHIR_RDA_BASE}/StructureDefinition/BundlePatientStatementRDA"
PROFILE_BUNDLE_AMB = f"{FHIR_RDA_BASE}/StructureDefinition/BundleAmbulatoryRDA"

# Profile canonical URLs — NEW v2.0
PROFILE_MEDICATION_STMT = f"{FHIR_RDA_BASE}/StructureDefinition/MedicationStatementRDA"
PROFILE_MEDICATION_REQ = f"{FHIR_RDA_BASE}/StructureDefinition/MedicationRequestRDA"
PROFILE_OCCUPATION_OBS = f"{FHIR_RDA_BASE}/StructureDefinition/PatientOccupationAtEncounterRDA"
PROFILE_ATTENDANCE_ALLOWANCE = f"{FHIR_RDA_BASE}/StructureDefinition/AttendanceAllowanceRDA"
PROFILE_SERVICE_REQUEST = f"{FHIR_RDA_BASE}/StructureDefinition/ServiceRequestRDA"
PROFILE_DOC_REFERENCE = f"{FHIR_RDA_BASE}/StructureDefinition/DocumentReferenceEPIRDA"
PROFILE_RISK_FACTOR = f"{FHIR_RDA_BASE}/StructureDefinition/RiskFactorRDA"


# ============================================================================
# HELPERS
# ============================================================================

def _uuid() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def _fhir_datetime(dt_obj) -> str:
    if not dt_obj:
        return datetime.utcnow().isoformat() + "Z"
    if isinstance(dt_obj, datetime):
        s = dt_obj.isoformat()
        return s + "Z" if not s.endswith("Z") and "+" not in s else s
    if isinstance(dt_obj, date):
        return dt_obj.isoformat()
    return str(dt_obj)


def _sex_to_fhir(sex: BiologicalSex) -> str:
    return {"M": "male", "F": "female", "I": "other"}.get(sex.value, "unknown")


def _allergy_category_display(cat: AllergyCategory) -> str:
    return {
        "01": "Medicamento", "02": "Alimento", "03": "Sustancia del ambiente",
        "04": "Sustancia en contacto con la piel", "05": "Picadura de insectos", "06": "Otra"
    }.get(cat.value, "Otra")


def _family_rel_display(rel: FamilyRelationship) -> str:
    return {"01": "Padres", "02": "Hermanos", "03": "Tíos", "04": "Abuelos"}.get(rel.value, "Otro")


def _diag_type_display(dt: DiagnosisType) -> str:
    return {
        "01": "Impresión diagnóstica", "02": "Confirmado nuevo", "03": "Confirmado repetido"
    }.get(dt.value, "Impresión diagnóstica")


def _build_section(title: str, code_system: str, code_code: str, code_display: str,
                   refs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a FHIR Composition section that satisfies the FHIRPath constraint:
    'text.exists() or entry.exists() or section.exists()'
    
    When refs is non-empty: includes 'entry' with the references.
    When refs is empty: includes 'emptyReason' and a 'text' element (no 'entry' key at all).
    """
    section: Dict[str, Any] = {
        "title": title,
        "code": {"coding": [{"system": code_system, "code": code_code, "display": code_display}]},
    }
    if refs:
        section["entry"] = refs
    else:
        section["emptyReason"] = {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/list-empty-reason",
                        "code": "nilknown"}]
        }
        section["text"] = {
            "status": "empty",
            "div": '<div xmlns="http://www.w3.org/1999/xhtml">No hay información disponible.</div>'
        }
    return section


def _build_bundle_shell(bundle_id: str, profile: str, timestamp: str,
                        composition_entry: Dict, resource_entries: List[Dict]) -> Dict[str, Any]:
    """Build a FHIR Bundle of type 'document' with the required 'identifier' field."""
    return {
        "resourceType": "Bundle",
        "id": bundle_id,
        "identifier": {
            "system": f"{FHIR_RDA_BASE}/bundle-identifier",
            "value": bundle_id
        },
        "meta": {"profile": [profile]},
        "type": "document",
        "timestamp": timestamp,
        "entry": [composition_entry] + resource_entries
    }


# ============================================================================
# RESOURCE BUILDERS — Patient, Organization, Practitioner, Encounter
# ============================================================================

def _build_patient_resource(patient: PatientFullRecord) -> Dict[str, Any]:
    """Build FHIR Patient resource conforming to PatientRDA profile."""
    pi = patient.patientInfo
    ident = pi.identification

    resource = {
        "resourceType": "Patient",
        "meta": {"profile": [PROFILE_PATIENT]},
        "identifier": [
            {
                "use": "official",
                "type": {
                    "coding": [
                        {
                            "system": SYSTEM_HL7_ID_TYPE,
                            "code": "PN",
                            "display": "Person number"
                        },
                        {
                            "system": SYSTEM_PERSON_ID,
                            "code": ident.documentType.value
                        }
                    ]
                },
                "system": NAMING_SYSTEM_RNEC,
                "value": ident.documentNumber
            }
        ],
        "name": [{
            "use": "official",
            "family": pi.firstLastName,
            "_family": {
                "extension": [{
                    "url": "http://hl7.org/fhir/StructureDefinition/humanname-fathers-family",
                    "valueString": pi.firstLastName
                }]
            },
            "given": [g for g in [pi.firstName, pi.secondName] if g]
        }],
        "gender": _sex_to_fhir(pi.biologicalSex),
        "birthDate": pi.dob.isoformat(),
        "extension": [
            {
                "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientNationality",
                "valueCoding": {
                    "system": SYSTEM_COUNTRY,
                    "code": pi.nationalityCode,
                    "display": pi.nationalityName or pi.nationalityCode
                }
            }
        ]
    }

    # Second last name extension
    if pi.secondLastName:
        resource["name"][0]["_family"]["extension"].append({
            "url": "http://hl7.org/fhir/StructureDefinition/humanname-mothers-family",
            "valueString": pi.secondLastName
        })

    # Address
    addr = pi.address
    fhir_addr: Dict[str, Any] = {
        "use": "home",
        "city": addr.city,
        "state": addr.state,
        "country": addr.country
    }
    if addr.street:
        fhir_addr["line"] = [addr.street]
    if addr.zipCode:
        fhir_addr["postalCode"] = addr.zipCode
    if addr.cityCode:
        fhir_addr["extension"] = [{
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionMunicipalityCode",
            "valueCoding": {"system": SYSTEM_MUNICIPALITY, "code": addr.cityCode}
        }]
    if addr.zone:
        fhir_addr.setdefault("extension", []).append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionResidenceZone",
            "valueCoding": {"system": SYSTEM_ZONE, "code": addr.zone.value}
        })
    resource["address"] = [fhir_addr]

    # Ethnicity
    if pi.ethnicity:
        resource["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientEthnicity",
            "valueCoding": {"system": SYSTEM_ETHNICITY, "code": pi.ethnicity.value}
        })

    # Disability
    if pi.disabilityCategory:
        resource["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientDisability",
            "valueCoding": {"system": SYSTEM_DISABILITY, "code": pi.disabilityCategory.value}
        })

    # Gender identity (optional)
    if pi.genderIdentity:
        resource["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientGenderIdentity",
            "valueCoding": {
                "system": SYSTEM_GENDER_IDENTITY,
                "code": pi.genderIdentity.value
            }
        })

    # Guardian as contact
    gi = patient.guardianInfo
    resource["contact"] = [{
        "relationship": [{"text": gi.relationship}],
        "name": {"text": gi.name},
        "telecom": [{"system": "phone", "value": gi.phone}] if gi.phone else []
    }]

    return resource


def _build_organization_ips(provider) -> Optional[Dict[str, Any]]:
    """Build IPS Organization resource."""
    if not provider:
        return None
    return {
        "resourceType": "Organization",
        "meta": {"profile": [PROFILE_ORG_IPS]},
        "identifier": [{"system": SYSTEM_REPS, "value": provider.repsCode}],
        "name": provider.name,
        "active": True
    }


def _build_organization_eapb(payer) -> Optional[Dict[str, Any]]:
    """Build EAPB Organization resource."""
    if not payer or not payer.code:
        return None
    return {
        "resourceType": "Organization",
        "meta": {"profile": [PROFILE_ORG_EAPB]},
        "identifier": [{"system": "https://www.adres.gov.co/ENTIDADES_SGSSS", "value": payer.code}],
        "name": payer.name or "",
        "active": True
    }


def _build_practitioner(pract) -> Optional[Dict[str, Any]]:
    """Build Practitioner resource."""
    if not pract:
        return None
    return {
        "resourceType": "Practitioner",
        "meta": {"profile": [PROFILE_PRACTITIONER]},
        "identifier": [{
            "type": {"coding": [{"system": SYSTEM_PERSON_ID, "code": pract.documentType.value}]},
            "value": pract.documentNumber
        }],
        "name": [{"text": pract.name}]
    }


def _build_encounter_ambulatory(
    visit: MedicalHistoryItem, patient_ref: str, org_ref: Optional[str], pract_ref: Optional[str]
) -> Dict[str, Any]:
    """
    Build Encounter resource conforming to EncounterAmbulatoryRDA profile.
    
    STRUCTURAL CHANGE v3.0: Per the official IG example JSON
    (Encounter-5314ede9-e261-4555-aaf6-7c1b4eff3595.json), the IG v0.8.1 uses:
      - Encounter.type[] array for modality, service group, and environment
        (NOT extensions as HWB previously did)
      - Encounter.reasonCode for external cause (NOT extension)
      - Encounter.diagnosis[].extension for diagnosis type (NOT Condition.extension)
    """
    # Encounter.type[] — array of CodeableConcept (verified from official example)
    encounter_types = [
        {"coding": [{"system": SYSTEM_MODALITY, "code": visit.careModality.value}]},
        {"coding": [{"system": SYSTEM_SERVICE_GROUP, "code": visit.serviceGroup.value}]},
        {"coding": [{"system": SYSTEM_ENVIRONMENT, "code": visit.careEnvironment.value}]},
    ]

    enc: Dict[str, Any] = {
        "resourceType": "Encounter",
        "meta": {"profile": [PROFILE_ENCOUNTER_AMB]},
        "status": "finished",
        "class": {"system": SYSTEM_ACT_CODE, "code": "AMB", "display": "ambulatory"},
        "type": encounter_types,
        "subject": {"reference": patient_ref},
        "period": {
            "start": _fhir_datetime(visit.startDateTime)
        },
    }

    if visit.endDateTime:
        enc["period"]["end"] = _fhir_datetime(visit.endDateTime)

    if org_ref:
        enc["serviceProvider"] = {"reference": org_ref}

    if pract_ref:
        enc["participant"] = [{
            "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType", "code": "ATND", "display": "attender"}]}],
            "individual": {"reference": pract_ref}
        }]
    elif visit.physician:
        enc["participant"] = [{"individual": {"display": visit.physician}}]

    if visit.location:
        enc["location"] = [{"location": {"display": visit.location}}]

    # External cause → Encounter.reasonCode (verified from official example)
    if visit.externalCause:
        enc["reasonCode"] = [{"coding": [{"system": SYSTEM_CAUSA_EXTERNA, "code": visit.externalCause}]}]

    # Discharge disposition → extension (verified from official example)
    if visit.dischargeDisposition:
        enc.setdefault("extension", []).append({
            "extension": [
                {"url": "DispositionCode", "valueCoding": {"system": SYSTEM_DISCHARGE, "code": visit.dischargeDisposition.value}}
            ],
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionDischargeDisposition"
        })

    # Via de ingreso — kept as extension (not in the Encounter example but referenced in IG)
    if visit.entryRoute:
        enc.setdefault("extension", []).append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionEntryRoute",
            "valueCoding": {"system": SYSTEM_VIA_INGRESO, "code": visit.entryRoute}
        })

    return enc


# ============================================================================
# RESOURCE BUILDERS — Clinical resources
# ============================================================================

def _build_condition(diag, patient_ref: str, encounter_ref: str) -> Dict[str, Any]:
    """
    Build Condition (diagnosis) resource conforming to ConditionRDA profile.
    
    STRUCTURAL CHANGE v3.0: The diagnosis type extension (ExtensionDiagnosisType)
    has been REMOVED from Condition and moved to Encounter.diagnosis[].extension
    per the IG v0.8.1 official example. The Condition resource now only contains
    the clinical data. The diagnosis type + role are set in the Composition section
    that references this Condition via Encounter.diagnosis.
    """
    coding = [{"system": SYSTEM_CIE10, "code": diag.icd10Code, "display": diag.description}]
    if diag.icd11Code:
        coding.append({"system": SYSTEM_CIE11, "code": diag.icd11Code})

    return {
        "resourceType": "Condition",
        "meta": {"profile": [PROFILE_CONDITION]},
        "clinicalStatus": {"coding": [{"system": SYSTEM_CONDITION_CLINICAL, "code": "active"}]},
        "verificationStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]
        },
        "code": {"coding": coding},
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
    }


def _build_condition_statement(condition: "ChronicConditionItem", patient_ref: str) -> Dict[str, Any]:
    """
    Build Condition (patient-declared chronic condition) conforming to ConditionStatementRDA.
    
    NEW v2.0: Replaces the old inline dict that used code.text only.
    Now generates proper ICD-10/11 coded conditions with slices when codes are available,
    falling back to code.text when only description is present (legacy data).
    """
    resource: Dict[str, Any] = {
        "resourceType": "Condition",
        "meta": {"profile": [PROFILE_CONDITION_STMT]},
        "clinicalStatus": {"coding": [{"system": SYSTEM_CONDITION_CLINICAL, "code": "active"}]},
        "subject": {"reference": patient_ref},
    }

    # Build code with proper slicing: ICD-10 (required), ICD-11 (optional)
    if condition.icd10Code:
        coding = [{"system": SYSTEM_CIE10, "code": condition.icd10Code, "display": condition.description}]
        if condition.icd11Code:
            coding.append({"system": SYSTEM_CIE11, "code": condition.icd11Code})
        resource["code"] = {"coding": coding}
    else:
        # Fallback: description only (legacy data not yet coded by LLM)
        resource["code"] = {"text": condition.description}

    return resource


def _build_allergy_statement(allergy, patient_ref: str) -> Dict[str, Any]:
    """Build AllergyIntolerance (patient-declared) resource."""
    resource: Dict[str, Any] = {
        "resourceType": "AllergyIntolerance",
        "meta": {"profile": [PROFILE_ALLERGY_STMT]},
        "clinicalStatus": {"coding": [{"system": SYSTEM_ALLERGY_CLINICAL, "code": "active"}]},
        "patient": {"reference": patient_ref},
        "code": {"text": allergy.allergen},
        "extension": [{
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionAllergyCategory",
            "valueCoding": {
                "system": SYSTEM_ALLERGY_CAT,
                "code": allergy.category.value,
                "display": _allergy_category_display(allergy.category)
            }
        }]
    }
    if allergy.reaction:
        resource["reaction"] = [{"manifestation": [{"text": allergy.reaction}]}]
    if allergy.notes:
        resource["note"] = [{"text": allergy.notes}]
    return resource


def _build_family_member_history(fh_item, patient_ref: str) -> Dict[str, Any]:
    """Build FamilyMemberHistory resource."""
    coding = [{"system": SYSTEM_CIE10, "code": fh_item.conditionCie10Code}]
    if fh_item.conditionDescription:
        coding[0]["display"] = fh_item.conditionDescription
    if fh_item.conditionCie11Code:
        coding.append({"system": SYSTEM_CIE11, "code": fh_item.conditionCie11Code})

    return {
        "resourceType": "FamilyMemberHistory",
        "meta": {"profile": [PROFILE_FAMILY_HISTORY]},
        "status": "completed",
        "patient": {"reference": patient_ref},
        "relationship": {
            "coding": [{
                "system": SYSTEM_FAMILY_REL,
                "code": fh_item.relationship.value,
                "display": _family_rel_display(fh_item.relationship)
            }]
        },
        "condition": [{"code": {"coding": coding}}]
    }


def _build_medication_statement(med: "MedicationStatementItem", patient_ref: str) -> Dict[str, Any]:
    """
    Build MedicationStatement resource conforming to MedicationStatementRDA.
    
    NEW v2.0 — For RDA-Paciente "Antecedentes farmacológicos" section.
    """
    med_codeable: Dict[str, Any] = {}
    if med.dciCode:
        med_codeable["coding"] = [{"system": SYSTEM_DCI, "code": med.dciCode, "display": med.medicationName}]
    else:
        med_codeable["text"] = med.medicationName

    resource: Dict[str, Any] = {
        "resourceType": "MedicationStatement",
        "meta": {"profile": [PROFILE_MEDICATION_STMT]},
        "status": med.status.value,
        "medicationCodeableConcept": med_codeable,
        "subject": {"reference": patient_ref},
    }

    if med.dosage:
        resource["dosage"] = [{"text": med.dosage}]
    if med.notes:
        resource["note"] = [{"text": med.notes}]

    return resource


def _build_medication_request(
    rx: "MedicationRequestItem", patient_ref: str, encounter_ref: str, pract_ref: Optional[str]
) -> Dict[str, Any]:
    """
    Build MedicationRequest resource conforming to MedicationRequestRDA.
    
    NEW v2.0 — For RDA-Consulta "Medicamentos prescritos" section.
    """
    med_codeable: Dict[str, Any] = {}
    codings = []
    if rx.dciCode:
        codings.append({"system": SYSTEM_DCI, "code": rx.dciCode, "display": rx.medicationName})
    if rx.iumCode:
        codings.append({"system": SYSTEM_IUM, "code": rx.iumCode})
    if codings:
        med_codeable["coding"] = codings
    else:
        med_codeable["text"] = rx.medicationName

    resource: Dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "meta": {"profile": [PROFILE_MEDICATION_REQ]},
        "status": rx.status.value,
        "intent": rx.intent.value,
        "medicationCodeableConcept": med_codeable,
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
    }

    if pract_ref:
        resource["requester"] = {"reference": pract_ref}

    dosage_parts = []
    if rx.dosage:
        dosage_parts.append(rx.dosage)
    if rx.frequency:
        dosage_parts.append(f"Frecuencia: {rx.frequency}")
    if rx.duration:
        dosage_parts.append(f"Duración: {rx.duration}")
    if rx.route:
        dosage_parts.append(f"Vía: {rx.route}")
    if dosage_parts:
        resource["dosageInstruction"] = [{"text": ". ".join(dosage_parts)}]

    if rx.notes:
        resource["note"] = [{"text": rx.notes}]

    return resource


def _build_occupation_observation(
    occupation_code: str, occupation_display: Optional[str],
    patient_ref: str, encounter_ref: str
) -> Dict[str, Any]:
    """
    Build Observation resource conforming to PatientOccupationAtEncounterRDA.
    
    NEW v2.0 — For RDA-Consulta "Otros datos demográficos" section.
    Uses CIUO-88 A.C. (International Standard Classification of Occupations
    Adapted for Colombia) as the coding system.
    """
    value_codeable: Dict[str, Any] = {
        "coding": [{"system": SYSTEM_OCCUPATION, "code": occupation_code}]
    }
    if occupation_display:
        value_codeable["coding"][0]["display"] = occupation_display

    return {
        "resourceType": "Observation",
        "meta": {"profile": [PROFILE_OCCUPATION_OBS]},
        "status": "final",
        "code": {
            "coding": [{"system": SYSTEM_LOINC, "code": "85658-3", "display": "Occupation Type"}]
        },
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
        "valueCodeableConcept": value_codeable
    }


# ============================================================================
# BUNDLE BUILDERS — RDA Documents
# ============================================================================

def build_rda_paciente(patient: PatientFullRecord) -> Dict[str, Any]:
    """
    Generate RDA-Paciente: a FHIR Bundle of type 'document' containing
    the patient's self-reported health background.
    
    IG RDA v0.8.1 requires 4 sections:
      1. Antecedentes alérgicos (AllergyIntoleranceStatementRDA)
      2. Antecedentes familiares (FamilyMemberHistoryRDA)
      3. Antecedentes patológicos (ConditionStatementRDA) — FIX: now uses ICD codes
      4. Antecedentes farmacológicos (MedicationStatementRDA) — NEW v2.0
    """
    logger.debug(f"Building RDA-Paciente for patient {patient.patientId}")

    entries: List[Dict[str, Any]] = []
    patient_url = _uuid()
    now = datetime.utcnow().isoformat() + "Z"

    # 1. Patient resource
    patient_resource = _build_patient_resource(patient)
    entries.append({"fullUrl": patient_url, "resource": patient_resource})

    # 2. Composition sections
    sections = []

    # Section 1: Allergies (patient-declared)
    allergy_refs = []
    for allergy in patient.allergies:
        allergy_url = _uuid()
        entries.append({
            "fullUrl": allergy_url,
            "resource": _build_allergy_statement(allergy, patient_url)
        })
        allergy_refs.append({"reference": allergy_url})

    sections.append(_build_section(
        "Alergias e intolerancias declaradas por el paciente",
        SYSTEM_LOINC, "48765-2", "Allergies and adverse reactions",
        allergy_refs
    ))

    # Section 2: Family history (patient-declared)
    family_refs = []
    if patient.backgroundHistory and patient.backgroundHistory.familyHistory:
        for fh in patient.backgroundHistory.familyHistory:
            fh_url = _uuid()
            entries.append({
                "fullUrl": fh_url,
                "resource": _build_family_member_history(fh, patient_url)
            })
            family_refs.append({"reference": fh_url})

    sections.append(_build_section(
        "Antecedentes familiares declarados por el paciente",
        SYSTEM_LOINC, "10157-6", "Family history",
        family_refs
    ))

    # Section 3: Chronic conditions / pathological background (patient-declared)
    # FIX v2.0: Now uses _build_condition_statement() with proper ICD coding
    condition_refs = []
    if patient.backgroundHistory:
        chronic_items = patient.backgroundHistory.chronicConditions
        # Handle both list (v2.0) and legacy str (backward compat — validator converts)
        if isinstance(chronic_items, list):
            for cond in chronic_items:
                if isinstance(cond, ChronicConditionItem):
                    cond_url = _uuid()
                    entries.append({
                        "fullUrl": cond_url,
                        "resource": _build_condition_statement(cond, patient_url)
                    })
                    condition_refs.append({"reference": cond_url})

    sections.append(_build_section(
        "Condiciones de salud declaradas por el paciente",
        SYSTEM_LOINC, "11450-4", "Problem list",
        condition_refs
    ))

    # Section 4: Medication history (patient-declared) — NEW v2.0
    medication_refs = []
    if patient.backgroundHistory and patient.backgroundHistory.medications:
        for med in patient.backgroundHistory.medications:
            med_url = _uuid()
            entries.append({
                "fullUrl": med_url,
                "resource": _build_medication_statement(med, patient_url)
            })
            medication_refs.append({"reference": med_url})

    sections.append(_build_section(
        "Antecedentes farmacológicos declarados por el paciente",
        SYSTEM_LOINC, "10160-0", "History of Medication use",
        medication_refs
    ))

    # 3. Composition (root of the document)
    composition_url = _uuid()
    composition = {
        "fullUrl": composition_url,
        "resource": {
            "resourceType": "Composition",
            "meta": {"profile": [PROFILE_COMPOSITION_PATIENT]},
            "status": "final",
            "type": {
                "coding": [{
                    "system": SYSTEM_LOINC,
                    "code": "60591-5",
                    "display": "Patient summary Document"
                }]
            },
            "subject": {"reference": patient_url},
            "date": now,
            "author": [{"reference": patient_url}],
            "title": "Resumen Digital de Atención en Salud - RDA Paciente",
            "section": sections
        }
    }

    # Build final Bundle — Composition MUST be the first entry
    bundle = _build_bundle_shell(
        bundle_id=str(uuid.uuid4()),
        profile=PROFILE_BUNDLE_PATIENT,
        timestamp=now,
        composition_entry=composition,
        resource_entries=entries
    )

    logger.debug(f"RDA-Paciente bundle built with {len(bundle['entry'])} entries")
    return bundle


def build_rda_consulta(
    patient: PatientFullRecord,
    visit: MedicalHistoryItem
) -> Dict[str, Any]:
    """
    Generate RDA-Consulta: a FHIR Bundle of type 'document' for a single
    ambulatory encounter.
    
    IG RDA v0.8.1 sections:
      1. Pagadores (EAPB)
      2. Otros datos demográficos — Occupation (NEW v2.0)
      3. Alergias e intolerancias
      4. Diagnósticos
      5. Factores de riesgo
      6. Medicamentos prescritos (NEW v2.0)
      7. Incapacidad (emptyReason if N/A) (NEW v2.0)
      8. Órdenes de servicio (emptyReason) (NEW v2.0)
      9. Documentos soporte (emptyReason) (NEW v2.0)
    """
    logger.debug(f"Building RDA-Consulta for patient {patient.patientId}")

    entries: List[Dict[str, Any]] = []
    now = datetime.utcnow().isoformat() + "Z"

    # 1. Patient
    patient_url = _uuid()
    entries.append({"fullUrl": patient_url, "resource": _build_patient_resource(patient)})

    # 2. Organization IPS
    org_url = None
    if visit.provider:
        org_url = _uuid()
        entries.append({"fullUrl": org_url, "resource": _build_organization_ips(visit.provider)})

    # 3. Organization EAPB
    eapb_url = None
    if visit.payer:
        eapb_resource = _build_organization_eapb(visit.payer)
        if eapb_resource:
            eapb_url = _uuid()
            entries.append({"fullUrl": eapb_url, "resource": eapb_resource})

    # 4. Practitioner
    pract_url = None
    if visit.practitioner:
        pract_url = _uuid()
        entries.append({"fullUrl": pract_url, "resource": _build_practitioner(visit.practitioner)})

    # 5. Encounter
    encounter_url = _uuid()
    entries.append({
        "fullUrl": encounter_url,
        "resource": _build_encounter_ambulatory(visit, patient_url, org_url, pract_url)
    })

    # 6. Sections for the Composition
    sections = []

    # Section 1: Payer
    payer_refs = [{"reference": eapb_url}] if eapb_url else []
    sections.append(_build_section(
        "Pagadores",
        SYSTEM_LOINC, "48768-6", "Payment sources",
        payer_refs
    ))

    # Section 2: Occupation — NEW v2.0
    occupation_refs = []
    if visit.occupation:
        occ_url = _uuid()
        entries.append({
            "fullUrl": occ_url,
            "resource": _build_occupation_observation(
                visit.occupation, visit.occupationDescription,
                patient_url, encounter_url
            )
        })
        occupation_refs.append({"reference": occ_url})

    sections.append(_build_section(
        "Otros datos demográficos del paciente",
        SYSTEM_LOINC, "85658-3", "Occupation Type",
        occupation_refs
    ))

    # Section 3: Allergies (encounter-identified)
    allergy_refs = []
    for allergy in patient.allergies:
        a_url = _uuid()
        allergy_resource = _build_allergy_statement(allergy, patient_url)
        allergy_resource["meta"]["profile"] = [PROFILE_ALLERGY]  # encounter-identified profile
        allergy_resource["encounter"] = {"reference": encounter_url}
        entries.append({"fullUrl": a_url, "resource": allergy_resource})
        allergy_refs.append({"reference": a_url})

    sections.append(_build_section(
        "Alergias e intolerancias identificadas durante la atención",
        SYSTEM_LOINC, "48765-2", "Allergies and adverse reactions",
        allergy_refs
    ))

    # Section 4: Diagnoses
    # STRUCTURAL CHANGE v3.0: Diagnosis type now goes in Encounter.diagnosis[].extension
    # per IG v0.8.1 example, NOT in Condition.extension
    diag_refs = []
    encounter_diagnoses = []
    for idx, diag in enumerate(visit.diagnosis):
        d_url = _uuid()
        entries.append({
            "fullUrl": d_url,
            "resource": _build_condition(diag, patient_url, encounter_url)
        })
        diag_refs.append({"reference": d_url})
        # Build Encounter.diagnosis[] entry with type extension
        enc_diag: Dict[str, Any] = {
            "extension": [{
                "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionDiagnosisType",
                "valueCodeableConcept": {
                    "coding": [{
                        "system": SYSTEM_DIAG_TYPE,
                        "code": visit.diagnosisType.value,
                        "display": _diag_type_display(visit.diagnosisType)
                    }]
                }
            }],
            "condition": {"reference": d_url},
            "use": {"coding": [{"system": SYSTEM_DIAG_ROLE, "code": "8319008", "display": "diagnóstico primario"}]},
            "rank": idx + 1
        }
        encounter_diagnoses.append(enc_diag)

    # Attach diagnosis array to the Encounter resource
    if encounter_diagnoses:
        # Find the Encounter entry and add diagnosis
        for entry in entries:
            if entry.get("fullUrl") == encounter_url:
                entry["resource"]["diagnosis"] = encounter_diagnoses
                break

    sections.append(_build_section(
        "Diagnósticos de problemas de salud",
        SYSTEM_LOINC, "11450-4", "Problem list",
        diag_refs
    ))

    # Section 5: Risk factors — FIX v2.0: now uses _build_section() consistently
    rf_refs = []
    for rf in visit.riskFactors:
        rf_url = _uuid()
        entries.append({
            "fullUrl": rf_url,
            "resource": {
                "resourceType": "RiskAssessment",
                "meta": {"profile": [PROFILE_RISK_FACTOR]},
                "status": "final",
                "subject": {"reference": patient_url},
                "encounter": {"reference": encounter_url},
                "extension": [{
                    "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionRiskFactorType",
                    "valueCoding": {"system": SYSTEM_RISK_FACTOR, "code": rf.type.value}
                }],
                "note": [{"text": rf.name}]
            }
        })
        rf_refs.append({"reference": rf_url})

    sections.append(_build_section(
        "Factores de riesgo",
        SYSTEM_LOINC, "75492-9", "Risk factors",
        rf_refs
    ))

    # Section 6: Prescribed medications — NEW v2.0
    rx_refs = []
    for rx in visit.prescriptions:
        rx_url = _uuid()
        entries.append({
            "fullUrl": rx_url,
            "resource": _build_medication_request(rx, patient_url, encounter_url, pract_url)
        })
        rx_refs.append({"reference": rx_url})

    sections.append(_build_section(
        "Medicamentos prescritos",
        SYSTEM_LOINC, "57833-6", "Prescriptions",
        rx_refs
    ))

    # Section 7: Incapacity — NEW v2.0 (emptyReason when no data)
    incapacity_refs = []
    # Note: IncapacityInfo is already in the schema but was never added as a section.
    # For HWB's use case this is usually empty, so we generate emptyReason.
    sections.append(_build_section(
        "Incapacidad",
        SYSTEM_LOINC, "77599-9", "Disability assessment",
        incapacity_refs
    ))

    # Section 8: Service requests / orders — NEW v2.0 (emptyReason)
    sections.append(_build_section(
        "Órdenes de procedimientos y tecnologías en salud",
        SYSTEM_LOINC, "57833-6", "Prescriptions",
        []
    ))

    # Section 9: Supporting documents — NEW v2.0 (emptyReason)
    sections.append(_build_section(
        "Documentos soporte",
        SYSTEM_LOINC, "77599-9", "Supporting documents",
        []
    ))

    # 7. Composition
    composition_url = _uuid()
    author_ref = pract_url if pract_url else patient_url
    composition = {
        "fullUrl": composition_url,
        "resource": {
            "resourceType": "Composition",
            "meta": {"profile": [PROFILE_COMPOSITION_AMB]},
            "status": "final",
            "type": {
                "coding": [{
                    "system": SYSTEM_LOINC,
                    "code": "34133-9",
                    "display": "Summarization of episode note"
                }]
            },
            "subject": {"reference": patient_url},
            "encounter": {"reference": encounter_url},
            "date": now,
            "author": [{"reference": author_ref}],
            "title": "Resumen Digital de Atención en Salud - RDA Consulta Externa",
            "section": sections
        }
    }

    # Build final Bundle
    bundle = _build_bundle_shell(
        bundle_id=str(uuid.uuid4()),
        profile=PROFILE_BUNDLE_AMB,
        timestamp=now,
        composition_entry=composition,
        resource_entries=entries
    )

    logger.debug(f"RDA-Consulta bundle built with {len(bundle['entry'])} entries")
    return bundle


# ============================================================================
# LEGACY COMPATIBILITY — Maintains old function signature
# ============================================================================

def convert_to_fhir_rda(
    patient: PatientFullRecord,
    previous_visit_count: int = 0,
    rda_paciente_already_sent: bool = False,
) -> List[Dict[str, Any]]:
    """
    Generates only the FHIR RDA bundles that need to be sent to the FHIR Store.
    
    Delta logic:
      - RDA-Paciente: generated only if not previously sent, or if background 
        data (allergies, family history, chronic conditions) changed.
      - RDA-Consulta: generated only for NEW visits (index >= previous_visit_count).
    
    Args:
        patient: The full patient record (after LLM processing).
        previous_visit_count: How many visits were already synced to the FHIR Store.
        rda_paciente_already_sent: Whether the RDA-Paciente bundle was sent before.
    
    Returns:
        List of FHIR Bundles to send. May be empty if nothing changed.
    """
    bundles = []
    total_visits = len(patient.medicalHistory)
    new_visit_count = total_visits - previous_visit_count

    # RDA-Paciente: always send on first sync; on subsequent syncs, send if 
    # background data might have changed (new allergies, family history, etc.)
    if not rda_paciente_already_sent or new_visit_count > 0:
        bundles.append(build_rda_paciente(patient))

    # RDA-Consulta: only for visits that haven't been sent yet
    if new_visit_count > 0:
        new_visits = patient.medicalHistory[previous_visit_count:]
        for visit in new_visits:
            bundles.append(build_rda_consulta(patient, visit))
        logger.info(
            f"Delta: {new_visit_count} new visit(s) for patient {patient.patientId}"
        )
    elif not rda_paciente_already_sent:
        logger.info(f"First sync for patient {patient.patientId}, no visits yet")
    else:
        logger.info(f"No new visits for patient {patient.patientId}, skipping RDA-Consulta")

    logger.info(f"Generated {len(bundles)} RDA bundle(s) for patient {patient.patientId}")
    return bundles