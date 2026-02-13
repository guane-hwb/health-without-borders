# Local Development Environment Setup

This guide outlines the steps required to configure a local development environment for the **UNICEF Border Health Backend**. The setup utilizes Docker to containerize the database service, ensuring isolation from the host system and parity with the production environment.

## 1. Repository Setup

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/your-org/unicef-backend.git](https://github.com/your-org/unicef-backend.git)
    cd unicef-backend
    ```

2.  **Install Dependencies:**
    The project uses `uv` for fast dependency resolution and virtual environment management.
    ```bash
    uv sync
    ```
    *This command creates a `.venv` directory and installs all required packages defined in `pyproject.toml`.*

---

## 2. Environment Configuration

The application requires specific environment variables to function.

1.  **Create the Environment File:**
    Copy the example template to a new `.env` file.
    ```bash
    cp .env.example .env
    ```

2.  **Configure Variables:**
    Open `.env` and ensure the following values are set for local development. These credentials must match the database container configuration in the next step.

    ```ini
    # Database Connection (Local Docker)
    DATABASE_URL=postgresql://postgres:password@localhost:5432/unicef_local

    # Security (Insecure key for development only)
    SECRET_KEY=dev_secret_key_change_in_production
    ALGORITHM=HS256
    ACCESS_TOKEN_EXPIRE_MINUTES=1440

    # General
    PROJECT_NAME="UNICEF Local Dev"
    DEBUG=True
    ```

---

## 3. Database Setup (Docker)

To avoid installing PostgreSQL directly on the host operating system, a ephemeral Docker container is used.

1.  **Start the Database Container:**
    Run the following command to spin up a PostgreSQL 15 instance.

    ```bash
    docker run --name unicef-db-local \
        -e POSTGRES_USER=postgres \
        -e POSTGRES_PASSWORD=password \
        -e POSTGRES_DB=unicef_local \
        -p 5432:5432 \
        -d postgres:15
    ```

    * **`-p 5432:5432`**: Maps the container's port to your machine's localhost.
    * **`-d`**: Runs the container in detached mode (background).

2.  **Verify Status:**
    Ensure the container is running:
    ```bash
    docker ps
    ```

---

## 4. Data Initialization

A fresh database container starts empty. The schema must be created and initial data seeded.

1.  **Create Database Tables:**
    Generates the schema based on SQLAlchemy models.
    ```bash
    uv run python scripts/create_tables.py
    ```

2.  **Create Admin User:**
    Creates a default superuser for testing authentication.
    ```bash
    uv run python scripts/create_generic_user.py
    ```
    * **Default Credentials:** `medico@unicef.org` / `salud_frontera`

3.  **Load Medical Catalogs:**
    Populates the database with ICD-10 (Diagnoses) and CVX (Vaccines) codes.
    ```bash
    uv run python scripts/load_catalogs.py
    ```

---

## 5. Running the Application

Start the development server with hot-reloading enabled.

```bash
uv run uvicorn app.main:app --reload

```

* **API Root:** `http://localhost:8000`
* **Interactive Documentation (Swagger UI):** `http://localhost:8000/docs`

---

## 6. Testing

To execute the automated test suite:

```bash
uv run pytest

```

---

## 7. Troubleshooting

* **Database Connection Refused:**
* Ensure the Docker container is running (`docker ps`).
* Verify that the `DATABASE_URL` in `.env` matches the credentials used in the `docker run` command.
* Check if port 5432 is occupied by another local Postgres installation.


* **Module Not Found:**
* Ensure you are running commands with `uv run ...` to utilize the virtual environment.
* Run `uv sync` to ensure all dependencies are up to date.
```