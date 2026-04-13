# FHIR RDA Architecture — Resolution 1888/2025 Compliance

This document describes the FHIR R4 interoperability layer that generates **Resumen Digital de Atención en Salud (RDA)** bundles in compliance with Colombia's Resolution 1888 of 2025, which mandates the Interoperabilidad de la Historia Clínica Electrónica (IHCE).

Implementation Guide: [vulcano.ihcecol.gov.co/guia](https://vulcano.ihcecol.gov.co/guia/)

---

## 1. Bundle Types

The system generates two types of FHIR R4 Bundles, both of type `document`:

**RDA-Paciente** — Patient self-reported health background. Contains allergies, family history, and chronic conditions declared by the patient or their guardian. Generated once and refreshed when background data changes.

**RDA-Consulta** — Ambulatory encounter clinical data. Contains the encounter metadata, diagnoses (ICD-10/11), allergies identified during the visit, risk factors, and discharge disposition. One bundle per medical visit.

---

## 2. Delta Sync Logic

The system tracks which bundles have already been sent to the FHIR Store using two database columns on the `patients` table:

- `synced_visit_count` (Integer): Number of `medicalHistory` entries already sent.
- `rda_paciente_sent` (Boolean): Whether the RDA-Paciente bundle has been sent.

On each `/sync` call:

1. **RDA-Paciente** is regenerated only if it hasn't been sent yet, or if there are new visits (which may imply updated background data).
2. **RDA-Consulta** bundles are generated only for visits at index `>= synced_visit_count`.
3. Tracking columns are updated **only after successful GCP upload** — if GCP fails, the next sync retries automatically.

---

## 3. FHIR Resource Mapping

### RDA-Paciente Bundle

| FHIR Resource | Source | Profile |
|---|---|---|
| Patient | `patientInfo` + `identification` | PatientRDA |
| AllergyIntolerance | `allergies[]` | AllergyIntoleranceDeclaredRDA |
| FamilyMemberHistory | `backgroundHistory.familyHistory[]` | FamilyMemberHistoryRDA |
| Condition | `backgroundHistory.chronicConditions` | ConditionDeclaredRDA |
| Composition | Wraps all sections | CompositionPatientRDA |

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
| Composition | Wraps all sections | CompositionAmbulatoryRDA |

---

## 4. Terminology Systems

| System | URI | Usage |
|---|---|---|
| ICD-10 (WHO) | `http://hl7.org/fhir/sid/icd-10` | Diagnosis coding |
| ICD-11 (WHO) | `http://id.who.int/icd/release/11/mms` | Diagnosis coding (optional) |
| SISPRO | `https://www.sispro.gov.co/terminologias/...` | Modality, service group, environment, diagnosis type, allergy category, risk factors |
| DIVIPOLA | DANE codes | Municipality identification |
| LOINC | `http://loinc.org` | Composition section codes |

---

## 5. Key FHIRPath Constraints

The Google Cloud Healthcare API FHIR Store validates these constraints before accepting bundles:

1. **Bundle identifier**: Document bundles must have `identifier.system` and `identifier.value`.
2. **Composition sections**: Each section must have `text`, `entry` (non-empty), or nested `section`. Empty sections use `emptyReason` + a minimal `text` div.
3. **Referential integrity**: All `reference` fields must resolve to an entry within the bundle.

---

## 6. AI-Powered Medical Coding

Two LLM tasks run during `/sync` before FHIR bundle generation:

**Diagnosis extraction** (`extract_diagnoses`): Analyzes free-text `clinicalEvaluation` fields to produce ICD-10/11 codes. The `diagnosisType` is set by the physician at the encounter level, not by the LLM.

**Family history coding** (`code_family_history_item`): Maps `conditionDescription` free text (e.g., "Diabetes") to ICD-10/11 codes. Only runs for items that don't already have codes.

Both tasks use Gemini via Vertex AI with `temperature=0.0` for deterministic output and structured JSON response schemas.

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
