"""
FHIR RDA Service — Resolution 1888/2025 + IG RDA v0.8.1 Full Compliance

REWRITTEN v3.0 to match field-by-field with the official MinSalud Postman
collection v1.4 (InteropAPI_Minsalud_Sandbox_-_Prestadores_v_1_4).

Source of truth: JSON bodies from "enviar-rda-paciente" and
"enviar-rda-consulta-externa" in the Postman collection.

KEY CHANGES v2.0 → v3.0 (all verified against Postman):
  - Bundle: Added "language": "es-CO"
  - References: Changed from urn:uuid to #id-style (contained references)
  - Composition: Corrected LOINC codes, added confidentiality/attester/custodian/event
  - Patient: Colombian surname extensions, _city/_country/_gender extensions,
             active=true, deceasedBoolean=false, address.type="physical"
  - Organization IPS: Dual-coding with 2 identifiers (NIT + CodigoPrestador)
  - Practitioner: Dual-coding + Colombian surname extensions
  - AllergyIntolerance: TipoAlergia in code.coding (NOT extension), verificationStatus
  - RiskAssessment: Type in code.coding, description in code.text, status="registered"
  - Occupation Observation: SNOMED CT (184104002), NOT LOINC
  - Encounter: identifier, Location resource, diagnosis[].extension for type
  - Condition: category + verificationStatus per Postman
  - Section LOINC codes corrected for 4 sections
  - Incapacity: Observation (AttendanceAllowanceRDA) with SNOMED components
"""

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.phi_sanitizer import safe_patient_ref
from app.schemas.patient import (
    AllergyCategory,
    BiologicalSex,
    ChronicConditionItem,
    ColombianGenderGroup,
    DiagnosisType,
    FamilyRelationship,
    IncapacityInfo,
    MedicalHistoryItem,
    MedicationRequestItem,
    MedicationStatementItem,
    PatientFullRecord,
    RiskFactorType,
)

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS — FHIR System URIs (ALL verified against Postman v1.4)
# ============================================================================

FHIR_RDA_BASE = "https://fhir.minsalud.gov.co/rda"

# Patient demographics
SYSTEM_PERSON_ID = f"{FHIR_RDA_BASE}/CodeSystem/ColombianPersonIdentifier"
SYSTEM_ETHNICITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianEthnicGroup"
SYSTEM_DISABILITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianDisabilityClassification"
SYSTEM_GENDER_IDENTITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianGenderIdentity"
SYSTEM_GENDER_GROUP = f"{FHIR_RDA_BASE}/CodeSystem/ColombianGenderGroup"
SYSTEM_ZONE = f"{FHIR_RDA_BASE}/CodeSystem/ColombianResidenceZone"
SYSTEM_MUNICIPALITY = f"{FHIR_RDA_BASE}/CodeSystem/DIVIPOLA"
SYSTEM_COUNTRY = f"{FHIR_RDA_BASE}/CodeSystem/ISO31661"

# Encounter context
SYSTEM_MODALITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianTechModality"
SYSTEM_SERVICE_GROUP = f"{FHIR_RDA_BASE}/CodeSystem/GrupoServicios"
SYSTEM_ENVIRONMENT = f"{FHIR_RDA_BASE}/CodeSystem/EntornoAtencion"
SYSTEM_CAUSA_EXTERNA = f"{FHIR_RDA_BASE}/CodeSystem/RIPSCausaExternaVersion2"
SYSTEM_DIAG_TYPE = f"{FHIR_RDA_BASE}/CodeSystem/RIPSTipoDiagnosticoPrincipalVersion2"
SYSTEM_DIAG_ROLE = f"{FHIR_RDA_BASE}/CodeSystem/ColombianDiagnosisRole"
SYSTEM_DISCHARGE = f"{FHIR_RDA_BASE}/CodeSystem/CondicionyDestinoUsuarioEgreso"
SYSTEM_VIA_INGRESO = f"{FHIR_RDA_BASE}/CodeSystem/ViaIngreso"
SYSTEM_REPS_SERVICES = f"{FHIR_RDA_BASE}/CodeSystem/REPShealthcareServices"
SYSTEM_CUPS = f"{FHIR_RDA_BASE}/CodeSystem/CUPS"
SYSTEM_FINALIDAD_CONSULTA = f"{FHIR_RDA_BASE}/CodeSystem/RIPSFinalidadConsultaVersion2"

# Clinical terminologies
SYSTEM_FAMILY_REL = f"{FHIR_RDA_BASE}/CodeSystem/ParentescoAntecedente"
SYSTEM_OCCUPATION = f"{FHIR_RDA_BASE}/CodeSystem/CIUO88AC"
SYSTEM_INCAPACITY = f"{FHIR_RDA_BASE}/CodeSystem/ColombianLicenseScope"
SYSTEM_ALLERGY_CAT = f"{FHIR_RDA_BASE}/CodeSystem/TipoAlergia"
SYSTEM_RISK_FACTOR = f"{FHIR_RDA_BASE}/CodeSystem/FactorRiesgo"
SYSTEM_ORG_IDS = f"{FHIR_RDA_BASE}/CodeSystem/ColombianOrganizationIdentifiers"
SYSTEM_DCI = f"{FHIR_RDA_BASE}/CodeSystem/MipresINN"
SYSTEM_IUM = f"{FHIR_RDA_BASE}/CodeSystem/IUM"
SYSTEM_HEALTH_TECH_CAT = f"{FHIR_RDA_BASE}/CodeSystem/ColombianHealthTechnologyCategory"

# International terminologies
SYSTEM_CIE10 = "http://hl7.org/fhir/sid/icd-10"
SYSTEM_CIE11 = "http://hl7.org/fhir/sid/icd-11"
SYSTEM_LOINC = "http://loinc.org"
SYSTEM_SNOMED = "http://snomed.info/sct"
SYSTEM_UNITS = "http://unitsofmeasure.org"
SYSTEM_CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
SYSTEM_CONDITION_VER_STATUS = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
SYSTEM_ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
SYSTEM_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
SYSTEM_HL7_ID_TYPE = "http://terminology.hl7.org/CodeSystem/v2-0203"
SYSTEM_PARTICIPATION = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
SYSTEM_CONFIDENTIALITY = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"
SYSTEM_CONDITION_CATEGORY = "http://terminology.hl7.org/CodeSystem/condition-category"

# NamingSystem URIs
NAMING_SYSTEM_RNEC = f"{FHIR_RDA_BASE}/NamingSystem/RNEC"
NAMING_SYSTEM_REPS = f"{FHIR_RDA_BASE}/NamingSystem/REPS"
NAMING_SYSTEM_ENCOUNTERS = f"{FHIR_RDA_BASE}/NamingSystem/Encounters"

