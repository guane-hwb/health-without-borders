# Health Without Borders — Backend API

**Interoperable Medical Records Platform for Migrant Children and Adolescents.**

This site documents the **Backend API** of the Health Without Borders initiative — a RESTful API built to guarantee continuity of medical care for migrant populations through a secure, standardized (HL7/FHIR) system resilient to intermittent connectivity in border areas.

Source code: [github.com/guanes/health-without-borders](https://github.com/guanes/health-without-borders)

---

## 🚀 Key Features

* **Serverless Architecture:** Deployed on Google Cloud Run for automatic scalability and cost efficiency.
* **Robust Security:** Authentication via JWT with secure rotation and password encryption using Bcrypt. Full RBAC and multi-tenancy.
* **Medical Standardization:** Integrated international catalogs (ICD-10 for diagnoses and CVX for vaccination).
* **Offline-First:** Optimized endpoints for data synchronization from mobile devices with limited connectivity.
* **HL7v2 Interoperability:** Native integration with Google Cloud Healthcare API for clinical message exchange.
* **AI-Assisted Diagnosis:** Gemini LLM integration for automated ICD-10/11 code extraction from clinical notes.

---

## 📚 Documentation Index

### Architecture
* [**HL7v2 Strategy (ADR)**](architecture/hl7v2-strategy.md) — Architectural Decision Record comparing GCP, Mirth Connect, and In-House solutions.
* [**HL7v2 Mapping Specifications**](architecture/hl7-mapping.md) — JSON to HL7v2 field mapping reference.
* [**AI & NLP Integration**](architecture/ai-integration.md) — Prompt engineering strategy for automated ICD-10/11 coding using Vertex AI.

### Development
* [**Local Setup Guide**](development/setup.md) — How to configure Docker and run the API locally.
* [**QA & PR Workflow**](development/qa-plan.md) — Quality assurance process and Pull Request standards.

### Infrastructure & Security
* [**Database Schema**](infrastructure/database.md) — Data modeling, JSONB usage, and table dictionary.
* [**GCP Deployment Guide**](infrastructure/gcp-deploy.md) — Step-by-step instructions for deploying to Google Cloud Run.
* [**Cloud Healthcare API**](infrastructure/healthcare-api.md) — GCP HL7v2 Store and Pub/Sub configuration.
* [**Security Protocols**](infrastructure/security.md) — JWT configuration, RBAC, encryption standards, and data flow.

---

## ⚡ Quick Start

```bash
# 1. Clone repository
git clone https://github.com/guanes/health-without-borders.git
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

## 🤝 Contributing

We welcome contributions from the community. Please read the [CONTRIBUTING.md](https://github.com/guanes/health-without-borders/blob/main/CONTRIBUTING.md) guide and our [Code of Conduct](https://github.com/guanes/health-without-borders/blob/main/CODE_OF_CONDUCT.md) before submitting a Pull Request.

This project is licensed under the **MIT License**.