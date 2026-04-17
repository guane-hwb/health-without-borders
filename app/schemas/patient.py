from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

# ============================================================================
# ENUMS — Coded values per Resolution 866/2021 & 1888/2025
# ============================================================================

class DocumentType(str, Enum):
    """Tipo de documento de identificación — Fuente Maestro Persona MinSalud."""
    CC = "CC"   # Cédula de Ciudadanía
    CE = "CE"   # Cédula de Extranjería
    PA = "PA"   # Pasaporte
    RC = "RC"   # Registro Civil
    TI = "TI"   # Tarjeta de Identidad
    SC = "SC"   # Salvoconducto
    PE = "PE"   # Permiso Especial de Permanencia
    PT = "PT"   # Permiso por Protección Temporal
    MS = "MS"   # Menor sin identificación
    AS = "AS"   # Adulto sin identificación
    CN = "CN"   # Certificado de nacido vivo
    DE = "DE"   # Documento extranjero


class BiologicalSex(str, Enum):
    """Sexo biológico — SISPRO CodeSystem Sexo."""
    M = "M"     # Masculino
    F = "F"     # Femenino
    I = "I"     # Indeterminado # noqa: E741


class GenderIdentity(str, Enum):
    """Identidad de género — SISPRO CodeSystem MDECIdentidadGenero (opcional)."""
    MASCULINO = "01"
    FEMENINO = "02"
    TRANSGENERO = "03"
    NO_BINARIO = "04"
    NO_REPORTA = "99"


class EthnicGroup(str, Enum):
    """Etnia — SISPRO CodeSystem Etnia."""
    INDIGENA = "01"
    ROM_GITANO = "02"
    RAIZAL = "03"
    PALENQUERO = "04"
    AFROCOLOMBIANO = "05"
    NINGUNO = "06"


class ResidenceZone(str, Enum):
    """Zona territorial de residencia — SISPRO CodeSystem Zona."""
    URBANA = "U"
    RURAL = "R"


class DisabilityCategory(str, Enum):
    """Categoría de discapacidad — SISPRO CodeSystem CategoriaDiscapacidad."""
    NINGUNA = "00"
    FISICA = "01"
    INTELECTUAL = "02"
    AUDITIVA = "03"
    VISUAL = "04"
    SORDOCEGUERA = "05"
    PSICOSOCIAL = "06"
    MULTIPLE = "07"


class CareModality(str, Enum):
    """Modalidad de realización de la tecnología — Res. 866/2021 Elem. 18.1."""
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
    country: str = Field("COL", description="Código ISO 3166-1 del país de residencia (Elem. 11.1)")
    countryName: Optional[str] = Field(None, description="Nombre del país de residencia (Elem. 11.2)")
    zone: Optional[ResidenceZone] = Field(None, description="Zona territorial — Urbana/Rural (Elem. 14)")


class PatientIdentification(BaseModel):
    """Identificación del paciente según Maestro Persona MinSalud (Elems. 2.1, 2.2)."""
    documentType: DocumentType = Field(..., description="Tipo de documento de identificación (Elem. 2.1)")
    documentNumber: str = Field(..., description="Número de documento de identificación (Elem. 2.2)")


class PatientInfo(BaseModel):
    """Datos demográficos del paciente — Sección RDA Identificación del Paciente."""
    # Identification (Elems. 2.1, 2.2)
    identification: PatientIdentification

    # Names (Elems. 3.1 - 3.4)
    firstLastName: str = Field(..., description="Primer apellido (Elem. 3.1)")
    secondLastName: Optional[str] = Field(None, description="Segundo apellido (Elem. 3.2)")
    firstName: str = Field(..., description="Primer nombre (Elem. 3.3)")
    secondName: Optional[str] = Field(None, description="Segundo nombre (Elem. 3.4)")

    # Birth date (Elem. 4)
    dob: date = Field(..., description="Fecha de nacimiento (Elem. 4)")

    # Nationality (Elems. 1.1, 1.2) — critical for migrant children
    nationalityCode: str = Field("COL", description="Código ISO 3166-1 del país de nacionalidad (Elem. 1.1)")
    nationalityName: Optional[str] = Field(None, description="Nombre del país de nacionalidad (Elem. 1.2)")

    # Biological sex & gender identity (Elems. 5, 6)
    biologicalSex: BiologicalSex = Field(..., description="Sexo biológico (Elem. 5)")
    genderIdentity: Optional[GenderIdentity] = Field(None, description="Identidad de género — Opcional (Elem. 6)")

    # Ethnicity (Elems. 13.1, 13.2)
    ethnicity: Optional[EthnicGroup] = Field(None, description="Etnia (Elem. 13.1)")
    ethnicCommunity: Optional[str] = Field(None, description="Comunidad étnica — alfanumérico (Elem. 13.2)")

    # Disability (Elem. 10)
    disabilityCategory: Optional[DisabilityCategory] = Field(None, description="Categoría de discapacidad (Elem. 10)")

    # Address / Residence (Elems. 11.1-14)
    address: Address

    # Blood type (not in Res. 866 but clinically relevant for project)
    bloodType: Optional[str] = Field(None, description="Tipo de sangre (uso clínico interno)")

    # Vitals — carried at patient level for latest snapshot
    weight: Optional[float] = Field(None, description="Peso en Kg")
    height: Optional[float] = Field(None, description="Talla en cm")


