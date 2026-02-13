# Infrastructure and Deployment Guide (Google Cloud Platform)

This document details the technical procedures for deploying the **UNICEF Border Health Backend** to Google Cloud Platform (GCP). It covers resource provisioning, container building, service deployment, and database initialization.

## Architecture Overview

The system utilizes a fully serverless and containerized architecture:

* **Compute:** [Google Cloud Run](https://cloud.google.com/run) (Stateless containers).
* **Database:** [Google Cloud SQL](https://cloud.google.com/sql) (Managed PostgreSQL 15).
* **Registry:** [Artifact Registry](https://cloud.google.com/artifact-registry) (Docker image storage).
* **CI/CD:** [Cloud Build](https://cloud.google.com/build) (Serverless build pipeline).

---

## 1. Prerequisites

Before proceeding, ensure the following requirements are met:

1.  **GCP Project:** Active project with ID `migrants-unicef`.
2.  **Google Cloud SDK:** `gcloud` CLI installed and authenticated (`gcloud auth login`).
3.  **Python Environment:** Python 3.11+ and `uv` package manager installed locally.
4.  **Permissions:** The active user must have `Editor` or `Owner` roles, or specific permissions for Cloud Run, Cloud Build, and Artifact Registry.

---

## 2. Service Enablement

Enable the required APIs to allow communication between GCP services.

```bash
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    --project=migrants-unicef

```

---

## 3. Database Provisioning (Cloud SQL)

Cloud Run requires an external persistence layer.

1. **Create Instance:**
* **Type:** PostgreSQL 15.
* **Region:** `us-central1` (Must match Cloud Run region for low latency).
* **Tier:** `Enterprise` for production or `Sandbox` for development.
* **Instance ID:** `unicef-db-dev`.


2. **Configure Database:**
* Create a logical database named `postgres` (default) or `unicef_db`.
* Create a user `postgres` with a strong password.


3. **Retrieve Connection Name:**
* Note the "Instance Connection Name" from the dashboard (Format: `migrants-unicef:us-central1:unicef-db-dev`).



---

## 4. Artifact Registry Setup

Create a secure private repository to store the Docker images.

```bash
gcloud artifacts repositories create unicef-repo \
    --repository-format=docker \
    --location=us-central1 \
    --description="UNICEF Backend Docker Repository"

```

---

## 5. Build and Push

The application code is packaged into a container image using Cloud Build. This process occurs remotely on Google's infrastructure.

*Execute from the project root:*

```bash
gcloud builds submit \
    --tag us-central1-docker.pkg.dev/migrants-unicef/unicef-repo/backend:v1 \
    .

```

---

## 6. Service Deployment (Cloud Run)

Deploy the container to Cloud Run. This step injects production environment variables and establishes the secure connection to Cloud SQL via Unix Sockets.

**Note:** Replace `[YOUR_DB_PASSWORD]` and `[YOUR_SECRET_KEY]` with actual secure values.

```bash
gcloud run deploy unicef-backend-dev \
    --image us-central1-docker.pkg.dev/migrants-unicef/unicef-repo/backend:v1 \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated \
    --add-cloudsql-instances="migrants-unicef:us-central1:unicef-db-dev" \
    --set-env-vars="PROJECT_NAME=UNICEF_Prod" \
    --set-env-vars="API_V1_STR=/api/v1" \
    --set-env-vars="SECRET_KEY=[YOUR_SECRET_KEY]" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=migrants-unicef:us-central1:unicef-db-dev" \
    --set-env-vars="DB_USER=postgres" \
    --set-env-vars="DB_PASS=[YOUR_DB_PASSWORD]" \
    --set-env-vars="DB_NAME=postgres" \
    --port 8080

```

### Verification

Upon success, the CLI will output a URL ending in `.run.app`. This is the public API endpoint.

---

## 7. Data Initialization (Schema & Seeding)

Cloud Run is stateless and cannot execute administrative tasks (like DB migrations) directly during startup. Initialization is performed via a secure tunnel from a local machine.

### 7.1. Install Cloud SQL Auth Proxy

Required to connect `localhost` to the Cloud SQL instance securely.

```bash
# Linux
curl -o cloud-sql-proxy [https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.0/cloud-sql-proxy.linux.amd64](https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.0/cloud-sql-proxy.linux.amd64)
chmod +x cloud-sql-proxy

```

### 7.2. Open Tunnel

Run this in a separate terminal window. Keep it open.

```bash
./cloud-sql-proxy migrants-unicef:us-central1:unicef-db-dev

```

*Wait for the message: "Ready for new connections".*

### 7.3. Execute Migration Scripts

In a **new terminal window**, configure local environment variables to point to the tunnel and run the initialization scripts.

```bash
# Configure connection to local tunnel
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_USER=postgres
export DB_PASS=[YOUR_DB_PASSWORD]
export DB_NAME=postgres

# 1. Create Tables (SQLAlchemy)
uv run python scripts/create_tables.py

# 2. Create Initial Admin User
uv run python scripts/create_generic_user.py

# 3. Load Medical Catalogs (ICD-10 / Vaccines)
uv run python scripts/load_catalogs.py

```

---

## 8. Updating the Application

### Configuration Changes

To update environment variables (e.g., changing project name) without rebuilding the code:

```bash
gcloud run services update unicef-backend-dev \
    --update-env-vars PROJECT_NAME="UNICEF New Name" \
    --region us-central1

```

### Code Changes

To deploy code updates, the full build-deploy cycle is required:

1. **Build:** `gcloud builds submit ...` (See Step 5)
2. **Deploy:** `gcloud run deploy ...` (See Step 6)

---

## 9. Monitoring and Logs

* **Logs:** Application logs (stdout/stderr) are available in the [Cloud Run Console](https://console.cloud.google.com/run) under the "Logs" tab.
* **Database:** Query performance and connection stats are available in the [Cloud SQL Console](https://console.cloud.google.com/sql).
* **SQL Studio:** Use "Cloud SQL Studio" in the GCP Console to execute raw SQL queries against the production database directly from the browser.

```