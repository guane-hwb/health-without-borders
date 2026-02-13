# Salud Sin Fronteras - Backend

![Python Version](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-009688.svg)
![Docker](https://img.shields.io/badge/Docker-Available-2496ED.svg)
![GCP](https://img.shields.io/badge/Google_Cloud-Run-4285F4.svg)
![License](https://img.shields.io/badge/license-MIT-green)

**Plataforma Interoperable de Historias Médicas para Niños, Niñas y Adolescentes Migrantes.**

Este repositorio contiene el código fuente del **Backend**, una API RESTful desarrollada para UNICEF Colombia. Su objetivo es garantizar la continuidad de la atención médica de población migrante mediante un sistema seguro, estandarizado (HL7/FHIR) y resiliente a condiciones de conectividad intermitente en zonas fronterizas.

---

## 🚀 Características Principales

* **Arquitectura Serverless:** Desplegado en Google Cloud Run para escalabilidad automática y eficiencia de costos.
* **Seguridad Robusta:** Autenticación vía JWT (JSON Web Tokens) con rotación segura y encriptación de contraseñas con Bcrypt.
* **Estandarización Médica:** Catálogos internacionales integrados (CIE-10 para diagnósticos y CVX para vacunación).
* **Offline-First:** Endpoints optimizados para sincronización de datos desde dispositivos móviles con conectividad limitada.
* **Gestión de Roles:** Sistema RBAC (Role-Based Access Control) para administradores y personal médico.

---

## 🛠️ Stack Tecnológico

* **Lenguaje:** Python 3.11+
* **Framework Web:** FastAPI
* **Base de Datos:** PostgreSQL 15 (Cloud SQL en Producción)
* **ORM:** SQLAlchemy 2.0
* **Gestor de Paquetes:** `uv` (Astral)
* **Infraestructura:** Docker, Google Artifact Registry, Google Cloud Build.

---

## 📚 Documentación

La documentación detallada del proyecto se encuentra organizada en la carpeta `docs/`:

* [**Guía de Infraestructura y Despliegue**](docs/infrastructure/gcp-deploy.md): Instrucciones paso a paso para desplegar en Google Cloud Platform.
* [**Arquitectura de Base de Datos**](docs/infrastructure/database.md): Modelado de datos y diccionario de tablas.
* [**Seguridad**](docs/infrastructure/security.md): Protocolos de autenticación y manejo de datos sensibles.

---

## ⚡ Quick Start (Desarrollo Local)

Siga estos pasos para levantar el entorno de desarrollo en su máquina local.

### Prerrequisitos
* Python 3.11 o superior.
* Docker Desktop (para la base de datos local).
* Herramienta `uv` instalada (`pip install uv`).

### 1. Clonar el repositorio
```bash
git clone [https://github.com/organizacion/unicef-backend.git](https://github.com/organizacion/unicef-backend.git)
cd unicef-backend

```

### 2. Configurar Variables de Entorno

Cree un archivo `.env` en la raíz basado en el ejemplo:

```bash
cp .env.example .env

```

*Asegúrese de configurar `DATABASE_URL` apuntando a su instancia local.*

### 3. Levantar Base de Datos (Docker)

Ejecute una instancia temporal de PostgreSQL:

```bash
docker run --name unicef-db-local \
    -e POSTGRES_PASSWORD=password \
    -e POSTGRES_DB=unicef_local \
    -p 5432:5432 \
    -d postgres:15

```

### 4. Instalar Dependencias e Inicializar

```bash
# Instalar librerías
uv sync

# Crear tablas y usuario administrador
uv run python scripts/create_tables.py
uv run python scripts/create_generic_user.py

# Cargar catálogos médicos (Puede tardar unos minutos)
uv run python scripts/load_catalogs.py

```

### 5. Ejecutar Servidor

```bash
uv run uvicorn app.main:app --reload

```

El servicio estará disponible en: [http://localhost:8000/docs](https://www.google.com/search?q=http://localhost:8000/docs)

---

## 🧪 Testing

Para ejecutar la suite de pruebas automatizadas:

```bash
uv run pytest

```

---

## 🤝 Contribución

Este proyecto sigue estándares estrictos de desarrollo. Antes de enviar un Pull Request, asegúrese de:

1. Seguir la guía de estilo PEP-8.
2. No subir credenciales o secretos al repositorio.
3. Documentar cualquier endpoint nuevo en Swagger.

Para más detalles, lea [CONTRIBUTING.md](https://www.google.com/search?q=CONTRIBUTING.md).

---

## 📄 Licencia

Este proyecto está bajo la Licencia MIT - ver el archivo [LICENSE](https://www.google.com/search?q=LICENSE) para más detalles.

---

**Desarrollado para UNICEF Colombia - 2026**
```