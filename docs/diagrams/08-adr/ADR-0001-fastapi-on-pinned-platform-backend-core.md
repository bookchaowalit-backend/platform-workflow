# ADR-0001 — FastAPI service on a pinned `platform-backend-core`

- Status: accepted (retro-documented)
- Date of evidence: 2026-10-23 (PR #1 merged as `1429550`), core repinned 2026-10-29 (`9bfe8bc`)

## Context

ทุก platform service ใช้ runtime "core" ร่วม (HTTP boundary, JWT verify, DB pool, migrations) แต่แต่ละ repo เป็นเจ้าของโค้ดตัวเอง
core ไม่ได้อยู่บน PyPI จึงเสี่ยง dependency confusion

## Decision

- ใช้ FastAPI + uvicorn ผ่าน `platform_backend_core.create_app('platform-workflow', ...)` (`app.py`)
- ติดตั้ง core จาก Git commit ที่ review แล้ว `dc1fa7d2693594b3c79b0b2539a83a6a9465a560` ผ่าน `[tool.uv.sources]` และ `uv.lock`; Docker build ใช้ `uv sync --frozen`
- ไม่ import hub, sibling database หรือ checkout path (`docs/RUNTIME.md`)

## Consequences

- ได้ body cap, timeout, redacted 500, `/healthz`, `/metrics` จาก core โดยไม่เขียนเอง
- การอัปเกรด core = แก้ commit ใน `pyproject.toml` แล้ว `uv lock`; `tests/test_build_hygiene.py` จับ lock กับ source ที่ไม่ตรงกัน
- core `_Boundary` กลืน exception เป็น 500 จึงต้องเพิ่ม `CaptureErrors` เอง (ADR-0005)

## Evidence

`pyproject.toml`, `uv.lock`, `Dockerfile`, `src/platform_workflow/app.py`, `tests/test_build_hygiene.py` (`CorePinTests`), `docs/RUNTIME.md`, `git log` `1429550`, `9bfe8bc`
