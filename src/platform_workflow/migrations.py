"""Service-owned PostgreSQL migrations. Runtime roles must not bypass RLS."""
from platform_backend_core import Migration

SQL = """
CREATE TABLE workflow_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL,
    operation text NOT NULL DEFAULT 'enqueue' CHECK (operation='enqueue'),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    fingerprint text NOT NULL,
    queue text NOT NULL,
    job_type text NOT NULL,
    payload jsonb NOT NULL CHECK (octet_length(payload::text)<=65536),
    run_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 100),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 100),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','dead','cancelled')),
    lease_owner text,
    lease_token uuid,
    lease_until timestamptz,
    result jsonb CHECK (octet_length(result::text)<=16384),
    error_code text CHECK (length(error_code)<=80),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(workspace_id,operation,idempotency_key),
    UNIQUE(workspace_id,id),
    CHECK ((status='running') = (lease_until IS NOT NULL))
);
CREATE INDEX workflow_jobs_poll ON workflow_jobs(workspace_id,queue,status,run_at,created_at,id);
CREATE TABLE workflow_attempts (
    workspace_id uuid NOT NULL,
    job_id uuid NOT NULL,
    attempt integer NOT NULL,
    lease_token uuid NOT NULL,
    owner text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    outcome text CHECK(outcome IN ('succeeded','failed','expired','cancelled')),
    error_code text,
    PRIMARY KEY(workspace_id,job_id,attempt),
    FOREIGN KEY(workspace_id,job_id) REFERENCES workflow_jobs(workspace_id,id)
);
ALTER TABLE workflow_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_jobs_workspace ON workflow_jobs
USING (workspace_id=NULLIF(current_setting('app.workspace_id',true),'')::uuid)
WITH CHECK (workspace_id=NULLIF(current_setting('app.workspace_id',true),'')::uuid);
ALTER TABLE workflow_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_attempts_workspace ON workflow_attempts
USING (workspace_id=NULLIF(current_setting('app.workspace_id',true),'')::uuid)
WITH CHECK (workspace_id=NULLIF(current_setting('app.workspace_id',true),'')::uuid);
"""


def migrations():
    return [Migration('001_workflow', SQL)]
