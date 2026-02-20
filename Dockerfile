FROM python:3.11-slim

# 1. Instalar uv (La forma oficial y más rápida)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 2. Configurar el directorio de trabajo
WORKDIR /app

# UV_COMPILE_BYTECODE=1: Compila a .pyc para un inicio más rápido
# UV_LINK_MODE=copy: Copia archivos en lugar de enlaces simbólicos (mejor para Docker)
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Actualizar repositorios, aplicar parches de seguridad (upgrade) e instalar dependencias
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copiar SOLO los archivos de dependencias primero (para aprovechar el caché de Docker)
COPY pyproject.toml uv.lock ./

# Instalar dependencias usando uv
# --frozen: Falla si el lockfile no coincide (seguridad para producción)
# --no-dev: No instala pytest ni herramientas de desarrollo
# --no-install-project: Solo instala las librerías
RUN uv sync --frozen --no-dev --no-install-project

# Copiar el resto del código
COPY . .

# Sincronizar el proyecto final
RUN uv sync --frozen --no-dev

# 9. Agregar el entorno virtual al PATH
# Esto hace que al escribir 'uvicorn' use el del entorno virtual automáticamente
ENV PATH="/app/.venv/bin:$PATH"

# 10. Comando de ejecución
# Cloud Run inyecta la variable $PORT automáticamente
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]