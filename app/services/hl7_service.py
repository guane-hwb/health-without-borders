from datetime import datetime
from app.schemas.patient import PatientFullRecord


def format_date(dt_obj) -> str:
    """
    Convierte fecha a formato HL7 (YYYYMMDD). 
    Si viene vacía (None), devuelve string vacío para no romper el mensaje.
    """
    if not dt_obj:
        return ""
    return dt_obj.strftime("%Y%m%d")

def escape_hl7(text: str) -> str:
    """
    Limpia caracteres que podrían romper el formato HL7 (| ^ & ~)
    """
    if not text:
        return ""
    # Reemplazamos los separadores de HL7 por espacios o guiones para evitar roturas
    return str(text).replace("|", "-").replace("^", " ").replace("&", "y").replace("~", "-")

def convert_to_hl7(patient: PatientFullRecord) -> str:
    """
    Genera el mensaje HL7v2 manualmente para cumplir exactamente con el formato
    híbrido (ORU^R01 + RXA).
    """
    
    # Variables de tiempo
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M%S")
    msg_control_id = f"MSG{now.strftime('%f')}"
    
    # 1. MSH (Header)
    # Basado en PDF Página 54 [cite: 1861]
    msh = (
        f"MSH|^~\\&|UNICEF_APP|PUESTO_SALUD|GOOGLE_HEALTHCARE|GCP|"
        f"{timestamp}||ORU^R01|{msg_control_id}|P|2.5.1"
    )

    # 2. PID (Identificación) [cite: 1862]
    # Nombre formateado: Apellido^Nombre
    patient_name = f"{escape_hl7(patient.patientInfo.lastName)}^{escape_hl7(patient.patientInfo.firstName)}"
    
    # Dirección formateada
    addr = patient.patientInfo.address
    address_str = f"{escape_hl7(addr.street)}^{escape_hl7(addr.city)}^{escape_hl7(addr.state)}^{escape_hl7(addr.zipCode)}^{escape_hl7(addr.country)}"
    
    # Fecha nacimiento (YYYYMMDD)
    dob_str = format_date(patient.patientInfo.dob)
    
    pid = (
        f"PID|1||{patient.patientId}^^^MR||{patient_name}||"
        f"{dob_str}|{escape_hl7(patient.patientInfo.gender)}|||{address_str}|||||||||||"
    )

    # 3. NK1 (Familiar/Acudiente) [cite: 1865]
    guardian_name = escape_hl7(patient.guardianInfo.name)
    guardian_rel = escape_hl7(patient.guardianInfo.relationship)
    guardian_phone = escape_hl7(patient.guardianInfo.phone)
    
    nk1 = f"NK1|1|{guardian_name}|{guardian_rel}|{address_str}||{guardian_phone}"

    # Construimos el cuerpo con una lista de segmentos
    segments = [msh, pid, nk1]

    # 4. Historial Médico (Visitas y Diagnósticos)
    # PDF usa PV1 (Visita) + DG1 (Diagnóstico) + OBX (Observación) [cite: 1869-1878]
    counter_id = 1
    for visit in patient.medicalHistory:
        visit_date = visit.date.strftime("%Y%m%d")
        
        # PV1 - Visita
        pv1 = f"PV1|{counter_id}|O|{escape_hl7(visit.location)}|||||{escape_hl7(visit.physician)}|||||||||{visit_date}"
        segments.append(pv1)
        
        # DG1 - Diagnóstico
        # El PDF usa: DG1|Id|I|Codigo^Descripcion^110|...
        dx = visit.diagnosis
        dg1 = f"DG1|{counter_id}|I|{dx.icd10Code}^{escape_hl7(dx.description)}^110|{escape_hl7(dx.description)}||F"
        segments.append(dg1)
        
        # OBX - Observaciones (Opcional según PDF)
        if visit.observations:
            obx = f"OBX|{counter_id}|CE|Z00.1^Examen^110|1|{escape_hl7(visit.observations)}||||||F"
            segments.append(obx)
            
        counter_id += 1

    # 4.5 DATOS FÍSICOS (Signos Vitales)
    # Agregamos segmentos OBX globales si existen los datos
    # Peso (LOINC 29463-7: Body weight)
    if patient.patientInfo.weight:
        # Formato: OBX|idx|NM|Codigo^Desc^LN||Valor|Unidad|||||F
        obx_weight = f"OBX|{len(segments)}|NM|29463-7^Body weight^LN||{patient.patientInfo.weight}|kg|||||F"
        segments.append(obx_weight)

    # Talla (LOINC 8302-2: Body height)
    if patient.patientInfo.height:
        obx_height = f"OBX|{len(segments)}|NM|8302-2^Body height^LN||{patient.patientInfo.height}|cm|||||F"
        segments.append(obx_height)

    # 5. Vacunas (Segmento RXA)
    # Formato PDF: RXA|0|1|Fecha|Fecha|Codigo^Nombre^CVX|Dosis|...
    for i, vaccine in enumerate(patient.vaccinationRecord):
        vac_date = format_date(vaccine.date)
        rxa = (
            f"RXA|0|{i+1}|{vac_date}|{vac_date}|"
            f"{vaccine.vaccineCode}^{escape_hl7(vaccine.vaccineName)}^CVX|"
            f"{vaccine.dose}|{escape_hl7(vaccine.lotNumber)}|||||CP"
        )
        segments.append(rxa)

    # Unir todo con saltos de línea (\r es el estándar clásico HL7, \n es más moderno/legible)
    return "\r".join(segments)