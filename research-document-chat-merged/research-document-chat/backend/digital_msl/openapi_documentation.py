"""OpenAPI security metadata for Digital MSL and repository routes."""
from __future__ import annotations

# Paths that must NOT require Bearer auth in OpenAPI (matches app middleware / route design).
PUBLIC_PATH_PREFIXES = (
    "/api/v1/digital-msl/portal/login",
    "/api/v1/digital-msl/portal/register",
    "/api/v1/digital-msl/portal/register/options",
    "/api/v1/digital-msl/config",
    "/api/v1/digital-msl/ops/status",
    "/api/v1/digital-msl/channels/email/inbound",
    "/api/v1/digital-msl/channels/whatsapp/inbound",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)

PROTECTED_PATH_PREFIXES = (
    "/api/v1/digital-msl/",
    "/api/v1/repositories/",
    "/api/v1/documents/",
)

SECURITY_SCHEME = {
    "DMSLBearer": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "session_id",
        "description": "Portal login session_id returned from POST /api/v1/digital-msl/portal/login as access_token.",
    }
}


def path_requires_bearer(path: str, method: str) -> bool:
    if method.upper() == "OPTIONS":
        return False
    for public in PUBLIC_PATH_PREFIXES:
        if path == public or path.startswith(public + "/"):
            return False
    for prefix in PROTECTED_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def apply_openapi_security(schema: dict) -> dict:
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {}).update(SECURITY_SCHEME)
    paths = schema.get("paths") or {}
    for path, operations in paths.items():
        for method, op in operations.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            if path_requires_bearer(path, method):
                op["security"] = [{"DMSLBearer": []}]
                desc = (op.get("description") or "").strip()
                extra = "Requires valid portal Bearer session. Repository-scoped routes also enforce role and assignment server-side."
                op["description"] = f"{desc}\n\n{extra}".strip() if desc else extra
            else:
                op.pop("security", None)
    schema.setdefault("info", {})["x-dmsl-auth"] = (
        "Digital MSL uses session_id as Bearer token. Legacy repository routes accept the same Bearer token; "
        "RESEARCH_CHAT_ALLOW_DEV_AUTH may allow unauthenticated local dev only when explicitly enabled."
    )
    return schema