class GuardianInfo(BaseModel):
    """Información del acudiente/tutor del menor."""
    name: str
    relationship: str
    phone: str
    device_uid: Optional[str] = Field(None, description="Hardware ID de la manilla NFC del acudiente")


# ============================================================================
# SUB-MODELS — Health backgrounds (antecedentes)
# ============================================================================

class FamilyHistoryItem(BaseModel):
    """
    Antecedente familiar estructurado — Res. 866/2021 Elems. 47.3, 47.4.
    
    Frontend sends: conditionDescription (required) + relationship (required).
    Backend LLM resolves: conditionCie10Code + conditionCie11Code.
    """
    conditionCie10Code: Optional[str] = Field(None, description="Código CIE-10 — resuelto por LLM (Elem. 47.3)")
    conditionCie11Code: Optional[str] = Field(None, description="Código CIE-11 — resuelto por LLM (opcional)")
    conditionDescription: str = Field(..., description="Descripción de la condición (entrada del frontend)")
    relationship: FamilyRelationship = Field(..., description="Parentesco (Elem. 47.4)")


class BackgroundHistory(BaseModel):
    """Antecedentes de salud declarados por el paciente."""
    chronicConditions: Optional[str] = Field(
        default=None,
        description="Condiciones crónicas (texto libre para captura rápida)."
    )
    personalHistory: Optional[str] = Field(
        default=None,
        description="Historial médico, quirúrgico o de nacimiento."
    )
    familyHistory: List[FamilyHistoryItem] = Field(
        default_factory=list,
        description="Antecedentes familiares estructurados con CIE-10 y parentesco."
    )
    familyHistoryNotes: Optional[str] = Field(
        default=None,
        description="Notas libres de antecedentes familiares (captura rápida cuando no se codifica)."
    )


# ============================================================================
# SUB-MODELS — Allergies
# ============================================================================

class AllergyInfo(BaseModel):
    """Alergia o intolerancia — Res. 866/2021 Elems. 47.1, 47.2."""
    category: AllergyCategory = Field(..., description="Tipo de alergia codificado (Elem. 47.1)")
    allergen: str = Field(..., description="Nombre del alérgeno (Elem. 47.2)")
    reaction: Optional[str] = Field(None, description="Descripción de la reacción adversa")
    notes: Optional[str] = None


# ============================================================================
# SUB-MODELS — Vaccinations
# ============================================================================

class VaccinationRecordItem(BaseModel):
    date: date
    vaccineName: str
    vaccineCode: str  # CVX Code
    dose: int
    administratedBy: str
    administratedAt: str
    status: str


# ============================================================================
# SUB-MODELS — Clinical encounter data
# ============================================================================

class ClinicalEvaluation(BaseModel):
    """Datos ingresados por el médico en el formulario de la consulta."""
    historyOfCurrentIllness: Optional[str] = Field(None, description="Enfermedad actual / motivo de consulta")
    generalPhysicalExamination: Optional[str] = Field(None, description="Examen físico general")
    systemsExamination: Optional[str] = Field(None, description="Revisión por sistemas")
    treatmentPlanObservations: Optional[str] = Field(None, description="Plan de tratamiento y observaciones")


class DiagnosisItem(BaseModel):
    """
    Diagnóstico estructurado con codificación CIE-10/11 — Res. 866/2021 Elems. 37.1, 37.2.
    
    Todos los campos son resueltos por el LLM a partir de la evaluación clínica.
    El tipo de diagnóstico (Elem. 37.3) se define a nivel del encuentro en MedicalHistoryItem.
    """
    icd10Code: str = Field(..., description="Código diagnóstico CIE-10 (Elem. 37.1)")
    icd11Code: Optional[str] = Field(None, description="Código diagnóstico CIE-11 (opcional)")
    description: str = Field(..., description="Nombre del diagnóstico (Elem. 37.2)")


class RiskFactor(BaseModel):
    """Factor de riesgo — Res. 866/2021 Elems. 48.1, 48.2."""
    type: RiskFactorType = Field(..., description="Tipo de factor de riesgo (Elem. 48.1)")
    name: str = Field(..., description="Nombre del factor de riesgo (Elem. 48.2)")


class IncapacityInfo(BaseModel):
    """Datos de incapacidad — Res. 866/2021 Elems. 45.1, 45.2, 46."""
    scope: IncapacityScope = Field(..., description="Alcance de la incapacidad (Elem. 45.1)")
    days: int = Field(..., description="Días de incapacidad (Elem. 45.2)")
    maternityLeaveDays: Optional[int] = Field(None, description="Días de licencia de maternidad (Elem. 46)")


