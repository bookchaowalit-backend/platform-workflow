# 20 — Observability

```mermaid
flowchart LR
  subgraph SVC["platform-workflow API"]
    BND["Core _Boundary"]
    CAP["CaptureErrors"]
    RZ["/readyz"]
    MET["/metrics counters"]
    LOG["stderr log: request route status correlation_id"]
    SLOG["stderr: sentry enabled for service"]
  end
  subgraph WRK["Worker CLI"]
    WLOG["stderr: worker token unavailable"]
    WCAP["gap: no capture() in worker.py or cli.py; handler_failed only in attempt history"]
  end
  BND --> MET
  BND --> LOG
  CAP -->|"scrubbed event, handled false"| SEN["Sentry project platform-workflow"]
  WCAP --> SEN
  LOG --> RLOG["Railway logs"]
  SLOG --> RLOG
  WLOG --> RLOG
  UPT["platform-uptime-worker, hourly"] -->|"GET"| RZ
  RAIL["Railway healthcheck"] -->|"GET at deploy"| RZ
  MET -.->|"no scraper configured"| NONE["not collected"]
  DB[("workflow_attempts: per-attempt history")] -->|"GET /v1/jobs/id/attempts"| OPS["Operator with workspace token"]
```

Sources: `src/platform_workflow/_sentry.py`, `src/platform_workflow/app.py`, `src/platform_workflow/cli.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/http.py` (`_LOG.info`, metrics), `.railway/railway.ts`, `docs/RUNBOOK.md`, ops record: book-platforms-status (2026-11-03) (uptime worker hourly `/readyz`, Sentry project, "Observability repo is an instrumentation library ... owner decision pending (a scraper would keep sleeping services awake)")

## What is watched

| Signal | Collected by | Alert |
|---|---|---|
| `/readyz` | platform-uptime-worker hourly (sleeping set) | per uptime worker config (outside repo) |
| Unhandled exceptions (API 500, worker) | Sentry `platform-workflow` | Sentry alert rules (outside repo) |
| HTTP counters `/metrics` | none | none |
| Request log with correlation id | Railway logs | none |
| Queue age, dead-letter growth, connection exhaustion | **not collected** | RUNBOOK says "Alert on" these, but no collector exists; needs per-workspace polls |

ไม่มี PostHog, ไม่มี heartbeat ใน repo นี้

## Open questions

- alert ของ uptime worker และ Sentry ส่งไปช่องไหน (Telegram?) — ไม่อยู่ใน repo
- จะวัด queue age / dead-letter อย่างไรโดยไม่ปลุก service ที่ sleep (เกี่ยวกับ owner decision เรื่อง observability)
