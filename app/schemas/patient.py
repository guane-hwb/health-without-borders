"""
Patient Schema — Resolution 1888/2025 + IG RDA v0.8.1 Compliance

Rewritten to match the official MinSalud Postman collection v1.4 field by field.

CHANGELOG v3.0 (Postman-verified full conformity):
  - FIX: ResidenceZone codes changed from "U"/"R" to "01"/"02" per Postman.
  - NEW: ColombianGenderGroup enum (01=Hombre, 02=Mujer, 03=Indeterminado)
         for the _gender.extension (ExtensionBiologicalGender).
  - CHANGED: PractitionerInfo now includes firstName, secondName,
         firstLastName, secondLastName for proper Practitioner name generation
         with ExtensionFathersFamilyName / ExtensionMothersFamilyName.
  - CHANGED: ProviderInfo now includes nitNumber for dual-coding Organization
         identifiers (NIT + CodigoPrestador).
  - CHANGED: Address.countryName now has display name (e.g. "Colombia") for
         address.country and _country.extension generation.
  - All existing fields, enums, and models are preserved for backward compat.
"""

import uuid as _uuid
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

# ============================================================================
# ENUMS — Resolution 866/2021 coded domains
# ============================================================================

class DocumentType(str, Enum):
    """
    Tipo de documento de identificación de persona.
    CodeSystem: ColombianPersonIdentifier
    URI: https://fhir.minsalud.gov.co/rda/CodeSystem/ColombianPersonIdentifier
    Fuente: https://vulcano.ihcecol.gov.co/CodeSystem-ColombianPersonIdentifier.html
    """
    CN = "CN"    # Certificado de nacido vivo
    RC = "RC"    # Registro civil
    TI = "TI"    # Tarjeta de identidad
    CC = "CC"    # Cédula de ciudadanía
    PA = "PA"    # Pasaporte
    CD = "CD"    # Carné diplomático
    CE = "CE"    # Cédula de extranjería
    SC = "SC"    # Salvoconducto de permanencia
    PE = "PE"    # Permiso Especial de Permanencia
    PT = "PT"    # Permiso Temporal de Permanencia
    PPT = "PPT"  # Permiso por protección temporal
    DE = "DE"    # Documento Extranjero
    AS = "AS"    # Adulto sin identificar
    MS = "MS"    # Menor sin identificar
    SI = "SI"    # Sin identificación


class BiologicalSex(str, Enum):
    """Sexo biológico — Res. 866/2021 Elem. 3."""
    M = "M"   # Masculino
    F = "F"   # Femenino
    I = "I"   # Indeterminado / Intersexual # noqa: E741


class ColombianGenderGroup(str, Enum):
    """
    Grupo de sexo biológico colombiano — para extensión _gender en Patient.
    CodeSystem: ColombianGenderGroup
    URI: https://fhir.minsalud.gov.co/rda/CodeSystem/ColombianGenderGroup
    Fuente: Postman MinSalud v1.4

    Se usa en Patient._gender.extension[ExtensionBiologicalGender].valueCoding.
    Es DIFERENTE de ColombianGenderIdentity (identidad de género).
    """
    HOMBRE = "01"
    MUJER = "02"
    INDETERMINADO = "03"


class ResidenceZone(str, Enum):
    """
    Zona territorial de residencia — Res. 866/2021 Elem. 14.
    CodeSystem: ColombianResidenceZone
    URI: https://fhir.minsalud.gov.co/rda/CodeSystem/ColombianResidenceZone

    CAMBIO v3.0: Códigos cambiados de "U"/"R" a "01"/"02" según Postman.
    """
    URBANA = "01"
    RURAL = "02"


class Ethnicity(str, Enum):
    """
    Pertenencia étnica.
    CodeSystem: ColombianEthnicGroup
    URI: https://fhir.minsalud.gov.co/rda/CodeSystem/ColombianEthnicGroup
    """
    INDIGENA = "1"
    ROM = "2"
    RAIZAL = "3"
    PALENQUERO = "4"
    NEGRO_AFROCOLOMBIANO = "5"
    OTRAS_ETNIAS = "6"
    NINGUNA = "99"


