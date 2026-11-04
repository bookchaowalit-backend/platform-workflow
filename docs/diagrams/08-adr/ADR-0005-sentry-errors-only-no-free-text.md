# ADR-0005 — Sentry errors only, no free text (fail closed)

- Status: accepted (retro-documented)
- Date of evidence: 2026-10-29 (`2b89eb7` opt-in Sentry), 2026-11-03 (`301da88`, PR #19)

## Context

service ถือ recipient, address และ phone ใน payload; PR #7–#18 พยายาม scrub free text ด้วย pattern แต่ review พบ bypass ซ้ำ ๆ (`_sentry.py` docstring)

## Decision

- Sentry เปิดเมื่อมี `SENTRY_DSN` เท่านั้น; `traces_sample_rate=None`, `trace_propagation_targets=[]`, `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`, logging integration ปิด
- `_scrub_event` ทิ้ง exception value, message, logentry, breadcrumbs, request (เหลือ method), extra, user, dynamic sampling context
- `CaptureErrors` เป็น ASGI layer ในสุด เพราะ core `_Boundary` แปลง exception เป็น 500 ก่อนถึง integration ของ Sentry; ไม่สร้าง event ถ้าไม่มี client/transport
- gap: worker/CLI ไม่เรียก `capture()` (`worker.py`, `cli.py`); handler ที่ล้มถูกบันทึกเป็น `handler_failed` ใน attempt history เท่านั้น

## Consequences

- event มีแค่ type, stack (file/function/line), route template, tags, release (`RAILWAY_GIT_COMMIT_SHA`)
- debug ต้องอาศัย stack + `x-request-id` log แทนข้อความ error

## Evidence

`src/platform_workflow/_sentry.py`, `src/platform_workflow/app.py`, `tests/test_sentry.py`, `tests/runtime/test_sentry_boundary.py`, `gh pr list` (#6–#19), ops record: book-platforms-status (2026-11-03)
