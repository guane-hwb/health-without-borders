"""
FHIR RDA Service — Resolution 1888/2025 Compliance

Generates FHIR R4 Bundles of type "document" for:
  - RDA-Paciente (patient self-reported health background)
  - RDA-Consulta (ambulatory encounter clinical data)

Each Bundle follows the IHCE Implementation Guide:
  https://vulcano.ihcecol.gov.co/guia/

Architecture:
  Bundle(type=document)
    ├── Composition (root — sections with references)
    ├── Patient (PatientRDA profile)
    ├── Organization (IPS — CareDeliveryOrganizationRDA)
    ├── Organization (EAPB — HealthBenefitPlanAdminOrganizationRDA)  [optional]
    ├── Practitioner (PractitionerRDA)
    ├── Encounter (EncounterAmbulatoryRDA)
    ├── Condition (ConditionRDA / ConditionStatementRDA)
    ├── AllergyIntolerance (AllergyIntoleranceRDA / StatementRDA)
    ├── FamilyMemberHistory (FamilyMemberHistoryRDA)
    └── ... additional resources per sections
"""

import uuid
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from app.schemas.patient import (
    PatientFullRecord, MedicalHistoryItem, AllergyCategory, BiologicalSex,
    DiagnosisType, FamilyRelationship
)

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS — FHIR System URIs for Colombian RDA
# ============================================================================

FHIR_RDA_BASE = "https://fhir.minsalud.gov.co/rda"

# Terminology systems
SYSTEM_SISPRO_ID_TYPE = "https://web.sispro.gov.co/CodeSystem/identification-type"
SYSTEM_SISPRO_SEX = "https://web.sispro.gov.co/CodeSystem/Sexo"
SYSTEM_SISPRO_ETHNICITY = "https://web.sispro.gov.co/CodeSystem/Etnia"
SYSTEM_SISPRO_DISABILITY = "https://web.sispro.gov.co/CodeSystem/CategoriaDiscapacidad"
SYSTEM_SISPRO_ZONE = "https://web.sispro.gov.co/CodeSystem/Zona"
SYSTEM_SISPRO_MUNICIPALITY = "https://web.sispro.gov.co/CodeSystem/Municipio"
SYSTEM_SISPRO_COUNTRY = "urn:iso:std:iso:3166"
SYSTEM_SISPRO_VIA_INGRESO = "https://web.sispro.gov.co/CodeSystem/ViaIngresoUsuario"
SYSTEM_SISPRO_CAUSA_EXTERNA = "https://web.sispro.gov.co/CodeSystem/RIPSCausaExterna"
SYSTEM_SISPRO_MODALITY = "https://web.sispro.gov.co/CodeSystem/ModalidadAtencion"
SYSTEM_SISPRO_SERVICE_GROUP = "https://web.sispro.gov.co/CodeSystem/GrupoServicios"
SYSTEM_SISPRO_ENVIRONMENT = "https://web.sispro.gov.co/CodeSystem/EntornoAtencion"
SYSTEM_SISPRO_DISCHARGE = "https://web.sispro.gov.co/CodeSystem/CondicionyDestinoUsuarioEgreso"
SYSTEM_SISPRO_ALLERGY_CAT = "https://web.sispro.gov.co/CodeSystem/CategoriaAlergia"
SYSTEM_SISPRO_FAMILY_REL = "https://web.sispro.gov.co/CodeSystem/ParentescoAntecedente"
SYSTEM_SISPRO_DIAG_TYPE = "https://web.sispro.gov.co/CodeSystem/TipoDiagnostico"
SYSTEM_SISPRO_RISK_FACTOR = "https://web.sispro.gov.co/CodeSystem/TipoFactorRiesgo"
SYSTEM_REPS = "https://web.sispro.gov.co/CodeSystem/IPSCodHabilitacion"

SYSTEM_CIE10 = "http://hl7.org/fhir/sid/icd-10"
SYSTEM_CIE11 = "http://id.who.int/icd/release/11/mms"
SYSTEM_LOINC = "http://loinc.org"
SYSTEM_UNITS = "http://unitsofmeasure.org"
SYSTEM_CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
SYSTEM_ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
SYSTEM_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

