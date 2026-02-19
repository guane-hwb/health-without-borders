# Infrastructure and Deployment Guide (Google Cloud Platform)

This document details the technical procedures for deploying the **Health Without Borders API** to Google Cloud Platform (GCP). It covers resource provisioning, container building, service deployment, and database initialization.

## Architecture Overview

The system utilizes a fully serverless, highly available, and containerized architecture:

* **Compute:** [Google Cloud Run](https://cloud.google.com/run) (Stateless containers).
* **Database:** [Google Cloud SQL](https://cloud.google.com/sql) (Managed PostgreSQL 15).
* **Interoperability:** [Cloud Healthcare API](https://cloud.google.com/healthcare-api) (HL7v2 Message Store & FHIR translation).
* **Registry:** [Artifact Registry](https://cloud.google.com/artifact-registry) (Docker image storage).
* **CI/CD:** [Cloud Build](https://cloud.google.com/build) (Serverless build pipeline).

---

## ⚙️ Environment Variables Reference

Throughout this guide, replace the following placeholders `<...>` with your specific project details:

* `<GCP_PROJECT_ID>`: Your Google Cloud Project ID (e.g., `health-borders-prod`).
* `<REGION>`: The GCP region for your resources (e.g., `us-central1`).
* `<DB_INSTANCE_NAME>`: The name of your Cloud SQL server (e.g., `hwb-db-cluster`).
* `<DB_NAME>`: The logical database inside the instance (e.g., `hwb_db`).
* `<DB_PASSWORD>`: The password for the `postgres` user.
* `<REPO_NAME>`: Your Artifact Registry name (e.g., `hwb-repo`).
* `<CLOUD_RUN_SERVICE>`: The name of your backend service (e.g., `hwb-backend-api`).

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
    healthcare.googleapis.com

```

---

## 3. Database Provisioning (Cloud SQL)

Cloud Run requires an external persistence layer for relational data.

**1. Create the Cloud SQL Instance (Server):**

```bash
gcloud sql instances create <DB_INSTANCE_NAME> \
    --database-version=POSTGRES_15 \
    --cpu=1 --memory=4GB \
    --region=<REGION> \
    --root-password="<DB_PASSWORD>"

```

**2. Create the Logical Database:**

```bash
gcloud sql databases create <DB_NAME> --instance=<DB_INSTANCE_NAME>

```

*(Optional) To delete an existing database:*

```bash
gcloud sql databases delete <OLD_DB_NAME> --instance=<DB_INSTANCE_NAME>

```

---

## 4. Healthcare Interoperability Setup

Create the infrastructure to store and validate HL7v2 medical messages.

**1. Create a Healthcare Dataset:**

```bash
gcloud healthcare datasets create <DATASET_NAME> --location=<REGION>

```

**2. Create the HL7v2 Store within the Dataset:**

```bash
gcloud healthcare hl7v2-stores create <HL7_STORE_NAME> \
    --dataset=<DATASET_NAME> \
    --location=<REGION>

```

---

## 5. Artifact Registry Setup

Create a secure private repository to store the Docker images.

```bash
gcloud artifacts repositories create <REPO_NAME> \
    --repository-format=docker \
    --location=<REGION> \
    --description="Health Without Borders Docker Repository"

```

---

## 6. Build and Push (Cloud Build)

Package the application code into a container image using Google's infrastructure.

*Execute from the project root:*

```bash
gcloud builds submit \
    --tag <REGION>-docker.pkg.dev/<GCP_PROJECT_ID>/<REPO_NAME>/backend:v1 \
    .

```

---

## 7. Service Deployment (Cloud Run)

Deploy the container to Cloud Run. This step establishes the secure Unix Socket connection to Cloud SQL and injects the Healthcare API configuration.

```bash
gcloud run deploy <CLOUD_RUN_SERVICE> \
    --image <REGION>-docker.pkg.dev/<GCP_PROJECT_ID>/<REPO_NAME>/backend:v1 \
    --region <REGION> \
    --allow-unauthenticated \
    --add-cloudsql-instances="<GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=<GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>" \
    --set-env-vars="DB_USER=postgres" \
    --set-env-vars="DB_PASS=<DB_PASSWORD>" \
    --set-env-vars="DB_NAME=<DB_NAME>" \
    --set-env-vars="GCP_PROJECT_ID=<GCP_PROJECT_ID>" \
    --set-env-vars="GCP_LOCATION=<REGION>" \
    --set-env-vars="GCP_DATASET_ID=<DATASET_NAME>" \
    --set-env-vars="GCP_HL7_STORE_ID=<HL7_STORE_NAME>" \
    --set-env-vars="SECRET_KEY=<YOUR_JWT_SECRET_KEY>"

```

Upon success, the CLI will output a URL ending in `.run.app`. This is your live API endpoint.

---

## 8. Data Initialization (Schema & Seeding)

Because Cloud Run is stateless, database migrations and initial catalog seeding must be executed via a secure tunnel from your local machine.

### 8.1. Start the Cloud SQL Auth Proxy

In a new terminal window, open the tunnel to your database instance and keep it running:

```bash
./cloud-sql-proxy <GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>

```

*Wait for the message: "Ready for new connections".*

### 8.2. Execute Initialization Scripts

In a **separate terminal window**, force the `DATABASE_URL` to route through the secure local tunnel, bypassing any local `.env` misconfigurations.

```bash
# 1. Force the explicit connection string to the local proxy
export DATABASE_URL="postgresql://postgres:<DB_PASSWORD>@127.0.0.1:5432/<DB_NAME>"

# 2. Create Tables (SQLAlchemy)
uv run python scripts/create_tables.py

# 3. Create Initial Admin User
uv run python scripts/create_generic_user.py

# 4. Load Medical Catalogs (ICD-10 / Vaccines)
uv run python scripts/load_catalogs.py

```

---

## 9. Monitoring and Maintenance

* **Application Logs:** Available in the [Cloud Run Console](https://console.cloud.google.com/run) under the "Logs" tab.
* **Database Performance:** Query stats and connections are visible in the [Cloud SQL Console](https://console.cloud.google.com/sql).
* **HL7v2 Metrics:** Message ingestion rates and validation errors can be monitored in the [Cloud Healthcare API Dashboard](https://console.cloud.google.com/healthcare).
