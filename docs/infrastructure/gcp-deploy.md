# Infrastructure and Deployment Guide (Google Cloud Platform)

This document details the technical procedures for deploying the **Health Without Borders API** to Google Cloud Platform (GCP). It covers resource provisioning, container building, secure secret management, service deployment, and CI/CD automation.

## Architecture Overview

The system utilizes a fully serverless, highly available, and containerized architecture:

* **Compute:** [Google Cloud Run](https://cloud.google.com/run) (Stateless containers).
* **Database:** [Google Cloud SQL](https://cloud.google.com/sql) (Managed PostgreSQL 15).
* **Interoperability:** [Cloud Healthcare API](https://cloud.google.com/healthcare-api) (HL7v2 Message Store & FHIR translation).
* **Security:** [Secret Manager](https://cloud.google.com/secret-manager) (Secure vault for passwords and keys).
* **Registry:** [Artifact Registry](https://cloud.google.com/artifact-registry) (Docker image storage).
* **CI/CD:** [Cloud Build](https://cloud.google.com/build) (Serverless build pipeline).

---

## ⚙️ Environment Variables Reference

Throughout this guide, replace the following placeholders `<...>` with your specific project details:

* `<GCP_PROJECT_ID>`: Your Google Cloud Project ID (e.g., `migrants-unicef`).
* `<PROJECT_NUMBER>`: Your numeric GCP Project ID (used for service accounts).
* `<REGION>`: The GCP region for your resources (e.g., `us-central1`).
* `<DB_INSTANCE_NAME>`: The name of your Cloud SQL server (e.g., `hwb-db-cluster`).
* `<DB_NAME>`: The logical database inside the instance (e.g., `hwb_db`).
* `<DB_PASSWORD>`: The password for the `postgres` user.
* `<SECRET_KEY>`: Your secure JWT signing key.
* `<REPO_NAME>`: Your Artifact Registry name (e.g., `hwb-repo`).
* `<CLOUD_RUN_SERVICE>`: The name of your backend service (e.g., `hwb-backend-dev`).

---

## 1. Prerequisites

Before proceeding, ensure the following requirements are met:

1. **GCP Project:** An active Google Cloud project.
2. **Google Cloud SDK:** `gcloud` CLI installed and authenticated (`gcloud auth login`).
3. **Python Environment:** Python 3.11+ and the `uv` package manager installed locally.
4. **Permissions:** The active user must have `Editor` or `Owner` roles.

Set your default project in the CLI:

```bash
gcloud config set project <GCP_PROJECT_ID>

```

---

## 2. Service Enablement

Enable the required APIs to allow communication between GCP services.

```bash
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    healthcare.googleapis.com \
    secretmanager.googleapis.com

```

---

## 3. Database Provisioning (Cloud SQL)

Cloud Run requires an external persistence layer for relational data. Since we enforce a strict "No Public IP" policy, we must first establish a VPC Peering connection between our network and Google's internal services.

**1. Establish VPC Peering (Run Once per Project):**

```bash
# Reserve private IP range for Google Services
gcloud compute addresses create google-managed-services-default \
    --global \
    --purpose=VPC_PEERING \
    --prefix-length=16 \
    --description="peering range for Google" \
    --network=default

# Connect the peering
gcloud services vpc-peerings connect \
    --service=servicenetworking.googleapis.com \
    --ranges=google-managed-services-default \
    --network=default \
    --project=<GCP_PROJECT_ID>

```

**2. Create the Cloud SQL Instance (Server):**

Choose the appropriate command based on your target environment.

*Option A: Development / Sandbox (Cost-Optimized)*

```bash
gcloud sql instances create <DB_INSTANCE_NAME> \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region=<REGION> \
    --storage-size=10GB \
    --storage-type=SSD \
    --no-storage-auto-increase \
    --network=default \
    --no-assign-ip \
    --root-password="<DB_PASSWORD>"

```

*Option B: Production (High Availability & Performance)*

```bash
gcloud sql instances create <DB_INSTANCE_NAME> \
    --database-version=POSTGRES_15 \
    --tier=db-custom-2-8192 \
    --region=<REGION> \
    --availability-type=REGIONAL \
    --storage-size=100GB \
    --storage-type=SSD \
    --storage-auto-increase \
    --enable-point-in-time-recovery \
    --network=default \
    --no-assign-ip \
    --root-password="<DB_PASSWORD>"

```

*(Note: Production uses a dedicated 2-core 8GB RAM machine, is replicated across two zones for High Availability, and enables Point-in-Time recovery).*

**3. Create the Logical Database:**

```bash
gcloud sql databases create <DB_NAME> --instance=<DB_INSTANCE_NAME>

```
---

## 4. Security & Secret Manager Setup 🔒

To avoid hardcoded credentials in environment variables, store sensitive data in Secret Manager.

**1. Create the secrets:**

```bash
printf "<DB_PASSWORD>" | gcloud secrets create hwb-db-pass --data-file=-
printf "<SECRET_KEY>" | gcloud secrets create hwb-secret-key --data-file=-

```

**2. Grant read access to Cloud Run's default service account:**
*(Replace `<PROJECT_NUMBER>` with your actual numeric project ID)*

```bash
export COMPUTE_SA="<PROJECT_NUMBER>-compute@developer.gserviceaccount.com"

gcloud secrets add-iam-policy-binding hwb-db-pass \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding hwb-secret-key \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/secretmanager.secretAccessor"

```

---

## 5. Healthcare & Artifact Registry Setup

**1. Healthcare Interoperability:**

```bash
gcloud healthcare datasets create <DATASET_NAME> --location=<REGION>

gcloud healthcare hl7v2-stores create <HL7_STORE_NAME> \
    --dataset=<DATASET_NAME> \
    --location=<REGION>

```

**2. Artifact Registry (Docker storage):**

```bash
gcloud artifacts repositories create <REPO_NAME> \
    --repository-format=docker \
    --location=<REGION> \
    --description="HWB Docker Repository"

```

---

## 6. Initial Manual Deployment (Bootstrap)

For the very first deployment, build and deploy manually to establish the Cloud Run service, its VPC connectors, and Secret Manager links.

**1. Build the image:**

```bash
gcloud builds submit \
    --tag <REGION>-docker.pkg.dev/<GCP_PROJECT_ID>/<REPO_NAME>/backend:bootstrap \
    .

```

**2. Deploy to Cloud Run:**
*(Notice the use of `--update-secrets` instead of plain text environment variables for sensitive data).*

```bash
gcloud run deploy <CLOUD_RUN_SERVICE> \
    --image <REGION>-docker.pkg.dev/<GCP_PROJECT_ID>/<REPO_NAME>/backend:bootstrap \
    --region <REGION> \
    --allow-unauthenticated \
    --add-cloudsql-instances="<GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=<GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>" \
    --set-env-vars="DB_USER=postgres" \
    --set-env-vars="DB_NAME=<DB_NAME>" \
    --set-env-vars="GCP_PROJECT_ID=<GCP_PROJECT_ID>" \
    --set-env-vars="GCP_LOCATION=<REGION>" \
    --set-env-vars="GCP_DATASET_ID=<DATASET_NAME>" \
    --set-env-vars="GCP_HL7_STORE_ID=<HL7_STORE_NAME>" \
    --set-env-vars="HL7_SENDING_APP=HWB_BACKEND" \
    --set-env-vars="HL7_SENDING_FACILITY=HWB_FIELD_CLINIC" \
    --set-env-vars="HL7_RECEIVING_APP=GCP_HEALTHCARE" \
    --set-env-vars="HL7_RECEIVING_FACILITY=GCP_DATALAKE" \
    --set-env-vars="HL7_PROCESSING_ID=D" \
    --set-env-vars="HL7_VERSION_ID=2.5.1" \
    --update-secrets="DB_PASS=hwb-db-pass:latest,SECRET_KEY=hwb-secret-key:latest"

```

---

## 7. CI/CD Automation (Cloud Build) 🚀

Once the bootstrap deployment is successful, subsequent deployments should be fully automated via GitHub and Cloud Build.

1. Go to the **Cloud Build** console in GCP.
2. Connect your GitHub repository in the **Repositories** tab.
3. Create a new **Trigger**:
* **Event:** Push to a branch (e.g., `^main$` or `^develop$`).
* **Source:** Select your connected repository.
* **Configuration:** Cloud Build configuration file (yaml or json).
* **Location:** `/cloudbuild.yaml`.
* **Service Account:** Ensure you select the Compute Engine default service account (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`).


4. Ensure the `cloudbuild.yaml` file is present in the root of your repository.
5. Any subsequent `git push` will trigger automated tests, build the image, and deploy safely to Cloud Run inheriting the secrets configuration.

---

## 8. Data Initialization (Schema & Seeding)

Because our database lacks a public IP for security reasons, initializing the database from a local laptop requires a temporary security bypass. We will temporarily assign a public IP, run our scripts securely through the encrypted Auth Proxy, and then lock the instance down again.

**1. Temporarily Open the Vault (Assign Public IP):**
```bash
gcloud sql instances patch <DB_INSTANCE_NAME> --assign-ip

```

*(Wait 1-2 minutes for the instance to update).*

**2. Start the Cloud SQL Auth Proxy:**
In a new terminal window, open the secure encrypted tunnel:

```bash
./cloud-sql-proxy <GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME> --port 5433

```

**3. Execute Initialization Scripts:**
In a **separate terminal window**, force the `DATABASE_URL` to route through the local tunnel:

```bash
export DATABASE_URL="postgresql://postgres:<DB_PASSWORD>@127.0.0.1:5433/<DB_NAME>"

uv run python scripts/create_tables.py
uv run python scripts/create_generic_user.py
uv run python scripts/load_catalogs.py

```

**4. Close the Vault (Revoke Public IP):**
Once the scripts complete successfully, immediately restore the maximum security posture:

```bash
gcloud sql instances patch <DB_INSTANCE_NAME> --no-assign-ip

```
---

## 9. Monitoring and Maintenance

* **Application Logs:** [Cloud Run Console](https://console.cloud.google.com/run) -> "Logs" tab.
* **Database Performance:** [Cloud SQL Console](https://console.cloud.google.com/sql).
* **HL7v2 Metrics:** [Cloud Healthcare API Dashboard](https://console.cloud.google.com/healthcare).
* **CI/CD Pipeline:** [Cloud Build History](https://console.cloud.google.com/cloud-build/builds).