# Extension URLs
EXT_BASE = f"{FHIR_RDA_BASE}/StructureDefinition"
EXT_FATHERS_FAMILY = f"{EXT_BASE}/ExtensionFathersFamilyName"
EXT_MOTHERS_FAMILY = f"{EXT_BASE}/ExtensionMothersFamilyName"
EXT_NATIONALITY = f"{EXT_BASE}/ExtensionPatientNationality"
EXT_ETHNICITY = f"{EXT_BASE}/ExtensionPatientEthnicity"
EXT_DISABILITY = f"{EXT_BASE}/ExtensionPatientDisability"
EXT_GENDER_IDENTITY = f"{EXT_BASE}/ExtensionPatientGenderIdentity"
EXT_BIOLOGICAL_GENDER = f"{EXT_BASE}/ExtensionBiologicalGender"
EXT_BIRTH_TIME = f"{EXT_BASE}/ExtensionBirthTime"
EXT_DIVIPOLA = f"{EXT_BASE}/ExtensionDivipolaMunicipality"
EXT_COUNTRY_CODE = f"{EXT_BASE}/ExtensionCountryCode"
EXT_RESIDENCE_ZONE = f"{EXT_BASE}/ExtensionResidenceZone"
EXT_DIAG_TYPE = f"{EXT_BASE}/ExtensionDiagnosisType"
EXT_DISCHARGE = f"{EXT_BASE}/ExtensionDischargeDisposition"
EXT_ENTRY_ROUTE = f"{EXT_BASE}/ExtensionEntryRoute"

# Profile canonical URLs
PROFILE_PATIENT = f"{EXT_BASE}/PatientRDA"
PROFILE_ORG_IPS = f"{EXT_BASE}/CareDeliveryOrganizationRDA"
PROFILE_ORG_EAPB = f"{EXT_BASE}/HealthBenefitPlanAdminOrganizationRDA"
PROFILE_PRACTITIONER = f"{EXT_BASE}/PractitionerRDA"
PROFILE_ENCOUNTER_AMB = f"{EXT_BASE}/EncounterAmbulatoryRDA"
PROFILE_CONDITION = f"{EXT_BASE}/ConditionRDA"
PROFILE_CONDITION_STMT = f"{EXT_BASE}/ConditionStatementRDA"
PROFILE_ALLERGY = f"{EXT_BASE}/AllergyIntoleranceRDA"
PROFILE_ALLERGY_STMT = f"{EXT_BASE}/AllergyIntoleranceStatementRDA"
PROFILE_FAMILY_HISTORY = f"{EXT_BASE}/FamilyMemberHistoryRDA"
PROFILE_COMPOSITION_PATIENT = f"{EXT_BASE}/CompositionPatientStatementRDA"
PROFILE_COMPOSITION_AMB = f"{EXT_BASE}/CompositionAmbulatoryRDA"
PROFILE_BUNDLE_PATIENT = f"{EXT_BASE}/BundlePatientStatementRDA"
PROFILE_BUNDLE_AMB = f"{EXT_BASE}/BundleAmbulatoryRDA"
PROFILE_MEDICATION_STMT = f"{EXT_BASE}/MedicationStatementRDA"
PROFILE_MEDICATION_REQ = f"{EXT_BASE}/MedicationRequestRDA"
PROFILE_OCCUPATION_OBS = f"{EXT_BASE}/PatientOccupationAtEncounterRDA"
PROFILE_ATTENDANCE_ALLOWANCE = f"{EXT_BASE}/AttendanceAllowanceRDA"
PROFILE_SERVICE_REQUEST = f"{EXT_BASE}/ServiceRequestRDA"
PROFILE_DOC_REFERENCE = f"{EXT_BASE}/DocumentReferenceEPIRDA"
PROFILE_RISK_FACTOR = f"{EXT_BASE}/RiskFactorRDA"
PROFILE_LOCATION = f"{EXT_BASE}/CareDeliveryLocationRDA"


# ============================================================================
# HELPERS
# ============================================================================

def _fhir_datetime(dt_obj) -> str:
    if not dt_obj:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(dt_obj, datetime):
        s = dt_obj.isoformat()
        return s + "Z" if not s.endswith("Z") and "+" not in s else s
    if isinstance(dt_obj, date):
        return dt_obj.isoformat()
    return str(dt_obj)


def _sex_to_fhir(sex: BiologicalSex) -> str:
    return {"M": "male", "F": "female", "I": "other"}.get(sex.value, "unknown")


def _sex_to_gender_group(sex: BiologicalSex) -> ColombianGenderGroup:
    return {
        "M": ColombianGenderGroup.HOMBRE,
        "F": ColombianGenderGroup.MUJER,
        "I": ColombianGenderGroup.INDETERMINADO,
    }.get(sex.value, ColombianGenderGroup.INDETERMINADO)


def _gender_group_display(gg: ColombianGenderGroup) -> str:
    return {"01": "Hombre", "02": "Mujer", "03": "Indeterminado"}.get(gg.value, "Indeterminado")


def _allergy_category_display(cat: AllergyCategory) -> str:
    return {
        "01": "Medicamento", "02": "Alimento", "03": "Sustancia del ambiente",
        "04": "Sustancia en contacto con la piel", "05": "Picadura de insectos", "06": "Otra",
    }.get(cat.value, "Otra")


def _family_rel_display(rel: FamilyRelationship) -> str:
    return {"01": "Padres", "02": "Hermanos", "03": "Tíos", "04": "Abuelos"}.get(rel.value, "Otro")


def _diag_type_display(dt: DiagnosisType) -> str:
    return {
        "01": "Impresión diagnóstica", "02": "Confirmado Nuevo", "03": "Confirmado Repetido",
    }.get(dt.value, "Impresión diagnóstica")


def _risk_factor_display(rf_type: RiskFactorType) -> str:
    return {
        "01": "Químicos", "02": "Físicos", "03": "Biomecánicos",
        "04": "Psicosociales", "05": "Biológicos", "06": "Otro",
    }.get(rf_type.value, "Otro")


def _zone_display(code: str) -> str:
    return {"01": "Urbana", "02": "Rural"}.get(code, "Urbana")


def _doc_type_display(code: str) -> str:
    return {
        "CC": "Cédula ciudadanía", "TI": "Tarjeta de identidad",
        "RC": "Registro civil", "CE": "Cédula de extranjería",
        "PA": "Pasaporte", "CN": "Certificado de nacido vivo",
        "CD": "Carné diplomático", "SC": "Salvoconducto de permanencia",
        "PE": "Permiso Especial de Permanencia", "PT": "Permiso Temporal de Permanencia",
        "PPT": "Permiso por protección temporal", "DE": "Documento Extranjero",
        "AS": "Adulto sin identificar", "MS": "Menor sin identificar",
        "SI": "Sin identificación",
    }.get(code, code)


def _modality_display(code: str) -> str:
    return {"01": "Intramural", "02": "Extramural domiciliaria",
            "03": "Extramural jornada de salud", "04": "Telemedicina"}.get(code, "")


