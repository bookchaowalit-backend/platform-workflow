# ADR-0002 — PostgreSQL durable queue with lease fencing, at-least-once

- Status: accepted (retro-documented)
- Date of evidence: 2026-10-23 (`1429550`, PR #1 "Add durable workspace jobs and fenced workers")

## Context

legacy scheduler ไม่มี per-run idempotency key และไม่มี durable history (BOO-165 gate); contract ต้องการ gate "durable execution", "retry semantics", "duplicate job protection"

## Decision

- เก็บ job และ attempt ใน PostgreSQL (`workflow_jobs`, `workflow_attempts`) ไม่ใช้ broker แยก
- claim ด้วย `FOR UPDATE SKIP LOCKED`, lease token UUID + `lease_until` จาก database clock; ทุก mutation ของ worker ผ่าน `_fenced`
- dedupe ด้วย `UNIQUE(workspace_id, operation, idempotency_key)` + sha256 fingerprint ของ request ทั้งหมด + advisory lock ต่อ workspace
- retry `min(3600, 2^attempts)` s, dead-letter ที่ `max_attempts`; delivery เป็น **at least once** ไม่สัญญา exactly once

## Consequences

- handler ต้อง idempotent หรือใช้ provider idempotency/outbox เอง
- ไม่มี replay อัตโนมัติของ dead-letter; history ต้องมี retention plan ก่อน production (`docs/RUNTIME.md`)
- throughput ขึ้นกับ connection budget ของ Neon (`docs/RUNBOOK.md`)

## Evidence

`src/platform_workflow/migrations.py`, `src/platform_workflow/store.py`, `src/platform_workflow/worker.py`, `docs/RUNTIME.md` ("Durability and limits"), `tests/runtime/test_jobs.py`, `docs/gates/BOO-165-scheduler-ownership-retry-history.md`
