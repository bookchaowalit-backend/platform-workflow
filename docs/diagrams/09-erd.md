# 09 — ERD

schema มาจาก migration เดียว `001_workflow` (`src/platform_workflow/migrations.py`) และตาราง ledger ของ core `MigrationRunner`
central [ERD-DIAGRAMS.md](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/ERD-DIAGRAMS.md) ไม่มีตารางของ service นี้

```mermaid
erDiagram
  workflow_jobs ||--o{ workflow_attempts : "has attempts"
  workflow_jobs {
    uuid id PK "gen_random_uuid()"
    uuid workspace_id "NOT NULL, RLS key"
    text operation "enqueue only"
    text idempotency_key "1..200 chars"
    text fingerprint "sha256 of canonical request"
    text queue
    text job_type
    jsonb payload "max 65536 bytes"
    timestamptz run_at
    int attempts "0..100"
    int max_attempts "1..100, default 3"
    text status "queued running succeeded dead cancelled"
    text lease_owner
    uuid lease_token
    timestamptz lease_until "NOT NULL iff running"
    jsonb result "max 16384 bytes"
    text error_code "max 80"
    timestamptz created_at
    timestamptz updated_at
  }
  workflow_attempts {
    uuid workspace_id PK "RLS key"
    uuid job_id PK "FK with workspace_id"
    int attempt PK
    uuid lease_token
    text owner
    timestamptz started_at
    timestamptz finished_at
    text outcome "succeeded failed expired cancelled"
    text error_code
  }
  platform_schema_migrations {
    text namespace PK "workflow"
    text version PK "001_workflow"
    text checksum "sha256 of SQL"
    timestamptz applied_at
  }
```

Sources: `src/platform_workflow/migrations.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/migrations.py`

## Constraints, indexes และ RLS

| Object | Rule |
|---|---|
| `workflow_jobs` | `UNIQUE(workspace_id, operation, idempotency_key)`, `UNIQUE(workspace_id, id)`, `CHECK ((status='running') = (lease_until IS NOT NULL))` |
| index `workflow_jobs_poll` | `(workspace_id, queue, status, run_at, created_at, id)` |
| `workflow_attempts` | PK `(workspace_id, job_id, attempt)`, FK `(workspace_id, job_id)` → `workflow_jobs(workspace_id, id)` |
| RLS | ทั้งสองตาราง `ENABLE` + `FORCE ROW LEVEL SECURITY`; policy `USING` และ `WITH CHECK` = `workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid` ไม่มี workspace = เห็น 0 แถว |
| Runtime role | NOSUPERUSER/NOBYPASSRLS; schema USAGE + SELECT/INSERT/UPDATE บนสองตาราง; ไม่มี DDL, ไม่มี DELETE (`docs/RUNBOOK.md`); core ปฏิเสธ role ที่ bypass ได้ |
| Migration | advisory lock + checksum; ห้ามแก้ checksum หรือ drop table ตอน rollback |

Sources: `src/platform_workflow/migrations.py`, `docs/RUNBOOK.md`, `src/platform_workflow/app.py` (`readyz` privilege query), `platform-backend-core@dc1fa7d:src/platform_backend_core/db.py`

## Open questions

- ชื่อ runtime role ใน Neon `platform-workflow` (ops record ระบุชื่อ role เฉพาะของ security/payment)
- retention ของ terminal job และ idempotency key ยังไม่กำหนด (`docs/RUNTIME.md`: "requires an environment-specific retention plan")