class DisabilityCategory(str, Enum):
    """
    Categoría de discapacidad.
    CodeSystem: ColombianDisabilityClassification
    URI: https://fhir.minsalud.gov.co/rda/CodeSystem/ColombianDisabilityClassification
    """
    FISICA = "01"
    VISUAL = "02"
    AUDITIVA = "03"
    INTELECTUAL = "04"
    SICOSOCIAL = "05"
    SORDOCEGUERA = "06"
    MULTIPLE = "07"
    SIN_DISCAPACIDAD = "08"


class GenderIdentity(str, Enum):
    """
    Identidad de género.
    CodeSystem: ColombianGenderIdentity
    URI: https://fhir.minsalud.gov.co/rda/CodeSystem/ColombianGenderIdentity
    """
    MASCULINO = "01"
    FEMENINO = "02"
    TRANSGENERO = "03"
    NEUTRO = "04"
    NO_DECLARA = "05"


class CareModality(str, Enum):
    """Modalidad de atención — Res. 866/2021 Elem. 18.1."""
    INTRAMURAL = "01"
    EXTRAMURAL_MOVIL = "02"
    EXTRAMURAL_DOMICILIARIA = "03"
    EXTRAMURAL_JORNADA = "04"
    EXTRAMURAL_PREHOSPITALARIA = "05"
    TELEMEDICINA_INTERACTIVA = "06"
    TELEMEDICINA_NO_INTERACTIVA = "07"
    TELEMEDICINA_TELEXPERTICIA = "08"
    TELEMEDICINA_TELEMONITOREO = "09"


class ServiceGroup(str, Enum):
    """Grupo de servicios — Res. 866/2021 Elem. 18.2."""
    CONSULTA_EXTERNA = "01"
    APOYO_DIAGNOSTICO = "02"
    INTERNACION = "03"
    QUIRURGICO = "04"
    ATENCION_INMEDIATA = "05"


class CareEnvironment(str, Enum):
    """Entorno donde se realiza la atención — Res. 866/2021 Elem. 19."""
    HOGAR = "01"
    COMUNITARIO = "02"
    ESCOLAR = "03"
    LABORAL = "04"
    INSTITUCIONAL = "05"


class AllergyCategory(str, Enum):
    """Código que indica tipo de alergia — Res. 866/2021 Elem. 47.1."""
    MEDICAMENTO = "01"
    ALIMENTO = "02"
    SUSTANCIA_AMBIENTE = "03"
    SUSTANCIA_PIEL = "04"
    PICADURA_INSECTOS = "05"
    OTRA = "06"


class FamilyRelationship(str, Enum):
    """Parentesco del antecedente familiar — Res. 866/2021 Elem. 47.4."""
    PADRES = "01"
    HERMANOS = "02"
    TIOS = "03"
    ABUELOS = "04"


class DiagnosisType(str, Enum):
    """Tipo de diagnóstico — Res. 866/2021 Elem. 37.3."""
    IMPRESION_DIAGNOSTICA = "01"
    CONFIRMADO_NUEVO = "02"
    CONFIRMADO_REPETIDO = "03"


class RiskFactorType(str, Enum):
    """Tipo de factor de riesgo — Res. 866/2021 Elem. 48.1."""
    QUIMICOS = "01"
    FISICOS = "02"
    BIOMECANICOS = "03"
    PSICOSOCIALES = "04"
    BIOLOGICOS = "05"
    OTRO = "06"


class DischargeDisposition(str, Enum):
    """Condición y destino del usuario al egreso — Res. 866/2021 Elem. 41."""
    ALTA_VOLUNTARIA = "01"
    PACIENTE_MUERTO = "02"
    REMITIDO = "03"
    ALTA_MEDICA = "04"


class IncapacityScope(str, Enum):
    """Alcance de la incapacidad — Res. 866/2021 Elem. 45.1."""
    NUEVA = "01"
    PRORROGA = "02"


class MedicationStatus(str, Enum):
    """Status for MedicationStatement — FHIR R4 value set."""
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class MedicationRequestStatus(str, Enum):
    """Status for MedicationRequest — FHIR R4 value set."""
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MedicationRequestIntent(str, Enum):
    """Intent for MedicationRequest — FHIR R4 value set."""
    ORDER = "order"
    PLAN = "plan"
    PROPOSAL = "proposal"


