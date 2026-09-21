# Architectural Decision Record (ADR): AI-Powered Clinical NLP

## 1. Context

Medical staff in field operations record clinical notes as free-text (History of Present Illness, Physical Exam, etc.). Requiring them to manually search and map these notes to complex WHO ICD-10/ICD-11 codes on a tablet in offline environments creates friction and data quality issues.

Additionally, patients and guardians report family medical history and chronic conditions using colloquial terms ("mi papá es diabético", "tengo hipertensión") that need to be mapped to standardized ICD codes for the FHIR RDA bundles required by Resolution 1888/2025.

## 2. Decision

We integrated a Generative AI processor (**Google Vertex AI / Gemini**) to handle three medical coding tasks during the sync process:

1. **Diagnosis extraction** — Analyzes free-text clinical evaluation fields to produce structured ICD-10/11 coded diagnoses.
2. **Family history coding** — Maps condition descriptions (e.g., "Diabetes") to ICD-10/11 codes.
3. **Chronic condition coding** — Maps patient-declared chronic conditions (e.g., "Hipertensión") to ICD-10/11 codes.

All three tasks run automatically during `POST /api/v1/patients/sync` before the data is persisted and FHIR bundles are generated. Every LLM-generated code is then validated against the official IHCE terminology catalogs before being accepted.

## 3. Architecture & Security

### Model Execution

- **Model:** gemini-2.5-pro (via Vertex AI).
- **Configuration:** `temperature=0.0` (Greedy Decoding) for deterministic coding. `thinking_config` enabled for internal step-by-step reasoning.

### Safety & Compliance

- **Data Privacy:** By using `vertexai=True`, the request is routed through GCP's enterprise infrastructure. Data is NOT used to train Google's consumer models.
- **Safety Settings:** Harm Block Thresholds are set to `BLOCK_NONE` to prevent legitimate anatomical or clinical terms from being falsely flagged.

### Validation Layer

- **Structured Output:** Each task uses a dedicated Gemini response schema (`DIAGNOSIS_RESPONSE_SCHEMA`, `FAMILY_HISTORY_RESPONSE_SCHEMA`, `CHRONIC_CONDITION_RESPONSE_SCHEMA`) to enforce the exact JSON structure returned.
- **Pydantic Parsing:** Raw JSON is immediately parsed by `DiagnosisItem` Pydantic models.
- **Post-LLM Terminology Validation:** Every ICD-10/11 code returned by the LLM is validated against the Vulcano IHCE terminology catalogs (`app/data/icd10_codes.json` and `app/data/icd11_codes.json`). If an ICD-10 code is not in the catalog, it is replaced with the honest fallback `R69`. If an ICD-11 code is invalid, it is stripped (set to `null`) since ICD-11 is optional per the IG.
- **Honest Fallback:** On LLM error or invalid code, the system uses `R69` ("Causas de morbilidad desconocidas y no especificadas") instead of fabricating a false diagnosis. The bundle is always generated and sent — the fallback never blocks transmission.

## 4. Task Details

### 4.1. Diagnosis Extraction (`extract_diagnoses`)

Input: Four free-text fields from `clinicalEvaluation` (history of current illness, physical exam, systems review, treatment plan).

Output: `List[DiagnosisItem]` — each with `icd10Code`, `icd11Code` (nullable), and `description` in Spanish.

The `diagnosisType` field (impresión diagnóstica / confirmado) is NOT determined by the LLM — it is set by the physician at the encounter level.

Fallback on error: `R69 — Causas de morbilidad desconocidas y no especificadas`.

### 4.2. Family History Coding (`code_family_history_item`)

Input: Single `conditionDescription` string (e.g., "Glaucoma").

Output: Dictionary with `icd10Code`, `icd11Code` (nullable), and `description` in Spanish.

Only runs for `FamilyHistoryItem` entries that have a description but no ICD codes yet — items already coded (e.g., from a previous sync) are skipped.

Fallback on error: `R69` with the original description preserved.

### 4.3. Chronic Condition Coding (`code_chronic_condition`)

Input: Single `chronicDescription` string (e.g., "Hipertensión arterial").

Output: Dictionary with `icd10Code`, `icd11Code` (nullable), and `description` in Spanish.

Only runs for `ChronicConditionItem` entries that have a description but no ICD codes yet.

Fallback on error: `R69` with the original description preserved.

## 5. Terminology Validation

All LLM-generated ICD codes pass through a post-LLM validation step powered by `app/services/terminology.py`. This service loads the ICD-10 and ICD-11 catalogs from the Vulcano IHCE Implementation Guide at application startup.

### Catalog Source

