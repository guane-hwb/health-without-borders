import logging
from datetime import datetime
from hl7apy.core import Segment
from app.schemas.patient import PatientFullRecord
from app.core.config import settings

# Setup Logger
logger = logging.getLogger(__name__)

def convert_to_hl7(patient: PatientFullRecord) -> str:
    """
    Generates a hybrid HL7v2 message using hl7apy segments.
    
    We use hl7apy to guarantee correct character escaping and exact field positioning 
    (avoiding manual string concatenation), while maintaining the flexible 
    hybrid structure (PV1, DG1, RXA) required by the MVP and supported by GCP.
    """
    logger.debug(f"Starting HL7 conversion using hl7apy for patient ID: {patient.patientId}")

    segments = []
    now = datetime.now()

    # --- 1. MSH (Message Header) ---
    msh = Segment("MSH", version=settings.HL7_VERSION_ID)
    # Valores dinámicos desde el .env / config.py
    msh.msh_3 = settings.HL7_SENDING_APP
    msh.msh_4 = settings.HL7_SENDING_FACILITY
    msh.msh_5 = settings.HL7_RECEIVING_APP
    msh.msh_6 = settings.HL7_RECEIVING_FACILITY
    
    msh.msh_9 = "ORU^R01^ORU_R01"
    msh.msh_10 = f"MSG{now.strftime('%f')}"
    msh.msh_11 = settings.HL7_PROCESSING_ID
    msh.msh_12 = settings.HL7_VERSION_ID
    segments.append(msh)

    # --- 2. PID (Patient Identification) ---
    pid = Segment("PID", version=settings.HL7_VERSION_ID)
    pid.pid_1 = "1"
    pid.pid_3 = patient.patientId
    pid.pid_5.pid_5_1 = patient.patientInfo.lastName or "UNKNOWN"
    pid.pid_5.pid_5_2 = patient.patientInfo.firstName or "UNKNOWN"
    
    if patient.patientInfo.dob:
        pid.pid_7 = patient.patientInfo.dob.strftime("%Y%m%d")
        
    pid.pid_8 = patient.patientInfo.gender or "U"
    
    addr = patient.patientInfo.address
    if addr:
        pid.pid_11.pid_11_1 = addr.street or ""
        pid.pid_11.pid_11_3 = addr.city or ""
        pid.pid_11.pid_11_4 = addr.state or ""
        pid.pid_11.pid_11_5 = addr.zipCode or ""
        pid.pid_11.pid_11_6 = addr.country or ""
    segments.append(pid)

    # --- 3. NK1 (Guardian) ---
    if patient.guardianInfo:
        nk1 = Segment("NK1", version=settings.HL7_VERSION_ID)
        nk1.nk1_1 = "1"
        nk1.nk1_2.nk1_2_1 = patient.guardianInfo.name or ""
        nk1.nk1_3 = patient.guardianInfo.relationship or ""
        if patient.guardianInfo.phone:
            nk1.nk1_5 = patient.guardianInfo.phone
        segments.append(nk1)

    # --- 4. Medical History (PV1, DG1, OBX) ---
    for i, visit in enumerate(patient.medicalHistory, start=1):
        # Visita Médica
        pv1 = Segment("PV1", version=settings.HL7_VERSION_ID)
        pv1.pv1_1 = str(i)
        pv1.pv1_2 = "O"  # Outpatient (Ambulatorio)
        pv1.pv1_3 = visit.location or ""
        pv1.pv1_7 = visit.physician or ""
        if visit.date:
            pv1.pv1_44 = visit.date.strftime("%Y%m%d")
        segments.append(pv1)

        # Diagnóstico
        dg1 = Segment("DG1", version=settings.HL7_VERSION_ID)
        dg1.dg1_1 = str(i)
        dg1.dg1_2 = "I"  # ICD (CIE)
        dg1.dg1_3.dg1_3_1 = visit.diagnosis.icd10Code
        dg1.dg1_3.dg1_3_2 = visit.diagnosis.description
        dg1.dg1_3.dg1_3_3 = "I10" # ICD-10
        dg1.dg1_4 = visit.diagnosis.description
        dg1.dg1_6 = "F"  # Final
        segments.append(dg1)

        # Notas de Observación
        if visit.observations:
            obx = Segment("OBX", version=settings.HL7_VERSION_ID)
            obx.obx_1 = str(i)
            obx.obx_2 = "CE"  # Coded Entry
            obx.obx_3.obx_3_1 = "Z00.1"
            obx.obx_3.obx_3_2 = "Examen"
            obx.obx_3.obx_3_3 = "I10" # ICD-10
            obx.obx_5 = visit.observations
            obx.obx_11 = "F"
            segments.append(obx)

    # --- 5. Vitals (Global OBX) ---
    if patient.patientInfo.weight:
        obx_w = Segment("OBX", version=settings.HL7_VERSION_ID)
        obx_w.obx_1 = "W1"
        obx_w.obx_2 = "NM"  # Numeric
        obx_w.obx_3.obx_3_1 = "29463-7"
        obx_w.obx_3.obx_3_2 = "Body weight"
        obx_w.obx_3.obx_3_3 = "LN"  # LOINC standard
        obx_w.obx_5 = str(patient.patientInfo.weight)
        obx_w.obx_6 = "kg"
        obx_w.obx_11 = "F"
        segments.append(obx_w)

    if patient.patientInfo.height:
        obx_h = Segment("OBX", version=settings.HL7_VERSION_ID)
        obx_h.obx_1 = "H1"
        obx_h.obx_2 = "NM"
        obx_h.obx_3.obx_3_1 = "8302-2"
        obx_h.obx_3.obx_3_2 = "Body height"
        obx_h.obx_3.obx_3_3 = "LN"
        obx_h.obx_5 = str(patient.patientInfo.height)
        obx_h.obx_6 = "cm"
        obx_h.obx_11 = "F"
        segments.append(obx_h)

    # --- 6. Vaccines (RXA) ---
    for i, vaccine in enumerate(patient.vaccinationRecord, start=1):
        rxa = Segment("RXA", version=settings.HL7_VERSION_ID)
        rxa.rxa_1 = "0"
        rxa.rxa_2 = str(i)
        vac_date = vaccine.date.strftime("%Y%m%d") if vaccine.date else ""
        rxa.rxa_3 = vac_date
        rxa.rxa_4 = vac_date
        rxa.rxa_5.rxa_5_1 = vaccine.vaccineCode
        rxa.rxa_5.rxa_5_2 = vaccine.vaccineName
        rxa.rxa_5.rxa_5_3 = "CVX"
        rxa.rxa_6 = str(vaccine.dose) if vaccine.dose else "1"
        rxa.rxa_15 = vaccine.lotNumber or ""
        rxa.rxa_20 = "CP"  # Complete
        segments.append(rxa)

    # hl7apy's .value attribute extracts the perfectly formatted string for each segment
    final_message = "\r".join([seg.value for seg in segments])
    logger.debug(f"HL7 message constructed. Total segments: {len(segments)}")
    
    return final_message