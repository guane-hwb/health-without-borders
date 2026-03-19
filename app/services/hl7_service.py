import logging
from datetime import datetime
from hl7apy.core import Segment
from app.schemas.patient import PatientFullRecord
from app.core.config import settings

logger = logging.getLogger(__name__)

def convert_to_hl7(patient: PatientFullRecord) -> str:
    """
    Generates a hybrid HL7v2 message using hl7apy segments.
    """
    logger.debug(f"Starting HL7 conversion using hl7apy for patient ID: {patient.patientId}")

    segments = []
    now = datetime.now()

    # MSH (Message Header)
    msh = Segment("MSH", version=settings.HL7_VERSION_ID)
    msh.msh_3 = settings.HL7_SENDING_APP
    msh.msh_4 = settings.HL7_SENDING_FACILITY
    msh.msh_5 = settings.HL7_RECEIVING_APP
    msh.msh_6 = settings.HL7_RECEIVING_FACILITY
    msh.msh_9 = "ORU^R01^ORU_R01"
    msh.msh_10 = f"MSG{now.strftime('%f')}"
    msh.msh_11 = settings.HL7_PROCESSING_ID
    msh.msh_12 = settings.HL7_VERSION_ID
    segments.append(msh)

    # PID (Patient Identification)
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

    # NK1 (Guardian)
    if patient.guardianInfo:
        nk1 = Segment("NK1", version=settings.HL7_VERSION_ID)
        nk1.nk1_1 = "1"
        nk1.nk1_2.nk1_2_1 = patient.guardianInfo.name or ""
        nk1.nk1_3 = patient.guardianInfo.relationship or ""
        if patient.guardianInfo.phone:
            nk1.nk1_5 = patient.guardianInfo.phone
        segments.append(nk1)

    # Medical History (PV1, DG1)
    for i, visit in enumerate(patient.medicalHistory, start=1):
        # Each visit gets a PV1 segment, and each diagnosis within that visit gets a DG1 segment. 
        # This allows us to capture multiple visits and their associated diagnoses in a structured way.
        pv1 = Segment("PV1", version=settings.HL7_VERSION_ID)
        pv1.pv1_1 = str(i)
        pv1.pv1_2 = "O"  # Outpatient
        pv1.pv1_3 = visit.location or ""
        pv1.pv1_7 = visit.physician or ""
        if visit.date:
            pv1.pv1_44 = visit.date.strftime("%Y%m%d")
        segments.append(pv1)

        # Diagnósticos (uno o más por visita)
        for diagnosis_idx, diagnosis in enumerate(visit.diagnosis, start=1):
            dg1 = Segment("DG1", version=settings.HL7_VERSION_ID)
            dg1.dg1_1 = f"{i}.{diagnosis_idx}"
            dg1.dg1_2 = "I"  # ICD (CIE)
            dg1.dg1_3.dg1_3_1 = diagnosis.icd10Code
            dg1.dg1_3.dg1_3_2 = diagnosis.description
            dg1.dg1_3.dg1_3_3 = "I10" # ICD-10
            dg1.dg1_4 = diagnosis.description
            dg1.dg1_6 = "F"  # Final
            segments.append(dg1)

        # Notas clínicas estructuradas (clinicalEvaluation)
        clinical_notes = [
            ("HPI", "History of present illness", visit.clinicalEvaluation.historyOfCurrentIllness),
            ("PEX", "General physical examination", visit.clinicalEvaluation.generalPhysicalExamination),
            ("SYS", "Systems examination", visit.clinicalEvaluation.systemsExamination),
            ("TPL", "Treatment plan observations", visit.clinicalEvaluation.treatmentPlanObservations),
        ]
        obx_idx = 1
        for code, label, text in clinical_notes:
            if not text:
                continue
            obx = Segment("OBX", version=settings.HL7_VERSION_ID)
            obx.obx_1 = f"{i}.{obx_idx}"
            obx.obx_2 = "TX"  # Text data
            obx.obx_3.obx_3_1 = code
            obx.obx_3.obx_3_2 = label
            obx.obx_3.obx_3_3 = "L"   # Local coding system
            obx.obx_5 = text
            obx.obx_11 = "F"
            segments.append(obx)
            obx_idx += 1

    # --- 5. Vitals (Global OBX) ---
    if patient.patientInfo.weight:
        obx_w = Segment("OBX", version=settings.HL7_VERSION_ID)
        obx_w.obx_1 = "W1"
        obx_w.obx_2 = "NM"
        obx_w.obx_3.obx_3_1 = "29463-7"
        obx_w.obx_3.obx_3_2 = "Body weight"
        obx_w.obx_3.obx_3_3 = "LN"
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

    # Vaccines (RXA)
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
        if vaccine.administratedBy:
            rxa.rxa_10 = vaccine.administratedBy
        if vaccine.administratedAt:
            rxa.rxa_11 = vaccine.administratedAt
        rxa.rxa_20 = "CP"
        segments.append(rxa)

    # Allergies (AL1)
    for i, allergy in enumerate(patient.allergies, start=1):
        al1 = Segment("AL1", version=settings.HL7_VERSION_ID)
        al1.al1_1 = str(i)
        al1.al1_2 = "DA" # Drug Allergy
        
        al1.al1_3.al1_3_1 = "UNK"
        al1.al1_3.al1_3_2 = allergy.allergen 
        al1.al1_3.al1_3_3 = "LOCAL" 
        
        al1.al1_4 = "U" # unknown severity
        al1.al1_5 = allergy.reaction.value if hasattr(allergy.reaction, 'value') else allergy.reaction
        
        segments.append(al1)

    final_message = "\r".join([seg.value for seg in segments])
    logger.debug(f"HL7 message constructed. Total segments: {len(segments)}")
    
    return final_message