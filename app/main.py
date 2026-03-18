from fastapi import FastAPI
from app.core.config import settings
from app.api.v1.api import api_router
from app.core.logging import setup_logging

setup_logging()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Backend Health Without Borders Project - Open Source",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Include all API routers
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/health-check", tags=["Health"])
def health_check():
    """
    Simple health check to verify the service is running.
    """
    return {"status": "ok", "environment": "production" if not settings.DEBUG else "development"}


if __name__ == "__main__":
    import uvicorn
    # In production, we run this via CLI, not main
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)