class RetiredDeviceReason(str, Enum):
    """
    Motivo por el que se retira la manilla anterior en un re-etiquetado.

    Solo aplica cuando un ``/sync`` reemplaza el ``device_uid`` de un registro
    existente. Si el ``device_uid`` cambia y la app no envía el motivo, el
    servidor registra el retiro igual con el valor genérico ``replaced``.
    """
    LOST = "lost"        # Manilla perdida
    DAMAGED = "damaged"  # Manilla dañada


# ============================================================================
# SUB-MODELS — Patient demographics
# ============================================================================

class Address(BaseModel):
    """Dirección de residencia habitual del paciente."""
    street: Optional[str] = None
    city: str = Field(..., description="Nombre del municipio de residencia habitual")
    cityCode: Optional[str] = Field(None, description="Código DIVIPOLA del municipio (Elem. 12.1)")
    state: str = Field(..., description="Departamento")
    zipCode: Optional[str] = None
    country: str = Field(
        "COL",
        description=(
            "Código ISO 3166-1 del país de residencia (Elem. 11.1). Se acepta "
            "alfa-3 ('COL'), alfa-2 o numérico; la app envía alfa-3. El bundle "
            "FHIR siempre emite el numérico, que es lo que exige el IG del RDA."
        ),
    )
    countryName: Optional[str] = Field(None, description="Nombre del país de residencia (Elem. 11.2)")
    zone: Optional[ResidenceZone] = Field(None, description="Zona territorial (Elem. 14)")


class PatientIdentification(BaseModel):
    """Identificación del paciente (Elems. 2.1, 2.2)."""
    documentType: DocumentType = Field(..., description="Tipo de documento (Elem. 2.1)")
    documentNumber: str = Field(..., description="Número de documento (Elem. 2.2)")


class PatientInfo(BaseModel):
    """Datos demográficos del paciente — Sección RDA Identificación."""
    identification: PatientIdentification
    firstLastName: str = Field(..., description="Primer apellido (Elem. 1.1)")
    secondLastName: Optional[str] = Field(None, description="Segundo apellido (Elem. 1.2)")
    firstName: str = Field(..., description="Primer nombre (Elem. 1.3)")
    secondName: Optional[str] = Field(None, description="Segundo nombre (Elem. 1.4)")
    dob: date = Field(..., description="Fecha de nacimiento (Elem. 7)")
    nationalityCode: str = Field(
        ...,
        description=(
            "Código ISO 3166-1 del país de nacionalidad (Elem. 9). Se acepta "
            "alfa-3 ('COL'), alfa-2 o numérico; la app envía alfa-3. El bundle "
            "FHIR siempre emite el numérico, que es lo que exige el IG del RDA."
        ),
    )
    nationalityName: Optional[str] = Field(None, description="Nombre del país (Elem. 10)")
    biologicalSex: BiologicalSex = Field(..., description="Sexo biológico (Elem. 3)")
    ethnicity: Optional[Ethnicity] = Field(None, description="Pertenencia étnica (Elem. 5)")
    disabilityCategory: Optional[DisabilityCategory] = Field(None, description="Discapacidad (Elem. 6)")
    genderIdentity: Optional[GenderIdentity] = Field(None, description="Identidad de género (Elem. 4)")
    address: Address
    bloodType: Optional[str] = Field(None, description="Tipo de sangre (uso clínico interno)")
    weight: Optional[float] = Field(None, description="Peso en Kg")
    height: Optional[float] = Field(None, description="Talla en cm")


class GuardianConsent(BaseModel):
    """
    Prueba de consentimiento informado del guardián — Ley 1581/2012 (Habeas Data).

    Almacena la evidencia de que el guardián autorizó el tratamiento de datos
    del menor. La firma se almacena como imagen PNG codificada en base64.
    """
    accepted: bool = Field(..., description="Guardián aceptó la política de privacidad")
    acceptedAt: datetime = Field(..., description="Timestamp ISO 8601 del momento de aceptación")
    email: Optional[str] = Field(None, description="Correo para envío del comprobante de consentimiento")
    signatureBase64: Optional[str] = Field(None, description="Firma biométrica como imagen PNG en base64")


class GuardianInfo(BaseModel):
    """Información del acudiente/tutor del menor."""
    name: str
    relationship: str
    phone: str
    device_uid: Optional[str] = Field(None, description="Hardware ID de la manilla NFC")
    documentType: Optional[str] = Field(None, description="Tipo de documento del guardián")
    documentNumber: Optional[str] = Field(None, description="Número de documento del guardián")
    consent: Optional[GuardianConsent] = Field(None, description="Consentimiento informado — Ley 1581/2012")


