# Architectural Decision Record (ADR): AI-Powered Clinical NLP Extraction

## 1. Context
Medical staff in field operations often record clinical notes as free-text (History of Present Illness, Physical Exam, etc.). Requiring them to manually search and map these notes to complex WHO ICD-10/ICD-11 codes on a tablet in offline environments creates friction and data quality issues.

## 2. Decision
We integrated a Generative AI processor (**Google Vertex AI**) to act as a clinical NLP (Natural Language Processing) engine. 

Instead of manual catalog lookups, the backend intercepts the synchronization payload (`POST /api/v1/patients/sync`), extracts the free-text clinical evaluation, and uses a highly structured Prompt Engineering technique (Chain of Thought + Few-Shot Prompting) to infer the exact WHO terminal codes.

## 3. Architecture & Security Mechanisms

### Model Execution
* **Model:** gemini-2.5-pro (via Vertex AI).
* **Configuration:** `temperature=0.0` (Greedy Decoding) to eliminate hallucinations and enforce deterministic coding. `thinking_config` enabled for internal step-by-step reasoning.

### Safety & Compliance
* **Data Privacy:** By using `vertexai=True`, the request is routed through GCP's enterprise infrastructure. Data is NOT used to train Google's consumer models, ensuring HIPAA/GDPR compliance.
* **Safety Settings:** Harm Block Thresholds are explicitly set to `BLOCK_NONE` to prevent legitimate anatomical or clinical terms from being falsely flagged as inappropriate content.

### Validation Layer
* **Structured Output:** The model is constrained to return a strictly typed JSON array matching our `RESPONSE_SCHEMA`.
* **Pydantic Parsing:** The raw JSON is immediately parsed by the `DiagnosisItem` Pydantic model. If the extraction fails, a safe fallback diagnosis (`Z00.0 - General medical examination`) is injected with the `is_ai_generated` flag set to `True` to prevent sync failures.