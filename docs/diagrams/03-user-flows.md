# 03 — User flows (API call flows per caller)

service นี้ไม่มี UI; "user flow" คือลำดับ API call ของแต่ละ caller

## Producer: enqueue แล้วติดตามผล

```mermaid
flowchart TD
  A["Get token from identity with jobs:write and jobs:read"] --> B["POST /v1/jobs, header Idempotency-Key, body queue job_type payload run_at max_attempts"]
  B -->|"401"| B1["Missing or invalid bearer"]
  B -->|"403"| B2["Scope missing"]
  B -->|"422"| B3["Body or domain bound failed"]
  B -->|"409"| B4["Key reused with other payload, or 10000 pending reached"]
  B -->|"201"| C["Job JSON without lease_token, fingerprint, idempotency_key"]
  C --> D["GET /v1/jobs/id"]
  D -->|"queued or running"| D
  D -->|"succeeded"| E["Read result"]
  D -->|"dead"| F["GET /v1/jobs/id/attempts, read error_code per attempt"]
  C --> G["Optional POST /v1/jobs/id/cancel"]
  G -->|"409"| G1["Job already succeeded or dead"]
  G -->|"200"| G2["status=cancelled, repeat call returns same row"]
```

Sources: `src/platform_workflow/app.py`, `src/platform_workflow/store.py`, `docs/RUNTIME.md` (endpoint table)

## Worker: poll loop ของ CLI worker

```mermaid
flowchart TD
  S["platform-workflow worker"] --> T{"Token source"}
  T -->|"WORKFLOW_WORKER_CLIENT_ID + PRIVATE_KEY_PEM"| T1["ServiceTokenSource: POST issuer/v1/token, scope jobs:work"]
  T -->|"WORKFLOW_WORKER_AUTHORIZATION"| T2["Fixed bearer value"]
  T1 --> V["Verify token before every poll"]
  T2 --> V
  V -->|"ServiceTokenError"| W30["Log worker token unavailable, wait 30 s"]
  W30 --> V
  V -->|"verified principal"| C["JobStore.claim on WORKFLOW_WORKER_QUEUES"]
  C -->|"no job"| W1["Wait 1 s"]
  W1 --> V
  C -->|"job"| H["Run handler health.noop, heartbeat renews lease"]
  H --> R{"Outcome"}
  R -->|"ok"| OK["complete"]
  R -->|"error"| FL["fail handler_failed or unknown_handler"]
  R -->|"lease lost"| LL["Skip ack"]
  OK --> V
  FL --> V
  LL --> V
```

CLI worker เป็น diagnostic fixture: handler มีแค่ `health.noop` (`cli.py`); product จริงต้อง embed `Worker` พร้อม handler registry ของตัวเอง
worker เรียก `JobStore` ตรงผ่าน database ไม่ได้เรียก HTTP `/v1/worker/*` (endpoint HTTP ใช้สำหรับ worker ภายนอกที่ทำ protocol เดียวกัน)

Sources: `src/platform_workflow/cli.py`, `src/platform_workflow/worker.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/service_token.py`, `docs/RUNTIME.md` ("Workers")

## Operator: release step

```mermaid
flowchart LR
  M["platform-workflow migrate with migration role DSN"] --> A["platform-workflow api, PORT 8080"]
  A --> R["GET /readyz checks tables, privileges, FORCE RLS"]
  R -->|"200 ready"| LIVE["Serve traffic"]
  R -->|"503 unavailable"| FIX["Missing migration or grants"]
```

Sources: `docs/RUNBOOK.md`, `src/platform_workflow/cli.py`, `src/platform_workflow/app.py` (`readyz`), `railway.toml`

## Open questions

- ยังไม่มี caller จริงของ `/v1/jobs` ใน repo (ไม่มี consumer adapter); ใครคือ producer รายแรกหลัง cutover
- worker service client ยังไม่ได้ตั้ง ("The workflow worker token" อยู่ใน owner follow-ups ของ ops record) — จะ deploy worker เป็น service แยกเมื่อไร
