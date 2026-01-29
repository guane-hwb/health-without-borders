import logging
from datetime import datetime
from typing import Optional, List
from app.schemas.patient import PatientFullRecord

# Setup Logger
logger = logging.getLogger(__name__)

def format_hl7_date(dt_obj: Optional[datetime]) -> str:
    """
    Formats a datetime object to HL7 standard (YYYYMMDD).
    Returns an empty string if the date is None to avoid breaking the pipe structure.
    """
    if not dt_obj:
        return ""
    return dt_obj.strftime("%Y%m%d")

def escape_hl7_chars(text: str) -> str:
    """
    Escapes characters that are reserved in HL7 (|, ^, &, ~) 
    to prevent structural corruption of the message.
    """
    if not text:
        return ""
    # We replace separators with safe alternatives (e.g., hyphens or spaces)
    return str(text).replace("|", "-").replace("^", " ").replace("&", "y").replace("~", "-")

def convert_to_hl7(patient: PatientFullRecord) -> str:
    """
    Generates an HL7v2 message (ORU^R01 profile).
    
    Structure based on UNICEF requirements:
    - MSH: Message Header
    - PID: Patient Identification
    - NK1: Next of Kin (Guardian)
    - PV1: Patient Visit
    - DG1: Diagnosis
    - OBX: Observations (Physical data like Weight/Height)
    - RXA: Pharmacy/Vaccination Administration (Hybrid approach)
    
    Returns:
        str: Raw HL7 message string separated by Carriage Returns (\r).
    """
    
    logger.debug(f"Starting HL7 conversion for patient ID: {patient.patientId}")

    # --- Time Variables ---
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M%S")
    # Unique Control ID for this specific message instance
    msg_control_id = f"MSG{now.strftime('%f')}" 
    
    # --- 1. MSH (Message Header) ---
    # Fixed configuration for Google Cloud Healthcare API
    msh = (
        f"MSH|^~\\&|UNICEF_APP|PUESTO_SALUD|GOOGLE_HEALTHCARE|GCP|"
        f"{timestamp}||ORU^R01|{msg_control_id}|P|2.5.1"
    )

    # --- 2. PID (Patient Identification) ---
    # Formatting Name: LastName^FirstName
    patient_name = f"{escape_hl7_chars(patient.patientInfo.lastName)}^{escape_hl7_chars(patient.patientInfo.firstName)}"
    
    # Formatting Address
    addr = patient.patientInfo.address
    address_str = (
        f"{escape_hl7_chars(addr.street)}^{escape_hl7_chars(addr.city)}^"
        f"{escape_hl7_chars(addr.state)}^{escape_hl7_chars(addr.zipCode)}^"
        f"{escape_hl7_chars(addr.country)}"
    )
    
    dob_str = format_hl7_date(patient.patientInfo.dob)
    
    pid = (
        f"PID|1||{patient.patientId}^^^MR||{patient_name}||"
        f"{dob_str}|{escape_hl7_chars(patient.patientInfo.gender)}|||{address_str}|||||||||||"
    )

    # --- 3. NK1 (Next of Kin / Guardian) ---
    guardian_name = escape_hl7_chars(patient.guardianInfo.name)
    guardian_rel = escape_hl7_chars(patient.guardianInfo.relationship)
    guardian_phone = escape_hl7_chars(patient.guardianInfo.phone)
    
    nk1 = f"NK1|1|{guardian_name}|{guardian_rel}|{address_str}||{guardian_phone}"

    # Initialize segment list
    segments: List[str] = [msh, pid, nk1]

    # --- 4. Medical History (PV1 + DG1 + OBX) ---
    # Iterates through historical visits
    counter_id = 1
    for visit in patient.medicalHistory:
        visit_date = visit.date.strftime("%Y%m%d")
        
        # PV1 - Patient Visit
        pv1 = (
            f"PV1|{counter_id}|O|{escape_hl7_chars(visit.location)}|||||"
            f"{escape_hl7_chars(visit.physician)}|||||||||{visit_date}"
        )
        segments.append(pv1)
        
        # DG1 - Diagnosis
        dx = visit.diagnosis
        dg1 = (
            f"DG1|{counter_id}|I|{dx.icd10Code}^{escape_hl7_chars(dx.description)}^110|"
            f"{escape_hl7_chars(dx.description)}||F"
        )
        segments.append(dg1)
        
        # OBX - Observations (Optional notes)
        if visit.observations:
            obx = f"OBX|{counter_id}|CE|Z00.1^Examen^110|1|{escape_hl7_chars(visit.observations)}||||||F"
            segments.append(obx)
            
        counter_id += 1

    # --- 4.5 Physical Data (Vital Signs) ---
    # We use global OBX segments for Weight and Height if available
    
    # Weight (LOINC 29463-7)
    if patient.patientInfo.weight:
        obx_weight = (
            f"OBX|{len(segments)}|NM|29463-7^Body weight^LN||"
            f"{patient.patientInfo.weight}|kg|||||F"
        )
        segments.append(obx_weight)

    # Height (LOINC 8302-2)
    if patient.patientInfo.height:
        obx_height = (
            f"OBX|{len(segments)}|NM|8302-2^Body height^LN||"
            f"{patient.patientInfo.height}|cm|||||F"
        )
        segments.append(obx_height)

    # --- 5. Vaccinations (RXA Segments) ---
    # Using '0' as ID since RXA is not strictly linked to PV1 in this hybrid profile
    for i, vaccine in enumerate(patient.vaccinationRecord):
        vac_date = format_hl7_date(vaccine.date)
        rxa = (
            f"RXA|0|{i+1}|{vac_date}|{vac_date}|"
            f"{vaccine.vaccineCode}^{escape_hl7_chars(vaccine.vaccineName)}^CVX|"
            f"{vaccine.dose}|{escape_hl7_chars(vaccine.lotNumber)}|||||CP"
        )
        segments.append(rxa)

    # Join with Carriage Return (\r) as required by Google Cloud Healthcare API
    logger.debug(f"HL7 message constructed. Total segments: {len(segments)}")
    return "\r".join(segments)