# ============================================================================
# SUB-MODELS — Health backgrounds (antecedentes)
# ============================================================================

class FamilyHistoryItem(BaseModel):
    """Antecedente familiar estructurado — Res. 866/2021 Elems. 47.3, 47.4."""
    conditionCie10Code: Optional[str] = Field(None, description="Código CIE-10")
    conditionCie11Code: Optional[str] = Field(None, description="Código CIE-11 (opcional)")
    conditionDescription: str = Field(..., description="Descripción de la condición")
    relationship: FamilyRelationship = Field(..., description="Parentesco (Elem. 47.4)")


class ChronicConditionItem(BaseModel):
    """Antecedente patológico estructurado — IG RDA v0.8.1 ConditionStatementRDA."""
    chronicDescription: str = Field(..., description="Descripción de la condición crónica (entrada del frontend)")
    chronicCie10Code: Optional[str] = Field(None, description="Código CIE-10 — resuelto por LLM")
    chronicCie11Code: Optional[str] = Field(None, description="Código CIE-11 — resuelto por LLM (opcional)")


class MedicationStatementItem(BaseModel):
    """Antecedente farmacológico — MedicationStatementRDA."""
    medicationName: str = Field(..., description="Nombre del medicamento")
    dciCode: Optional[str] = Field(None, description="Código DCI — MIPRES")
    status: MedicationStatus = Field(default=MedicationStatus.ACTIVE)
    dosage: Optional[str] = Field(None, description="Posología (texto libre)")
    notes: Optional[str] = Field(None, description="Observaciones adicionales")


class BackgroundHistory(BaseModel):
    """Antecedentes de salud declarados por el paciente."""
    chronicConditions: List[ChronicConditionItem] = Field(default_factory=list)
    personalHistory: Optional[str] = Field(default=None)
    familyHistory: List[FamilyHistoryItem] = Field(default_factory=list)
    familyHistoryNotes: Optional[str] = Field(default=None)
    medications: List[MedicationStatementItem] = Field(default_factory=list)

# ============================================================================
# SUB-MODELS — Allergies
# ============================================================================

class AllergyInfo(BaseModel):
    """Alergia o intolerancia — Res. 866/2021 Elems. 47.1, 47.2."""
    category: AllergyCategory = Field(..., description="Tipo de alergia (Elem. 47.1)")
    allergen: str = Field(..., description="Nombre del alérgeno (Elem. 47.2)")
    reaction: Optional[str] = Field(None, description="Descripción de la reacción adversa")
    notes: Optional[str] = None


# ============================================================================
# SUB-MODELS — Vaccinations
# ============================================================================

class VaccinationRecordItem(BaseModel):
    vaccinationId: Optional[str] = Field(None, description="UUID v4 del registro de vacunación")
    date: date
    vaccineName: str
    vaccineCode: str
    dose: int
    administratedBy: str
    administratedAt: str
    status: str

    @model_validator(mode='before')
    @classmethod
    def _ensure_vaccination_id(cls, data):
        """Assign a UUID if the frontend didn't provide one."""
        if isinstance(data, dict) and not data.get('vaccinationId'):
            data['vaccinationId'] = str(_uuid.uuid4())
        return data


# ============================================================================
# SUB-MODELS — Clinical encounter data
# ============================================================================

class ClinicalEvaluation(BaseModel):
    """Datos ingresados por el médico en la consulta."""
    historyOfCurrentIllness: Optional[str] = Field(None)
    generalPhysicalExamination: Optional[str] = Field(None)
    systemsExamination: Optional[str] = Field(None)
    treatmentPlanObservations: Optional[str] = Field(None)


class DiagnosisItem(BaseModel):
    """Diagnóstico CIE-10/11 — Res. 866/2021 Elems. 37.1, 37.2."""
    icd10Code: str = Field(..., description="Código CIE-10 (Elem. 37.1)")
    icd11Code: Optional[str] = Field(None, description="Código CIE-11 (opcional)")
    description: str = Field(..., description="Nombre del diagnóstico (Elem. 37.2)")


