# Core 22 SDLC diagram pack — platform-workflow

ชุดภาพ Core 22 ตาม SDLC Diagram Catalog v2 วาดจากโค้ดและ config ใน repo นี้ (ณ `origin/main` `301da88`, 2026-11-03)
repo นี้คือ platform service `workflow`: durable workspace-scoped jobs API + leased worker บน PostgreSQL, สร้างบน `platform-backend-core` ที่ pin commit `dc1fa7d`

ทุกไฟล์มี `Sources:` ใต้ภาพ และ "Open questions" สำหรับสิ่งที่ยืนยันจากโค้ดไม่ได้
ข้อมูล hosting ที่ไม่อยู่ใน repo อ้างจาก `ops record: book-platforms-status (2026-11-03)` เฉพาะบรรทัดที่ระบุ workflow
core อ่านจาก clone ของ `bookchaowalit-backend/platform-backend-core` ที่ commit `dc1fa7d` (อ้างเป็น `platform-backend-core@dc1fa7d:<path>`)

**ข้อค้นพบหลัก:** `README.md`, `PRODUCT.md`, `docs/RUNBOOK.md`, `docs/RUNTIME.md` ยังเขียนว่า "not deployed" แต่ ops record บันทึกว่า `workflow-api-production-63de` live บน Railway ตั้งแต่ 2026-10-29

| # | Item | Status | File | Sources read |
|---|---|---|---|---|
| 01 | Business process As-Is / To-Be | done (+ reused BOO-165 gate) | [01-business-process.md](01-business-process.md) | `store.py`, `app.py`, `worker.py`, `PRODUCT.md`, `docs/UPGRADE-PLAN.md`, `docs/gates/BOO-165-*.md` |
| 02 | Use cases | done | [02-use-cases.md](02-use-cases.md) | `app.py`, `store.py`, `cli.py`, core `http.py` / `auth.py`, ops record |
| 03 | User flows (API call flows) | done | [03-user-flows.md](03-user-flows.md) | `app.py`, `cli.py`, `worker.py`, core `service_token.py`, `docs/RUNTIME.md` |
| 04 | Traceability (RTM) | done | [04-traceability.md](04-traceability.md) | `PRODUCT.md`, `contract.json`, `docs/RUNTIME.md`, `tests/**`, `check.yml` |
| 05 | C4 Context | done | [05-c4-context.md](05-c4-context.md) | `cli.py`, `app.py`, `_sentry.py`, `contract.json`, ops record |
| 06 | C4 Container | done | [06-c4-container.md](06-c4-container.md) | `Dockerfile`, `.railway/railway.ts`, `cli.py`, `migrations.py`, core `migrations.py` |
| 07 | C4 Component | done | [07-c4-component.md](07-c4-component.md) | `src/platform_workflow/*.py`, core `http.py` / `auth.py` / `db.py` |
| 08 | ADRs | done (5) | [08-adr/](08-adr/) | `pyproject.toml`, `uv.lock`, `migrations.py`, `store.py`, `.railway/railway.ts`, `_sentry.py`, git log, ops record |
| 09 | ERD | done | [09-erd.md](09-erd.md) | `migrations.py`, core `migrations.py` / `db.py`, `docs/RUNBOOK.md` |
| 10 | Sequences | done (4) | [10-sequences.md](10-sequences.md) | `app.py`, `store.py`, `worker.py`, `cli.py`, core |
| 11 | State machines | done (job, attempt, worker) | [11-state-machines.md](11-state-machines.md) | `store.py`, `migrations.py`, `cli.py` |
| 12 | API & integrations | done (+ reused `docs/RUNTIME.md`) | [12-api-integration.md](12-api-integration.md) | `app.py`, `store.py`, core `http.py`, `cli.py`, `_sentry.py` |
| 13 | Threat model | done | [13-threat-model.md](13-threat-model.md) | `app.py`, `store.py`, `cli.py`, `_sentry.py`, `migrations.py`, core, tests |
| 14 | Backup & DR | done (RTO/RPO not defined) | [14-backup-dr.md](14-backup-dr.md) | `docs/RUNBOOK.md`, `migrations.py`, ops record |
| 15 | CI/CD pipeline | done | [15-cicd-pipeline.md](15-cicd-pipeline.md) | `check.yml`, `scripts/check.sh`, platform-ci-workflows `ci-python.yml` / `security.yml` @ `87dc5e1c` |
| 16 | Deployment | done | [16-deployment.md](16-deployment.md) | `.railway/railway.ts`, `Dockerfile`, `cli.py`, ops record |
| 17 | Test coverage matrix | done | [17-test-coverage-matrix.md](17-test-coverage-matrix.md) | `tests/**`, `check.yml`, `docs/RUNTIME.md` |
| 18 | Git & release workflow | done | [18-git-release-workflow.md](18-git-release-workflow.md) | `git log --first-parent`, `gh pr list` |
| 19 | Service dependency map | done | [19-service-dependency-map.md](19-service-dependency-map.md) | `cli.py`, `app.py`, `pyproject.toml`, `Dockerfile`, `check.yml`, ops record |
| 20 | Observability | done | [20-observability.md](20-observability.md) | `_sentry.py`, core `http.py`, `docs/RUNBOOK.md`, ops record |
| 21 | Incident response | done (+ reused `docs/RUNBOOK.md`) | [21-incident-response.md](21-incident-response.md) | `docs/RUNBOOK.md`, `store.py`, `_sentry.py`, ops record |
| 22 | DORA | done (5/5 as git/gh proxies; real deploys not visible) | [22-dora.md](22-dora.md) | `git log`, `gh run list`, `gh pr list` |

สรุปสถาปัตยกรรมระดับ docpack: [`DOCPACK/02-architecture.md`](../../DOCPACK/02-architecture.md) (2026-11-04)

## ADR list

- [ADR-0001](08-adr/ADR-0001-fastapi-on-pinned-platform-backend-core.md) FastAPI บน `platform-backend-core` ที่ pin commit
- [ADR-0002](08-adr/ADR-0002-postgres-durable-queue-with-lease-fencing.md) PostgreSQL durable queue + lease fencing, at-least-once
- [ADR-0003](08-adr/ADR-0003-workspace-isolation-jwt-ws-and-force-rls.md) Workspace isolation: JWT `ws` + FORCE RLS + non-bypass role
- [ADR-0004](08-adr/ADR-0004-railway-docker-serverless-cli-deploy.md) Railway Docker serverless, CLI deploy
- [ADR-0005](08-adr/ADR-0005-sentry-errors-only-no-free-text.md) Sentry errors only, no free text

## Related existing docs (reused, not copied)

- [docs/RUNTIME.md](../RUNTIME.md), [docs/RUNBOOK.md](../RUNBOOK.md), [docs/CONTRACT-SURFACE.md](../CONTRACT-SURFACE.md), [docs/UPGRADE-PLAN.md](../UPGRADE-PLAN.md), [docs/gates/BOO-165-scheduler-ownership-retry-history.md](../gates/BOO-165-scheduler-ownership-retry-history.md)
- central architecture-docs ไม่มีภาพเฉพาะของ service นี้ ([C4-DIAGRAMS.md](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/C4-DIAGRAMS.md), [ERD-DIAGRAMS.md](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/ERD-DIAGRAMS.md), [ATTACK-PATH-DIAGRAMS.md](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/ATTACK-PATH-DIAGRAMS.md))

Validate: `node ~/work/core22/tools/validate.mjs docs/diagrams/*.md docs/diagrams/08-adr/*.md`
