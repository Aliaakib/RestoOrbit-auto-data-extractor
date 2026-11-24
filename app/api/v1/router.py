"""Root router aggregating all v1 endpoints."""

from fastapi import APIRouter

from app.api.v1.extract import router as extract_router
from app.api.v1.health import router as health_router
from app.api.v1.mailbox import router as mailbox_router
from app.api.v1.rules import router as rules_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(health_router)
v1_router.include_router(extract_router)
v1_router.include_router(mailbox_router)
v1_router.include_router(rules_router)
