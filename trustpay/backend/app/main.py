from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.accounts import router as accounts_router
from app.api.beneficiaries import router as beneficiaries_router
from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.config import settings

app = FastAPI(title=settings.PROJECT_NAME, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix=settings.API_V1_PREFIX)
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(accounts_router, prefix=settings.API_V1_PREFIX)
app.include_router(beneficiaries_router, prefix=settings.API_V1_PREFIX)
app.include_router(payments_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": settings.PROJECT_NAME, "status": "ok"}
