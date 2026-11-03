# ADR-0003 — Workspace isolation: verified JWT `ws` + FORCE RLS + non-bypass runtime role

- Status: accepted (retro-documented)
- Date of evidence: 2026-10-23 (`1429550`); identity issuer trust 2026-10-29 (ops record)

## Context

หลาย product ใช้ queue เดียวกัน; payload เป็นข้อมูลส่วนตัวของ workspace

## Decision

- authority มาจาก claim `ws` ของ RS256 JWT ที่ verify แล้วเท่านั้น (`TokenVerifier`: iss, aud, exp, jti, ไม่รับ `del`/`obo`); body ห้ามมี field เกิน (`extra='forbid'`)
- scope แบบ exact: `jobs:read`, `jobs:write`, `jobs:work`; ไม่มี wildcard
- ทุก transaction ตั้ง `app.workspace_id`; ตารางทั้งสอง ENABLE + FORCE RLS ด้วย policy USING/WITH CHECK เดียวกัน
- runtime role ต้อง NOSUPERUSER NOBYPASSRLS (core `Database._configure` ปฏิเสธ role อื่น); `/readyz` ตรวจ grant และ RLS
- issuer ที่ trust: platform-identity, audience `<svc>-api` (ops record: book-platforms-status (2026-11-03))

## Consequences

- ไม่มี cross-workspace endpoint; backlog metrics ต้อง poll ต่อ workspace (`docs/RUNBOOK.md`)
- worker หนึ่ง process ทำงานได้ workspace เดียว

## Evidence

`src/platform_workflow/migrations.py`, `src/platform_workflow/store.py` (`_scope`), `src/platform_workflow/app.py`, `src/platform_workflow/cli.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/auth.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/db.py`, `docs/RUNBOOK.md`, `tests/runtime/test_jobs.py`, `tests/runtime/test_app_worker.py`
