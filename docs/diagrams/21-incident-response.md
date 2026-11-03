# 21 — Incident response

ขั้นตอน diagnose/recover จริงอยู่ใน [docs/RUNBOOK.md](../RUNBOOK.md) "Diagnose and recover" (reused); ภาพนี้ต่อจาก alert channel ที่พบจริง

```mermaid
flowchart TD
  A1["Uptime worker: /readyz not 200"] --> TRI
  A2["Sentry issue in platform-workflow"] --> TRI
  A3["Caller reports 401, 409, 5xx"] --> TRI
  TRI{"Triage"}
  TRI -->|"503 readiness"| R1["Check Neon resume, migration applied, runtime role grants, FORCE RLS"]
  TRI -->|"401 everywhere"| R2["Check JWT_ISSUER, JWT_AUDIENCE, JWKS reachable at identity"]
  TRI -->|"Sentry 500"| R3["Read type, stack, route; match x-request-id in Railway logs"]
  TRI -->|"Jobs stuck running"| R4["Worker loss: lease expires, next claim reclaims or dead-letters"]
  TRI -->|"Dead-letter growth"| R5["Read /v1/jobs/id/attempts with workspace token; reconcile provider effects"]
  R1 --> FIX
  R2 --> FIX
  R3 --> FIX
  R4 --> FIX
  R5 --> FIX
  FIX{"Fix path"}
  FIX -->|"config"| F1["railway variables, redeploy"]
  FIX -->|"code"| F2["Branch, PR, check.yml green, merge, railway up --ci"]
  FIX -->|"severe"| F3["Rollback: route consumers to task adapter, stop producers and workers, keep DB"]
  F1 --> V["Verify /readyz 200"]
  F2 --> V
  F3 --> V
  V --> PM["Postmortem: not defined in repo"]
```

Sources: `docs/RUNBOOK.md`, `src/platform_workflow/app.py`, `src/platform_workflow/store.py` (lease reclaim, dead-letter), `src/platform_workflow/_sentry.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/http.py` (`x-request-id`), ops record: book-platforms-status (2026-11-03) (uptime worker, Sentry project, deploy recipe)

ข้อจำกัดที่ RUNBOOK ระบุ: ไม่มี automated replay ของ dead-letter; ไม่มี endpoint cross-workspace สำหรับดู backlog รวม

## Open questions

- severity, on-call และ template postmortem ยังไม่กำหนดใน repo
- ปลายทาง alert ของ uptime worker และ Sentry
