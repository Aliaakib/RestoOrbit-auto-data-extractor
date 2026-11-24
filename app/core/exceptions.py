"""Domain exceptions and centralized FastAPI exception handlers."""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class ExtractorError(Exception):
    """Base application error carrying an HTTP status code and machine-readable code."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        code: str = "EXTRACTOR_ERROR",
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details


class BadRequestError(ExtractorError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message, status.HTTP_400_BAD_REQUEST, "BAD_REQUEST", details)


class UnauthorizedError(ExtractorError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message, status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED")


class ForbiddenError(ExtractorError):
    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__(message, status.HTTP_403_FORBIDDEN, "FORBIDDEN")


class NotFoundError(ExtractorError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status.HTTP_404_NOT_FOUND, "NOT_FOUND")


class UnsupportedReportError(ExtractorError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(
            message,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "UNSUPPORTED_REPORT_FORMAT",
            details,
        )


class ConflictError(ExtractorError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message, status.HTTP_409_CONFLICT, "CONFLICT", details)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach consistent JSON error responses to the FastAPI application."""

    @app.exception_handler(ExtractorError)
    async def handle_extractor_error(request: Request, exc: ExtractorError) -> JSONResponse:
        logger.warning(
            "domain_exception",
            code=exc.code,
            status_code=exc.status_code,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": exc.errors(),
                },
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal error occurred",
                },
            },
        )
