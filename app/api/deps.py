"""FastAPI dependency injection helpers for authentication, settings, and services."""

from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import AuthContext, decode_jwt_token, verify_service_key
from app.db.pool import DatabasePool
from app.services.email_ingestor import EmailIngestor
from app.services.extraction_pipeline import ExtractionPipeline
from app.services.scheduler import MailboxPollingScheduler


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_pool(request: Request) -> DatabasePool:
    return request.app.state.db_pool


def get_pipeline(request: Request) -> ExtractionPipeline:
    return request.app.state.pipeline


def get_ingestor(request: Request) -> EmailIngestor:
    return request.app.state.ingestor


def get_scheduler(request: Request) -> MailboxPollingScheduler:
    return request.app.state.scheduler


async def get_auth_context(
    authorization: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    settings: Settings = Depends(get_app_settings),
) -> AuthContext:
    """Authenticate caller via RestoOrbit JWT Bearer token or internal X-Service-Key."""
    if x_service_key:
        return verify_service_key(x_service_key, settings)

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return decode_jwt_token(token, settings)

    raise UnauthorizedError("Missing Bearer JWT token or X-Service-Key header")


async def require_manager_or_above(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Restrict state-changing extraction/rule operations to authorized roles."""
    allowed = {"admin", "superadmin", "manager", "system", "owner"}
    if auth.role.lower() not in allowed:
        raise ForbiddenError(
            f"Role '{auth.role}' is not permitted to perform this extraction action"
        )
    return auth
