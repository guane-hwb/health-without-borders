# Health Without Borders - Backend

![Python Version](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.128.0-009688.svg)
![Docker](https://img.shields.io/badge/Docker-Available-2496ED.svg)
![GCP](https://img.shields.io/badge/Google_Cloud-Run-4285F4.svg)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/guane-hwb/health-without-borders/actions/workflows/ci.yml/badge.svg)
[![Coverage](https://codecov.io/gh/guane-hwb/health-without-borders/graph/badge.svg)](https://codecov.io/gh/guane-hwb/health-without-borders)

**Interoperable Medical Records Platform for Migrant Children and Adolescents.**

This repository contains the source code for the Backend API of the Health Without Borders initiative. Its goal is to guarantee the continuity of medical care for migrant populations through a secure, FHIR R4-standardized system resilient to intermittent connectivity in border areas.

---

## Key Features

* **FHIR R4 Interoperability:** Generates RDA (Resumen Digital de Atención en Salud) bundles compliant with Colombia's Resolution 1888/2025 and the IHCE Implementation Guide.
* **AI-Assisted Medical Coding:** Gemini LLM integration for automated ICD-10/11 code extraction from clinical notes and family history coding.
* **Offline-First:** Optimized sync endpoints for mobile devices with limited connectivity.
* **Delta Sync:** Only new FHIR bundles are generated and sent — no duplicate transmissions.
* **Robust Security:** JWT authentication, RBAC with multi-tenancy, hardware 2FA for minors via NFC bracelets.
* **Serverless Architecture:** Deployed on Google Cloud Run for automatic scalability.

---

## Tech Stack

* **Language:** Python 3.11+
* **Web Framework:** FastAPI (async)
* **Database:** PostgreSQL 15 (Cloud SQL in Production)
* **ORM:** SQLAlchemy 2.0
* **FHIR Store:** Google Cloud Healthcare API (R4)
* **LLM:** Google Vertex AI (Gemini 2.5 Pro)
* **Package Manager:** `uv` (Astral)
* **Infrastructure:** Docker, Google Artifact Registry, Google Cloud Build.

---

## Project Structure

```
health-without-borders/
├── app/                              # Main application package
│   ├── main.py                       # FastAPI entry point
│   ├── api/                          # API versioning and routing
│   │   ├── deps.py                   # Shared dependencies (DB session, auth)
│   │   └── v1/endpoints/             # Endpoint modules
│   │       ├── catalogs.py           # ICD-10, CVX catalog endpoints
│   │       ├── login.py              # Authentication & JWT
│   │       ├── organizations.py      # Organization management
│   │       ├── patients.py           # Patient sync, scan, search
│   │       └── users.py              # User management
│   ├── core/                         # Configuration & utilities
│   ├── db/                           # Database layer (models, session)
│   ├── schemas/                      # Pydantic models (RDA-compliant)
│   └── services/                     # Business logic layer
│       ├── fhir/                     # FHIR Store abstraction (vendor-neutral)
│       │   ├── base.py               # FHIRStoreBackend Protocol
│       │   ├── gcp.py                # Google Cloud Healthcare API backend
│       │   ├── noop.py               # No-op backend (dev/testing)
│       │   └── factory.py            # Backend selection via FHIR_BACKEND env var
│       ├── llm/                      # LLM abstraction (vendor-neutral)
│       │   ├── base.py               # MedicalCodingService Protocol
│       │   ├── gemini.py             # Google Vertex AI / Gemini backend
│       │   ├── noop.py               # No-op backend (dev/testing)
│       │   ├── factory.py            # Backend selection via LLM_BACKEND env var
│       │   ├── prompts.py            # Provider-agnostic prompt builders
│       │   └── schemas.py            # Provider-agnostic output schemas
│       ├── fhir_service.py           # FHIR R4 RDA bundle generation
│       └── patient_service.py        # Patient CRUD & strict lookup
├── datalake/                         # Sample patient JSON files for testing
├── docs/                             # MkDocs documentation source
├── scripts/                          # DB initialization and data loading
├── tests/                            # Automated test suite (pytest)
├── .env.example                      # Environment variables template
├── cloudbuild.yaml                   # Google Cloud Build CI/CD pipeline
├── Dockerfile                        # Container image definition
├── mkdocs.yml                        # MkDocs configuration
├── pyproject.toml                    # Project metadata & dependencies
└── SECURITY.md                       # Vulnerability reporting policy
```

---

## Documentation

Full technical documentation is published at **[guanes.github.io/health-without-borders](https://guanes.github.io/health-without-borders/)**.

Key sections:
* [FHIR RDA Architecture](https://guanes.github.io/health-without-borders/architecture/fhir-rda/)
* [AI & NLP Integration](https://guanes.github.io/health-without-borders/architecture/ai-integration/)
* [Local Setup Guide](https://guanes.github.io/health-without-borders/development/setup/)
* [Security Protocols](https://guanes.github.io/health-without-borders/infrastructure/security/)
* [GCP Deployment](https://guanes.github.io/health-without-borders/infrastructure/gcp-deploy/)
* [Project Board](https://github.com/guane-hwb/health-without-borders/projects/1)

---

## Quick Start (Local Development)

```bash
# 1. Clone repository
git clone https://github.com/guane-hwb/health-without-borders.git
cd health-without-borders

# 2. Copy environment variables template
cp .env.example .env
# Edit .env with your local values

# 3. Install dependencies
uv sync

# 4. Start local database
docker run --name hwb-db-local -e POSTGRES_PASSWORD=password -e POSTGRES_DB=hwb_local -p 5432:5432 -d postgres:15

# 5. Initialize schema and catalogs
uv run python scripts/create_tables.py
uv run python scripts/load_catalogs.py

# 6. Run the server
uv run uvicorn app.main:app --reload
```

The interactive API will be available at: **http://localhost:8000/docs**

---

## Testing

```bash
uv run pytest --cov=app --cov-report=term-missing
```

The project maintains **≥ 75% code coverage**. Coverage is enforced automatically on every PR via the CI pipeline.

---

## Contributing

We welcome contributions from the community! Before submitting a Pull Request, please read [CONTRIBUTING.md](CONTRIBUTING.md) and review the [QA & PR Workflow](docs/development/qa-plan.md).

This project is released with a [Contributor Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to abide by its terms.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

**Developed for Health Without Borders**
