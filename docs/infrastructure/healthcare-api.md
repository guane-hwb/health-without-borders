# FHIR Store Configuration — Google Cloud Healthcare API

This document details the configuration of the **Google Cloud Healthcare API FHIR Store** used to ingest and manage RDA bundles (Resolution 1888/2025) in the Health Without Borders project.

> **Note:** The current implementation uses GCP as the FHIR Store backend. A future abstraction layer will allow deploying with alternative backends (Azure FHIR, AWS HealthLake, HAPI FHIR) for open-source flexibility.

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

The backend authenticates using a service account with minimal permissions:

```bash
gcloud healthcare fhir-stores add-iam-policy-binding hwb-fhir-store \
    --dataset=hwb-dataset \
    --location=us-central1 \
    --member="serviceAccount:hwb-backend@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/healthcare.fhirResourceEditor"
```

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