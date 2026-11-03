# 05 — C4 Context

ยังไม่มีภาพ C4 ของ service นี้ใน central architecture-docs ([C4-DIAGRAMS.md](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/C4-DIAGRAMS.md) ไม่ได้กล่าวถึง platform-workflow) จึงวาดใหม่

```mermaid
C4Context
  title platform-workflow - system context
  Person(owner, "Owner / operator", "Runs migrate, sets Railway env")
  System_Ext(producer, "Calling platform or service", "Holds jobs:write / jobs:read token")
  System_Ext(workerp, "Worker process", "Identity service client with jobs:work")
  System(wf, "platform-workflow", "Durable workspace-scoped jobs API and leased worker protocol")
  System_Ext(identity, "platform-identity", "Issuer, JWKS, /v1/token client_credentials")
  SystemDb_Ext(neon, "Neon PostgreSQL platform-workflow", "workflow_jobs, workflow_attempts")
  System_Ext(sentry, "Sentry platform-workflow", "Error events without free text")
  System_Ext(uptime, "platform-uptime-worker", "Hourly /readyz probe")
  System_Ext(hub, "Remote MCP hub", "Cell platform-workflow")
  Rel(producer, wf, "Enqueue, read, cancel", "HTTPS JSON + Bearer JWT")
  Rel(workerp, wf, "Claim, renew, complete, fail", "HTTPS or direct JobStore")
  Rel(hub, wf, "call_cell", "HTTPS + identity token")
  Rel(wf, identity, "Fetch JWKS", "HTTPS")
  Rel(workerp, identity, "Get jobs:work token", "HTTPS private_key_jwt")
  Rel(wf, neon, "SQL with app.workspace_id", "TLS")
  Rel(wf, sentry, "Unhandled exceptions", "HTTPS")
  Rel(uptime, wf, "GET /readyz", "HTTPS")
  Rel(owner, wf, "migrate, deploy", "railway CLI")
```

Sources: `src/platform_workflow/cli.py` (`verifier_from_env`, `worker_authorization_from_env`), `src/platform_workflow/app.py`, `src/platform_workflow/_sentry.py`, `docs/RUNBOOK.md`, `contract.json` (`depends_on`: identity, observability), ops record: book-platforms-status (2026-11-03) (Neon project `platform-workflow`, Sentry `platform-workflow`, uptime worker, MCP hub cell, identity issuer)

## Open questions

- edge gateway (`gateway.bookchaowalit.com`) มี route ไป workflow หรือไม่ — ops record ที่อ้างได้ไม่ได้ระบุ
- `observability` อยู่ใน `depends_on` แต่ไม่มีโค้ดเรียก observability platform (มีแค่ `/metrics` และ Sentry)
