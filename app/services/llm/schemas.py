"""
Gemini structured output schemas for medical coding tasks.
These schemas enforce the JSON structure that Gemini must return.
"""

# Schema for extracting diagnoses from clinical evaluation notes.
# Returns an array because one clinical encounter may produce multiple diagnoses.
DIAGNOSIS_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "description": "A list of medical diagnoses extracted from the clinical notes.",
    "items": {
        "type": "OBJECT",
        "properties": {
            "icd10Code": {
                "type": "STRING",
                "description": "The exact WHO ICD-10 code."
            },
            "icd11Code": {
                "type": "STRING",
                "description": "The corresponding WHO ICD-11 MMS code. Null if not certain."
            },
            "description": {
                "type": "STRING",
                "description": "Official medical description of the diagnosis in Spanish."
            }
        },
        "required": ["icd10Code", "description"]
    }
}

# Schema for coding a single family history condition.
# Returns a single object because each family history item is coded individually.
FAMILY_HISTORY_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "description": "ICD coding for a single family medical history condition.",
    "properties": {
        "icd10Code": {
            "type": "STRING",
            "description": "The exact WHO ICD-10 code for the condition."
        },
        "icd11Code": {
            "type": "STRING",
            "description": "The corresponding WHO ICD-11 MMS code. Null if not certain."
        },
        "description": {
            "type": "STRING",
            "description": "Official medical description of the condition in Spanish."
        }
    },
    "required": ["icd10Code", "description"]
}