"""Security primitives: JWT verification, service API key validation, and PDF upload sanitization."""

import hmac
from dataclasses import dataclass
from typing import Any

import jwt

from app.core.config import Settings
from app.core.exceptions import BadRequestError, UnauthorizedError

PDF_MAGIC_HEADER = b"%PDF-"


@dataclass(frozen=True)
class AuthContext:
    """Authenticated caller identity resolved from JWT or internal service key."""

    subject: str
    auth_type: str  # "jwt" | "service_key"
    role: str
    organization_id: str | None = None
    store_id: str | None = None
    email: str | None = None
    claims: dict[str, Any] | None = None


def decode_jwt_token(token: str, settings: Settings) -> AuthContext:
    """Verify and decode an RS/HS JWT issued by resto-orbit-backend."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Authentication token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid authentication token") from exc

    user_id = str(payload.get("id") or payload.get("sub") or payload.get("userId") or "")
    if not user_id:
        raise UnauthorizedError("Token payload missing user identifier")

    org_id = payload.get("organizationId") or payload.get("organization_id")
    store_id = payload.get("assignedStoreId") or payload.get("store_id") or payload.get("storeId")

    return AuthContext(
        subject=user_id,
        auth_type="jwt",
        role=str(payload.get("role") or "user"),
        organization_id=str(org_id) if org_id else None,
        store_id=str(store_id) if store_id else None,
        email=payload.get("email"),
        claims=payload,
    )


def verify_service_key(provided_key: str, settings: Settings) -> AuthContext:
    """Constant-time verification of internal service-to-service API key."""
    if not hmac.compare_digest(provided_key, settings.INTERNAL_API_KEY):
        raise UnauthorizedError("Invalid internal service API key")

    return AuthContext(
        subject="internal-service",
        auth_type="service_key",
        role="system",
        organization_id=None,
        store_id=None,
    )


def validate_pdf_bytes(content: bytes, filename: str, max_bytes: int) -> None:
    """Validate uploaded/ingested PDF payload size, magic header, and filename."""
    if not content:
        raise BadRequestError(f"PDF file '{filename}' is empty")

    if len(content) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise BadRequestError(
            f"PDF file '{filename}' exceeds maximum allowed size of {max_mb} MB"
        )

    # Strip leading UTF-8 BOM or whitespace before checking %PDF- signature
    header_slice = content[:64].lstrip()
    if not header_slice.startswith(PDF_MAGIC_HEADER):
        raise BadRequestError(
            f"File '{filename}' is not a valid PDF document (missing %PDF- signature)"
        )
