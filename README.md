# Health Without Borders - Backend

![Python Version](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-009688.svg)
![Docker](https://img.shields.io/badge/Docker-Available-2496ED.svg)
![GCP](https://img.shields.io/badge/Google_Cloud-Run-4285F4.svg)
![License](https://img.shields.io/badge/license-MIT-green)

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
├── app/                              # Main application package
│   ├── __init__.py
│   ├── main.py                       # FastAPI application entry point
│   ├── api/                          # API versioning and routing
│   │   ├── __init__.py
│   │   ├── deps.py                   # Shared dependencies (DB session, auth)
│   │   └── v1/                       # API v1 endpoints
│   │       ├── __init__.py
│   │       ├── api.py                # Route aggregation
│   │       └── endpoints/            # Endpoint modules
│   │           ├── __init__.py
│   │           ├── catalogs.py       # ICD-10, CVX catalog endpoints
│   │           ├── login.py          # Authentication & JWT
│   │           └── patients.py       # Patient CRUD & medical records
│   ├── core/                         # Core configuration & utilities
│   │   ├── __init__.py
│   │   ├── config.py                 # Environment variables & settings
│   │   ├── logging.py                # Structured logging
│   │   └── security.py               # JWT, password hashing, RBAC
│   ├── db/                           # Database layer
│   │   ├── __init__.py
│   │   ├── base.py                   # SQLAlchemy declarative base
│   │   ├── models.py                 # ORM models (Users, Patients, Records)
│   │   └── session.py                # Database session management
│   ├── schemas/                      # Pydantic models (request/response)
│   │   ├── __init__.py
│   │   ├── catalog.py                # Catalog schemas
│   │   ├── patient.py                # Patient schemas
│   │   ├── token.py                  # JWT token schemas
│   │   └── user.py                   # User schemas
│   ├── services/                     # Business logic layer
│   │   ├── __init__.py
│   │   ├── catalog_service.py        # Catalog operations
│   │   ├── gcp_service.py            # GCP Cloud Healthcare API integration
│   │   ├── hl7_service.py            # HL7v2 parsing & generation
│   │   └── patient_service.py        # Patient business logic
│   └── data/                         # Static data files
│       └── cie10.json                # ICD-10 catalog data
├── docs/                             # Project documentation
│   ├── architecture/
│   │   └── hl7v2-strategy.md         # Architectural Decision Record (ADR)
│   ├── development/
│   │   └── setup.md                  # Local development setup guide
│   └── infrastructure/
│       ├── database.md               # Database schema & modeling
│       ├── gcp-deploy.md             # Google Cloud Run deployment
│       ├── healthcare-api.md         # GCP Cloud Healthcare API config
│       └── security.md               # Security protocols & encryption
├── scripts/                          # Utility scripts
│   ├── __init__.py
│   ├── create_tables.py              # Initialize database schema
│   ├── create_generic_user.py        # Create default user for testing
│   └── load_catalogs.py              # Populate clinical catalogs (ICD-10, CVX)
├── tests/                            # Automated test suite
│   ├── conftest.py                   # Pytest fixtures & configuration
│   └── api/
│       └── v1/
│           └── test_patients.py      # Patient endpoint tests
├── .venv/                            # Python virtual environment
├── cloud-sql-proxy                   # Cloud SQL Proxy binary
├── Dockerfile                        # Container image definition
├── gcp_key.json                      # GCP service account credentials
├── pyproject.toml                    # Project metadata & dependencies
├── pytest.ini                        # Pytest configuration
└── README.md                         # This file
```

### Directory Purpose Summary

| Directory | Purpose |
|-----------|---------|
| `app/` | Main application code with API, business logic, and database models |
| `app/api/` | FastAPI routers and endpoint definitions |
| `app/core/` | Configuration, security, logging utilities |
| `app/db/` | SQLAlchemy ORM models and database session management |
| `app/schemas/` | Pydantic models for request/response validation |
| `app/services/` | Business logic and external integrations (GCP, HL7) |
| `docs/` | Architecture, deployment, and development guides |
| `scripts/` | Database initialization and data loading utilities |
| `tests/` | Automated test cases using pytest |

---

## 📚 Documentation

Detailed project documentation is organized in the `docs/` folder:

* **Development:**
  * [**Local Setup Guide**](docs/development/setup.md): Instructions for configuring Docker and running the API locally.
* **Infrastructure & Security:**
  * [**GCP Deployment Guide**](docs/infrastructure/gcp-deploy.md): Step-by-step instructions for deploying to Google Cloud Run.
  * [**Database Architecture**](docs/infrastructure/database.md): Data modeling, JSONB usage, and table dictionary.
  * [**Security Protocols**](docs/infrastructure/security.md): JWT configuration, RBAC, and data encryption standards.
* **Interoperability:**
  * [**Cloud Healthcare API**](docs/infrastructure/healthcare-api.md): GCP HL7v2 Store and Pub/Sub configuration.
  * [**HL7v2 Strategy (ADR)**](docs/architecture/hl7v2-strategy.md): Architectural Decision Record comparing GCP, Mirth Connect, and In-House solutions.

---

## ⚡ Quick Start (Local Development)

Please refer to the comprehensive [**Local Setup Guide**](docs/development/setup.md) for detailed instructions on spinning up the local Docker database and seeding the clinical catalogs.

**Basic commands summary:**
```bash
# 1. Clone repository
git clone [https://github.com/organization/health-without-borders.git](https://github.com/organization/health-without-borders.git)
cd health-without-borders

# 2. Install dependencies
uv sync

# 3. Start local database
docker run --name hwb-db-local -e POSTGRES_PASSWORD=password -e POSTGRES_DB=hwb_local -p 5432:5432 -d postgres:15

# 4. Initialize schema and catalogs
uv run python scripts/create_tables.py
uv run python scripts/load_catalogs.py

# 5. Run the server
uv run uvicorn app.main:app --reload

```

The service will be available at: http://localhost:8000/docs

---

## 🧪 Testing

To run the automated test suite before opening a Pull Request:

```bash
uv run pytest

```

---

## 🤝 Contributing

This project follows strict development standards. Before submitting a Pull Request, please ensure you:

1. Follow the PEP-8 style guide.
2. Do not commit credentials or secrets to the repository.
3. Document any new endpoint in Swagger.

For more details, read [CONTRIBUTING.md](https://www.google.com/search?q=CONTRIBUTING.md).

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.

---

**Developed for Health Without Borders**