# Profile canonical URLs
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
    """
    Build a FHIR Bundle of type 'document' with the required 'identifier' field.
    
    FHIR R4 FHIRPath constraint for document bundles:
    'type = "document" implies (identifier.system.exists() and identifier.value.exists())'
    """
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
# RESOURCE BUILDERS
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
                "type": {
                    "coding": [{
                        "system": SYSTEM_SISPRO_ID_TYPE,
                        "code": ident.documentType.value
                    }]
                },
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
                    "system": SYSTEM_SISPRO_COUNTRY,
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
            "valueCoding": {"system": SYSTEM_SISPRO_MUNICIPALITY, "code": addr.cityCode}
        }]
    if addr.zone:
        fhir_addr.setdefault("extension", []).append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionResidenceZone",
            "valueCoding": {"system": SYSTEM_SISPRO_ZONE, "code": addr.zone.value}
        })
    resource["address"] = [fhir_addr]

    # Ethnicity
    if pi.ethnicity:
        resource["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientEthnicity",
            "valueCoding": {"system": SYSTEM_SISPRO_ETHNICITY, "code": pi.ethnicity.value}
        })

    # Disability
    if pi.disabilityCategory:
        resource["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientDisability",
            "valueCoding": {"system": SYSTEM_SISPRO_DISABILITY, "code": pi.disabilityCategory.value}
        })

    # Gender identity (optional)
    if pi.genderIdentity:
        resource["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionPatientGenderIdentity",
            "valueCoding": {
                "system": "https://web.sispro.gov.co/CodeSystem/MDECIdentidadGenero",
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
            "type": {"coding": [{"system": SYSTEM_SISPRO_ID_TYPE, "code": pract.documentType.value}]},
            "value": pract.documentNumber
        }],
        "name": [{"text": pract.name}]
    }


def _build_encounter_ambulatory(
    visit: MedicalHistoryItem, patient_ref: str, org_ref: Optional[str], pract_ref: Optional[str]
) -> Dict[str, Any]:
    """Build Encounter resource conforming to EncounterAmbulatoryRDA."""
    enc: Dict[str, Any] = {
        "resourceType": "Encounter",
        "meta": {"profile": [PROFILE_ENCOUNTER_AMB]},
        "status": "finished",
        "class": {"system": SYSTEM_ACT_CODE, "code": "AMB", "display": "ambulatory"},
        "subject": {"reference": patient_ref},
        "period": {
            "start": _fhir_datetime(visit.startDateTime)
        },
        "extension": [
            {
                "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionCareModality",
                "valueCoding": {"system": SYSTEM_SISPRO_MODALITY, "code": visit.careModality.value}
            },
            {
                "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionServiceGroup",
                "valueCoding": {"system": SYSTEM_SISPRO_SERVICE_GROUP, "code": visit.serviceGroup.value}
            },
            {
                "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionCareEnvironment",
                "valueCoding": {"system": SYSTEM_SISPRO_ENVIRONMENT, "code": visit.careEnvironment.value}
            }
        ]
    }

    if visit.endDateTime:
        enc["period"]["end"] = _fhir_datetime(visit.endDateTime)

    if org_ref:
        enc["serviceProvider"] = {"reference": org_ref}

    if pract_ref:
        enc["participant"] = [{"individual": {"reference": pract_ref}}]
    elif visit.physician:
        enc["participant"] = [{"individual": {"display": visit.physician}}]

    if visit.location:
        enc["location"] = [{"location": {"display": visit.location}}]

    if visit.entryRoute:
        enc["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionEntryRoute",
            "valueCoding": {"system": SYSTEM_SISPRO_VIA_INGRESO, "code": visit.entryRoute}
        })

    if visit.externalCause:
        enc["extension"].append({
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionExternalCause",
            "valueCoding": {"system": SYSTEM_SISPRO_CAUSA_EXTERNA, "code": visit.externalCause}
        })

    return enc


def _build_condition(diag, patient_ref: str, encounter_ref: str, diagnosis_type: "DiagnosisType") -> Dict[str, Any]:
    """Build Condition (diagnosis) resource. diagnosis_type comes from the encounter level."""
    coding = [{"system": SYSTEM_CIE10, "code": diag.icd10Code, "display": diag.description}]
    if diag.icd11Code:
        coding.append({"system": SYSTEM_CIE11, "code": diag.icd11Code})

    return {
        "resourceType": "Condition",
        "meta": {"profile": [PROFILE_CONDITION]},
        "clinicalStatus": {"coding": [{"system": SYSTEM_CONDITION_CLINICAL, "code": "active"}]},
        "code": {"coding": coding},
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
        "extension": [{
            "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionDiagnosisType",
            "valueCoding": {
                "system": SYSTEM_SISPRO_DIAG_TYPE,
                "code": diagnosis_type.value,
                "display": _diag_type_display(diagnosis_type)
            }
        }]
    }


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
                "system": SYSTEM_SISPRO_ALLERGY_CAT,
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
                "system": SYSTEM_SISPRO_FAMILY_REL,
                "code": fh_item.relationship.value,
                "display": _family_rel_display(fh_item.relationship)
            }]
        },
        "condition": [{"code": {"coding": coding}}]
    }


