# platform-workflow — Product brief

**Slug:** `bookchaowalit-backend/platform-workflow`
**Updated:** 2026-10-24
**Status:** `pilot` contract plus a locally tested jobs runtime; nothing is deployed

## Purpose

Own the Book Platform `workflow` capability (jobs, queues, schedules, retries,
execution history) as a written, checked contract, with a durable jobs runtime that
can later replace the `infra/tasks` implementation recorded in the parent registry.

## What exists

- Offline contract checks (Python 3.11+, standard library): `scripts/check.py`
  validates `contract.json` against the vendored schema, `scripts/check_schema_pin.py`
  checks the schema pin, and `scripts/scheduler_inventory.py` keeps the scheduler
  inventory (`fixtures/gates/`) in step with the schedules found in the retired
  `solo-empire` sources and in `platform-ops-cli`; CI re-extracts `platform-ops-cli`
  at a pinned commit.
- The `platform_workflow` package (`src/platform_workflow/`): a FastAPI jobs API
  (enqueue with idempotency keys, list, status, attempts, cancel) and a worker
  protocol with fenced leases (claim, renew, complete, fail), on PostgreSQL with
  row level security keyed by the JWT `ws` claim, plus the `platform-workflow` CLI.
  It builds on `platform-backend-core`, installed from a pinned Git commit. See
  `docs/RUNTIME.md` and `docs/RUNBOOK.md`.
- Tests: `tests/` (contract, drift, scheduler inventory) and `tests/runtime/` (API
  and worker tests against a real disposable PostgreSQL).
- CI (GitHub-hosted, public repository): contract job on Python 3.11 and 3.12, the
  shared `ci-python` job (`uv sync --frozen`, Ruff, mypy, pytest with PostgreSQL
  under a coverage gate, wheel build) and the shared security workflow.

## Limits

- Not deployed: `Dockerfile` and `railway.toml` are staged release assets; no
  Railway service or other host runs them, and the existing task commands are not
  replaced. No production cutover gate has been passed.
- The D8 `codex/shared-infrastructure-20261015` pull request (#1) was merged into
  `main` as `1429550`; it added the runtime above.

Sentry waiver: Dockerfile and railway.toml are staged only; no Railway service or other host runs this code, so there is no runtime to report from.