class RiskFactor(BaseModel):
    """Factor de riesgo — Res. 866/2021 Elems. 48.1, 48.2."""
    type: RiskFactorType = Field(..., description="Tipo (Elem. 48.1)")
    name: str = Field(..., description="Nombre (Elem. 48.2)")


class IncapacityInfo(BaseModel):
    """Incapacidad — Res. 866/2021 Elems. 45.1, 45.2, 46."""
    scope: IncapacityScope = Field(..., description="Alcance (Elem. 45.1)")
    days: int = Field(..., description="Días (Elem. 45.2)")
    maternityLeaveDays: Optional[int] = Field(None, description="Días licencia maternidad (Elem. 46)")


class PractitionerInfo(BaseModel):
    """
    Profesional de salud — Res. 866/2021 Elems. 49.1, 49.2.

    v3.0: Añadidos campos de nombre desglosado para ExtensionFathersFamilyName /
    ExtensionMothersFamilyName en el Practitioner FHIR.
    """
    documentType: DocumentType = Field(..., description="Tipo de documento (Elem. 49.1)")
    documentNumber: str = Field(..., description="Número de documento (Elem. 49.2)")
    name: str = Field(..., description="Nombre completo (visualización / legacy)")
    firstName: Optional[str] = Field(None, description="Primer nombre")
    secondName: Optional[str] = Field(None, description="Segundo nombre")
    firstLastName: Optional[str] = Field(None, description="Primer apellido")
    secondLastName: Optional[str] = Field(None, description="Segundo apellido")


class ProviderInfo(BaseModel):
    """
    Prestador de servicios de salud — Res. 866/2021 Elem. 16.

    v3.0: Añadido nitNumber para dual-coding Organization (NIT + CodigoPrestador).
    """
    repsCode: str = Field(..., description="Código REPS (Elem. 16)")
    name: str = Field(..., description="Nombre del prestador")
    nitNumber: Optional[str] = Field(None, description="NIT del prestador")
    locationSeatCode: Optional[str] = Field(
        None, description="Código sede (ej: repsCode-01) para Location"
    )


class PayerInfo(BaseModel):
    """EAPB — Res. 866/2021 Elems. 15.1, 15.2."""
    code: Optional[str] = Field(None, description="Código EAPB (Elem. 15.1)")
    name: Optional[str] = Field(None, description="Nombre EAPB (Elem. 15.2)")


class MedicationRequestItem(BaseModel):
    """Prescripción de medicamento — MedicationRequestRDA."""
    medicationName: str = Field(..., description="Nombre del medicamento")
    dciCode: Optional[str] = Field(None, description="Código DCI — MIPRES/SISPRO")
    iumCode: Optional[str] = Field(None, description="IUM")
    dosage: Optional[str] = Field(None, description="Posología (texto libre)")
    quantity: Optional[str] = Field(None, description="Cantidad prescrita")
    frequency: Optional[str] = Field(None, description="Frecuencia")
    duration: Optional[str] = Field(None, description="Duración")
    route: Optional[str] = Field(None, description="Vía de administración")
    status: MedicationRequestStatus = Field(default=MedicationRequestStatus.ACTIVE)
    intent: MedicationRequestIntent = Field(default=MedicationRequestIntent.ORDER)
    notes: Optional[str] = Field(None, description="Indicaciones adicionales")


