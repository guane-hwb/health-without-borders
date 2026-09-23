import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.errors import ApiError
from app.core.logging import setup_logging
from app.core.nfc_startup import prepare_nfc_keyring_at_startup
from app.core.rate_limit import limiter
from app.core.request_limits import BodySizeLimitMiddleware
from app.services.terminology import terminology

setup_logging()

logger = logging.getLogger(__name__)

_nfc_keyring_errors = settings.nfc_keyring_errors()
if _nfc_keyring_errors:
    # Fail fast rather than serve a keyring that disables NFC on every device.
    # The messages never contain key material, only which variable is at fault.
    raise RuntimeError(
        "Invalid NFC key configuration:\n  - "
        + "\n  - ".join(_nfc_keyring_errors)
    )



@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare the NFC keyring before serving traffic."""
    prepare_nfc_keyring_at_startup()
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Backend Health Without Borders Project - Open Source",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

terminology.load()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Answer every unexpected error with a JSON 500 that carries no data.

    The error id ties the response to the log line, which records only the
    exception type — never its message, since database errors embed PHI.
    """
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        "Unhandled error error_id=%s method=%s route=%s type=%s",
        error_id,
        request.method,
        request.scope.get("route").path if request.scope.get("route") else "unknown",
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error_id": error_id},
    )


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """HTTPException plus a machine-readable ``code`` (see app.core.errors)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    FastAPI's default 422 echoes the offending ``input`` — for a model-level
    check on /sync that is the entire patient record. Keep type, location and
    message; drop the echoed data.
    """
    errors = [
        {key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

cors_origins = [origin.strip() for origin in settings.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

# Added last so it is the outermost user middleware: oversized bodies are
# refused before rate limiting, CORS or any body parsing.
app.add_middleware(BodySizeLimitMiddleware)

# Include all API routers
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/health-check", tags=["Health"])
def health_check():
    """
    Simple health check to verify the service is running.
    """
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    # In production, we run this via CLI, not main
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)