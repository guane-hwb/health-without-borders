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

- **Model:** `gemini-3.8-flash`, a GA model (`LLM_MODEL_NAME`, set in `cloudbuild.yaml`). Google lists it as available from 2026-09-02 until at least 2027-09-02; check the [model versions page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions) before that date. Until 2026-11 the service used `gemini-3-flash-preview`, a preview model without SLA; `gemini-2.5-pro`, which `.env.example` used to name, was retired from Vertex AI on 2026-10-20.
- **Region:** `us-central1` (`LLM_LOCATION`, set in `cloudbuild.yaml`), the FHIR Store's region, so the clinical notes are processed where the record is kept. `global` would let Google choose the region.
- **Timeout and budget:** each call has a timeout (`LLM_TIMEOUT_SECONDS`, default 30 s); a timeout or error falls back to R69. One `/sync` makes at most `LLM_MAX_CALLS_PER_SYNC` calls (20), `LLM_MAX_CONCURRENT_CALLS` at a time (4), and starts none after `LLM_SYNC_BUDGET_SECONDS` (45 s). A new visit the budget does not reach gets the R69 fallback; a background item stays uncoded and a later `/sync` codes it. A visit whose four clinical notes are empty gets the R69 fallback without a call.
- **Configuration:** `temperature=0.0` (Greedy Decoding) for deterministic coding. `thinking_config` enabled for internal step-by-step reasoning.

### Safety & Compliance

- **Data Privacy:** By using `vertexai=True`, the request is routed through GCP's enterprise infrastructure. Data is NOT used to train Google's consumer models.
- **Safety Settings:** Harm Block Thresholds are set to `BLOCK_NONE` to prevent legitimate anatomical or clinical terms from being falsely flagged.

### Validation Layer

- **Structured Output:** Each task uses a dedicated Gemini response schema (`DIAGNOSIS_RESPONSE_SCHEMA`, `FAMILY_HISTORY_RESPONSE_SCHEMA`, `CHRONIC_CONDITION_RESPONSE_SCHEMA`) to enforce the exact JSON structure returned.
- **Pydantic Parsing:** Raw JSON is immediately parsed by `DiagnosisItem` Pydantic models.
- **Post-LLM Terminology Validation:** Every ICD-10/11 code returned by the LLM is validated against the Vulcano IHCE terminology catalogs (`app/data/icd10_codes.json` and `app/data/icd11_codes.json`). If an ICD-10 code is not in the catalog, it is replaced with the honest fallback `R69`. If an ICD-11 code is invalid, it is stripped (set to `null`) since ICD-11 is optional per the IG; the response schemas declare `icd11Code` nullable.
- **Terminal codes:** the prompts and the validation apply one rule. When the text gives no detail, a 3-character category that has subcategories is coded as its "unspecified" subcategory: the model is asked for it, and a category it returns anyway is completed (`E14` → `E149`, `J45` → `J459`). A category without a "9" subcategory stays as it is (`Z00`), and one without subcategories is complete (`E86`, `R69`).
- **Codes sent by the client:** the app never sends diagnoses (it shows what the server coded), but a client that sends an ICD-10 code not in the catalog has that diagnosis dropped, and the visit is coded as if it had none; an invalid background code is cleared and the item is coded. Valid codes are kept as sent.
- **Display in the RDA:** `Condition.code.coding.display` is the catalog's name for the code, so it always matches the code; the clinician's or the LLM's wording goes in `code.text`. The ICD-10 catalog has the WHO names, in English except where the Vulcano fragment gives a Spanish one.
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

Only runs for `FamilyHistoryItem` entries that have a description but no ICD codes yet. An item whose text is already coded on the server reuses that coding instead of calling the LLM again.

The clinician's `conditionDescription` is **never replaced**: the LLM's description is stored in `conditionCodedDisplay`, and `codingSource` records `ai_suggested`, or `ai_fallback` when no model answer stands behind the code (the call failed, the code was not in the catalog, or there was no call). An R69 that the model returns on purpose is `ai_suggested`.

Fallback on error: `R69`, with the clinician's text untouched.

### 4.3. Chronic Condition Coding (`code_chronic_condition`)

Input: Single `chronicDescription` string (e.g., "Hipertensión arterial").

Output: Dictionary with `icd10Code`, `icd11Code` (nullable), and `description` in Spanish.

Only runs for `ChronicConditionItem` entries that have a description but no ICD codes yet (reusing a stored coding for the same text when there is one). As with family history, `chronicDescription` is never replaced; the code's name goes to `chronicCodedDisplay` with `codingSource`.

Fallback on error: `R69`, with the clinician's text untouched.

### 4.4. Provenance of AI output

Every diagnosis the LLM produces is stored with `source` (`ai_suggested`, or `ai_fallback` when the call failed, the code was not in the catalog or there was no call), `model` and `generatedAt`; a diagnosis the client sends for a new visit is marked `clinician`. In the RDA, a `Condition` is `confirmed` only for a clinician diagnosis of type 02/03 (confirmed); AI suggestions, fallbacks, legacy diagnoses with no source and diagnostic impressions (01) are sent as `provisional`. Only visits new to the server are sent to the LLM.

## 5. Terminology Validation

All LLM-generated ICD codes pass through a post-LLM validation step powered by `app/services/terminology.py`. This service loads the ICD-10 and ICD-11 catalogs (`app/data/`) into memory at application startup.

### Catalog Source

The catalogs come from the **WHO ICD API** and are written as compact `{code: display}` JSON files by `scripts/sync_terminology.py`. The WHO API has no Spanish ICD-10, so ICD-10 displays are in English except where the Ministry of Health's Vulcano IHCE fragment ([vulcano.ihcecol.gov.co](https://vulcano.ihcecol.gov.co/), `CodeSystem-ICD10CO.json`) provides a Spanish name; ICD-11 is downloaded in Spanish. The ICD is © WHO; the terms for redistributing these files are pending legal review (see `THIRD_PARTY_NOTICES.md`).

| File | Source | Contents |
|---|---|---|
| `app/data/icd10_codes.json` | WHO ICD-10, 2019 edition (+ Spanish names from Vulcano where available) | 12,597 entries, codes without dots, chapters and blocks included |
| `app/data/icd11_codes.json` | WHO ICD-11 MMS, release 2024-01, Spanish | 34,691 entries, chapters and blocks included |

Chapters, blocks and ICD-11 extension codes are in the files but never accepted as a diagnosis code (see `terminology.py`).

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
| `gemini` (default) | Uses Google Vertex AI with the model in `LLM_MODEL_NAME` |
| `noop` | Returns deterministic fallback codes (R69) without any LLM call |

### Adding a New LLM Backend

To support OpenAI, Anthropic, local Llama, or any other provider:

1. Create a new module in `app/services/llm/` implementing the `MedicalCodingService` Protocol — three methods: `extract_diagnoses()`, `code_family_history_item()`, and `code_chronic_condition()`.
2. Register it in `factory.py` with a new branch in `get_llm_service()`.
3. Set `LLM_BACKEND="your_backend_name"` in the environment.
4. Import and use the `terminology` singleton from `app.services.terminology` for post-LLM code validation.

The prompts and response schemas work across any structured-output-capable LLM, so most of the work is just adapting the API call format.
