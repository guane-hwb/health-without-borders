# FHIR RDA Architecture — Resolution 1888/2025 Compliance

This document describes the FHIR R4 interoperability layer that generates **Resumen Digital de Atención en Salud (RDA)** bundles in compliance with Colombia's Resolution 1888 of 2025, which mandates the Interoperabilidad de la Historia Clínica Electrónica (IHCE).

Implementation Guide: [vulcano.ihcecol.gov.co/guia](https://vulcano.ihcecol.gov.co/guia/)

---

## 1. Bundle Types

The system generates two types of FHIR R4 Bundles, both of type `document`:

**RDA-Paciente** — Patient self-reported health background. Contains allergies, family history, chronic conditions, and medication statements declared by the patient or their guardian. Generated once and refreshed when background data changes.

**RDA-Consulta** — Ambulatory encounter clinical data. Contains the encounter metadata, diagnoses (ICD-10/11), allergies identified during the visit, risk factors, prescribed medications, occupation, incapacity, and discharge disposition. One bundle per medical visit.

---

## 2. Delta Sync Logic

The system tracks which bundles have already been sent to the FHIR Store using three database columns on the `patients` table:

- `synced_encounter_ids` (JSON list): `encounterIdentifier` UUIDs of visits already transmitted.
- `rda_paciente_sent` (Boolean): Whether the RDA-Paciente bundle has been sent at least once.
- `background_data_hash` (SHA-256): Hash of background data fields (demographics, guardians, allergies, chronic conditions, medications).

On each `/sync` call:

1. **RDA-Paciente** is regenerated only if it has never been sent, or if the `background_data_hash` changed since the last sync (indicating updated demographics, allergies, or chronic conditions).
2. **RDA-Consulta** bundles are generated only for visits whose `encounterIdentifier` UUID is **not** in the `synced_encounter_ids` list. This encounter-based delta is robust against record merges from multiple devices — unlike index-based slicing, the order of entries in `medicalHistory` does not matter.
3. Tracking columns are updated **only after successful GCP upload** — if GCP fails, the next sync retries automatically.

---

## 3. FHIR Resource Mapping

### RDA-Paciente Bundle

| FHIR Resource | Source | Profile |
|---|---|---|
| Patient | `patientInfo` + `identification` | PatientRDA |
| AllergyIntolerance | `allergies[]` | AllergyIntoleranceStatementRDA |
| FamilyMemberHistory | `backgroundHistory.familyHistory[]` | FamilyMemberHistoryRDA |
| Condition | `backgroundHistory.chronicConditions[]` | ConditionStatementRDA |
| MedicationStatement | `backgroundHistory.medications[]` | MedicationStatementRDA |
| Composition | Wraps all 4 sections | CompositionPatientStatementRDA |

### RDA-Consulta Bundle

| FHIR Resource | Source | Profile |
|---|---|---|
| Patient | Same as RDA-Paciente | PatientRDA |
| Encounter | `medicalHistory[n]` metadata | EncounterAmbulatoryRDA |
| Condition | `medicalHistory[n].diagnosis[]` | ConditionRDA |
| AllergyIntolerance | `allergies[]` (encounter-linked) | AllergyIntoleranceRDA |
| Organization (IPS) | `medicalHistory[n].provider` | OrganizationIPS |
| Organization (EAPB) | `medicalHistory[n].payer` | OrganizationEAPB |
| Practitioner | `medicalHistory[n].practitioner` | PractitionerRDA |
| RiskAssessment | `medicalHistory[n].riskFactors[]` | RiskFactorRDA |
| MedicationRequest | `medicalHistory[n].prescriptions[]` | MedicationRequestRDA |
| Observation (Occupation) | `medicalHistory[n].occupation` | PatientOccupationAtEncounterRDA |
| Coverage (Incapacity) | `medicalHistory[n].incapacity` | AttendanceAllowanceRDA |
| ServiceRequest | emptyReason (not captured by HWB) | ServiceRequestRDA |
| DocumentReference | emptyReason (not captured by HWB) | DocumentReferenceEPIRDA |
| Composition | Wraps all 9 sections | CompositionAmbulatoryRDA |

---

## 4. Terminology Systems

| System | URI | Usage |
|---|---|---|
| ICD-10 (WHO) | `http://hl7.org/fhir/sid/icd-10` | Diagnosis coding (no-dot format: `A099` not `A09.9`) |
| ICD-11 (WHO) | `http://id.who.int/icd/release/11/mms` | Diagnosis coding (optional) |
| SISPRO | `https://www.sispro.gov.co/terminologias/...` | Modality, service group, environment, diagnosis type, allergy category, risk factors |
| DIVIPOLA | DANE codes | Municipality identification |
| LOINC | `http://loinc.org` | Composition section codes |
| SNOMED CT | `http://snomed.info/sct` | Occupation (184104002), incapacity component codes |

### Terminology Catalog Files

ICD-10 and ICD-11 codes are validated against the catalogs published by the Ministry of Health in the Vulcano IHCE Implementation Guide. These are stored as compact JSON lookup files:

| File | Source | Codes |
|---|---|---|
| `app/data/icd10_codes.json` | `CodeSystem-ICD10CO` from Vulcano | ~12,000 |
| `app/data/icd11_codes.json` | `CodeSystem-ICD11CO` from Vulcano | ~1,000 |

To update: `python scripts/sync_terminology.py` (see [AI & NLP Integration](../architecture/ai-integration.md#5-terminology-validation)).

---

## 5. Key FHIRPath Constraints

The Google Cloud Healthcare API FHIR Store validates these constraints before accepting bundles:

1. **Bundle identifier**: Document bundles must have `identifier.system` and `identifier.value`.
2. **Composition sections**: Each section must have `text`, `entry` (non-empty), or nested `section`. Empty sections use `emptyReason` + a minimal `text` div.
3. **Referential integrity**: All `reference` fields must resolve to an entry within the bundle.

---

## 6. AI-Powered Medical Coding

Three LLM tasks run during `/sync` before FHIR bundle generation:

**Diagnosis extraction** (`extract_diagnoses`): Analyzes free-text `clinicalEvaluation` fields to produce ICD-10/11 codes. The `diagnosisType` is set by the physician at the encounter level, not by the LLM.

**Family history coding** (`code_family_history_item`): Maps `conditionDescription` free text (e.g., "Diabetes") to ICD-10/11 codes. Only runs for items that don't already have codes.

**Chronic condition coding** (`code_chronic_condition`): Maps `chronicDescription` free text (e.g., "Hipertensión") to ICD-10/11 codes. Only runs for items that don't already have codes.

All three tasks use Gemini via Vertex AI with `temperature=0.0` for deterministic output and structured JSON response schemas. Every code returned by the LLM is validated against the Vulcano ICD-10/11 catalogs before being accepted into the bundle. Invalid codes are replaced with `R69` (honest fallback). See [AI & NLP Integration](../architecture/ai-integration.md) for full details.

---

## 7. FHIR Store Configuration (GCP)

Current setup in `hwb-fhir-store`:

| Setting | Value | Rationale |
|---|---|---|
| Version | R4 | Required by IHCE Implementation Guide |
| Referential integrity | Enabled | Prevents inconsistent references |
| Resource versioning | Enabled | Maintains audit trail per Res. 1888 Art. 6 |
| Strict search | Disabled | Lenient mode for development |
| Complex reference analysis | Enabled | Needed for extensions with references |
| Profile validation | Disabled (pending IG import) | Will enable after importing RDA profiles |

---

## 8. Vendor-Neutral Abstraction Layer

As a Digital Public Good, the project cannot depend exclusively on one cloud vendor. The FHIR Store is accessed through a Protocol-based abstraction layer that allows swapping the backend without touching the endpoint code.

### Architecture

```
app/services/fhir/
├── base.py       # FHIRStoreBackend Protocol — the contract
├── gcp.py        # Google Cloud Healthcare API implementation
├── noop.py       # No-op implementation (for local dev without a FHIR store)
├── factory.py    # Reads FHIR_BACKEND config, returns right instance
└── __init__.py   # Re-exports fhir_backend singleton + Protocol
```

The endpoint (`patients.py`) imports `fhir_backend` from `app.services.fhir`. It never imports a concrete backend directly. The factory reads the `FHIR_BACKEND` environment variable to decide which implementation to instantiate at startup.

### Configuration

Set `FHIR_BACKEND` in your `.env` file:

| Value | Effect |
|---|---|
| `gcp` (default) | Uses Google Cloud Healthcare API FHIR Store |
| `noop` | Discards bundles silently (useful for local dev and tests) |

### Adding a New Backend

To support another FHIR Store (Azure Health Data Services, AWS HealthLake, HAPI FHIR, a local Docker instance, etc.):

1. Create a new module in `app/services/fhir/` implementing the `FHIRStoreBackend` Protocol (just one method: `send_bundle(bundle: dict) -> FHIRBundleSendResult`).
2. Register it in `factory.py` with a new branch in `get_fhir_backend()`.
3. Set `FHIR_BACKEND="your_backend_name"` in the environment.

No other file in the codebase needs to change.
