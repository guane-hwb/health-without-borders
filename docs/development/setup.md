# Local Development Environment Setup



This guide outlines the steps required to configure an isolated local development environment for the **Health Without Borders API**. 

**Note:** This setup utilizes a local Docker container for the database. This ensures complete isolation from the host system and prevents accidental modifications to the production Cloud SQL databases described in `gcp-deploy.md`.

---

## 1. Repository Setup

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/](https://github.com/)<YOUR_ORG>/health-without-borders.git
    cd health-without-borders
    ```

2.  **Install Dependencies:**
    The project uses `uv` (by Astral) for ultra-fast dependency resolution and virtual environment management.
    ```bash
    uv sync
    ```
    *This command automatically creates a `.venv` directory and installs all required packages defined in `pyproject.toml`.*

---

## 2. Environment Configuration

The application requires specific environment variables to connect to the local Docker database.

1.  **Create the Environment File:**
    Copy the example template to create your local active `.env` file.
    ```bash
    cp .env.example .env
    ```

2.  **Configure Variables:**
    Open the `.env` file and ensure the following values are set. These credentials strictly map to the local Docker container we will create in the next step.

    ```ini
    # Database Connection (Local Docker)
    DATABASE_URL=postgresql://postgres:password@localhost:5432/hwb_local

    # Security (Insecure keys for local development ONLY)
    SECRET_KEY=dev_secret_key_change_in_production
    ALGORITHM=HS256
    ACCESS_TOKEN_EXPIRE_MINUTES=1440 # 24 hours

    # Vertex AI / LLM Configuration
    GCP_PROJECT_ID=migrants-unicef
    LLM_MODEL_NAME=gemini-1.5-pro-002

    # General
    PROJECT_NAME="HWB Local Dev"
    DEBUG=True
    ```

---

## 3. Database Setup (Docker)

To avoid installing PostgreSQL directly on your host operating system and managing versions, we spin up an ephemeral Docker container.

1.  **Start the Database Container:**
    Run the following command to initialize a local PostgreSQL 15 instance.

    ```bash
    docker run --name hwb-db-local \
        -e POSTGRES_USER=postgres \
        -e POSTGRES_PASSWORD=password \
        -e POSTGRES_DB=hwb_local \
        -p 5432:5432 \
        -d postgres:15
    ```

    * **`-p 5432:5432`**: Maps the container's internal Postgres port to your machine's localhost.
    * **`-d`**: Runs the container in detached mode (in the background).

2.  **Verify Status:**
    Ensure the container is running without errors:
    ```bash
    docker ps
    ```

---

## 4. Data Initialization (Local Seeding)

A fresh Docker database starts completely empty. You must create the schema and seed the initial data to test the API locally.

1.  **Create Database Tables:**
    Generates the relational schema based on the SQLAlchemy models.
    ```bash
    uv run python scripts/create_tables.py
    ```

2.  **Create an Admin User:**
    Creates a default superuser for testing JWT authentication in Swagger.
    ```bash
    uv run python scripts/create_generic_user.py
    ```
    * *(Check the script output or source code for the generated default login credentials, e.g., `doctor@hwb.org`).*

3.  **Load Medical Catalogs:**
    Populates the local database with international standards (ICD-10 for Diagnoses and CVX for Vaccines).
    ```bash
    uv run python scripts/load_catalogs.py
    ```

---

## 5. Running the Application

Start the local development server with hot-reloading enabled. Any changes made to the Python code will automatically restart the server.

```bash
uv run uvicorn app.main:app --reload

```

* **API Root:** `http://localhost:8000`
* **Interactive Documentation (Swagger UI):** `http://localhost:8000/docs`

---

## 6. Testing

It is mandatory to run the test suite before submitting a Pull Request.

```bash
uv run pytest

```

---

## 7. Troubleshooting

* **`Database Connection Refused` or `OperationalError`:**
* Ensure the Docker container is actively running (`docker ps`).
* Verify that the `DATABASE_URL` in your `.env` exactly matches the credentials and database name (`hwb_local`) used in the `docker run` command.
* Check if port `5432` is already occupied by a native PostgreSQL installation on your machine. If so, stop the native service or map Docker to a different port (e.g., `-p 5433:5432`).


* **`ModuleNotFoundError`:**
* Ensure you are prefixing all commands with `uv run ...` to execute them within the project's virtual environment.
* Run `uv sync` again to ensure all dependencies are properly downloaded.