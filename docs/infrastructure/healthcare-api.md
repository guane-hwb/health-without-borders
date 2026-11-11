# FHIR Store Configuration — Google Cloud Healthcare API

This document details the configuration of the **Google Cloud Healthcare API FHIR Store** used to ingest and manage RDA bundles (Resolution 1888/2025) in the Health Without Borders project.

> **Note:** The deployment uses GCP as the FHIR Store backend. The code talks to it through a vendor-neutral protocol (`app/services/fhir/base.py`, selected by `FHIR_BACKEND` in `factory.py`), so another backend (Azure FHIR, AWS HealthLake, HAPI FHIR) can be added without touching the RDA builder.

---

## 1. Architecture Overview

The interoperability layer uses a hierarchical structure in Google Cloud:

1. **Dataset** (`hwb-dataset`): Top-level container in `us-central1` for data residency compliance.
2. **FHIR Store** (`hwb-fhir-store`): R4-compliant store that receives document bundles via POST.
3. **Authentication**: GCP Application Default Credentials with `roles/healthcare.fhirStoreEditor` scope.

---

## 2. Resource Provisioning

### 2.1. Enable the API

```bash
gcloud services enable healthcare.googleapis.com
```

### 2.2. Create the Dataset

```bash
gcloud healthcare datasets create hwb-dataset \
    --location=us-central1
```

### 2.3. Create the FHIR Store

```bash
gcloud healthcare fhir-stores create hwb-fhir-store \
    --dataset=hwb-dataset \
    --location=us-central1 \
    --version=R4 \
    --enable-referential-integrity \
    --enable-resource-versioning
```

---

## 3. Store Configuration

| Setting | Value | Rationale |
|---|---|---|
| FHIR version | R4 | Required by IHCE IG |
| Referential integrity | Enabled | Bundles contain internal references that must resolve |
| Resource versioning | Enabled | RDA-Paciente is re-sent on updates; versioning preserves history |
| Strict search | Disabled | Lenient mode ignores unknown search parameters during development |
| Complex reference analysis | Enabled | Extensions use references in complex data types |
| Profile validation | Disabled | Enable after importing the RDA Implementation Guide profiles |
| BigQuery streaming | Not configured | Can be enabled later for analytics dashboards |
| Pub/Sub notifications | Not configured | Can be enabled for event-driven workflows |

---

## 4. IAM Configuration

The backend calls the Healthcare API as its Cloud Run service account. It is a dedicated account, `hwb-backend@`, holding only what the service needs: `roles/healthcare.fhirResourceEditor` on the FHIR store (below), `roles/cloudsql.client`, `roles/aiplatform.user` and `roles/secretmanager.secretAccessor` on its secrets, and the service deployed with `--service-account`.

The pilot deployment (`hwb-backend-dev`) runs this way since 2026-11-10: `cloudbuild.yaml` deploys it with `--service-account hwb-backend@...`. The commands that create the account and give it the other roles are in [GCP Deployment](gcp-deploy.md) § 4.


```bash
gcloud healthcare fhir-stores add-iam-policy-binding hwb-fhir-store \
    --dataset=hwb-dataset \
    --location=us-central1 \
    --member="serviceAccount:hwb-backend@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/healthcare.fhirResourceEditor"
```

### 4.1. Audit Logs of FHIR Data Access

Admin Activity logs (always on) record changes to the dataset and the store. Reading and writing FHIR resources is a *Data Access* operation, which Google leaves off by default. Without it, a read of the store from the console or by another account leaves no trace: `patient_access_log` only sees what goes through the backend.

On the pilot project, `DATA_READ` and `DATA_WRITE` are on for `healthcare.googleapis.com` since 2026-11-11. Every read, search and write of the store logs:
- the caller (the user or service account);
- its IP;
- the method (for example `fhir.rest.FhirService.SearchResources`);
- the store.

A search logs the resource type, not its parameters.

The entries go to their own log bucket, `hwb-fhir-audit`:
- **Retention:** 400 days, like `_Required`. The bucket is not locked, so the retention can still be changed.
- **No duplicates:** an exclusion keeps them out of `_Default`, whose 30 days stay as they are for the rest of the logs.
- **Who can read them:** they are private logs, readable only with `roles/logging.privateLogViewer`, which the project owners hold.
- **Where to look:** Logs Explorer → *Refine scope* → the `hwb-fhir-audit` bucket.

**Cost:** each entry is 1–3 KB. The pilot sends a few hundred bundles a month, about 1 MB, well inside Cloud Logging's monthly free ingestion; storage past 30 days costs fractions of a cent at that size.

To set it up in another project:

```bash
FILTER='LOG_ID("cloudaudit.googleapis.com/data_access") AND protoPayload.serviceName="healthcare.googleapis.com"'

gcloud logging buckets create hwb-fhir-audit --location=global --retention-days=400
gcloud logging sinks create hwb-fhir-audit \
    logging.googleapis.com/projects/PROJECT_ID/locations/global/buckets/hwb-fhir-audit \
    --log-filter="$FILTER"
gcloud logging sinks update _Default \
    --add-exclusion="name=hwb-fhir-audit,filter=$FILTER"

# set-iam-policy replaces the whole policy: start from a fresh copy (its etag
# makes the call fail if someone changed the policy meanwhile) and only add
# the auditConfigs block.
gcloud projects get-iam-policy PROJECT_ID --format=json > policy.json
#   "auditConfigs": [{"service": "healthcare.googleapis.com",
#     "auditLogConfigs": [{"logType": "DATA_READ"}, {"logType": "DATA_WRITE"}]}]
gcloud projects set-iam-policy PROJECT_ID policy.json
```

To turn it off, remove the `auditConfigs` block the same way. The bucket keeps what it already holds until its retention ends.

---

## 5. Bundle Ingestion

RDA bundles are sent as `POST` requests to the FHIR Store's Bundle endpoint:

```
POST https://healthcare.googleapis.com/v1/projects/{project}/locations/{location}/datasets/{dataset}/fhirStores/{store}/fhir/Bundle
Content-Type: application/fhir+json; charset=utf-8
Authorization: Bearer {token}
```

Each bundle is a FHIR `document` type with a `Composition` as the first entry. The store validates FHIRPath constraints before accepting the bundle.

---

## 6. Environment Variables

| Variable | Description | Example |
|---|---|---|
| `GCP_PROJECT_ID` | Google Cloud project ID | `migrants-unicef` |
| `GCP_LOCATION` | Dataset region | `us-central1` |
| `GCP_DATASET_ID` | Healthcare dataset name | `hwb-dataset` |
| `GCP_FHIR_STORE_ID` | FHIR store name | `hwb-fhir-store` |

---

## 7. Future: Profile Validation

To enable validation against the IHCE Implementation Guide profiles:

1. Package the StructureDefinition, ValueSet, and CodeSystem resources from the IG as a FHIR `transaction` bundle.
2. Import the bundle into the FHIR Store.
3. Enable profile validation, required field validation, reference type validation, and FHIRPath validation in the store settings.

This will cause the store to reject bundles that don't conform to the RDA profiles.