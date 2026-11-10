# ADR-0004 — Railway Docker image, serverless (sleep), CLI deploy

- Status: accepted (retro-documented)
- Date of evidence: `.railway/railway.ts` staged with PR #1 (`1429550`); live on Railway 2026-10-29 (ops record)

## Context

owner อนุมัติ deploy service ใหม่ทั้งหมดแบบ serverless เมื่อ 2026-10-29 (ops record); repo นี้เป็น public, CI ไม่มี deploy job

## Decision

- builder `DOCKERFILE`, startCommand `platform-workflow api`, healthcheck `/readyz` timeout 20 s, restart `ON_FAILURE` max 3, `numReplicas = 1` (`.railway/railway.ts`)
- image สอง stage, run เป็น UID/GID 65532, ไม่มี git/uv ใน runtime stage (`Dockerfile`)
- Railway service `workflow-api-production-63de` region `asia-southeast1-eqsg3a`, sleep on, Neon project `platform-workflow`, secrets ใน Infisical `/platform-workflow`; deploy ด้วย `railway up --ci` จาก `git archive main` (ops record: book-platforms-status (2026-11-03))

## Consequences

- deploy ไม่ทิ้งร่องรอยใน git/gh (DORA วัดได้บางส่วน, ดู [22-dora.md](../22-dora.md))
- cold start หลัง sleep + Neon resume ~6–9 s; core ตั้ง pool wait 20 s / connect timeout 15 s
- `README.md`, `PRODUCT.md`, `docs/RUNBOOK.md` ยังเขียนว่า "not deployed" — ขัดกับ ops record

## Evidence

`.railway/railway.ts`, `Dockerfile`, `.github/workflows/check.yml` (job `image` build only, no push), `docs/RUNTIME.md` ("Container image"), ops record: book-platforms-status (2026-11-03)