class PractitionerInfo(BaseModel):
    """Información del profesional de salud — Res. 866/2021 Elems. 49.1, 49.2."""
    documentType: DocumentType = Field(..., description="Tipo de documento del profesional (Elem. 49.1)")
    documentNumber: str = Field(..., description="Número de documento del profesional (Elem. 49.2)")
    name: str = Field(..., description="Nombre del profesional (para visualización)")


class ProviderInfo(BaseModel):
    """Identificación del prestador de servicios de salud — Res. 866/2021 Elem. 16."""
    repsCode: str = Field(..., description="Código REPS del prestador (Elem. 16)")
    name: str = Field(..., description="Nombre del prestador")


class PayerInfo(BaseModel):
    """Entidad responsable del plan de beneficios — Res. 866/2021 Elems. 15.1, 15.2."""
    code: Optional[str] = Field(None, description="Código de la EAPB en el SGSSS (Elem. 15.1)")
    name: Optional[str] = Field(None, description="Nombre de la EAPB (Elem. 15.2)")


class MedicalHistoryItem(BaseModel):
    """
    Un evento de atención médica (consulta/visita).
    Corresponde a un Encounter + datos asociados en el RDA.
    """
    # Encounter metadata (Res. 866/2021)
    type: str = Field("Consultation", description="Tipo de evento")
    startDateTime: datetime = Field(..., description="Fecha y hora de inicio de atención (Elem. 17)")
    endDateTime: Optional[datetime] = Field(None, description="Fecha y hora de fin de atención (Elem. 43)")

    # Care context (Elems. 18.1, 18.2, 19, 20, 21)
    careModality: CareModality = Field(
        default=CareModality.INTRAMURAL,
        description="Modalidad de atención (Elem. 18.1)"
    )
    serviceGroup: ServiceGroup = Field(
        default=ServiceGroup.CONSULTA_EXTERNA,
        description="Grupo de servicios (Elem. 18.2)"
    )
    careEnvironment: CareEnvironment = Field(
        default=CareEnvironment.INSTITUCIONAL,
        description="Entorno de atención (Elem. 19)"
    )
    entryRoute: Optional[str] = Field(None, description="Vía de ingreso — código SISPRO (Elem. 20)")
    externalCause: Optional[str] = Field(None, description="Causa externa — código SISPRO (Elem. 21)")

    # Provider & practitioner
    provider: Optional[ProviderInfo] = Field(None, description="Prestador de servicios (Elem. 16)")
    practitioner: Optional[PractitionerInfo] = Field(None, description="Profesional de salud (Elems. 49.1, 49.2)")

    # Legacy fields for backward compatibility
    location: Optional[str] = Field(None, description="Nombre legible del lugar de atención (legacy)")
    physician: Optional[str] = Field(None, description="Nombre del médico (legacy, usar practitioner)")

    # Clinical content
    clinicalEvaluation: ClinicalEvaluation = Field(default_factory=ClinicalEvaluation)
    diagnosis: List[DiagnosisItem] = Field(default_factory=list)
    diagnosisType: DiagnosisType = Field(
        default=DiagnosisType.IMPRESION_DIAGNOSTICA,
        description="Tipo de diagnóstico principal del encuentro (Elem. 37.3). "
                    "El médico selecciona: 01=Impresión diagnóstica, 02=Confirmado nuevo, 03=Confirmado repetido."
    )

    # Discharge (Elem. 41)
    dischargeDisposition: Optional[DischargeDisposition] = Field(None, description="Condición al egreso (Elem. 41)")

    # Risk factors (Elems. 48.1, 48.2)
    riskFactors: List[RiskFactor] = Field(default_factory=list)

    # Incapacity (Elems. 45.1, 45.2, 46)
    incapacity: Optional[IncapacityInfo] = Field(None, description="Datos de incapacidad")

    # Payer for this encounter
    payer: Optional[PayerInfo] = Field(None, description="EAPB para este encuentro (Elems. 15.1, 15.2)")


# ============================================================================
# MAIN MODEL — Full Patient Record (incoming JSON payload)
# ============================================================================

class PatientFullRecord(BaseModel):
    """
    Modelo principal del registro completo del paciente.
    Payload de POST /sync y respuesta de GET /scan.

    Diseñado para cumplir con los elementos de dato de la Resolución 1888/2025
    (RDA-Paciente y RDA-Consulta Externa).
    """
    patientId: str = Field(..., description="UUID v4 generado por el frontend")
    device_uid: str = Field(..., description="UID del hardware NFC/QR de la manilla")

    patientInfo: PatientInfo
    guardianInfo: GuardianInfo

    # Antecedentes de salud (RDA-Paciente)
    backgroundHistory: Optional[BackgroundHistory] = None
    allergies: List[AllergyInfo] = Field(default_factory=list)

    # Datos episódicos (RDA-Consulta Externa)
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
    vida_code: Optional[str] = Field(None, description="Código VIDA retornado por el mecanismo IHCE")
    message: str