def _service_group_display(code: str) -> str:
    return {"01": "Consulta externa", "02": "Apoyo diagnóstico",
            "03": "Internación", "04": "Quirúrgico", "05": "Atención inmediata"}.get(code, "")


def _environment_display(code: str) -> str:
    return {"01": "Hogar", "02": "Comunitario", "03": "Escolar",
            "04": "Laboral", "05": "Institucional"}.get(code, "")


def _ref(resource_id: str) -> str:
    """Build a #id reference. Post-processing will rewrite to urn:uuid for GCP."""
    return f"#{resource_id}"


def _assign_fullurls_and_rewrite_refs(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-process a Bundle for GCP Healthcare API compatibility.

    1. Assigns a urn:uuid fullUrl to each entry based on resource.id.
    2. Rewrites all #id references to the corresponding urn:uuid.
    3. Keeps resource.id intact (MinSalud needs it).

    This makes Bundles dual-compatible: GCP resolves via fullUrl/reference,
    MinSalud resolves via resource.id / #id.
    """
    import uuid as _uuid

    # Step 1: Build id → urn:uuid mapping and assign fullUrls
    id_to_urn: Dict[str, str] = {}
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        res_id = resource.get("id")
        if res_id:
            urn = f"urn:uuid:{_uuid.uuid4()}"
            id_to_urn[res_id] = urn
            entry["fullUrl"] = urn
        elif "fullUrl" not in entry:
            entry["fullUrl"] = f"urn:uuid:{_uuid.uuid4()}"

    # Step 2: Rewrite all #id references to urn:uuid
    def _rewrite(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "reference" and isinstance(v, str) and v.startswith("#"):
                    ref_id = v[1:]  # strip "#"
                    if ref_id in id_to_urn:
                        obj[k] = id_to_urn[ref_id]
                else:
                    _rewrite(v)
        elif isinstance(obj, list):
            for item in obj:
                _rewrite(item)

    _rewrite(bundle)
    return bundle


def _patient_id(patient: PatientFullRecord) -> str:
    ident = patient.patientInfo.identification
    return f"{ident.documentType.value}-{ident.documentNumber}"


# ============================================================================
# SECTION & BUNDLE SHELL BUILDERS
# ============================================================================

def _build_section(title: str, code_system: str, code_code: str,
                   code_display: str, refs: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    return {
        "resourceType": "Bundle",
        "id": bundle_id,
        "language": "es-CO",
        "identifier": {"system": f"{FHIR_RDA_BASE}/bundle-identifier", "value": bundle_id},
        "meta": {"profile": [profile]},
        "type": "document",
        "timestamp": timestamp,
        "entry": [composition_entry] + resource_entries,
    }


# ============================================================================
# RESOURCE BUILDERS — Patient
# ============================================================================

def _build_patient_resource(patient: PatientFullRecord) -> Dict[str, Any]:
    pi = patient.patientInfo
    ident = pi.identification
    pat_id = _patient_id(patient)

    resource: Dict[str, Any] = {
        "resourceType": "Patient",
        "id": pat_id,
        "meta": {"profile": [PROFILE_PATIENT]},
        "extension": [
            {
                "url": EXT_NATIONALITY,
                "valueCoding": {
                    "system": SYSTEM_COUNTRY,
                    "code": pi.nationalityCode,
                    "display": pi.nationalityName or pi.nationalityCode,
                },
            }
        ],
        "identifier": [
            {
                "type": {
                    "coding": [
                        {"system": SYSTEM_HL7_ID_TYPE, "code": "PN", "display": "Person number"},
                        {"system": SYSTEM_PERSON_ID, "code": ident.documentType.value,
                         "display": _doc_type_display(ident.documentType.value)},
                    ]
                },
                "id": "NationalPersonIdentifier-0",
                "use": "official",
                "system": NAMING_SYSTEM_RNEC,
                "value": ident.documentNumber,
            }
        ],
        "active": True,
        "gender": _sex_to_fhir(pi.biologicalSex),
        "birthDate": pi.dob.isoformat(),
        "deceasedBoolean": False,
    }

    # --- Name (Colombian surname extensions) ---
    family_value = pi.firstLastName
    if pi.secondLastName:
        family_value = f"{pi.firstLastName} {pi.secondLastName}"

    family_exts = [{"url": EXT_FATHERS_FAMILY, "valueString": pi.firstLastName}]
    if pi.secondLastName:
        family_exts.append({"url": EXT_MOTHERS_FAMILY, "valueString": pi.secondLastName})

    resource["name"] = [{
        "given": [g for g in [pi.firstName, pi.secondName] if g],
        "use": "official",
        "family": family_value,
        "_family": {"extension": family_exts},
    }]

    # --- Address (_city, _country, extension for zone) ---
    addr = pi.address
    fhir_addr: Dict[str, Any] = {
        "id": "HomeAddress-0",
        "use": "home",
        "type": "physical",
        "city": addr.city,
    }
    if addr.street:
        fhir_addr["line"] = [addr.street]
    if addr.state:
        fhir_addr["state"] = addr.state
    if addr.cityCode:
        fhir_addr["_city"] = {
            "extension": [{
                "url": EXT_DIVIPOLA,
                "valueCoding": {"code": addr.cityCode, "system": SYSTEM_MUNICIPALITY},
            }]
        }
    fhir_addr["country"] = addr.countryName or "Colombia"
    fhir_addr["_country"] = {
        "extension": [{
            "url": EXT_COUNTRY_CODE,
            "valueCoding": {"system": SYSTEM_COUNTRY, "code": addr.country},
        }]
    }
    if addr.zone:
        fhir_addr["extension"] = [{
            "url": EXT_RESIDENCE_ZONE,
            "valueCoding": {
                "system": SYSTEM_ZONE,
                "code": addr.zone.value,
                "display": _zone_display(addr.zone.value),
            },
        }]
    if addr.zipCode:
        fhir_addr["postalCode"] = addr.zipCode
    resource["address"] = [fhir_addr]

    # --- _gender extension (ColombianGenderGroup) ---
    gg = _sex_to_gender_group(pi.biologicalSex)
    resource["_gender"] = {
        "extension": [{
            "url": EXT_BIOLOGICAL_GENDER,
            "valueCoding": {
                "system": SYSTEM_GENDER_GROUP,
                "code": gg.value,
                "display": _gender_group_display(gg),
            },
        }]
    }

    # --- Additional patient extensions ---
    if pi.ethnicity:
        resource["extension"].append({
            "url": EXT_ETHNICITY,
            "valueCoding": {"system": SYSTEM_ETHNICITY, "code": pi.ethnicity.value},
        })
    if pi.disabilityCategory:
        resource["extension"].append({
            "url": EXT_DISABILITY,
            "valueCoding": {"system": SYSTEM_DISABILITY, "code": pi.disabilityCategory.value},
        })
    if pi.genderIdentity:
        resource["extension"].append({
            "url": EXT_GENDER_IDENTITY,
            "valueCoding": {"system": SYSTEM_GENDER_IDENTITY, "code": pi.genderIdentity.value},
        })

    # --- Guardian contacts ---
    contacts = []
    gi = patient.guardianInfo
    if gi and gi.name:
        contacts.append({
            "relationship": [{"text": gi.relationship}],
            "name": {"text": gi.name},
            "telecom": [{"system": "phone", "value": gi.phone}] if gi.phone else [],
        })
    gi2 = patient.guardian2Info
    if gi2 and gi2.name:
        contacts.append({
            "relationship": [{"text": gi2.relationship}],
            "name": {"text": gi2.name},
            "telecom": [{"system": "phone", "value": gi2.phone}] if gi2.phone else [],
        })
    if contacts:
        resource["contact"] = contacts

    return resource


# ============================================================================
# RESOURCE BUILDERS — Organization, Practitioner, Location
# ============================================================================

def _build_organization_ips(provider) -> Optional[Dict[str, Any]]:
    if not provider:
        return None
    org_id = provider.repsCode
    nit = provider.nitNumber or "Desconocido"
    return {
        "resourceType": "Organization",
        "id": org_id,
        "meta": {"profile": [PROFILE_ORG_IPS]},
        "identifier": [
            {
                "id": "TaxIdentifier-0",
                "use": "official",
                "type": {
                    "coding": [
                        {"system": SYSTEM_HL7_ID_TYPE, "code": "TAX", "display": "Tax ID number"},
                        {"system": SYSTEM_ORG_IDS, "code": "NIT",
                         "display": "Número de Identificación Tributaria"},
                    ]
                },
                "value": nit,
            },
            {
                "id": "HealthcareProviderIdentifier-0",
                "use": "official",
                "type": {
                    "coding": [
                        {"system": SYSTEM_HL7_ID_TYPE, "code": "PRN", "display": "Provider number"},
                        {"system": SYSTEM_ORG_IDS, "code": "CodigoPrestador",
                         "display": "Código de habilitación de prestador de servicios de salud"},
                    ]
                },
                "system": NAMING_SYSTEM_REPS,
                "value": org_id,
            },
        ],
    }


def _build_organization_eapb(payer) -> Optional[Dict[str, Any]]:
    if not payer or not payer.code:
        return None
    return {
        "resourceType": "Organization",
        "id": payer.code,
        "name": payer.name or "",
    }


def _build_practitioner(pract, pract_id: str) -> Optional[Dict[str, Any]]:
    if not pract:
        return None

    if pract.firstLastName:
        family_val = pract.firstLastName
        if pract.secondLastName:
            family_val = f"{pract.firstLastName} {pract.secondLastName}"
        family_exts = [{"url": EXT_FATHERS_FAMILY, "valueString": pract.firstLastName}]
        if pract.secondLastName:
            family_exts.append({"url": EXT_MOTHERS_FAMILY, "valueString": pract.secondLastName})
        given = [g for g in [pract.firstName, pract.secondName] if g]
        name_entry: Dict[str, Any] = {
            "use": "official",
            "family": family_val,
            "_family": {"extension": family_exts},
            "given": given if given else [pract.name.split()[0]] if pract.name else [],
        }
    else:
        parts = pract.name.split() if pract.name else [""]
        name_entry = {"use": "official", "family": parts[-1] if parts else "",
                      "given": parts[:-1] if len(parts) > 1 else parts}

    return {
        "resourceType": "Practitioner",
        "id": pract_id,
        "meta": {"profile": [PROFILE_PRACTITIONER]},
        "identifier": [{
            "id": "NationalPersonIdentifier-0",
            "use": "official",
            "type": {
                "coding": [
                    {"system": SYSTEM_HL7_ID_TYPE, "code": "PN", "display": "Person number"},
                    {"system": SYSTEM_PERSON_ID, "code": pract.documentType.value,
                     "display": _doc_type_display(pract.documentType.value)},
                ]
            },
            "value": pract.documentNumber,
        }],
        "name": [name_entry],
    }


def _build_location(provider) -> Optional[Dict[str, Any]]:
    if not provider:
        return None
    seat = provider.locationSeatCode or f"{provider.repsCode}-01"
    return {
        "resourceType": "Location",
        "id": seat,
        "meta": {"profile": [PROFILE_LOCATION]},
        "identifier": [{"use": "official", "system": NAMING_SYSTEM_REPS, "value": seat}],
        "name": provider.name,
        "managingOrganization": {"reference": _ref(provider.repsCode)},
    }


# ============================================================================
# RESOURCE BUILDERS — Encounter
# ============================================================================

def _build_encounter(
    visit: MedicalHistoryItem,
    patient_id: str,
    org_id: Optional[str],
    pract_id: Optional[str],
    location_id: Optional[str],
    encounter_id: str,
) -> Dict[str, Any]:
    enc_types: List[Dict[str, Any]] = [
        {"coding": [{"system": SYSTEM_MODALITY, "code": visit.careModality.value,
                      "display": _modality_display(visit.careModality.value)}]},
        {"coding": [{"system": SYSTEM_SERVICE_GROUP, "code": visit.serviceGroup.value,
                      "display": _service_group_display(visit.serviceGroup.value)}]},
    ]
    if visit.healthcareServiceCode:
        enc_types.append({"coding": [{"system": SYSTEM_REPS_SERVICES,
                                       "code": visit.healthcareServiceCode,
                                       "display": visit.healthcareServiceDisplay or ""}]})
    enc_types.append({"coding": [{"system": SYSTEM_ENVIRONMENT,
                                   "code": visit.careEnvironment.value,
                                   "display": _environment_display(visit.careEnvironment.value)}]})

    enc: Dict[str, Any] = {
        "resourceType": "Encounter",
        "id": encounter_id,
        "meta": {"profile": [PROFILE_ENCOUNTER_AMB]},
        "identifier": [{
            "id": "EncounterIdentifier",
            "use": "usual",
            "system": NAMING_SYSTEM_ENCOUNTERS,
            "value": visit.encounterIdentifier or f"ENC-{encounter_id}",
        }],
        "status": "finished",
        "class": {"system": SYSTEM_ACT_CODE, "code": "AMB", "display": "ambulatory"},
        "type": enc_types,
        "subject": {"reference": _ref(patient_id)},
        "period": {"start": _fhir_datetime(visit.startDateTime)},
    }

    if visit.endDateTime:
        enc["period"]["end"] = _fhir_datetime(visit.endDateTime)

    if visit.cupsCode:
        enc["serviceType"] = {"coding": [{"system": SYSTEM_CUPS, "code": visit.cupsCode,
                                           "display": visit.cupsDisplay or ""}]}

    if pract_id:
        enc["participant"] = [{
            "id": "AttenderPhysician",
            "type": [{"coding": [{"system": SYSTEM_PARTICIPATION, "code": "ATND", "display": "attender"}]}],
            "individual": {"reference": _ref(pract_id)},
        }]
    elif visit.physician:
        enc["participant"] = [{"individual": {"display": visit.physician}}]

    if visit.externalCause:
        enc["reasonCode"] = [{"coding": [{"system": SYSTEM_CAUSA_EXTERNA,
                                           "code": visit.externalCause,
                                           "display": visit.externalCauseDisplay or ""}]}]

    enc_extensions: List[Dict[str, Any]] = []
    if visit.dischargeDisposition:
        enc_extensions.append({
            "url": EXT_DISCHARGE,
            "extension": [{"url": "DispositionCode",
                           "valueCoding": {"system": SYSTEM_DISCHARGE,
                                           "code": visit.dischargeDisposition.value}}],
        })
    if visit.entryRoute:
        enc_extensions.append({
            "url": EXT_ENTRY_ROUTE,
            "valueCoding": {"system": SYSTEM_VIA_INGRESO, "code": visit.entryRoute},
        })
    if enc_extensions:
        enc["extension"] = enc_extensions

    if location_id:
        enc["location"] = [{"location": {"reference": _ref(location_id)}}]
    elif visit.location:
        enc["location"] = [{"location": {"display": visit.location}}]

    if org_id:
        enc["serviceProvider"] = {"reference": _ref(org_id)}

    return enc


# ============================================================================
# RESOURCE BUILDERS — Clinical resources
# ============================================================================

def _build_condition(diag, patient_id: str, cond_id: str) -> Dict[str, Any]:
    coding = [{"system": SYSTEM_CIE10, "code": diag.icd10Code, "display": diag.description}]
    if diag.icd11Code:
        coding.append({"system": SYSTEM_CIE11, "code": diag.icd11Code})
    return {
        "resourceType": "Condition",
        "id": cond_id,
        "meta": {"profile": [PROFILE_CONDITION]},
        "clinicalStatus": {"coding": [{"code": "active", "system": SYSTEM_CONDITION_CLINICAL,
                                        "display": "Active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed", "display": "Confirmed"}]},
        "category": [{"coding": [{"system": SYSTEM_CONDITION_CATEGORY,
                                   "code": "encounter-diagnosis",
                                   "display": "Encounter Diagnosis"}]}],
        "code": {"coding": coding},
        "subject": {"reference": _ref(patient_id)},
    }


def _build_condition_statement(cond: ChronicConditionItem, patient_id: str,
                                cond_id: str) -> Dict[str, Any]:
    resource: Dict[str, Any] = {
        "resourceType": "Condition",
        "id": cond_id,
        "meta": {"profile": [PROFILE_CONDITION_STMT]},
        "clinicalStatus": {"coding": [{"code": "active", "system": SYSTEM_CONDITION_CLINICAL,
                                        "display": "Active"}]},
        "verificationStatus": {"coding": [{"code": "unconfirmed", "display": "Unconfirmed"}]},
        "category": [{"coding": [{"system": SYSTEM_CONDITION_CATEGORY,
                                   "code": "encounter-diagnosis",
                                   "display": "Encounter Diagnosis"}]}],
        "subject": {"reference": _ref(patient_id)},
    }
    if cond.chronicCie10Code:
        coding = [{"system": SYSTEM_CIE10, "code": cond.chronicCie10Code,
                   "display": cond.chronicDescription}]
        if cond.chronicCie11Code:
            coding.append({"system": SYSTEM_CIE11, "code": cond.chronicCie11Code})
        resource["code"] = {"coding": coding}
    else:
        resource["code"] = {"text": cond.chronicDescription}
    return resource


def _build_allergy_statement(allergy, patient_id: str, a_id: str) -> Dict[str, Any]:
    resource: Dict[str, Any] = {
        "resourceType": "AllergyIntolerance",
        "id": a_id,
        "meta": {"profile": [PROFILE_ALLERGY_STMT]},
        "clinicalStatus": {"coding": [{"code": "active", "display": "Active"}]},
        "verificationStatus": {"coding": [{"code": "unconfirmed", "display": "Unconfirmed"}]},
        "code": {
            "coding": [{"system": SYSTEM_ALLERGY_CAT, "code": allergy.category.value,
                        "display": _allergy_category_display(allergy.category)}],
            "text": allergy.allergen,
        },
        "patient": {"reference": _ref(patient_id)},
    }
    if allergy.reaction:
        resource["reaction"] = [{"manifestation": [{"text": allergy.reaction}]}]
    if allergy.notes:
        resource["note"] = [{"text": allergy.notes}]
    return resource


def _build_allergy_encounter(allergy, patient_id: str, encounter_id: str,
                              a_id: str) -> Dict[str, Any]:
    r = _build_allergy_statement(allergy, patient_id, a_id)
    r["meta"]["profile"] = [PROFILE_ALLERGY]
    r["encounter"] = {"reference": _ref(encounter_id)}
    r.pop("verificationStatus", None)
    return r


def _build_family_member_history(fh, patient_id: str, fmh_id: str) -> Dict[str, Any]:
    coding = [{"system": SYSTEM_CIE10, "code": fh.conditionCie10Code or ""}]
    if fh.conditionDescription:
        coding[0]["display"] = fh.conditionDescription
    if fh.conditionCie11Code:
        coding.append({"system": SYSTEM_CIE11, "code": fh.conditionCie11Code})
    return {
        "resourceType": "FamilyMemberHistory",
        "id": fmh_id,
        "meta": {"profile": [PROFILE_FAMILY_HISTORY]},
        "status": "partial",
        "patient": {"reference": _ref(patient_id)},
        "relationship": {"coding": [{"system": SYSTEM_FAMILY_REL,
                                      "code": fh.relationship.value,
                                      "display": _family_rel_display(fh.relationship)}]},
        "condition": [{"code": {"coding": coding}}],
    }


def _build_medication_statement(med: MedicationStatementItem, patient_id: str,
                                 m_id: str) -> Dict[str, Any]:
    mc: Dict[str, Any] = {}
    if med.dciCode:
        mc["coding"] = [{"system": SYSTEM_DCI, "code": med.dciCode, "display": med.medicationName}]
    else:
        mc["text"] = med.medicationName
    r: Dict[str, Any] = {
        "resourceType": "MedicationStatement",
        "id": m_id,
        "meta": {"profile": [PROFILE_MEDICATION_STMT]},
        "status": med.status.value,
        "medicationCodeableConcept": mc,
        "subject": {"reference": _ref(patient_id)},
    }
    if med.dosage:
        r["dosage"] = [{"text": med.dosage}]
    if med.notes:
        r["note"] = [{"text": med.notes}]
    return r


def _build_medication_request(rx: MedicationRequestItem, patient_id: str,
                               encounter_id: str, pract_id: Optional[str],
                               rx_id: str, now: str) -> Dict[str, Any]:
    mc: Dict[str, Any] = {}
    codings = []
    if rx.dciCode:
        codings.append({"system": SYSTEM_DCI, "code": rx.dciCode, "display": rx.medicationName})
    if rx.iumCode:
        codings.append({"system": SYSTEM_IUM, "code": rx.iumCode})
    mc = {"coding": codings} if codings else {"text": rx.medicationName}

    r: Dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "id": rx_id,
        "meta": {"profile": [PROFILE_MEDICATION_REQ]},
        "status": rx.status.value,
        "intent": rx.intent.value,
        "category": [{"coding": [{"system": SYSTEM_HEALTH_TECH_CAT, "code": "02",
                                   "display": "Medicamento con registro sanitario"}]}],
        "reportedBoolean": True,
        "medicationCodeableConcept": mc,
        "subject": {"reference": _ref(patient_id)},
        "encounter": {"reference": _ref(encounter_id)},
        "authoredOn": now,
    }
    if pract_id:
        r["requester"] = {"reference": _ref(pract_id)}
    parts = []
    if rx.dosage:
        parts.append(rx.dosage)
    if rx.frequency:
        parts.append(f"Frecuencia: {rx.frequency}")
    if rx.duration:
        parts.append(f"Duración: {rx.duration}")
    if rx.route:
        parts.append(f"Vía: {rx.route}")
    if parts:
        r["dosageInstruction"] = [{"text": ". ".join(parts)}]
    if rx.notes:
        r["note"] = [{"text": rx.notes}]
    return r


def _build_risk_assessment(rf, patient_id: str, encounter_id: str,
                            rf_id: str) -> Dict[str, Any]:
    return {
        "resourceType": "RiskAssessment",
        "id": rf_id,
        "meta": {"profile": [PROFILE_RISK_FACTOR]},
        "status": "registered",
        "code": {
            "coding": [{"system": SYSTEM_RISK_FACTOR, "code": rf.type.value,
                        "display": _risk_factor_display(rf.type)}],
            "text": rf.name,
        },
        "subject": {"reference": _ref(patient_id)},
        "encounter": {"reference": _ref(encounter_id)},
    }


def _build_occupation_obs(occ_code: str, occ_display: Optional[str],
                           patient_id: str, obs_id: str) -> Dict[str, Any]:
    vc: Dict[str, Any] = {"coding": [{"system": SYSTEM_OCCUPATION, "code": occ_code}]}
    if occ_display:
        vc["coding"][0]["display"] = occ_display
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "meta": {"profile": [PROFILE_OCCUPATION_OBS]},
        "status": "final",
        "code": {
            "coding": [{"system": SYSTEM_SNOMED, "code": "184104002",
                        "display": "ocupación del paciente"}],
            "text": "Ocupación del paciente en el momento de la atención",
        },
        "subject": {"reference": _ref(patient_id)},
        "valueCodeableConcept": vc,
    }


def _build_attendance_allowance(incap: IncapacityInfo, patient_id: str,
                                 encounter_id: str, obs_id: str) -> Dict[str, Any]:
    components = [{
        "id": "LicenseScope",
        "code": {
            "coding": [{"system": SYSTEM_SNOMED, "code": "255590007", "display": "alcance"}],
            "text": "Incapacidad - Alcance de la incapacidad",
        },
        "valueCodeableConcept": {"coding": [{
            "system": SYSTEM_INCAPACITY, "code": incap.scope.value,
            "display": "Nueva" if incap.scope.value == "01" else "Prórroga",
        }]},
    }]
    if incap.maternityLeaveDays is not None:
        components.append({
            "id": "MaternityLicenseTime",
            "code": {
                "coding": [{"system": SYSTEM_SNOMED, "code": "410670007", "display": "tiempo"}],
                "text": "Días de licencia de maternidad",
            },
            "valueQuantity": {"value": incap.maternityLeaveDays, "unit": "días",
                              "system": SYSTEM_UNITS, "code": "d"},
        })
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "meta": {"profile": [PROFILE_ATTENDANCE_ALLOWANCE]},
        "status": "final",
        "code": {
            "coding": [{"system": SYSTEM_SNOMED, "code": "160983005",
                        "display": "permiso de concurrencia"}],
            "text": "Datos incapacidad (SIPE – Sistema de Incapacidades y Prestaciones Economicas)",
        },
        "subject": {"reference": _ref(patient_id)},
        "encounter": {"reference": _ref(encounter_id)},
        "component": components,
    }


# ============================================================================
# BUNDLE BUILDERS — RDA-Paciente
# ============================================================================

def build_rda_paciente(patient: PatientFullRecord) -> Dict[str, Any]:
    """
    RDA-Paciente: Composition type LOINC 102089-0.
    Matches Postman "enviar-rda-paciente" field by field.
    """
    logger.debug("Building RDA-Paciente for patient %s", safe_patient_ref(patient.patientId))

    entries: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    pat_id = _patient_id(patient)

    # Patient resource
    entries.append({"resource": _build_patient_resource(patient)})

    # Organization IPS (from first visit if available)
    org_id = None
    provider = None
    if patient.medicalHistory:
        provider = patient.medicalHistory[0].provider
    if provider:
        org_id = provider.repsCode
        entries.append({"resource": _build_organization_ips(provider)})

    # Practitioner (from first visit if available)
    pract_id = None
    pract = None
    if patient.medicalHistory:
        pract = patient.medicalHistory[0].practitioner
    if pract:
        pract_id = f"{pract.documentType.value}-{pract.documentNumber}"
        entries.append({"resource": _build_practitioner(pract, pract_id)})

    # --- Sections ---
    sections = []
    idx = {"cond": 0, "allergy": 0, "fmh": 0, "medstmt": 0}

    # Section 1: Chronic conditions (ConditionStatementRDA)
    cond_refs = []
    if patient.backgroundHistory:
        for c in patient.backgroundHistory.chronicConditions:
            cid = f"Condition-{idx['cond']}" 
            idx["cond"] += 1
            entries.append({"resource": _build_condition_statement(c, pat_id, cid)})
            cond_refs.append({"reference": _ref(cid)})
    sections.append(_build_section(
        "Historial de diagnósticos de problemas de salud",
        SYSTEM_LOINC, "11450-4", "Problem list - Reported", cond_refs))

    # Section 2: Allergies (AllergyIntoleranceStatementRDA)
    allergy_refs = []
    for a in patient.allergies:
        aid = f"AllergyIntolerance-{idx['allergy']}" 
        idx["allergy"] += 1
        entries.append({"resource": _build_allergy_statement(a, pat_id, aid)})
        allergy_refs.append({"reference": _ref(aid)})
    sections.append(_build_section(
        "Historial de alergias, intolerancias y reacciones adversas",
        SYSTEM_LOINC, "48765-2", "Allergies and adverse reactions Document", allergy_refs))

    # Section 3: Medication history (MedicationStatementRDA)
    med_refs = []
    if patient.backgroundHistory and patient.backgroundHistory.medications:
        for m in patient.backgroundHistory.medications:
            mid = f"MedicationStatement-{idx['medstmt']}" 
            idx["medstmt"] += 1
            entries.append({"resource": _build_medication_statement(m, pat_id, mid)})
            med_refs.append({"reference": _ref(mid)})
    sections.append(_build_section(
        "Historial de medicamentos",
        SYSTEM_LOINC, "10160-0", "History of Medication use Narrative", med_refs))

    # Section 4: Family history (FamilyMemberHistoryRDA)
    fmh_refs = []
    if patient.backgroundHistory and patient.backgroundHistory.familyHistory:
        for fh in patient.backgroundHistory.familyHistory:
            fid = f"FamilyMemberHistory-{idx['fmh']}" 
            idx["fmh"] += 1
            entries.append({"resource": _build_family_member_history(fh, pat_id, fid)})
            fmh_refs.append({"reference": _ref(fid)})
    sections.append(_build_section(
        "Historial de antecedentes familiares",
        SYSTEM_LOINC, "10157-6", "History of family member diseases Narrative", fmh_refs))

    # --- Composition ---
    author_ref = _ref(pract_id) if pract_id else (_ref(org_id) if org_id else _ref(pat_id))
    composition: Dict[str, Any] = {
        "resource": {
            "resourceType": "Composition",
            "meta": {"profile": [PROFILE_COMPOSITION_PATIENT]},
            "status": "final",
            "type": {"coding": [{"system": SYSTEM_LOINC, "code": "102089-0",
                                  "display": "FHIR resource patient medical record"}]},
            "subject": {"reference": _ref(pat_id)},
            "date": now,
            "author": [{"reference": author_ref}],
            "title": "Resumen Digital de Atención en Salud - RDA de antecedentes manifestados por el paciente",
            "confidentiality": "N",
            "section": sections,
        }
    }
    if org_id:
        composition["resource"]["attester"] = [{"mode": "legal", "party": {"reference": _ref(org_id)}}]
        composition["resource"]["custodian"] = {"reference": _ref(org_id)}

    # event with modality + service group + period (from first visit)
    if patient.medicalHistory:
        v0 = patient.medicalHistory[0]
        composition["resource"]["event"] = [{
            "code": [
                {"coding": [{"system": SYSTEM_MODALITY, "code": v0.careModality.value,
                              "display": _modality_display(v0.careModality.value)}]},
                {"coding": [{"system": SYSTEM_SERVICE_GROUP, "code": v0.serviceGroup.value,
                              "display": _service_group_display(v0.serviceGroup.value)}]},
            ],
            "period": {
                "start": _fhir_datetime(v0.startDateTime),
                "end": _fhir_datetime(v0.endDateTime) if v0.endDateTime else _fhir_datetime(v0.startDateTime),
            },
        }]

    bundle = _build_bundle_shell(
        bundle_id=str(uuid.uuid4()), profile=PROFILE_BUNDLE_PATIENT,
        timestamp=now, composition_entry=composition, resource_entries=entries)

    bundle = _assign_fullurls_and_rewrite_refs(bundle)
    logger.debug(f"RDA-Paciente bundle built with {len(bundle['entry'])} entries")
    return bundle


# ============================================================================
# BUNDLE BUILDERS — RDA-Consulta Externa
# ============================================================================

def build_rda_consulta(patient: PatientFullRecord,
                       visit: MedicalHistoryItem) -> Dict[str, Any]:
    """
    RDA-Consulta: Composition type LOINC 51845-6.
    Matches Postman "enviar-rda-consulta-externa" field by field.
    """
    logger.debug("Building RDA-Consulta for patient %s", safe_patient_ref(patient.patientId))

    entries: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    pat_id = _patient_id(patient)

    # 1. Patient
    entries.append({"resource": _build_patient_resource(patient)})

    # 2. Organization IPS
    org_id = None
    location_id = None
    if visit.provider:
        org_id = visit.provider.repsCode
        entries.append({"resource": _build_organization_ips(visit.provider)})
        loc_resource = _build_location(visit.provider)
        if loc_resource:
            location_id = loc_resource["id"]
            # Location added after Encounter below

    # 3. Practitioner
    pract_id = None
    if visit.practitioner:
        pract_id = f"{visit.practitioner.documentType.value}-{visit.practitioner.documentNumber}"
        entries.append({"resource": _build_practitioner(visit.practitioner, pract_id)})

    # 4. Encounter
    encounter_id = "Encounter-0"
    enc_resource = _build_encounter(visit, pat_id, org_id, pract_id, location_id, encounter_id)
    # Diagnoses will be attached below

    # --- Sections + clinical resources ---
    sections = []
    idx = {"cond": 0, "allergy": 0, "rf": 0, "rx": 0, "sr": 0, "obs": 0}

    # Section 1: Payer
    eapb_id = None
    payer_refs = []
    if visit.payer:
        eapb_r = _build_organization_eapb(visit.payer)
        if eapb_r:
            eapb_id = eapb_r["id"]
            entries.append({"resource": eapb_r})
            payer_refs.append({"reference": _ref(eapb_id)})
    sections.append(_build_section(
        "Entidad(es) responsable(s) por el plan de beneficios en salud (consulta)",
        SYSTEM_LOINC, "48768-6", "Payment sources Document", payer_refs))

    # Section 2: Otros datos demográficos — Occupation (SNOMED 184104002)
    occ_refs = []
    if visit.occupation:
        oid = f"Observation-{idx['obs']}" 
        idx["obs"] += 1
        entries.append({"resource": _build_occupation_obs(
            visit.occupation, visit.occupationDescription, pat_id, oid)})
        occ_refs.append({"reference": _ref(oid)})
    sections.append(_build_section(
        "Otros datos demográficos",
        SYSTEM_LOINC, "74208-0", "Demographic information + History of occupation Document",
        occ_refs))

    # Section 3: Incapacidad (AttendanceAllowanceRDA)
    incap_refs = []
    if visit.incapacity:
        iid = f"Observation-{idx['obs']}" 
        idx["obs"] += 1
        entries.append({"resource": _build_attendance_allowance(
            visit.incapacity, pat_id, encounter_id, iid)})
        incap_refs.append({"reference": _ref(iid)})
    sections.append(_build_section(
        "Datos incapacidad (SIPE – Sistema de Incapacidades y Prestaciones Economicas)",
        SYSTEM_LOINC, "105583-9", "Worker Sick leave form", incap_refs))

    # Section 4: Diagnoses
    diag_refs = []
    encounter_diagnoses = []
    for i, diag in enumerate(visit.diagnosis):
        cid = f"Condition-{idx['cond']}" 
        idx["cond"] += 1
        entries.append({"resource": _build_condition(diag, pat_id, cid)})
        diag_refs.append({"reference": _ref(cid)})
        encounter_diagnoses.append({
            "id": "MainDiagnosis" if i == 0 else f"Diagnosis-{i}",
            "extension": [{
                "url": EXT_DIAG_TYPE,
                "valueCoding": {"system": SYSTEM_DIAG_TYPE, "code": visit.diagnosisType.value,
                                "display": _diag_type_display(visit.diagnosisType)},
            }],
            "condition": {"reference": _ref(cid)},
            "use": {"coding": [{"system": SYSTEM_DIAG_ROLE, "code": "8319008",
                                "display": "diagnóstico primario"}]},
            "rank": i + 1,
        })
    if encounter_diagnoses:
        enc_resource["diagnosis"] = encounter_diagnoses
    sections.append(_build_section(
        "Historial de diagnósticos de problemas de salud",
        SYSTEM_LOINC, "11450-4", "Problem list - Reported", diag_refs))

    # Now add Encounter + Location entries
    entries.append({"resource": enc_resource})
    if location_id and visit.provider:
        entries.append({"resource": _build_location(visit.provider)})

    # Section 5: Allergies (encounter-identified)
    allergy_refs = []
    for a in patient.allergies:
        aid = f"AllergyIntolerance-{idx['allergy']}" 
        idx["allergy"] += 1
        entries.append({"resource": _build_allergy_encounter(a, pat_id, encounter_id, aid)})
        allergy_refs.append({"reference": _ref(aid)})
    sections.append(_build_section(
        "Historial de alergias, intolerancias y reacciones adversas",
        SYSTEM_LOINC, "48765-2", "Allergies and adverse reactions Document", allergy_refs))

    # Section 6: Risk factors
    rf_refs = []
    for rf in visit.riskFactors:
        rid = f"RiskAssessment-{idx['rf']}" 
        idx["rf"] += 1
        entries.append({"resource": _build_risk_assessment(rf, pat_id, encounter_id, rid)})
        rf_refs.append({"reference": _ref(rid)})
    sections.append(_build_section(
        "Factores de riesgo",
        SYSTEM_LOINC, "75492-9", "Risk assessment and screening note", rf_refs))

    # Section 7: Prescribed medications
    rx_refs = []
    for rx in visit.prescriptions:
        rxid = f"MedicationRequest-{idx['rx']}" 
        idx["rx"] += 1
        entries.append({"resource": _build_medication_request(
            rx, pat_id, encounter_id, pract_id, rxid, now)})
        rx_refs.append({"reference": _ref(rxid)})
    sections.append(_build_section(
        "Historial de medicamentos",
        SYSTEM_LOINC, "10160-0", "History of Medication use Narrative", rx_refs))

    # Section 8: Service requests / orders (emptyReason)
    sections.append(_build_section(
        "Órdenes, prescripciones o solicitudes de servicio",
        SYSTEM_LOINC, "61146-1", "Orders for services Document", []))

    # Section 9: Supporting documents (emptyReason)
    sections.append(_build_section(
        "Documentos de soporte",
        SYSTEM_LOINC, "55107-7", "Addendum Document", []))

    # --- Composition ---
    author_ref = _ref(org_id) if org_id else (_ref(pract_id) if pract_id else _ref(pat_id))
    attester_ref = _ref(pract_id) if pract_id else (_ref(org_id) if org_id else _ref(pat_id))
    custodian_ref = _ref(org_id) if org_id else _ref(pat_id)

    comp: Dict[str, Any] = {
        "resource": {
            "resourceType": "Composition",
            "meta": {"profile": [PROFILE_COMPOSITION_AMB]},
            "status": "final",
            "type": {"coding": [{"system": SYSTEM_LOINC, "code": "51845-6",
                                  "display": "Outpatient Consult note"}]},
            "subject": {"reference": _ref(pat_id)},
            "encounter": {"reference": _ref(encounter_id)},
            "date": now,
            "author": [{"reference": author_ref}],
            "title": "RDA Consulta",
            "confidentiality": "N",
            "attester": [{"mode": "legal", "party": {"reference": attester_ref}}],
            "custodian": {"reference": custodian_ref},
            "event": [{
                "period": {
                    "start": _fhir_datetime(visit.startDateTime),
                    "end": _fhir_datetime(visit.endDateTime) if visit.endDateTime
                           else _fhir_datetime(visit.startDateTime),
                },
            }],
            "section": sections,
        }
    }

    bundle = _build_bundle_shell(
        bundle_id=str(uuid.uuid4()), profile=PROFILE_BUNDLE_AMB,
        timestamp=now, composition_entry=comp, resource_entries=entries)

    bundle = _assign_fullurls_and_rewrite_refs(bundle)
    logger.debug(f"RDA-Consulta bundle built with {len(bundle['entry'])} entries")
    return bundle


# ============================================================================
# LEGACY COMPATIBILITY — Delta sync logic
# ============================================================================

def convert_to_fhir_rda(
    patient: PatientFullRecord,
    synced_encounter_ids: list[str] | None = None,
    rda_paciente_already_sent: bool = False,
    background_data_changed: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Generates only the FHIR RDA bundles that need to be sent.

    H1 + H7 delta logic:
      - RDA-Paciente: generated only if not previously sent, or if
        background data changed (detected via hash comparison by the caller).
      - RDA-Consulta: generated only for visits whose encounterIdentifier
        is NOT in the set of already-synced encounter IDs.

    Args:
        patient: The full patient record.
        synced_encounter_ids: Set of encounter UUIDs already sent to FHIR.
        rda_paciente_already_sent: Whether RDA-Paciente was sent at least once.
        background_data_changed: Whether the background data hash changed (H1).

    Returns:
        Tuple of (list of FHIR bundles, list of NEW encounter IDs that were bundled).
        The caller uses the new encounter IDs to update the sync tracking.
    """
    bundles: list[dict[str, Any]] = []
    synced_set = set(synced_encounter_ids or [])
    patient_ref = safe_patient_ref(patient.patientId)

    # H7: Identify new visits by encounter UUID, not by list index
    new_visits: list[MedicalHistoryItem] = []
    new_encounter_ids: list[str] = []
    for visit in patient.medicalHistory:
        enc_id = visit.encounterIdentifier
        if enc_id and enc_id not in synced_set:
            new_visits.append(visit)
            new_encounter_ids.append(enc_id)

    # H1: Re-send RDA-Paciente only when background data changed or never sent
    needs_rda_paciente = (
        not rda_paciente_already_sent
        or background_data_changed
    )

    if needs_rda_paciente:
        bundles.append(build_rda_paciente(patient))
        if background_data_changed and rda_paciente_already_sent:
            logger.info(
                "Background data changed for patient %s — regenerating RDA-Paciente",
                patient_ref,
            )

    # Generate RDA-Consulta for each genuinely new visit
    for visit in new_visits:
        bundles.append(build_rda_consulta(patient, visit))

    if new_visits:
        logger.info(
            "Delta: %d new visit(s) for patient %s (by encounterIdentifier)",
            len(new_visits), patient_ref,
        )
    elif not rda_paciente_already_sent:
        logger.info("First sync for patient %s, no visits yet", patient_ref)
    elif not needs_rda_paciente:
        logger.info("No changes for patient %s, skipping all bundles", patient_ref)

    logger.info("Generated %d RDA bundle(s) for patient %s", len(bundles), patient_ref)
    return bundles, new_encounter_ids