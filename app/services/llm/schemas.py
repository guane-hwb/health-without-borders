"""
Gemini structured output schemas for medical coding tasks.
These schemas enforce the JSON structure that Gemini must return.

IMPORTANT: ICD-10 codes in the Colombian IHCE (Vulcano) catalog use the
format WITHOUT dots (e.g., 'A099' not 'A09.9'). The field descriptions
reinforce this to reduce LLM hallucination of dotted formats.
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
                "description": (
                    "The exact WHO ICD-10 code WITHOUT dots. "
                    "Examples: 'A099' not 'A09.9', 'J069' not 'J06.9', 'E86' (3-char codes stay as-is). "
                    "If uncertain, use 'R69'."
                ),
            },
            "icd11Code": {
                "type": "STRING",
                "description": (
                    "The corresponding WHO ICD-11 MMS code, or null if not 100% certain. "
                    "ICD-11 codes keep their original format with dots (e.g., '1A40.Z')."
                ),
            },
            "description": {
                "type": "STRING",
                "description": "Official medical description of the diagnosis in Spanish.",
            },
        },
        "required": ["icd10Code", "description"],
    },
}

# Schema for coding a single family history condition.
# Returns a single object because each family history item is coded individually.
FAMILY_HISTORY_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "description": "ICD coding for a single family medical history condition.",
    "properties": {
        "icd10Code": {
            "type": "STRING",
            "description": (
                "The exact WHO ICD-10 code WITHOUT dots. "
                "Examples: 'C509' not 'C50.9', 'H409' not 'H40.9'. "
                "If uncertain, use 'R69'."
            ),
        },
        "icd11Code": {
            "type": "STRING",
            "description": (
                "The corresponding WHO ICD-11 MMS code, or null if not 100% certain."
            ),
        },
        "description": {
            "type": "STRING",
            "description": "Official medical description of the condition in Spanish.",
        },
    },
    "required": ["icd10Code", "description"],
}

CHRONIC_CONDITION_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "description": "ICD coding for a single chronic condition reported by the patient.",
    "properties": {
        "icd10Code": {
            "type": "STRING",
            "description": (
                "The exact WHO ICD-10 code WITHOUT dots. "
                "Examples: 'E11' (3-char), 'J459' not 'J45.9'. "
                "If uncertain, use 'R69'."
            ),
        },
        "icd11Code": {
            "type": "STRING",
            "description": (
                "The corresponding WHO ICD-11 MMS code, or null if not 100% certain."
            ),
        },
        "description": {
            "type": "STRING",
            "description": "Official medical description of the chronic condition in Spanish.",
        },
    },
    "required": ["icd10Code", "description"],
}
