# Cloud Healthcare API & HL7v2 Configuration

This document details the configuration of the **Google Cloud Healthcare API**, specifically the HL7v2 Store used to ingest, store, and manage clinical messages from external hospital systems (e.g., SIP/EHR) in the **Health Without Borders** project.

## ⚙️ Environment Variables Reference

Throughout this guide, replace the following placeholders `<...>` with your specific project details:

* `<GCP_PROJECT_ID>`: Your Google Cloud Project ID.
* `<REGION>`: The GCP region for your resources (e.g., `us-central1`).
* `<DATASET_NAME>`: The name of your Healthcare Dataset (e.g., `hwb-dataset`).
* `<HL7_STORE_NAME>`: The specific HL7v2 data store (e.g., `hwb-hl7-store`).
* `<PUBSUB_TOPIC>`: The topic name for real-time notifications (e.g., `hl7-ingestion-topic`).
* `<SERVICE_ACCOUNT_EMAIL>`: The identity running the backend service.

---

## 1. Architecture Overview



The interoperability layer consists of a hierarchical structure hosted in Google Cloud, designed for compliance and event-driven processing:

1.  **Dataset:** A top-level container for all healthcare data (FHIR, HL7v2, DICOM) located in a specific region to enforce data residency laws.
2.  **HL7v2 Store:** A specific data store within the dataset designed to ingest, parse, and validate HL7v2 pipe-delimited messages.
3.  **Pub/Sub (Optional but Recommended):** A messaging queue that triggers asynchronous backend workflows whenever a new medical record is ingested.

---

## 2. Resource Provisioning

### 2.1. Enable the API
The Cloud Healthcare API must be enabled in your GCP project.

```bash
gcloud services enable healthcare.googleapis.com

```

### 2.2. Create the Dataset

The dataset acts as the administrative boundary for compliance (HIPAA/GDPR) and data residency.

```bash
gcloud healthcare datasets create <DATASET_NAME> \
    --location=<REGION>

```

### 2.3. Create the Pub/Sub Topic (Event-Driven Integration)

To allow the backend to react to new messages instantly (without polling), create a notification topic.

```bash
gcloud pubsub topics create <PUBSUB_TOPIC>

```

### 2.4. Create the HL7v2 Store

Configure the store to accept and parse messages, and link it to the Pub/Sub topic created above.

* **Parser Version:** V3 (Recommended for strict validation and better compatibility).
* **Reject Duplicates:** Disabled (allows updates/corrections to existing messages).

```bash
gcloud healthcare hl7v2-stores create <HL7_STORE_NAME> \
    --dataset=<DATASET_NAME> \
    --location=<REGION> \
    --parser-version=V3 \
    --pubsub-topic=projects/<GCP_PROJECT_ID>/topics/<PUBSUB_TOPIC>

```

---

## 3. Access Control (IAM)

To allow the Backend API (Cloud Run) or external ingestion scripts to write messages to the store, specific Identity and Access Management (IAM) roles are required.

### 3.1. Required Roles

The identity running the ingestion process must have the following roles:

| Role Name | Role ID | Purpose |
| --- | --- | --- |
| **Healthcare HL7v2 Store Editor** | `roles/healthcare.hl7V2StoreEditor` | Allows creating, ingesting, and deleting HL7v2 messages. |
| **Healthcare Dataset Viewer** | `roles/healthcare.datasetViewer` | Allows listing datasets and stores to verify their existence. |

### 3.2. Assigning Roles via CLI

Bind the necessary permissions to your service account:

```bash
gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
    --member="serviceAccount:<SERVICE_ACCOUNT_EMAIL>" \
    --role="roles/healthcare.hl7V2StoreEditor"

gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
    --member="serviceAccount:<SERVICE_ACCOUNT_EMAIL>" \
    --role="roles/healthcare.datasetViewer"

```

---

## 4. Message Ingestion & Verification

### 4.1. Ingestion Endpoint (REST API)

The standard REST endpoint for creating (ingesting) a message follows this structure:

`POST https://healthcare.googleapis.com/v1/projects/<GCP_PROJECT_ID>/locations/<REGION>/datasets/<DATASET_NAME>/hl7V2Stores/<HL7_STORE_NAME>/messages`

### 4.2. Base64 Encoding Requirement

Unlike standard REST APIs, the Healthcare API requires the raw HL7 message content to be **Base64 encoded** within the JSON payload.

**Example Payload (`message.json`):**

```json
{
  "message": {
    "data": "TVnDiA... (Base64 encoded MSH segment)..."
  }
}

```

### 4.3. Manual Verification (cURL)

To list ingested messages and verify connectivity from a local terminal authorized via `gcloud`:

```bash
curl -X GET \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    "[https://healthcare.googleapis.com/v1/projects/](https://healthcare.googleapis.com/v1/projects/)<GCP_PROJECT_ID>/locations/<REGION>/datasets/<DATASET_NAME>/hl7V2Stores/<HL7_STORE_NAME>/messages?view=FULL"

```

---

## 5. Operations & Best Practices

* **Monitoring Dashboard:** Message counts, ingestion errors, and latency can be monitored visually in the [Cloud Healthcare Console](https://console.cloud.google.com/healthcare/browser).
* **Data Export:** Raw messages can be periodically exported to **Google Cloud Storage (GCS)** buckets via the console for long-term auditing, backup, or bulk analysis.
* **Audit Compliance:** Access logs (who viewed or ingested a message) are automatically generated by Google Cloud Audit Logging. Ensure Data Access audit logs are enabled in your project's IAM settings.
* **Error Handling:** If an HL7 message fails validation (e.g., missing mandatory MSH segments), the API will return a `400 Bad Request`. Ensure your upstream systems are capturing and alerting on these rejections.