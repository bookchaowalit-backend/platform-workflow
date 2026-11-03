# 01 — Business process (As-Is / To-Be)

`platform-workflow` เป็น platform service เจ้าของ capability `workflow` (jobs, queue, retry, execution history) ของ Book Platform
งานจริงที่โค้ดทำวันนี้คือ **durable jobs queue** บน PostgreSQL: producer ส่ง job เข้า, worker claim ด้วย lease, ผลลัพธ์ถูกเก็บเป็น attempt history
business invariant อยู่ใน platform ที่เรียกใช้ ไม่ได้อยู่ที่นี่ (`docs/CONTRACT-SURFACE.md` "Behaviour notes")

## As-Is (จากโค้ดวันนี้)

```mermaid
flowchart TD
  P["Producer: calling platform with jobs:write token"] -->|"POST /v1/jobs + Idempotency-Key"| E{"Same key seen in this workspace?"}
  E -->|"yes, same fingerprint"| R["Return existing job, 201"]
  E -->|"yes, other fingerprint"| C409["409 idempotency key payload mismatch"]
  E -->|"no"| B{"Pending jobs below 10000?"}
  B -->|"no"| C409B["409 workspace pending limit reached"]
  B -->|"yes"| Q["workflow_jobs row status=queued, run_at"]
  Q --> W["Worker with jobs:work token: POST /v1/worker/claim"]
  W --> L["status=running, attempts+1, lease_token, workflow_attempts row"]
  L --> H["Trusted handler runs payload; heartbeat renews lease every lease_seconds/3"]
  H -->|"handler ok"| S["complete: status=succeeded, result stored"]
  H -->|"handler error or unknown type"| F["fail: error_code only"]
  F -->|"attempts below max_attempts"| RQ["status=queued, run_at = now + min(3600, 2^attempts) s"]
  RQ --> W
  F -->|"attempts reached max"| D["status=dead (dead-letter record)"]
  L -->|"lease expired, attempts left"| W
  L -->|"lease expired on final attempt"| D
  Q -->|"POST /v1/jobs/id/cancel"| X["status=cancelled"]
  L -->|"cancel"| X
```

Sources: `src/platform_workflow/app.py`, `src/platform_workflow/store.py`, `src/platform_workflow/worker.py`, `src/platform_workflow/migrations.py`, `docs/RUNTIME.md`

### As-Is ของ scheduling เดิม (นอก repo นี้)

contract ยังระบุ interface ปัจจุบันเป็น `infra/tasks` และ `infra/scripts` (`current-in-solo-empire`) แต่ gate BOO-165 พบว่า path นี้ย้ายไป `platform-ops-cli` แล้ว
inventory 69 jobs (3 systemd timer ที่ยัง active ใน source, ที่เหลือ retired) ได้รับการ accept โดย owner 2026-10-16 — ดูรายละเอียดใน [BOO-165 gate](../gates/BOO-165-scheduler-ownership-retry-history.md) (reused)

```mermaid
flowchart LR
  T["systemd user timers in platform-ops-cli, 3 active in source"] --> J["Task entry points infra/tasks and infra/scripts"]
  J --> LOG["History: systemd journal and state files, no durable per-run record"]
  RET["Retired: 40 local-cron jobs, 6 crontab, 9 Windows tasks, 6 GHA schedules"] -.->|"retired"| X["no runtime"]
```

Sources: `docs/gates/BOO-165-scheduler-ownership-retry-history.md`, `contract.json`, `docs/CONTRACT-SURFACE.md`, `fixtures/gates/workflow-scheduler-inventory.json`

## To-Be (จาก PRODUCT.md, UPGRADE-PLAN.md และ gate doc เท่านั้น)

```mermaid
flowchart LR
  A["Consumers use task adapters today"] --> G1["Gates: durable execution, retry semantics, duplicate job protection"]
  G1 --> G2["Sanitized fixtures: success, duplicate delivery, timeout, provider failure"]
  G2 --> G3["Shadow parity and rollback rehearsal recorded in parent registry"]
  G3 --> CUT["Cutover: platform-workflow replaces infra/tasks implementation"]
  CUT --> OWN["PROPOSED: platform-workflow owns schedule definitions and attempt history for re-homed jobs"]
```

Sources: `PRODUCT.md`, `docs/UPGRADE-PLAN.md`, `README.md` ("Migration gate"), `contract.json` (`migration`, `migration_gates`), `docs/gates/BOO-165-scheduler-ownership-retry-history.md` ("Still open")

ข้อสังเกต: กล่อง `OWN` และกฎ retry `max_attempts = 2`, idempotency key `(job_id, scheduled_for)` ยังเป็น **PROPOSED, NOT ACCEPTED** ตาม gate doc

## Open questions

- contract ประกาศ capability `scheduler` และ `approval` แต่โค้ดไม่มี recurring schedule (มีแค่ `run_at` ครั้งเดียว) และไม่มี approval เลย — จะทำใน repo นี้หรือถอดออกจาก contract?
- ข้อเสนอ ownership/retry/idempotency/history 7 ข้อใน BOO-165 ยังรอ owner ตัดสิน
- consumer รายแรกที่จะย้ายมาใช้ queue นี้คือใคร (gate doc เสนอ 3 systemd timer ของ platform-ops-cli)
