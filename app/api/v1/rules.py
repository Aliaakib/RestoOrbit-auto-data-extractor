"""CRUD endpoints for dynamic selective extraction rules."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_auth_context, get_db_pool, require_manager_or_above
from app.core.exceptions import NotFoundError
from app.core.security import AuthContext
from app.db.pool import DatabasePool
from app.models.schemas import CreateExtractionRuleRequest

router = APIRouter(prefix="/rules", tags=["Extraction Rules"])


@router.get("")
async def list_rules(
    auth: AuthContext = Depends(get_auth_context),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """List active custom extraction rules for the caller's organization."""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, organization_id, name, report_key, match_keywords,
                       sender_pattern, subject_pattern, default_shift_type,
                       field_mappings, is_active, created_at, updated_at
                FROM extractor_rules
                WHERE is_active = 1
                  AND (organization_id IS NULL OR organization_id = %s)
                ORDER BY created_at DESC
                """,
                (auth.organization_id,),
            )
            rows = await cur.fetchall()
    return {"success": True, "data": rows}


@router.post("")
async def create_or_update_rule(
    payload: CreateExtractionRuleRequest,
    auth: AuthContext = Depends(require_manager_or_above),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """Create a dynamic selective extraction rule for additional PDF report formats."""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO extractor_rules (
                  organization_id, name, report_key, match_keywords,
                  sender_pattern, subject_pattern, default_shift_type,
                  field_mappings, is_active
                ) VALUES (
                  %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, 1
                )
                ON CONFLICT (
                  COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid),
                  report_key
                ) WHERE is_active = 1
                DO UPDATE SET
                  name = EXCLUDED.name,
                  match_keywords = EXCLUDED.match_keywords,
                  sender_pattern = EXCLUDED.sender_pattern,
                  subject_pattern = EXCLUDED.subject_pattern,
                  default_shift_type = EXCLUDED.default_shift_type,
                  field_mappings = EXCLUDED.field_mappings,
                  updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (
                    auth.organization_id,
                    payload.name,
                    payload.report_key,
                    json.dumps(payload.match_keywords),
                    payload.sender_pattern,
                    payload.subject_pattern,
                    payload.default_shift_type,
                    json.dumps([m.model_dump() for m in payload.field_mappings]),
                ),
            )
            row = await cur.fetchone()
    return {"success": True, "data": row}


@router.delete("/{rule_id}")
async def deactivate_rule(
    rule_id: UUID,
    auth: AuthContext = Depends(require_manager_or_above),
    db_pool: DatabasePool = Depends(get_db_pool),
) -> dict:
    """Soft-deactivate a custom extraction rule."""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE extractor_rules
                SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND (organization_id IS NULL OR organization_id = %s)
                RETURNING id
                """,
                (str(rule_id), auth.organization_id),
            )
            row = await cur.fetchone()
    if not row:
        raise NotFoundError(f"Extraction rule '{rule_id}' not found")
    return {"success": True, "message": "Rule deactivated", "id": str(row["id"])}
