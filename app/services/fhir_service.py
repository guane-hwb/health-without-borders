import uuid
import logging
from datetime import datetime
from typing import Dict, Any
from app.schemas.patient import PatientFullRecord

logger = logging.getLogger(__name__)

def generate_uuid() -> str:
    return f"urn:uuid:{uuid.uuid4()}"

def convert_to_fhir_rda(patient: PatientFullRecord) -> Dict[str, Any]:
    """
    Generates a FHIR Bundle (Resumen Digital de Atención - RDA) in JSON format
    compliant with Colombian Resolution 1888 of 2025.
    """
    logger.debug(f"Starting FHIR RDA conversion for patient ID: {patient.patientId}")

    # The RDA must be packaged as a FHIR Bundle
    bundle = {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "entry": []
    }

    # 1. Add Patient Resource
    patient_id = generate_uuid()
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
            "gender": "unknown" # Adjust based on your mapping (male, female, other, unknown)
        }
    }
    
    if patient.patientInfo.dob:
        patient_resource["resource"]["birthDate"] = patient.patientInfo.dob.strftime("%Y-%m-%d")

    # Add Guardian Contact if available and patient is a minor
    if patient.guardianInfo:
        patient_resource["resource"]["contact"] = [{
            "relationship": [{"text": patient.guardianInfo.relationship or "Guardian"}],
            "name": {"text": patient.guardianInfo.name or ""},
            "telecom": [{"system": "phone", "value": patient.guardianInfo.phone}] if patient.guardianInfo.phone else []
        }]

    bundle["entry"].append(patient_resource)

    # 2. Add Medical History (Encounters & Conditions/Diagnoses)
    for visit in patient.medicalHistory:
        encounter_id = generate_uuid()
        encounter_date = visit.date.isoformat() + "Z" if visit.date else datetime.utcnow().isoformat() + "Z"
        
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
                "location": [{"location": {"display": visit.location or "Unknown Location"}}]
            }
        }
        bundle["entry"].append(encounter_resource)

        # Diagnoses mapped to Condition Resources
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

        # Notes as Observations
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

    # 3. Vitals
    if patient.patientInfo.weight or patient.patientInfo.height:
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

    # 4. Vaccinations (Immunization)
    for vaccine in patient.vaccinationRecord:
        vac_date = vaccine.date.isoformat() + "Z" if vaccine.date else datetime.utcnow().isoformat() + "Z"
        immunization_resource = {
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "Immunization",
                "status": "completed",
                "vaccineCode": {
                    "coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": vaccine.vaccineCode, "display": vaccine.vaccineName}]
                },
                "patient": {"reference": patient_id},
                "occurrenceDateTime": vac_date,
                "doseQuantity": {"value": vaccine.dose or 1}
            }
        }
        bundle["entry"].append(immunization_resource)

    # 5. Allergies (AllergyIntolerance)
    for allergy in patient.allergies:
        allergy_resource = {
            "fullUrl": generate_uuid(),
            "resource": {
                "resourceType": "AllergyIntolerance",
                "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
                "patient": {"reference": patient_id},
                "code": {"text": allergy.allergen},
                "reaction": [{"description": str(allergy.reaction.value if hasattr(allergy.reaction, 'value') else allergy.reaction)}]
            }
        }
        bundle["entry"].append(allergy_resource)

    logger.debug(f"FHIR Bundle constructed. Total entries: {len(bundle['entry'])}")
    return bundle