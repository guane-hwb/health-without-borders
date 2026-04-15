# Health Without Borders — Backend API

**Interoperable Medical Records Platform for Migrant Children and Adolescents.**

This site documents the Backend API of the Health Without Borders initiative — a RESTful API built to guarantee continuity of medical care for migrant populations through a secure, FHIR R4-standardized system resilient to intermittent connectivity in border areas.

Source code: [github.com/guane-hwb/health-without-borders](https://github.com/guane-hwb/health-without-borders)

---

## Key Features

- **FHIR R4 Interoperability:** Generates RDA (Resumen Digital de Atención en Salud) bundles compliant with Colombia's Resolution 1888/2025 and the IHCE Implementation Guide.
- **AI-Assisted Medical Coding:** Gemini LLM integration for automated ICD-10/11 code extraction from clinical notes and family history coding.
- **Offline-First:** Optimized sync endpoints for mobile devices with limited connectivity.
- **Robust Security:** JWT authentication, RBAC with multi-tenancy, hardware 2FA for minors via NFC bracelets.
- **Serverless Architecture:** Deployed on Google Cloud Run for automatic scalability.
- **Delta Sync:** Only new FHIR bundles are generated and sent to the FHIR Store — no duplicate transmissions.

---

## Documentation Index

### Architecture
- [**FHIR RDA Architecture**](architecture/fhir-rda.md) — Bundle types, delta sync logic, resource mapping, and terminology systems.
- [**AI & NLP Integration**](architecture/ai-integration.md) — Prompt engineering strategy for automated ICD-10/11 coding using Vertex AI.

### Development
- [**Local Setup Guide**](development/setup.md) — How to configure Docker and run the API locally.
- [**QA & PR Workflow**](development/qa-plan.md) — Quality assurance process and Pull Request standards.
- [**Remediation Program**](development/remediation-program.md) — Security, quality, and compliance remediation tracking.

### Infrastructure & Security
- [**Database Schema**](infrastructure/database.md) — Data modeling, JSONB usage, and table dictionary.
- [**GCP Deployment Guide**](infrastructure/gcp-deploy.md) — Step-by-step instructions for deploying to Google Cloud Run.
- [**FHIR Store Configuration**](infrastructure/healthcare-api.md) — GCP Healthcare API FHIR Store setup and IAM.
- [**Security Protocols**](infrastructure/security.md) — JWT configuration, RBAC, encryption standards, and data flow.
- [**ISO 27001 Mapping**](infrastructure/iso27001-technical-mapping.md) — Technical control mapping to ISO/IEC 27001:2022.

---

## Quick Start

```bash
# 1. Clone repository
git clone https://github.com/guane-hwb/health-without-borders.git
cd health-without-borders

# 2. Copy environment variables template
cp .env.example .env

# 3. Install dependencies
uv sync

# 4. Start local database
docker run --name hwb-db-local \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=hwb_local \
  -p 5432:5432 -d postgres:15

# 5. Initialize schema and catalogs
uv run python scripts/create_tables.py
uv run python scripts/load_catalogs.py

# 6. Run the server
uv run uvicorn app.main:app --reload
```

The interactive API will be available at: **http://localhost:8000/docs**

---

## Contributing

We welcome contributions from the community. Please read the [CONTRIBUTING.md](https://github.com/guane-hwb/health-without-borders/blob/main/CONTRIBUTING.md) guide and our [Code of Conduct](https://github.com/guane-hwb/health-without-borders/blob/main/CODE_OF_CONDUCT.md) before submitting a Pull Request.

This project is licensed under the **MIT License**.