The catalogs are FHIR CodeSystem resources published by the Ministry of Health at [vulcano.ihcecol.gov.co](https://vulcano.ihcecol.gov.co/). They are downloaded and transformed into compact `{code: display}` JSON files using `scripts/sync_terminology.py`.

| File | Source | Contents |
|---|---|---|
| `app/data/icd10_codes.json` | `CodeSystem-ICD10CO.json` | ~12,000 ICD-10 codes (WHO, no dots) |
| `app/data/icd11_codes.json` | `CodeSystem-ICD11CO.json` | ~1,000 ICD-11 codes |

### ICD-10 Code Format

The Colombian IHCE catalog uses ICD-10 codes **without dots** (e.g., `A099` not `A09.9`, `J069` not `J06.9`). The prompts and schemas explicitly instruct the LLM to return codes in this format. Three-character codes that have no subcategory stay as-is (e.g., `E86`, `I10`).

### Validation Rules

| Scenario | Action |
|---|---|
| ICD-10 code exists in catalog | Accept as-is |
| ICD-10 code NOT in catalog | Replace entire diagnosis with R69 |
| ICD-11 code exists in catalog | Accept as-is |
| ICD-11 code NOT in catalog | Strip to `null` (ICD-11 is optional) |
| LLM returns error / invalid JSON | Use R69 fallback |
| Catalog files not loaded | Graceful degradation — accept all codes with a log warning |

### Updating the Catalogs

```bash
# Download from Vulcano (requires network access):
python scripts/sync_terminology.py

# Or from locally saved files:
python scripts/sync_terminology.py --local CodeSystem-ICD10CO.json CodeSystem-ICD11CO.json
```

The script generates the compact JSON files in `app/data/`. These files are committed to the repository so that local development and CI work without network access to Vulcano.

## 6. Prompt Engineering

All three tasks use Chain of Thought + Few-Shot prompting with strict rules to prevent common LLM coding errors:

- **WHO-only codes** — US-specific ICD-10-CM codes (like Z00.129) are explicitly forbidden.
- **No-dot format** — ICD-10 codes are returned without dots (`A099` not `A09.9`) to match the Colombian IHCE catalog format.
- **ICD-11 caution** — The LLM must return `null` for `icd11Code` if it is not 100% certain of the exact code. It is better to return null than to hallucinate a code.
- **R69 for uncertainty** — When the LLM cannot determine a specific diagnosis, it is instructed to use R69 instead of guessing.
- **No overcoding** — Symptoms integral to the primary diagnosis (e.g., "abdominal pain" with gastroenteritis) are not coded separately.
- **Spanish descriptions** — All medical descriptions are returned in professional medical Spanish.

## 7. Vendor-Neutral Abstraction Layer

The LLM service is accessed through a Protocol-based abstraction layer that decouples the medical coding logic from any specific AI provider. This supports the Digital Public Good requirement of vendor neutrality.

### Architecture

```
app/services/llm/
├── base.py       # MedicalCodingService Protocol — the contract
├── gemini.py     # Google Vertex AI / Gemini implementation
├── noop.py       # No-op implementation (for local dev without LLM access)
├── factory.py    # Reads LLM_BACKEND config, returns right instance
├── prompts.py    # Provider-agnostic prompt builders
├── schemas.py    # Provider-agnostic structured output schemas
└── __init__.py   # Re-exports medical_llm_processor singleton + Protocol

app/services/
├── terminology.py  # ICD-10/11 catalog validation (loaded at startup)
```

The endpoint imports `medical_llm_processor` from `app.services.llm`. It never imports a concrete LLM client. The factory reads the `LLM_BACKEND` environment variable at startup to decide which implementation to use.

Note that `prompts.py` and `schemas.py` are provider-agnostic — prompts are plain strings and the response schemas are plain dicts. Any new LLM backend can reuse them. The terminology validation in `gemini.py` uses the shared `terminology` singleton from `app.services.terminology`.

### Configuration

Set `LLM_BACKEND` in your `.env` file:

| Value | Effect |
|---|---|
| `gemini` (default) | Uses Google Vertex AI with Gemini 2.5 Pro |
| `noop` | Returns deterministic fallback codes (R69) without any LLM call |

### Adding a New LLM Backend

To support OpenAI, Anthropic, local Llama, or any other provider:

1. Create a new module in `app/services/llm/` implementing the `MedicalCodingService` Protocol — three methods: `extract_diagnoses()`, `code_family_history_item()`, and `code_chronic_condition()`.
2. Register it in `factory.py` with a new branch in `get_llm_service()`.
3. Set `LLM_BACKEND="your_backend_name"` in the environment.
4. Import and use the `terminology` singleton from `app.services.terminology` for post-LLM code validation.

The prompts and response schemas work across any structured-output-capable LLM, so most of the work is just adapting the API call format.
