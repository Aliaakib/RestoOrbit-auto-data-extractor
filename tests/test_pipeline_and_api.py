"""API and pipeline integration tests using FastAPI TestClient with mocked DB pool."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.fixtures.pdf_factory import build_kitchen_speed_pdf, build_sales_and_labor_pdf


def _make_test_app():
    settings = Settings(
        ENV="test",
        JWT_SECRET="test_jwt_secret_key_2026_restoorbit",
        INTERNAL_API_KEY="test_internal_service_key",
        IMAP_ENABLED=False,
    )
    app = create_app(custom_settings=settings)
    return app, settings


def _make_jwt(settings: Settings, role: str = "manager") -> str:
    return jwt.encode(
        {
            "id": str(uuid4()),
            "organizationId": str(uuid4()),
            "role": role,
            "email": "manager@restoorbit.com",
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def test_health_live_and_auth_guard():
    app, _ = _make_test_app()
    client = TestClient(app)

    live = client.get("/api/v1/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "alive"

    unauth = client.get("/api/v1/extract/supported-reports")
    assert unauth.status_code == 401


def test_upload_extract_dry_run_sales_and_kitchen_reports():
    app, settings = _make_test_app()
    token = _make_jwt(settings, role="admin")

    # Replace db_pool with lightweight mock so tests run without live Postgres
    mock_db = MagicMock()
    mock_db.health_check = AsyncMock(return_value={"connected": False})
    mock_db.close = AsyncMock()
    app.state.db_pool = mock_db

    client = TestClient(app)

    # 1. Sales and Labor PDF dry-run upload
    sales_pdf = build_sales_and_labor_pdf()
    res1 = client.post(
        "/api/v1/extract/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("sales_labor.pdf", sales_pdf, "application/pdf")},
        data={"auto_import": "false"},
    )
    assert res1.status_code == 200
    body1 = res1.json()["data"]
    assert body1["report_type"] == "sales_and_labor_by_hour"
    assert body1["extraction"]["mapped_shift"]["net_sales"] == 3754.12
    assert body1["extraction"]["mapped_shift"]["labor_cost"] == 770.67
    assert body1["shift_import"]["action"] == "dry_run"

    # 2. Kitchen Speed of Service PDF batch dry-run upload
    kitchen_pdf = build_kitchen_speed_pdf()
    res2 = client.post(
        "/api/v1/extract/batch",
        headers={"X-Service-Key": settings.INTERNAL_API_KEY},
        files=[
            ("files", ("sales.pdf", sales_pdf, "application/pdf")),
            ("files", ("kitchen.pdf", kitchen_pdf, "application/pdf")),
        ],
        data={"auto_import": "false", "shift_type_override": "Shift 2"},
    )
    assert res2.status_code == 200
    batch_data = res2.json()["data"]
    assert batch_data["processed_count"] == 2
    assert batch_data["error_count"] == 0
    kitchen_result = batch_data["results"][1]
    assert kitchen_result["report_type"] == "kitchen_speed_of_service_by_hour"
    assert kitchen_result["extraction"]["mapped_shift"]["shift_minutes"] == 5
    assert kitchen_result["extraction"]["mapped_shift"]["shift_seconds"] == 39
    assert kitchen_result["extraction"]["store_code"] == "10912"


def test_upload_rejects_non_pdf_payload():
    app, settings = _make_test_app()
    token = _make_jwt(settings, role="admin")
    client = TestClient(app)

    res = client.post(
        "/api/v1/extract/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fake.pdf", b"not a real pdf", "application/pdf")},
        data={"auto_import": "false"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "BAD_REQUEST"
