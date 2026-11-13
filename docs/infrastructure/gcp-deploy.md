# Infrastructure and Deployment Guide (Google Cloud Platform)

This document details the technical procedures for deploying the **Health Without Borders API** to Google Cloud Platform (GCP). It covers resource provisioning, container building, secure secret management, service deployment, and CI/CD automation.

## Architecture Overview

The system utilizes a fully serverless, highly available, and containerized architecture:

* **Compute:** [Google Cloud Run](https://cloud.google.com/run) (Stateless containers).
* **Database:** [Google Cloud SQL](https://cloud.google.com/sql) (Managed PostgreSQL 15).
* **Interoperability:** [Cloud Healthcare API](https://cloud.google.com/healthcare-api) (FHIR R4 Store for RDA bundles).
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
    --backup-start-time=07:00 \
    --retained-backups-count=14 \
    --enable-point-in-time-recovery \
    --deletion-protection \
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
    --backup-start-time=07:00 \
    --retained-backups-count=14 \
    --deletion-protection \
    --network=default \
    --no-assign-ip \
    --root-password="<DB_PASSWORD>"

```

*(Note: Production uses a dedicated 2-core 8GB RAM machine, is replicated across two zones for High Availability, and enables Point-in-Time recovery).*

**Backups are off unless you ask for them, even in development.** Without the backup flags above, the instance has no backup at all: any incident or a deleted instance loses every record, consent and access log. `--backup-start-time` is UTC (07:00 UTC is 02:00 in Colombia). On an existing instance, add them with `gcloud sql instances patch` (the same flags); enabling point-in-time recovery restarts the instance for a few minutes. The pilot instance (`hwb-db-cluster`) was created without them and has had them since 2026-10-02.

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
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create hwb-nfc-master-key --data-file=-

```

The fourth secret, `nfc-kek`, is created only by following the KEK custody
procedure ([NFC Key Management](nfc-key-management.md) § 10): its backup must
exist before the service ever reads it. `cloudbuild.yaml` mounts `hwb-db-pass`,
`hwb-secret-key` and `nfc-kek` as `:latest`; never add a version to an existing
one (same document, § 6 and § 10). `hwb-nfc-master-key` is mounted by hand for
the first start with the KEK only, to import it as version 0 (same document,
§ 4); each deploy from `cloudbuild.yaml` removes it.

**2. Create the service's own account and give it each secret:**

The service runs as `hwb-backend@`, an account that holds only what the backend
needs. Never run it as the Compute Engine default account: that account gets
`roles/editor` on the whole project, so anyone who ran code inside the service
could read or delete every record, the database and the other services.

```bash
export RUNTIME_SA="hwb-backend@<GCP_PROJECT_ID>.iam.gserviceaccount.com"

gcloud iam service-accounts create hwb-backend \
    --display-name="HWB backend (Cloud Run runtime)"

# Cloud SQL connection and Vertex AI (Gemini)
gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
    --member="serviceAccount:${RUNTIME_SA}" --role="roles/cloudsql.client"
gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
    --member="serviceAccount:${RUNTIME_SA}" --role="roles/aiplatform.user"

for SECRET in hwb-db-pass hwb-secret-key nfc-kek; do
  gcloud secrets add-iam-policy-binding "${SECRET}" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/secretmanager.secretAccessor"
done

```

Grant `nfc-kek` once it exists (see above). `hwb-nfc-master-key` is read only by
the first start with the KEK; grant it for that start and remove the binding
right after ([NFC Key Management](nfc-key-management.md) § 4). The FHIR store
binding comes in § 5, once the store exists.

---

## 5. Healthcare & Artifact Registry Setup

**1. Healthcare Interoperability (FHIR R4 Store):**

```bash
gcloud healthcare datasets create <DATASET_NAME> --location=<REGION>

gcloud healthcare fhir-stores create <FHIR_STORE_NAME> \
    --dataset=<DATASET_NAME> \
    --location=<REGION> \
    --version=R4 \
    --enable-referential-integrity \
    --enable-resource-versioning

# The service writes bundles to this store and nowhere else
gcloud healthcare fhir-stores add-iam-policy-binding <FHIR_STORE_NAME> \
    --dataset=<DATASET_NAME> \
    --location=<REGION> \
    --member="serviceAccount:hwb-backend@<GCP_PROJECT_ID>.iam.gserviceaccount.com" \
    --role="roles/healthcare.fhirResourceEditor"

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
    --service-account hwb-backend@<GCP_PROJECT_ID>.iam.gserviceaccount.com \
    --allow-unauthenticated \
    --min-instances 1 \
    --max-instances 3 \
    --add-cloudsql-instances="<GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=<GCP_PROJECT_ID>:<REGION>:<DB_INSTANCE_NAME>" \
    --set-env-vars="DB_USER=postgres" \
    --set-env-vars="DB_NAME=<DB_NAME>" \
    --set-env-vars="GCP_PROJECT_ID=<GCP_PROJECT_ID>" \
    --set-env-vars="GCP_LOCATION=<REGION>" \
    --set-env-vars="GCP_DATASET_ID=<DATASET_NAME>" \
    --set-env-vars="GCP_FHIR_STORE_ID=<FHIR_STORE_NAME>" \
    --update-secrets="DB_PASS=hwb-db-pass:latest,SECRET_KEY=hwb-secret-key:latest"

```

---

## 7. CI/CD Automation (Cloud Build) 🚀

For this project, Cloud Build is the primary CI/CD gate. This avoids dependency on GitHub-hosted runner billing and keeps validation/deploy inside GCP.

### Trigger A: Pull Request quality gates

Create a trigger for pull requests targeting `develop`:

- **Event:** Pull request
- **Base branch:** `^develop$`
- **Behavior:** Pull requests are validated by the GitHub Actions workflow `.github/workflows/ci.yml` (lint, type-check, migrations check, dependency audit, tests with 100 % coverage, PostgreSQL integration tests). There is no separate Cloud Build configuration for pull requests.

### Trigger B: Deploy from `develop`

Create a trigger for merges/pushes to `develop`:

- **Event:** Push to branch
- **Branch regex:** `^develop$`
- **Configuration file:** `/cloudbuild.yaml`
- **Behavior:** Run tests, build image, push image, deploy to Cloud Run.

### What the pilot project actually has

- **`hwb-backend-dev-deploy`:** push to `^develop$`, runs `cloudbuild.yaml`, as `hwb-deployer@`, with no approval step. Every merge to `develop` is a deployment. The service itself runs as `hwb-backend@` (§ 4).
- **Capacity:** the deploy sets `--min-instances 1` and `--max-instances 3`, which go with the connection pool of each instance. See [Database § 1.1](database.md#11-connection-capacity) before changing either.
- **`develop` and `main` are protected on GitHub** (since 2026-11-04): a change needs a pull request, with `Lint & Test (Python 3.11)` and `Integration (PostgreSQL 15)` as required checks. This matters because the trigger deploys whatever lands on `develop`, and `cloudbuild.yaml` re-runs lint and tests, but not coverage, `pip-audit`, `alembic check` or the PostgreSQL suite.
- **`hwb-pr-quality-gate`** (pull requests to `^develop$`, pointing to the removed `cloudbuild.pr.yaml`) was deleted on 2026-10-22. It was disabled, but it ran as a service account with `roles/editor`, and Cloud Build reads the build file from the commit that fired it. Pull requests are checked by GitHub Actions.

### Service account for triggers

The trigger runs as `hwb-deployer@`, which can build, push and deploy, and
nothing else. It cannot read the secrets, the database or the FHIR store: the
deployed service reads them as `hwb-backend@`.

```bash
export DEPLOYER_SA="hwb-deployer@<GCP_PROJECT_ID>.iam.gserviceaccount.com"

gcloud iam service-accounts create hwb-deployer \
    --display-name="HWB deployer (Cloud Build trigger)"

# Deploy to Cloud Run (run.admin, because --allow-unauthenticated sets the
# service's IAM policy), see the VPC for Direct VPC egress, and write build logs
for ROLE in roles/run.admin roles/compute.networkViewer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
      --member="serviceAccount:${DEPLOYER_SA}" --role="${ROLE}"
done

# Push the image
gcloud artifacts repositories add-iam-policy-binding <REPO_NAME> \
    --location=<REGION> \
    --member="serviceAccount:${DEPLOYER_SA}" --role="roles/artifactregistry.writer"

# Deploy the service as hwb-backend@ (and only as that account)
gcloud iam service-accounts add-iam-policy-binding \
    hwb-backend@<GCP_PROJECT_ID>.iam.gserviceaccount.com \
    --member="serviceAccount:${DEPLOYER_SA}" --role="roles/iam.serviceAccountUser"

gcloud builds triggers update github <TRIGGER_NAME> \
    --service-account="projects/<GCP_PROJECT_ID>/serviceAccounts/${DEPLOYER_SA}"

```

`cloudbuild.yaml` writes build logs to Cloud Logging only
(`options.logging: CLOUD_LOGGING_ONLY`), which a trigger with its own account
requires.

**Rolling back.** Revisions deployed before 2026-11-10 run as the Compute Engine
default account, which no longer holds any role in the project. Do not send
traffic back to one of them: it cannot read the secrets, so it would not start.
To roll back, deploy the older image (it is in Artifact Registry under its
commit SHA) with `--service-account hwb-backend@...`.

---

## 8. Data Initialization (Schema & Seeding)

**Schema: automatic.** The service applies the database migrations (Alembic, `migrations/`) every time a new revision starts, before it serves traffic, using its own Cloud SQL connection. There is nothing to run by hand and the database never needs a public IP for it. A PostgreSQL advisory lock makes concurrent instances wait for each other. If a migration fails, the new revision does not start and Cloud Run keeps serving the previous one; the startup log says `Database migration failed: <ErrorType>`. Right after the migration, the startup log reports whether any table or column the models expect is missing (`Schema check: ...`). Set `RUN_MIGRATIONS_ON_STARTUP=false` only to disable this deliberately.

The first migration (`0001`) is a baseline that creates only what is missing, so a database built earlier with `scripts/create_tables.py` is simply recorded as being at `0001`.

**Seeding the first superadmin: manual, once.** Because our database lacks a public IP for security reasons, running `scripts/create_generic_user.py` from a local laptop requires a temporary security bypass. We will temporarily assign a public IP, run the script securely through the encrypted Auth Proxy, and then lock the instance down again.

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

**3. Execute the Seeding Script:**
In a **separate terminal window**, force the `DATABASE_URL` to route through the local tunnel:

```bash
export DATABASE_URL="postgresql://postgres:<DB_PASSWORD>@127.0.0.1:5433/<DB_NAME>"

uv run python scripts/create_generic_user.py

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
* **FHIR Store Metrics:** [Cloud Healthcare API Dashboard](https://console.cloud.google.com/healthcare).
* **Who read or wrote FHIR data:** Logs Explorer, scope the `hwb-fhir-audit` log bucket (Data Access audit logs, 400 days; see [Healthcare API](healthcare-api.md) § 4.1).
* **CI/CD Pipeline:** [Cloud Build History](https://console.cloud.google.com/cloud-build/builds).