import uuid
import logging
from datetime import datetime, date
from typing import Dict, Any
from app.schemas.patient import PatientFullRecord

logger = logging.getLogger(__name__)

def generate_uuid() -> str:
    """Generates a standard URN UUID for FHIR resources."""
    return f"urn:uuid:{uuid.uuid4()}"

def _format_fhir_datetime(dt_obj) -> str:
    """
    Helper function to safely format Python dates/datetimes into FHIR compliant strings.
    """
    if not dt_obj:
        return datetime.utcnow().isoformat() + "Z"
    
    if isinstance(dt_obj, datetime):
        iso_str = dt_obj.isoformat()
        if not iso_str.endswith('Z') and '+' not in iso_str:
            return iso_str + "Z"
        return iso_str
        
    if isinstance(dt_obj, date):
        return dt_obj.isoformat()
        
    return str(dt_obj)

def convert_to_fhir_rda(patient: PatientFullRecord) -> Dict[str, Any]:
    """
    Generates a FHIR Bundle (Resumen Digital de Atención - RDA) in JSON format
    compliant with Colombian Resolution 1888 of 2025.
    """
    logger.debug(f"Starting FHIR RDA conversion for patient ID: {patient.patientId}")

    # RDA Container as a collection of clinical data
    bundle = {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection", 
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "entry": []
    }

    # --- 1. PATIENT RESOURCE ---
    patient_id = generate_uuid()
    gender_map = {"M": "male", "F": "female"}
    fhir_gender = gender_map.get(patient.patientInfo.gender.upper(), "unknown")

    patient_resource = {
        "fullUrl": patient_id,
        "resource": {
            "resourceType": "Patient",
            "id": patient.patientId,
            "identifier": [
                {
                    "system": "http://terminology.sispro.gov.co/CodeSystem/identification-type",
                    "value": patient.patientId
                }
            ],
            "name": [
                {
                    "use": "official",
                    "family": patient.patientInfo.lastName or "UNKNOWN",
                    "given": [patient.patientInfo.firstName or "UNKNOWN"]
                }
            ],
            "gender": fhir_gender
        }
    }
    
    if patient.patientInfo.dob:
        patient_resource["resource"]["birthDate"] = patient.patientInfo.dob.strftime("%Y-%m-%d")

    if patient.patientInfo.address:
        addr = patient.patientInfo.address
        patient_resource["resource"]["address"] = [{
            "line": [addr.street] if addr.street else [],
            "city": addr.city,
            "state": addr.state,
            "postalCode": addr.zipCode,
            "country": addr.country
        }]

    if patient.guardianInfo:
        patient_resource["resource"]["contact"] = [{
            "relationship": [{"text": patient.guardianInfo.relationship}],
            "name": {"text": patient.guardianInfo.name},
            "telecom": [{"system": "phone", "value": patient.guardianInfo.phone}] if patient.guardianInfo.phone else []
        }]

    bundle["entry"].append(patient_resource)

    # --- 2. BACKGROUND HISTORY ---
    if patient.backgroundHistory:
        bg_mapping = [
            ("Chronic Conditions", patient.backgroundHistory.chronicConditions),
            ("Personal History", patient.backgroundHistory.personalHistory),
            ("Family History", patient.backgroundHistory.familyHistory)
        ]
        for bg_category, bg_text in bg_mapping:
            if bg_text and bg_text.lower() != "ninguna":
                bundle["entry"].append({
                    "fullUrl": generate_uuid(),
                    "resource": {
                        "resourceType": "Observation",
                        "status": "final",
                        "code": {"text": bg_category},
                        "subject": {"reference": patient_id},
                        "valueString": bg_text
                    }
                })

    # --- 3. MEDICAL HISTORY (Encounters, Conditions & Clinical Notes) ---
    for visit in patient.medicalHistory:
        encounter_id = generate_uuid()
        encounter_date = _format_fhir_datetime(visit.date)
        
        encounter_resource = {
            "fullUrl": encounter_id,
            "resource": {
                "resourceType": "Encounter",
                "status": "finished",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "AMB",
                    "display": "ambulatory"
                },
                "subject": {"reference": patient_id},
                "period": {"start": encounter_date},
                "location": [{"location": {"display": visit.location or "Unknown"}}],
                "participant": [{"individual": {"display": visit.physician}}] if visit.physician else []
            }
        }
        bundle["entry"].append(encounter_resource)

        for diagnosis in visit.diagnosis:
            condition_resource = {
                "fullUrl": generate_uuid(),
                "resource": {
                    "resourceType": "Condition",
                    "clinicalStatus": {
                        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
                    },
                    "code": {
                        "coding": [{
                            "system": "http://hl7.org/fhir/sid/icd-10",
                            "code": diagnosis.icd10Code,
                            "display": diagnosis.description
                        }]
                    },
                    "subject": {"reference": patient_id},
                    "encounter": {"reference": encounter_id}
                }
            }
            bundle["entry"].append(condition_resource)

        notes_mapping = [
            ("History of present illness", visit.clinicalEvaluation.historyOfCurrentIllness),
            ("Physical examination", visit.clinicalEvaluation.generalPhysicalExamination),
            ("Systems examination", visit.clinicalEvaluation.systemsExamination),
            ("Treatment plan", visit.clinicalEvaluation.treatmentPlanObservations)
        ]
        
        for category, text in notes_mapping:
            if text:
                obs_resource = {
                    "fullUrl": generate_uuid(),
                    "resource": {
                        "resourceType": "Observation",
                        "status": "final",
                        "code": {"text": category},
                        "subject": {"reference": patient_id},
                        "encounter": {"reference": encounter_id},
                        "valueString": text
                    }
                }
                bundle["entry"].append(obs_resource)

    # --- 4. VITALS & BIOMETRICS ---
    if patient.patientInfo.weight:
        bundle["entry"].append({
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
                "code": {"coding": [{"system": "http://loinc.org", "code": "29463-7", "display": "Body weight"}]},
                "subject": {"reference": patient_id},
                "valueQuantity": {"value": patient.patientInfo.weight, "unit": "kg", "system": "http://unitsofmeasure.org", "code": "kg"}
            }
        })
    
    if patient.patientInfo.height:
        bundle["entry"].append({
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
                "code": {"coding": [{"system": "http://loinc.org", "code": "8302-2", "display": "Body height"}]},
                "subject": {"reference": patient_id},
                "valueQuantity": {"value": patient.patientInfo.height, "unit": "cm", "system": "http://unitsofmeasure.org", "code": "cm"}
            }
        })
        
    if patient.patientInfo.bloodType:
        bundle["entry"].append({
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "code": {"coding": [{"system": "http://loinc.org", "code": "883-9", "display": "ABO group"}]},
                "subject": {"reference": patient_id},
                "valueString": patient.patientInfo.bloodType
            }
        })

    # --- 5. VACCINATIONS ---
    for vaccine in patient.vaccinationRecord:
        vac_date = _format_fhir_datetime(vaccine.date)
        
        immunization_resource = {
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "Immunization",
                "status": "completed" if vaccine.status.lower() == "completado" else "entered-in-error",
                "vaccineCode": {
                    "coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": vaccine.vaccineCode, "display": vaccine.vaccineName}]
                },
                "patient": {"reference": patient_id},
                "occurrenceDateTime": vac_date,
                "doseQuantity": {"value": vaccine.dose or 1},
                "performer": [{"actor": {"display": vaccine.administratedBy}}] if vaccine.administratedBy else [],
                "location": {"display": vaccine.administratedAt} if vaccine.administratedAt else None
            }
        }
        
        if not immunization_resource["resource"]["location"]:
            del immunization_resource["resource"]["location"]
            
        bundle["entry"].append(immunization_resource)

    # --- 6. ALLERGIES ---
    for allergy in patient.allergies:
        reaction_text = str(allergy.reaction.value if hasattr(allergy.reaction, 'value') else allergy.reaction)
        
        allergy_resource = {
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "AllergyIntolerance",
                "clinicalStatus": {
                    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]
                },
                "patient": {"reference": patient_id},
                "code": {"text": allergy.allergen},
                "note": [{"text": allergy.notes}] if allergy.notes else [],
                "reaction": [
                    {
                        "manifestation": [
                            {
                                "text": reaction_text
                            }
                        ]
                    }
                ]
            }
        }
        bundle["entry"].append(allergy_resource)

    logger.debug(f"FHIR Bundle constructed. Total entries: {len(bundle['entry'])}")
    return bundle