class MedicalHistoryItem(BaseModel):
    """Un evento de atención médica (consulta/visita) = Encounter + datos asociados."""
    type: str = Field("Consultation", description="Tipo de evento")
    startDateTime: datetime = Field(..., description="Inicio de atención (Elem. 17)")
    endDateTime: Optional[datetime] = Field(None, description="Fin de atención (Elem. 43)")

    careModality: CareModality = Field(default=CareModality.INTRAMURAL)
    serviceGroup: ServiceGroup = Field(default=ServiceGroup.CONSULTA_EXTERNA)
    careEnvironment: CareEnvironment = Field(default=CareEnvironment.INSTITUCIONAL)
    entryRoute: Optional[str] = Field(None, description="Vía de ingreso (Elem. 20)")
    externalCause: Optional[str] = Field(None, description="Causa externa (Elem. 21)")
    externalCauseDisplay: Optional[str] = Field(None)

    healthcareServiceCode: Optional[str] = Field(None, description="Código servicio REPS")
    healthcareServiceDisplay: Optional[str] = Field(None)
    cupsCode: Optional[str] = Field(None, description="Código CUPS")
    cupsDisplay: Optional[str] = Field(None)
    encounterIdentifier: Optional[str] = Field(None, description="ID del encuentro")

    provider: Optional[ProviderInfo] = Field(None)
    practitioner: Optional[PractitionerInfo] = Field(None)
    location: Optional[str] = Field(None, description="Lugar de atención (legacy)")
    physician: Optional[str] = Field(None, description="Médico (legacy)")

    clinicalEvaluation: ClinicalEvaluation = Field(default_factory=ClinicalEvaluation)
    diagnosis: List[DiagnosisItem] = Field(default_factory=list)
    diagnosisType: DiagnosisType = Field(default=DiagnosisType.IMPRESION_DIAGNOSTICA)
    dischargeDisposition: Optional[DischargeDisposition] = Field(None)
    riskFactors: List[RiskFactor] = Field(default_factory=list)
    incapacity: Optional[IncapacityInfo] = Field(None)
    payer: Optional[PayerInfo] = Field(None)

    occupation: Optional[str] = Field(None, description="Código CIUO-88 A.C. (Elem. 22)")
    occupationDescription: Optional[str] = Field(None)
    prescriptions: List[MedicationRequestItem] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def _ensure_encounter_id(cls, data):
        """Assign a UUID if the frontend didn't provide one."""
        if isinstance(data, dict) and not data.get('encounterIdentifier'):
            data['encounterIdentifier'] = str(_uuid.uuid4())
        return data


# ============================================================================
# MAIN MODEL — Full Patient Record
# ============================================================================

class PatientFullRecord(BaseModel):
    """
    Modelo principal del registro completo del paciente.
    Payload de POST /sync y respuesta de GET /scan.
    """
    patientId: str = Field(..., description="UUID v4 generado por el frontend")
    device_uid: str = Field(..., description="UID del hardware NFC/QR de la manilla")

    retiredDeviceReason: Optional[RetiredDeviceReason] = Field(
        None,
        description=(
            "Motivo de retiro de la manilla anterior cuando este sync reemplaza "
            "el device_uid (re-etiquetado por manilla perdida o dañada). Solo se "
            "usa si el device_uid cambia respecto al registro existente; si no se "
            "envía, el retiro se registra como 'replaced'. Es una señal de "
            "transporte: no se persiste en el registro clínico ni se devuelve en "
            "/scan, y nunca crea un registro duplicado."
        ),
    )

    patientInfo: PatientInfo
    guardianInfo: GuardianInfo
    guardian2Info: Optional[GuardianInfo] = Field(None, description="Segundo guardián/tutor (opcional)")

    backgroundHistory: Optional[BackgroundHistory] = None
    allergies: List[AllergyInfo] = Field(default_factory=list)

    medicalHistory: List[MedicalHistoryItem] = Field(default_factory=list)
    vaccinationRecord: List[VaccinationRecordItem] = Field(default_factory=list)

    class Config:
        from_attributes = True


# ============================================================================
# API RESPONSES
# ============================================================================

class PatientSyncResponse(BaseModel):
    status: str
    internal_id: str
    fhir_status: Optional[str] = "unknown"
    vida_code: Optional[str] = Field(None, description="Código VIDA retornado por IHCE")
    message: str


# ============================================================================
# API REQUESTS
# ============================================================================

class PatientSearchRequest(BaseModel):
    """
    Body for the strict patient lookup endpoint.

    Identity criteria travel in the request body (not the query string) so that
    the patient's document number, names and birth date never appear in access
    logs, proxy logs or browser history.
    """
    document_number: str = Field(
        ..., min_length=3,
        description="Número de documento de identidad del paciente (Res. 866 Elem. 2.2)",
    )
    birth_date: date = Field(..., description="Fecha de nacimiento del paciente (YYYY-MM-DD)")
    first_name: str = Field(..., min_length=2, description="Primer nombre del paciente")
    last_name: str = Field(
        ..., min_length=2,
        description="Primer o segundo apellido del paciente",
    )
    guardian_name: Optional[str] = Field(
        None, min_length=3,
        description="Nombre completo del acudiente (refuerza la verificación si el paciente tiene guardián)",
    )