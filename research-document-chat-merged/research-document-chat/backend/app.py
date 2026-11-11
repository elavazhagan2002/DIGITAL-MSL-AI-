"""
Standalone Research Document Chat Backend Application Entry Point.
Provides endpoints for:
1. Medical Literature Search (PubMed & ClinicalTrials.gov)
2. Document Import to Repository & DMS Indexing
3. Repository Scoped RAG Chat with Citation Validation
4. Repository & Document Management
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from digital_msl import auth as dmsl_auth
from digital_msl import upstream
from digital_msl.deps import PortalContext
from digital_msl.openapi_documentation import apply_openapi_security, path_requires_bearer

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from core.config import get_settings
from models.database import init_db
from routes.medical_literature import router as medical_literature_router
from routes.chat import router as chat_router
from routes.repositories import router as repositories_router
from digital_msl import store as digital_msl_store
from digital_msl.router import router as digital_msl_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("research_chat")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from digital_msl.config import check_demo_guard

    check_demo_guard()  # refuse to start: demo mode in production
    logger.info("Initializing SQLite/PostgreSQL Database...")
    init_db()
    digital_msl_store.init_store()
    from digital_msl.config import get_dmsl_settings as _dmsl_settings

    _approved = _dmsl_settings().approved_repositories()
    if not _approved:
        logger.warning("DIGITAL_MSL_APPROVED_REPOSITORY_IDS is empty: Digital MSL will answer nothing and escalate every question until it is configured.")
    elif "*" in _approved:
        logger.warning("DIGITAL_MSL_APPROVED_REPOSITORY_IDS=* allows every repository (development / tests only).")
    logger.info("Database initialized successfully.")
    from digital_msl.config import get_dmsl_settings
    from digital_msl.mailbox import poll_forever

    mail_settings = get_dmsl_settings()
    stop_mail = asyncio.Event()
    mail_task = None
    if (mail_settings.imap_host or "").strip() and (mail_settings.imap_user or mail_settings.smtp_user):
        mail_task = asyncio.create_task(poll_forever(stop_mail))
    if (mail_settings.whatsapp_waba_id or "").strip() and (mail_settings.whatsapp_token or "").strip():
        from digital_msl.whatsapp_bridge import subscribe_app_to_waba

        ok, detail = subscribe_app_to_waba()
        logger.info("WhatsApp WABA subscribed_apps: ok=%s %s", ok, detail[:200] if detail else "")
    yield
    stop_mail.set()
    if mail_task is not None:
        mail_task.cancel()
    logger.info("Shutting down Research Document Chat backend.")


settings = get_settings()

app = FastAPI(
    title="Research Document Chat & RAG Engine",
    description="Standalone research connector, document ingestion, and RAG chat with evidence citations.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        # extra local dev origins (parallel dev servers / e2e runs)
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:5176",
        "http://127.0.0.1:5177",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(medical_literature_router)
app.include_router(chat_router)
app.include_router(repositories_router)
app.include_router(digital_msl_router)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    app.openapi_schema = apply_openapi_security(schema)
    return app.openapi_schema


app.openapi = custom_openapi

_DMSL = "/api/v1/digital-msl"
DMSL_PUBLIC_ROUTES = {
    ("GET", f"{_DMSL}/config"),
    ("GET", f"{_DMSL}/ops/status"),
    ("POST", f"{_DMSL}/portal/login"),
    ("POST", f"{_DMSL}/portal/login/mfa"),
    ("POST", f"{_DMSL}/portal/register"),
    ("GET", f"{_DMSL}/portal/register/options"),
    ("POST", f"{_DMSL}/portal/register/verify"),
    ("POST", f"{_DMSL}/portal/register/resend"),
    ("GET", f"{_DMSL}/portal/unsubscribe"),
    # MFA enrolment may run from a login step token (no session yet); the handlers authenticate themselves.
    ("POST", f"{_DMSL}/portal/mfa/setup"),
    ("POST", f"{_DMSL}/portal/mfa/enable"),
    ("POST", f"{_DMSL}/channels/email/inbound"),
    ("POST", f"{_DMSL}/channels/whatsapp/inbound"),
    ("GET", f"{_DMSL}/channels/whatsapp/webhook"),
    ("POST", f"{_DMSL}/channels/whatsapp/webhook"),
}

# Per-IP, per-route ceiling on the credential and verification endpoints.
DMSL_RATE_LIMITED = {
    f"{_DMSL}/portal/login",
    f"{_DMSL}/portal/login/mfa",
    f"{_DMSL}/portal/register",
    f"{_DMSL}/portal/register/verify",
    f"{_DMSL}/portal/register/resend",
    f"{_DMSL}/portal/unsubscribe",
    f"{_DMSL}/portal/mfa/setup",
    f"{_DMSL}/portal/mfa/enable",
    f"{_DMSL}/portal/mfa/disable",
}


@app.middleware("http")
async def digital_msl_portal_auth(request: Request, call_next):
    path = request.url.path
    if not path.startswith(_DMSL):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    from digital_msl import security
    from digital_msl.config import get_dmsl_settings

    ip = request.client.host if request.client else ""
    # Every audit entry written while serving this request records where it came from.
    digital_msl_store.REQUEST_META.set({"ip": ip, "ua": request.headers.get("user-agent", "")[:255]})
    if path in DMSL_RATE_LIMITED:
        allowed, retry_after = security.rate_limit(f"{ip}|{path}", get_dmsl_settings().auth_rate_limit_per_minute)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please wait and try again."},
                headers={"Retry-After": str(retry_after)},
            )
    key = (request.method.upper(), path)
    if key in DMSL_PUBLIC_ROUTES:
        return await call_next(request)
    try:
        row = dmsl_auth.context_from_request(request)
        request.state.dmsl_portal = PortalContext(
            session_id=row["session_id"],
            user_id=row["user_id"],
            role=row.get("role") or "",
        )
    except StarletteHTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


_CSP = (
    "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; "
    "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; script-src 'self'; connect-src 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if not path.startswith(("/docs", "/redoc", "/openapi.json")):  # Swagger UI loads assets from a CDN
        headers.setdefault("Content-Security-Policy", _CSP)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    if path.startswith(_DMSL):
        headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or upstream.new_request_id()
    token = upstream.request_id_var.set(rid[:64])
    try:
        response = await call_next(request)
    finally:
        upstream.request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid[:64]
    return response


@app.exception_handler(upstream.UpstreamUnavailable)
async def upstream_unavailable_handler(request: Request, exc: upstream.UpstreamUnavailable):
    return JSONResponse(status_code=503, content=upstream.error_body(exc), headers=exc.headers or {})


@app.get("/health")
async def health_check():
    """App health. IKM reachability is reported separately and never makes this endpoint fail."""
    ikm = await asyncio.to_thread(upstream.ikm_status)
    return {
        "status": "healthy",
        "app": "Research Document Chat",
        "llm_provider": settings.llm_provider,
        "ikm": ikm,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8087, reload=True)
