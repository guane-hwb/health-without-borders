# Architectural Decision Record (ADR): AI-Powered Clinical NLP

## 1. Context
Medical staff in field operations record clinical notes as free-text (History of Present Illness, Physical Exam, etc.). Requiring them to manually search and map these notes to complex WHO ICD-10/ICD-11 codes on a tablet in offline environments creates friction and data quality issues.

Additionally, patients and guardians report family medical history using colloquial terms ("mi papá es diabético") that need to be mapped to standardized ICD codes for the FHIR RDA bundles required by Resolution 1888/2025.

## 2. Decision
We integrated a Generative AI processor (**Google Vertex AI / Gemini**) to handle two medical coding tasks during the sync process:

1. **Diagnosis extraction** — Analyzes free-text clinical evaluation fields to produce structured ICD-10/11 coded diagnoses.
2. **Family history coding** — Maps condition descriptions (e.g., "Diabetes") to ICD-10/11 codes.

Both tasks run automatically during `POST /api/v1/patients/sync` before the data is persisted and FHIR bundles are generated.

## 3. Architecture & Security

### Model Execution
* **Model:** gemini-2.5-pro (via Vertex AI).
* **Configuration:** `temperature=0.0` (Greedy Decoding) for deterministic coding. `thinking_config` enabled for internal step-by-step reasoning.

### Safety & Compliance
* **Data Privacy:** By using `vertexai=True`, the request is routed through GCP's enterprise infrastructure. Data is NOT used to train Google's consumer models.
* **Safety Settings:** Harm Block Thresholds are set to `BLOCK_NONE` to prevent legitimate anatomical or clinical terms from being falsely flagged.

### Validation Layer
* **Structured Output:** Each task uses a dedicated Gemini response schema (`DIAGNOSIS_RESPONSE_SCHEMA` for diagnoses, `FAMILY_HISTORY_RESPONSE_SCHEMA` for family history) to enforce the exact JSON structure returned.
* **Pydantic Parsing:** Raw JSON is immediately parsed by `DiagnosisItem` Pydantic models. If extraction fails, a safe fallback is injected to prevent sync failures.

## 4. Task Details

### 4.1. Diagnosis Extraction (`extract_diagnoses`)

Input: Four free-text fields from `clinicalEvaluation` (history of current illness, physical exam, systems review, treatment plan).

Output: `List[DiagnosisItem]` — each with `icd10Code`, `icd11Code` (nullable), and `description` in Spanish.

The `diagnosisType` field (impresión diagnóstica / confirmado) is NOT determined by the LLM — it is set by the physician at the encounter level.

Fallback on error: `Z00.0 — Examen médico general (Fallo en extracción IA)`.

### 4.2. Family History Coding (`code_family_history_item`)

Input: Single `conditionDescription` string (e.g., "Glaucoma").

Output: Dictionary with `icd10Code`, `icd11Code` (nullable), and `description` in Spanish.

Only runs for `FamilyHistoryItem` entries that have a description but no ICD codes yet — items already coded (e.g., from a previous sync) are skipped.

Fallback on error: `Z84.8` with the original description preserved.

## 5. Prompt Engineering

Both tasks use Chain of Thought + Few-Shot prompting with strict rules to prevent common LLM coding errors:

* **WHO-only codes** — US-specific ICD-10-CM codes (like Z00.129) are explicitly forbidden.
* **ICD-11 caution** — The LLM must return `null` for `icd11Code` if it is not 100% certain of the exact code. It is better to return null than to hallucinate a code.
* **No overcoding** — Symptoms integral to the primary diagnosis (e.g., "abdominal pain" with gastroenteritis) are not coded separately.
* **Spanish descriptions** — All medical descriptions are returned in professional medical Spanish.