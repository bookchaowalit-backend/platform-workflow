# 06 — C4 Container

image เดียว (`Dockerfile`) รันได้ 3 command: `api` (default CMD และ `railway.toml` startCommand), `worker`, `migrate`

```mermaid
C4Container
  title platform-workflow - containers
  System_Ext(producer, "Calling platform", "jobs:write / jobs:read")
  System_Ext(identity, "platform-identity", "JWKS and /v1/token")
  System_Ext(sentry, "Sentry", "Errors only")
  System_Boundary(b, "platform-workflow (Railway project, one image)") {
    Container(api, "API service", "Python 3.12, FastAPI, uvicorn", "platform-workflow api on PORT 8080; /v1/jobs, /v1/worker, /healthz, /readyz, /metrics")
    Container(worker, "Worker service", "Python 3.12 CLI", "platform-workflow worker; one workspace per process; staged, token not issued")
    Container(migrate, "Migrate one-off", "Python 3.12 CLI", "platform-workflow migrate; MigrationRunner namespace workflow")
  }
  ContainerDb(db, "PostgreSQL (Neon)", "PostgreSQL", "workflow_jobs, workflow_attempts, platform_schema_migrations; FORCE RLS")
  Rel(producer, api, "HTTPS JSON", "Bearer JWT")
  Rel(api, identity, "JWKS fetch", "HTTPS")
  Rel(worker, identity, "client_credentials token", "HTTPS")
  Rel(api, db, "Pool max WORKFLOW_POOL_MAX=8", "runtime role NOBYPASSRLS")
  Rel(worker, db, "Pool max 2", "runtime role NOBYPASSRLS")
  Rel(migrate, db, "DDL", "migration role")
  Rel(api, sentry, "Events", "HTTPS")
  Rel(worker, sentry, "capture", "HTTPS")
```

Sources: `Dockerfile`, `railway.toml`, `src/platform_workflow/cli.py`, `src/platform_workflow/migrations.py`, `docs/RUNBOOK.md`, `docs/RUNTIME.md` ("Container image", "Workers"), `platform-backend-core@dc1fa7d:src/platform_backend_core/migrations.py` (`platform_schema_migrations`), ops record: book-platforms-status (2026-11-03) ("The workflow worker token" in owner follow-ups)

## Open questions

- worker และ migrate รันเป็น Railway service/one-off จริงหรือไม่ — ops record บอกเฉพาะ `workflow-api-production-63de`
- `WORKFLOW_POOL_MAX` ใน production ตั้งไว้เท่าไร และงบ connection ของ Neon free project เท่าไร
