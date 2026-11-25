"""
Structured output schemas for medical coding tasks.

The ``*_RESPONSE_SCHEMA`` constants are written in the Vertex AI dialect that
Gemini takes (upper-case types, ``nullable``). ``json_output_schema`` turns them
into the standard JSON Schema that Claude's structured outputs take, so both
backends answer the same fields with the same descriptions.

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
                "nullable": True,
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
            "nullable": True,
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
            "nullable": True,
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


def json_output_schema(schema: dict) -> dict:
    """
    Standard JSON Schema for Claude's structured outputs from a Vertex AI schema.

    Types become lower case, ``nullable`` becomes a ``null`` type next to the
    original one, and every object closes with ``additionalProperties: false``,
    which structured outputs require.
    """
    converted: dict = {}
    for key, value in schema.items():
        if key == "nullable":
            continue
        if key == "type":
            value = value.lower()
        elif key == "properties":
            value = {name: json_output_schema(prop) for name, prop in value.items()}
        elif key == "items":
            value = json_output_schema(value)
        converted[key] = value
    if schema.get("nullable"):
        converted["type"] = [converted["type"], "null"]
    if converted.get("type") == "object":
        converted["additionalProperties"] = False
    return converted


# Claude's structured outputs take an object at the top, so the list of
# diagnoses goes under one key.
DIAGNOSIS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"diagnoses": json_output_schema(DIAGNOSIS_RESPONSE_SCHEMA)},
    "required": ["diagnoses"],
    "additionalProperties": False,
}
FAMILY_HISTORY_OUTPUT_SCHEMA = json_output_schema(FAMILY_HISTORY_RESPONSE_SCHEMA)
CHRONIC_CONDITION_OUTPUT_SCHEMA = json_output_schema(CHRONIC_CONDITION_RESPONSE_SCHEMA)