# ============================================================================
# BUNDLE BUILDERS — RDA Documents
# ============================================================================

def build_rda_paciente(patient: PatientFullRecord) -> Dict[str, Any]:
    """
    Generate RDA-Paciente: a FHIR Bundle of type 'document' containing
    the patient's self-reported health background (allergies, family history,
    chronic conditions).
    
    Per Resolution 1888/2025 Art. 3(a) and the IG at:
    https://vulcano.ihcecol.gov.co/RDA-paciente.html
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

    # Section: Allergies (patient-declared)
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

    # Section: Family history (patient-declared)
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

    # Section: Chronic conditions / pathological background (patient-declared)
    condition_refs = []
    if patient.backgroundHistory and patient.backgroundHistory.chronicConditions:
        cond_url = _uuid()
        entries.append({
            "fullUrl": cond_url,
            "resource": {
                "resourceType": "Condition",
                "meta": {"profile": [PROFILE_CONDITION_STMT]},
                "clinicalStatus": {"coding": [{"system": SYSTEM_CONDITION_CLINICAL, "code": "active"}]},
                "code": {"text": patient.backgroundHistory.chronicConditions},
                "subject": {"reference": patient_url}
            }
        })
        condition_refs.append({"reference": cond_url})

    sections.append(_build_section(
        "Condiciones de salud declaradas por el paciente",
        SYSTEM_LOINC, "11450-4", "Problem list",
        condition_refs
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
    
    Per Resolution 1888/2025 Art. 3(c) and the IG at:
    https://vulcano.ihcecol.gov.co/RDA-consulta.html
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

    # Section: Payer
    if eapb_url:
        sections.append({
            "title": "Pagadores",
            "entry": [{"reference": eapb_url}]
        })

    # Section: Allergies (encounter-identified)
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

    # Section: Diagnoses
    diag_refs = []
    for diag in visit.diagnosis:
        d_url = _uuid()
        entries.append({
            "fullUrl": d_url,
            "resource": _build_condition(diag, patient_url, encounter_url, visit.diagnosisType)
        })
        diag_refs.append({"reference": d_url})

    sections.append(_build_section(
        "Diagnósticos de problemas de salud",
        SYSTEM_LOINC, "11450-4", "Problem list",
        diag_refs
    ))

    # Section: Risk factors
    if visit.riskFactors:
        rf_refs = []
        for rf in visit.riskFactors:
            rf_url = _uuid()
            entries.append({
                "fullUrl": rf_url,
                "resource": {
                    "resourceType": "RiskAssessment",
                    "meta": {"profile": [f"{FHIR_RDA_BASE}/StructureDefinition/RiskFactorRDA"]},
                    "status": "final",
                    "subject": {"reference": patient_url},
                    "encounter": {"reference": encounter_url},
                    "extension": [{
                        "url": f"{FHIR_RDA_BASE}/StructureDefinition/ExtensionRiskFactorType",
                        "valueCoding": {"system": SYSTEM_SISPRO_RISK_FACTOR, "code": rf.type.value}
                    }],
                    "note": [{"text": rf.name}]
                }
            })
            rf_refs.append({"reference": rf_url})
        sections.append({
            "title": "Factores de riesgo",
            "entry": rf_refs
        })

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

def convert_to_fhir_rda(patient: PatientFullRecord) -> List[Dict[str, Any]]:
    """
    Main entry point. Generates all applicable RDA bundles for a patient record.
    
    Returns a list of FHIR Bundles:
      - Always: 1 RDA-Paciente bundle
      - Per visit: 1 RDA-Consulta bundle per MedicalHistoryItem
    """
    bundles = []

    # RDA-Paciente (always generated)
    bundles.append(build_rda_paciente(patient))

    # RDA-Consulta (one per encounter)
    for visit in patient.medicalHistory:
        bundles.append(build_rda_consulta(patient, visit))

    logger.info(f"Generated {len(bundles)} RDA bundles for patient {patient.patientId}")
    return bundles