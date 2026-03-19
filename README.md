# Health Without Borders - Backend

![Python Version](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.128.0-009688.svg)
![Docker](https://img.shields.io/badge/Docker-Available-2496ED.svg)
![GCP](https://img.shields.io/badge/Google_Cloud-Run-4285F4.svg)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/guanes/health-without-borders/actions/workflows/ci.yml/badge.svg)

**Interoperable Medical Records Platform for Migrant Children and Adolescents.**

This repository contains the source code for the **Backend**, a RESTful API developed for the Health Without Borders initiative. Its goal is to guarantee the continuity of medical care for migrant populations through a secure, standardized (HL7/FHIR) system that is resilient to intermittent connectivity conditions in border areas.

---

## 🚀 Key Features

* **Serverless Architecture:** Deployed on Google Cloud Run for automatic scalability and cost efficiency.
* **Robust Security:** Authentication via JWT (JSON Web Tokens) with secure rotation and password encryption using Bcrypt.
* **Medical Standardization:** Integrated international catalogs (ICD-10 for diagnoses and CVX for vaccination).
* **Offline-First:** Optimized endpoints for data synchronization from mobile devices with limited connectivity.
* **Role Management:** RBAC (Role-Based Access Control) system for administrators and medical staff.

---

## 🛠️ Tech Stack

* **Language:** Python 3.11+
* **Web Framework:** FastAPI
* **Database:** PostgreSQL 15 (Cloud SQL in Production)
* **ORM:** SQLAlchemy 2.0
* **Package Manager:** `uv` (Astral)
* **Infrastructure:** Docker, Google Artifact Registry, Google Cloud Build.

---

## 📂 Project Structure

```
health-without-borders/
├── .github/                          # GitHub configuration
│   ├── workflows/
│   │   ├── ci.yml                    # CI pipeline (lint + tests on every PR)
│   │   ├── docs.yml                  # Auto-deploy MkDocs to GitHub Pages
│   │   └── deploy-cloud-run.yml      # Deploy to Cloud Run via WIF
│   └── PULL_REQUEST_TEMPLATE.md      # PR checklist template
├── app/                              # Main application package
│   ├── main.py                       # FastAPI application entry point
│   ├── api/                          # API versioning and routing
│   │   ├── deps.py                   # Shared dependencies (DB session, auth)
│   │   └── v1/endpoints/             # Endpoint modules
│   │       ├── catalogs.py           # ICD-10, CVX catalog endpoints
│   │       ├── login.py              # Authentication & JWT
│   │       ├── organizations.py      # Organization management endpoints
│   │       ├── patients.py           # Patient CRUD & medical records
│   │       └── users.py              # User management endpoints
│   ├── core/                         # Core configuration & utilities
│   │   ├── config.py                 # Environment variables & settings
│   │   ├── logging.py                # Structured logging
│   │   ├── rate_limit.py             # Rate limiting (slowapi)
│   │   └── security.py               # JWT, password hashing, RBAC
│   ├── db/                           # Database layer
│   │   ├── models.py                 # ORM models (Users, Patients, Records)
│   │   └── session.py                # Database session management
│   ├── schemas/                      # Pydantic models (request/response)
│   └── services/                     # Business logic layer
│       ├── llm/                      # LLM integration (Gemini)
│       ├── gcp_service.py            # GCP Cloud Healthcare API integration
│       ├── hl7_service.py            # HL7v2 parsing & generation
│       └── patient_service.py        # Patient business logic
├── docs/                             # MkDocs documentation source
├── scripts/                          # DB initialization and data loading
├── tests/                            # Automated test suite (pytest)
│   └── api/v1/
│       ├── test_login.py             # Authentication & JWT tests
│       ├── test_organizations.py
│       ├── test_patients.py
│       └── test_users.py
├── .env.example                      # Environment variables template
├── cloudbuild.yaml                   # Google Cloud Build CI/CD pipeline
├── Dockerfile                        # Container image definition
├── LICENSE                           # MIT License
├── mkdocs.yml                        # MkDocs documentation configuration
├── PROJECT_CHARTER.md                # Project vision, mission and governance
├── pyproject.toml                    # Project metadata & dependencies
├── SECURITY.md                       # Security protocols & encryption
└── README.md                         # This file
```

---

## 📚 Documentation

Full technical documentation is published at **[guanes.github.io/health-without-borders](https://guanes.github.io/health-without-borders/)**.

Key sections:
* [Architecture & HL7v2 Strategy](https://guanes.github.io/health-without-borders/architecture/hl7v2-strategy/)
* [Local Setup Guide](https://guanes.github.io/health-without-borders/development/setup/)
* [Security Protocols](https://guanes.github.io/health-without-borders/infrastructure/security/)
* [GCP Deployment](https://guanes.github.io/health-without-borders/infrastructure/gcp-deploy/)

---

## ⚡ Quick Start (Local Development)

```bash
# 1. Clone repository
git clone https://github.com/guanes/health-without-borders.git
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

## 🧪 Testing

```bash
uv run pytest --cov=app --cov-report=term-missing
```

The project maintains **≥ 75% code coverage**. Coverage is enforced automatically on every PR via the CI pipeline.

---

## 🤝 Contributing

We welcome contributions from the community! Before submitting a Pull Request, please read [CONTRIBUTING.md](CONTRIBUTING.md) and review the [QA & PR Workflow](docs/development/qa-plan.md).

This project is released with a [Contributor Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to abide by its terms.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

**Developed for Health Without Borders**