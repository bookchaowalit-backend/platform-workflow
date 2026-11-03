# 02 — Use cases

actor คือสิ่งที่ service ตรวจจริง: JWT ที่ verify แล้ว (issuer, audience, `ws` workspace UUID) และ **exact scope** 3 ตัว `jobs:read`, `jobs:write`, `jobs:work`
ไม่มี owner/wildcard override (`docs/RUNTIME.md`) และ token ที่มี claim `del`/`obo` ถูกปฏิเสธ (core `TokenVerifier`)
endpoint ที่ไม่ต้อง auth คือ `/healthz`, `/readyz`, `/metrics`

```mermaid
flowchart LR
  subgraph Actors
    PROD["Producer: person or service token, scope jobs:write"]
    READ["Reader: token with jobs:read"]
    WORK["Worker: identity service client, scope jobs:work"]
    HUB["MCP hub: identity client mcp-hub, no jobs:work"]
    OPS["Operator: owner DB role and Railway env"]
    PROBE["Uptime probe and Railway healthcheck, no token"]
  end
  subgraph UC["platform-workflow use cases"]
    U1(["Enqueue job with Idempotency-Key"])
    U2(["Cancel job"])
    U3(["List jobs, keyset pagination"])
    U4(["Get job status and result"])
    U5(["Read attempt history"])
    U6(["Claim leased job"])
    U7(["Renew lease"])
    U8(["Complete job"])
    U9(["Fail job with safe error code"])
    U10(["Apply migrations"])
    U11(["Check liveness, readiness, metrics"])
  end
  PROD --> U1
  PROD --> U2
  READ --> U3
  READ --> U4
  READ --> U5
  WORK --> U6
  WORK --> U7
  WORK --> U8
  WORK --> U9
  HUB -.->|"owner scopes, exact set not in repo"| U3
  OPS --> U10
  PROBE --> U11
```

Sources: `src/platform_workflow/app.py`, `src/platform_workflow/store.py` (`_scope`), `src/platform_workflow/cli.py`, `docs/RUNTIME.md`, `platform-backend-core@dc1fa7d:src/platform_backend_core/http.py` (`require_scope`, `/healthz`, `/metrics`), `platform-backend-core@dc1fa7d:src/platform_backend_core/auth.py` (`TokenVerifier.verify`), ops record: book-platforms-status (2026-11-03) (MCP hub cell, "no jobs:work")

## Scope matrix

| Use case | Scope | ตรวจที่ |
|---|---|---|
| Enqueue, Cancel | `jobs:write` | `require_scope` ใน route + `_scope` ใน `JobStore` |
| List, Get, Attempts | `jobs:read` | เหมือนกัน |
| Claim, Renew, Complete, Fail | `jobs:work` | เหมือนกัน |
| Migrate | ไม่มี JWT; ใช้ `DATABASE_URL` ของ migration role | `cli.py migrate` |
| Health/ready/metrics | ไม่มี | core + `app.py` `/readyz` |

ทุก use case ที่มี scope ทำงานใน workspace เดียวจาก claim `ws` เท่านั้น; body ที่มี field แปลก (เช่น workspace) ถูกปฏิเสธเพราะ `extra='forbid'`

## Open questions

- MCP hub ถือ scope ของ workflow ตัวไหนบ้าง (ops record บอกเพียงว่าไม่มี `jobs:work` และ `call_cell` workflow ได้ 200)
- person token ยังใช้ไม่ได้จนกว่า owner ตั้ง `IDENTITY_GOOGLE_CLIENT_IDS` (ops record) — producer จริงตอนนี้คือใคร
- ไม่มี actor "approver" แม้ contract จะมี capability `approval`
