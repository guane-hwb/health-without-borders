from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import limiter
from app.services.terminology import terminology

setup_logging()

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
    """
    Prepare the NFC keyring before serving traffic.

    Two things have to happen once, in this order: the environment's version 0
    is imported so the keyring lives entirely in the database, and the current
    version is unwrapped to prove the configured KEK is the right one. A wrong
    KEK would otherwise surface as devices receiving an empty ring and NFC
    quietly not working — far worse than refusing to start.

    With no ``NFC_KEK`` configured both steps are no-ops and the ring is served
    from the environment exactly as before.
    """
    if settings.NFC_KEK.strip():
        from app.db.session import SessionLocal
        from app.services.nfc_key_service import ensure_initialised, load_keyring

        db = SessionLocal()
        try:
            ensure_initialised(db)
            ring = load_keyring(db)
            if ring is not None and ring["current"] is None and ring["keys"]:
                raise RuntimeError(
                    "The NFC keyring has live keys but no usable current "
                    "version: devices could read but not write. Check "
                    "nfc_keyring_state."
                )
            if ring is not None and not ring["keys"]:
                raise RuntimeError(
                    "NFC_KEK is configured but no key could be unwrapped. "
                    "Either the KEK is not the one that wrapped these rows, or "
                    "the keyring is empty. Devices would receive no NFC key."
                )
        finally:
            db.close()
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Backend Health Without Borders Project - Open Source",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

terminology.load()

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