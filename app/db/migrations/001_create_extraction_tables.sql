-- ==============================================================================
-- Auto-Data-Extractor Microservice Schema Migration 001
-- Creates audit/deduplication job log and configurable selective extraction rules
-- Compatible with existing RestoOrbit PostgreSQL database (resto-orbit-test)
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Extraction Jobs & Email Ingestion Audit Log
CREATE TABLE IF NOT EXISTS extractor_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id UUID NULL REFERENCES organizations(id) ON DELETE SET NULL,
  store_id UUID NULL REFERENCES stores(id) ON DELETE SET NULL,
  source_type VARCHAR(30) NOT NULL CHECK (source_type IN ('email', 'upload', 'api')),
  email_message_id VARCHAR(500) NULL,
  email_uid VARCHAR(100) NULL,
  email_subject VARCHAR(500) NULL,
  email_sender VARCHAR(255) NULL,
  email_received_at TIMESTAMPTZ NULL,
  filename VARCHAR(500) NOT NULL,
  file_sha256 VARCHAR(64) NOT NULL,
  file_size_bytes INTEGER NOT NULL DEFAULT 0,
  report_type VARCHAR(100) NOT NULL,
  report_date DATE NULL,
  shift_type VARCHAR(50) NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'extracted', 'imported', 'skipped_duplicate', 'failed')),
  extracted_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  mapped_shift_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  target_daily_shift_id UUID NULL REFERENCES daily_shifts(id) ON DELETE SET NULL,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_message TEXT NULL,
  processing_ms INTEGER NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_extractor_jobs_sha256
  ON extractor_jobs (file_sha256);

CREATE INDEX IF NOT EXISTS idx_extractor_jobs_org_status
  ON extractor_jobs (organization_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_extractor_jobs_report_date
  ON extractor_jobs (report_type, report_date DESC);

CREATE INDEX IF NOT EXISTS idx_extractor_jobs_email_msg_id
  ON extractor_jobs (email_message_id)
  WHERE email_message_id IS NOT NULL;

-- 2. Dynamic Selective Extraction Rules (Extensibility for new Back-Office PDFs)
CREATE TABLE IF NOT EXISTS extractor_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id UUID NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  report_key VARCHAR(100) NOT NULL,
  match_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
  sender_pattern VARCHAR(255) NULL,
  subject_pattern VARCHAR(255) NULL,
  default_shift_type VARCHAR(50) NULL CHECK (default_shift_type IN ('Shift 1', 'Shift 2')),
  field_mappings JSONB NOT NULL DEFAULT '[]'::jsonb,
  is_active SMALLINT NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_extractor_rules_key_org_active
  ON extractor_rules (COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid), report_key)
  WHERE is_active = 1;

-- Trigger for auto-updating updated_at if helper function exists
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'set_updated_at_timestamp') THEN
    DROP TRIGGER IF EXISTS trg_extractor_jobs_updated_at ON extractor_jobs;
    CREATE TRIGGER trg_extractor_jobs_updated_at
      BEFORE UPDATE ON extractor_jobs
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at_timestamp();

    DROP TRIGGER IF EXISTS trg_extractor_rules_updated_at ON extractor_rules;
    CREATE TRIGGER trg_extractor_rules_updated_at
      BEFORE UPDATE ON extractor_rules
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at_timestamp();
  END IF;
END $$;
