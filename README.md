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

## 📚 Documentation

Detailed project documentation is organized in the `docs/` folder:

* [**Infrastructure and Deployment Guide**](docs/infrastructure/gcp-deploy.md): Step-by-step instructions for deploying on Google Cloud Platform.
* [**Database Architecture**](docs/infrastructure/database.md): Data modeling and table dictionary.
* [**HL7v2 Interoperability**](docs/infrastructure/healthcare-api.md): Google Cloud Healthcare API configuration.
* [**Security**](docs/infrastructure/security.md): Authentication protocols and sensitive data handling.

---

## ⚡ Quick Start (Local Development)

Follow these steps to set up the development environment on your local machine.

### Prerequisites
* Python 3.11 or higher.
* Docker Desktop (for the local database).
* `uv` tool installed (`pip install uv`).

### 1. Clone the repository
```bash
git clone [https://github.com/organization/health-without-borders.git](https://github.com/organization/health-without-borders.git)
cd health-without-borders

```

### 2. Configure Environment Variables

Create a `.env` file in the root directory based on the example:

```bash
cp .env.example .env

```

*Make sure to configure the `DATABASE_URL` to point to your local instance.*

### 3. Start the Database (Docker)

Run a temporary PostgreSQL instance:

```bash
docker run --name hwb-db-local \
    -e POSTGRES_PASSWORD=password \
    -e POSTGRES_DB=hwb_local \
    -p 5432:5432 \
    -d postgres:15

```

### 4. Install Dependencies and Initialize

```bash
# Install libraries
uv sync

# Create tables and admin user
uv run python scripts/create_tables.py
uv run python scripts/create_generic_user.py

# Load medical catalogs (This may take a few minutes)
uv run python scripts/load_catalogs.py

```

### 5. Run the Server

```bash
uv run uvicorn app.main:app --reload

```

The service will be available at: http://localhost:8000/docs

---

## 🧪 Testing

To run the automated test suite:

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
