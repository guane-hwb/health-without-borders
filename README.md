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

*[TO BE GENERATED]*

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