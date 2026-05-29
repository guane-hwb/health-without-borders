# Pilot Readiness Checklist

This document consolidates the technical prerequisites, deployment steps, and validation criteria required to launch Health Without Borders at a new pilot site. It is intended for the technical team and the pilot site coordinator.

For detailed instructions on each subsystem, refer to the linked documentation pages.

---

## 1. Infrastructure Prerequisites

Before onboarding a pilot site, verify that all cloud infrastructure is operational.

| Component | Verification | Reference |
|---|---|---|
| Cloud SQL instance running | `gcloud sql instances describe <INSTANCE>` shows `RUNNABLE` | [GCP Deployment](../infrastructure/gcp-deploy.md) |
| FHIR Store accessible | Healthcare API dashboard shows the store with R4, referential integrity enabled | [FHIR Store Configuration](../infrastructure/healthcare-api.md) |
| Cloud Run service healthy | `GET /health-check` returns `200 OK` | [GCP Deployment](../infrastructure/gcp-deploy.md) |
| Secret Manager configured | `hwb-db-pass` and `hwb-secret-key` exist and are bound to Cloud Run | [GCP Deployment § Security](../infrastructure/gcp-deploy.md#4-security-secret-manager-setup) |
| CI/CD pipeline green | Latest Cloud Build on `develop` succeeded (lint + tests + deploy) | [QA & PR Workflow](../development/qa-plan.md) |
| ICD-10/11 terminology loaded | Application startup logs show `"Terminology loaded: N ICD-10 codes, M ICD-11 codes"` | [AI & NLP Integration § Terminology](../architecture/ai-integration.md#5-terminology-validation) |

---

## 2. Organization & User Setup

Each pilot site operates as an isolated organization in the multi-tenant system. The setup sequence is:

**Step 1 — Create the organization:**

A `superadmin` user creates the organization via `POST /api/v1/organizations` with the provider's name, REPS code, and NIT.

**Step 2 — Create an `org_admin` user:**

The `superadmin` provisions an `org_admin` for the new organization via `POST /api/v1/users`. This admin will manage the site's clinical staff.

**Step 3 — The `org_admin` creates clinical users:**

The site's `org_admin` creates `doctor` and `nurse` accounts for the healthcare professionals who will use the mobile app. Each user is scoped to their organization.

**Step 4 — Distribute credentials:**

Provide each user with their email and temporary password. Users authenticate via `POST /api/v1/login` to obtain a JWT token.

For the full RBAC matrix (who can do what), see [Database Schema § RBAC](../infrastructure/database.md#4-role-based-access-control-rbac-matrix).

---

## 3. Mobile App Configuration

The mobile app connects to the backend through these settings:

| Setting | Value | Notes |
|---|---|---|
| API Base URL | `https://<CLOUD_RUN_SERVICE_URL>/api/v1` | Provided after Cloud Run deployment |
| Authentication | Bearer JWT token from `/login` | 30-day expiry |
| NFC chip compatibility | NTAG 213/215/216 (patients), DESFire EV3 4K (guardians) | See [FHIR RDA Architecture](../architecture/fhir-rda.md) |
| Offline mode | App queues sync requests locally | Syncs automatically when connectivity returns |

**Pre-pilot device checklist:**

- [ ] App installed on all tablets/phones
- [ ] NFC reading tested on each device (scan a test wristband)
- [ ] Backend URL configured and login successful
- [ ] At least one test patient synced end-to-end (sync → FHIR Store)

---

## 4. Data Flow Validation

Before going live with real patients, validate the full pipeline with synthetic data.

### 4.1. End-to-end sync test

1. Log in as a `doctor` on the mobile app.
2. Register a synthetic patient with demographics, allergies, family history, and chronic conditions.
3. Add a medical consultation with clinical evaluation text.
4. Sync the patient to the backend.
5. Verify in the backend logs or FHIR Store that:
   - Two FHIR bundles were generated (RDA-Paciente + RDA-Consulta).
   - ICD-10/11 codes were assigned by the LLM and validated against the Vulcano catalog.
   - The bundles passed FHIR Store validation.

### 4.2. NFC round-trip test

1. Sync a patient as above.
2. Write the patient's triage data to an NFC wristband.
3. On a different device, scan the wristband via `GET /api/v1/patients/scan/{device_uid}`.
4. Verify the patient record is returned correctly.

### 4.3. Guardian 2FA test

1. Register a minor patient with a guardian's NFC card.
2. Attempt to scan the minor's wristband without the guardian card → verify access is denied.
3. Scan with the guardian card present → verify access is granted.

### 4.4. Nurse restriction test

1. Log in as a `nurse`.
2. Attempt to sync a patient with a new medical history entry → verify `403 Forbidden`.
3. Sync a patient with only new vaccination records → verify `201 Created`.

The sample synthetic patients in the `datalake/` directory can be used for these tests.

---

## 5. FHIR Compliance Verification

The system generates FHIR R4 RDA bundles aligned with [IG RDA v0.8.1](https://vulcano.ihcecol.gov.co/guia/). Before pilot launch, verify:

| Check | How to verify |
|---|---|
| RDA-Paciente has 4 sections | Inspect bundle: chronic conditions, allergies, medications, family history. Empty sections have `emptyReason`. |
| RDA-Consulta has 9 sections | Inspect bundle: payer, demographics, incapacity, diagnoses, allergies, risk factors, prescriptions, orders (emptyReason), documents (emptyReason). |
| Bundle passes GCP FHIR Store | Sync a patient and confirm `fhir_status: "success"` in the response. |
| ICD-10 codes are valid | Check application logs for `"ICD-10 code ... not found in Vulcano catalog"` warnings. Any such warning means the LLM generated an invalid code that was replaced with R69. Occasional R69 fallbacks are acceptable; frequent ones indicate a prompt or catalog issue. |
| ICD-10 codes use no-dot format | Inspect a few generated codes in the bundle — they should be in the format `A099`, not `A09.9`. |
| References resolve | No `#id` references remain in the stored bundle (the system rewrites them to `urn:uuid`). |

For the full resource mapping, see [FHIR RDA Architecture § Resource Mapping](../architecture/fhir-rda.md#3-fhir-resource-mapping).

---

## 6. Security Checklist

| Item | Status | Reference |
|---|---|---|
| JWT authentication enforced on all clinical endpoints | Verify with an unauthenticated request → `401` | [Security Protocols](../infrastructure/security.md) |
| RBAC roles enforced | Test with wrong role → `403` | [Database Schema § RBAC](../infrastructure/database.md#4-role-based-access-control-rbac-matrix) |
| Passwords hashed with bcrypt | Verified in codebase (`get_password_hash`) | [Security Protocols](../infrastructure/security.md) |
| Secrets in Secret Manager (not env vars) | Check Cloud Run configuration | [GCP Deployment § Security](../infrastructure/gcp-deploy.md#4-security-secret-manager-setup) |
| `.env` / credentials excluded from repo | `.gitignore` and `.dockerignore` verified | [Security Protocols](../infrastructure/security.md) |
| Multi-tenancy isolation | Users only see data within their organization | [Database Schema § RBAC](../infrastructure/database.md#44-design-rationale) |

---

## 7. Monitoring During Pilot

Once the pilot is live, monitor these operational metrics:

| Metric | Where to check | Alert threshold |
|---|---|---|
| API error rate | Cloud Run → Metrics → Request count by status | > 5% 5xx in 15 min |
| Sync latency (p95) | Cloud Run → Metrics → Request latencies | > 10s |
| FHIR bundle failures | Application logs: `"Patient sync FHIR warning"` | Any occurrence |
| Database connections | Cloud SQL → Metrics → Active connections | > 80% of max |
| LLM coding failures | Application logs: `"Gemini"` + `"error"` | Any occurrence |
| ICD validation fallbacks | Application logs: `"not found in Vulcano catalog"` | > 10% of syncs |

For log access, see [GCP Deployment § Monitoring](../infrastructure/gcp-deploy.md#9-monitoring-and-maintenance).

---

## 8. Rollback Procedure

If a critical issue is discovered during the pilot:

1. **Immediate:** Revert Cloud Run to the previous revision: `gcloud run services update-traffic <SERVICE> --to-revisions=<PREVIOUS_REVISION>=100 --region=<REGION>`.
2. **If data is affected:** The FHIR Store has resource versioning enabled — previous versions of resources are preserved. Cloud SQL can be restored via point-in-time recovery (production tier).
3. **If the issue is in the mobile app:** Distribute a hotfix APK or revert to the previous build.

---

## 9. Go / No-Go Criteria

The pilot can proceed when **all** of the following are true:

- [ ] All items in Section 1 (Infrastructure) are verified.
- [ ] Organization and users created (Section 2).
- [ ] All 4 data flow tests pass (Section 4).
- [ ] FHIR compliance checks pass (Section 5).
- [ ] Security checklist complete (Section 6).
- [ ] Monitoring dashboards accessible (Section 7).
- [ ] At least 3 healthcare staff trained on the mobile app.
- [ ] NFC wristbands and guardian cards available in sufficient quantity.
- [ ] Rollback procedure documented and tested (Section 8).
