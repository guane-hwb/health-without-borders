RESPONSE_SCHEMA = {
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
                "description": "The corresponding WHO ICD-11 MMS code. Null if not found."
            },
            "description": {
                "type": "STRING", 
                "description": "Official medical description of the diagnosis translated into Spanish."
            },
            "is_ai_generated": {
                "type": "BOOLEAN", 
                "description": "Must always be true."
            }
        },
        "required": ["icd10Code", "description", "is_ai_generated"]
    }
}