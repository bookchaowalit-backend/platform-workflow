# 11 — State machines

## Job (`workflow_jobs.status`)

```mermaid
stateDiagram-v2
  [*] --> queued: enqueue
  queued --> running: claim, run_at reached, attempts below max
  running --> running: renew with current lease_token
  running --> succeeded: complete with current lease_token
  running --> queued: fail, attempts below max, run_at = now + min(3600, 2^attempts) s
  running --> dead: fail at max_attempts
  running --> running: lease expired, reclaimed by next claim, attempts+1
  running --> dead: lease expired on final attempt, error_code lease_expired
  queued --> cancelled: cancel
  running --> cancelled: cancel
  cancelled --> cancelled: cancel again, idempotent
  succeeded --> succeeded: complete replay, same token and result
  succeeded --> [*]
  dead --> [*]
  cancelled --> [*]
```

กติกาเพิ่ม: `succeeded`/`dead` cancel ไม่ได้ (409); DB `CHECK` บังคับว่า `lease_until` มีค่าก็ต่อเมื่อ `running`; ไม่มี transition ออกจาก `dead` (ไม่มี replay endpoint)

Sources: `src/platform_workflow/store.py` (`claim`, `renew`, `complete`, `fail`, `cancel`), `src/platform_workflow/migrations.py`, `docs/RUNTIME.md` ("Durability and limits")

## Attempt (`workflow_attempts.outcome`)

```mermaid
stateDiagram-v2
  [*] --> open: claim inserts row, outcome NULL
  open --> succeeded: complete
  open --> failed: fail, error_code stored
  open --> expired: lease expired and job reclaimed or dead-lettered
  open --> cancelled: job cancelled
  succeeded --> [*]
  failed --> [*]
  expired --> [*]
  cancelled --> [*]
```

Sources: `src/platform_workflow/store.py`, `src/platform_workflow/migrations.py`

## Worker process

```mermaid
stateDiagram-v2
  [*] --> getToken
  getToken --> waitToken: ServiceTokenError
  waitToken --> getToken: after 30 s
  getToken --> polling: token verified
  polling --> idle: no job
  idle --> getToken: after 1 s
  polling --> handling: job claimed
  handling --> getToken: acked, failed or lease lost
  getToken --> stopped: SIGTERM or SIGINT
  handling --> stopped: handler finishes, then pool closes
  stopped --> [*]
```

Sources: `src/platform_workflow/cli.py`, `src/platform_workflow/worker.py`, `docs/RUNBOOK.md`
