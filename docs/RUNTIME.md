# Durable jobs runtime

Stage: **implemented-local**, not deployed. Correlation: `SHARED-INFRA-2026-10-15`.
The registry contract uses the schema's allowed `pilot` value. This runtime
has not replaced the existing task commands or passed a production cutover gate.

## Interfaces

Install `platform-workflow` with `platform-backend-core` from `uv.lock`.
Development uses the locked environment: `uv sync --frozen --all-extras`
(`uv.lock`; core comes from its reviewed Git commit via `[tool.uv.sources]`). Runtime imports only the installed core,
never the hub, a sibling's database, or an absolute checkout path.

### Container image

`docker build .` works from a clean checkout; no pre-staged wheel bundle is
needed. The `Dockerfile` is two stages:

1. `build` (`python:3.12-slim` plus `git` and uv 0.13.1, the CI version, pinned
   by image digest): `uv sync --frozen --no-dev --no-editable` installs this
   service and every runtime dependency from `uv.lock` into `/opt/venv`, so
   `platform-backend-core` comes from the same reviewed commit
   (`21d5f44d26220080cbed43b9807979dc84a8533a`) CI installs, with the lock's hashes.
   No test or dev extras are installed.
2. `runtime` (`python:3.12-slim`, no git, no uv): copies only `/opt/venv`, runs
   as UID/GID `65532`, `PORT=8080`, and keeps `CMD ["platform-workflow", "api"]`
   (`railway.toml` sets the same start command). Run `migrate` and workers as
   separate services or one-offs from the same image with a command override.

`.dockerignore` keeps `.git`, `.venv`, tests, build output and `.env*` out of the
build context. CI job `image` in `check.yml` runs `docker build` on every push and
pull request, then checks that the image imports the service and runs as a
non-root user. It never pushes or deploys. `tests/test_build_hygiene.py` (part of
`scripts/check.sh`) fails if the Dockerfile copies a git-ignored path, stops being
multi-stage, resolves anything outside `uv.lock` (`pip install`, a second core
pin), runs the final stage as anything but `65532:65532` or changes the CMD, if
`[tool.uv.sources]` and `uv.lock` disagree on the core commit, if CI stops
building the image, or if any workflow `uses:` is not pinned to a 40-hex commit
SHA (tag in a trailing comment). Upgrading the core means changing the commit in
`[tool.uv.sources]` and re-locking (`uv lock`).

`platform_workflow.app.create_workflow_app(database, verifier)` returns an
independent FastAPI app. `database` and `verifier` are the shared core's
`Database` and `TokenVerifier`. The verified JWT `ws` UUID is the sole workspace
authority. Body workspace fields are rejected. Required scopes are exact;
there is no owner/wildcard override.

| Endpoint | Scope | Behavior |
|---|---|---|
| `POST /v1/jobs` | `jobs:write` | Enqueue with required `Idempotency-Key`, JSON object `payload`, `job_type`, optional `queue` (default `default`), `run_at` (timezone required), `max_attempts` (1–100, default 3). Returns 201, including duplicate replay. |
| `GET /v1/jobs` | `jobs:read` | Bounded UUID keyset pagination: `limit` 1–100, `after` job UUID. Returns `{items:[...]}`. |
| `GET /v1/jobs/{id}` | `jobs:read` | Tenant-scoped status/result; absent or foreign jobs return 404. |
| `GET /v1/jobs/{id}/attempts` | `jobs:read` | Attempt history; worker token/owner omitted. |
| `POST /v1/jobs/{id}/cancel` | `jobs:write` | Cancel queued/running jobs. Repeated cancellation is idempotent. |
| `POST /v1/worker/claim` | `jobs:work` | Body `owner`, `queues` (1–20), `lease_seconds` (1–900); returns a leased job or null. |
| `POST /v1/worker/jobs/{id}/renew` | `jobs:work` | Body current `lease_token`, optional `lease_seconds`. |
| `POST /v1/worker/jobs/{id}/complete` | `jobs:work` | Body current `lease_token`, bounded JSON `result`. |
| `POST /v1/worker/jobs/{id}/fail` | `jobs:work` | Body current `lease_token`, safe `error_code`. |

Malformed/bounded input returns 422; changed idempotency payload, full workspace
backlog, lost lease and conflicting terminal transition return 409. Missing or
invalid credentials return 401, insufficient scopes 403. Public job reads
omit idempotency fingerprints and lease secrets. Job payload/result remain
workspace-private and must contain only information the calling product is
permitted to retain.

`JobStore(database, max_pending=10000)` exposes `enqueue`, `get`, `list`,
`attempts`, `cancel`, `claim`, `renew`, `complete`, `fail`; all take a shared
`Principal` as their first argument and enforce the same scopes. Claim takes
`owner`, `queues=('default',)`, `lease_seconds=30`. Lease operations take a UUID
`lease_token`; IDs/tokens are UUID objects in the Python interface.

## Durability and limits

Enqueue fingerprints the canonical complete request (queue, type, payload,
schedule and attempts), with normalized UTC schedules. Omitted schedule is
fingerprinted as null and resolved to database time only for the first insert.
`UNIQUE(workspace_id,operation,idempotency_key)` and transaction locks guarantee
concurrent duplicates share one job. Keys must be retained for the entire
business dedupe window; deletion/retention is an explicit operations decision.
There is no automatic deletion or retry endpoint that discards this history.

The database clock determines eligibility, lease expiry and retry times. Claims
use row locks with `FOR UPDATE SKIP LOCKED`; every successful claim increments
attempts and creates a history record. Expired leases can be reclaimed while
attempts remain. An expired final attempt becomes dead-letter on the next poll
of its queue. A caller cannot renew/ack/fail an expired or replaced token.
Completion replays with the same token and JSONB-equivalent result return the
existing success without mutation, even after the former lease's expiry; a different
result conflicts. PostgreSQL numeric normalization preserves replay across
scientific notation and equivalent decimal representations. Failure retries
use `min(3600, 2^attempts)` seconds and become `dead` at the maximum attempt count. Lease recovery is immediately eligible;
it does not add the handler-failure retry delay.

Cancellation immediately fences running workers and records cancellation; it
cannot retract an external effect already started. Success and dead-letter
records cannot be cancelled. Delivery is **at least once**. Applications must
use provider idempotency/inbox/outbox for business effects and must tolerate a
handler running after losing its lease. This service never promises exactly
once execution or runs caller-provided commands/URLs.

Actual NUL (U+0000) and surrogate code points are rejected recursively in JSON
keys and values before database access; literal backslash Unicode escape text
remains valid. API validation returns 422 and worker completion records
`invalid_result` without storing exception details.

Payloads are JSON objects bounded to 60,000 canonical UTF-8 bytes; results are
bounded to 14,000 bytes. PostgreSQL JSONB representation is separately checked
at 65,536 payload / 16,384 result bytes before mutation to handle whitespace
and numeric exponent expansion. Queue/type,
owner and error codes are 1–80 characters from letters/digits/`_.:-`. Request
bodies are bounded to 100,000 bytes. Error records store a code, never exception
text/tracebacks. Workers must return sanitized results. Each workspace has at
most 10,000 queued/running jobs by default. Attempt histories are bounded to 100
per job; total terminal history requires an environment-specific retention plan
before production. Pools, statements and HTTP timeouts come from the shared core.

## Workers

`Worker(store, principal, handlers, *, owner, queues=('default',),
lease_seconds=30).run_once()` claims at most one job, calls an injected trusted
handler `handler(payload) -> JSON`, renews on a bounded heartbeat thread, then
acks/fails with its lease token. It returns false when no job is available.
Handler exceptions persist only `handler_failed`; unknown handlers persist
`unknown_handler`. No provider adapters or dynamic command/plugin loading are
included. One worker executes one handler at a time. Applications supply bounded
handler/provider timeouts and an external process supervisor; a stuck synchronous
handler cannot be forcibly interrupted by this library.

The CLI worker is a diagnostic fixture with only `health.noop`, which returns
`{"ok":true}`. Supply a dedicated queue containing only that job type. Real
products embed `Worker` with their explicit handler registry. The CLI requires
an explicit `WORKFLOW_WORKER_QUEUES` list and exactly one token source:

- `WORKFLOW_WORKER_CLIENT_ID` and `WORKFLOW_WORKER_PRIVATE_KEY_PEM`: a
  platform-identity service client (platform-identity `docs/SERVICE-CLIENTS.md`)
  registered for audience `JWT_AUDIENCE` and scope `jobs:work` in this worker's
  workspace. The worker gets its token from `<JWT_ISSUER>/v1/token` and renews
  it before it expires. While identity cannot give it a token the worker skips
  polls, logs `worker token unavailable` and retries every 30 s; or
- `WORKFLOW_WORKER_AUTHORIZATION`: a fixed bearer value, for tests and short runs.

It verifies the token before every poll and never polls without a valid one. A
fixed value that has expired or fails verification stops the process. Its single verified workspace bounds every database
transaction. Run separate authorized processes for additional workspaces; there
is no global scanner, workspace discovery or BYPASSRLS credential.

## Local verification

The existing offline check is unchanged: `bash scripts/check.sh`.
The full suite requires `pytest`, `httpx`, `cryptography`, the installed core,
and an explicitly supplied disposable PostgreSQL admin `PLATFORM_TEST_DSN`:

```bash
python -m pytest -q
```

A missing DSN fails the runtime suite, never skips it. Tests create unique
`workflow_test_*` schemas and login runtime roles in the supplied disposable
database. Those runtime roles are NOSUPERUSER/NOBYPASSRLS and own their schema;
FORCE RLS therefore proves even table ownership cannot bypass tenant policies.
Only their uniquely named schema/role is removed. Never supply production DSNs.
The suite tests actual PostgreSQL locks/RLS, two spawned independent claim
processes, concurrent enqueue, expired lease recovery/fencing, heartbeat,
retry/dead-letter, cancellation, JWT scope/workspace boundaries and missing
migration readiness. No provider connection or external message occurs.

## Build hygiene 2026-10-29 (branch `build-hygiene`)

Ported from `platform-developer` `8047108`, adapted to this repository's `uv.lock`:

1. The `Dockerfile` already built from a clean checkout, but single-stage, with
   `pip install` of a core archive URL that duplicated the lock's pin, and with no
   `.dockerignore`. It is now the two-stage `uv sync --frozen` build under
   "Container image", and CI job `image` builds it without pushing.
2. `check.yml` used `actions/checkout@v4` and `actions/setup-python@v5` by tag.
   Both are pinned to commit SHAs (`11d5960a...` = v4, `a26af69b...` = v5;
   lightweight tags, resolved with `gh api .../git/ref/tags/...`), tag in a comment.

RED (tests written first, run on the old files, Python 3.12):
`python -m unittest tests.test_build_hygiene` failed 5 of 9 (no `.dockerignore`;
no `docker build` in CI; no `uv.lock`/`uv sync --frozen`; one `FROM`; unpinned
actions). The checker self-test, lock/`[tool.uv.sources]` agreement, the
clean-checkout COPY check and the non-root `USER`/CMD check passed.

GREEN: `bash scripts/check.sh` 67 unit tests OK (2 skipped without the pinned
ops source); `ruff check .` and `mypy` clean; `python -m pytest -q --cov` with a
local PostgreSQL (`pgserver`) 97 passed, 2 skipped, coverage 78% (gate 77). The
build stage was rehearsed without Docker (`uv sync --frozen --no-dev
--no-editable` into a fresh venv from a clean copy, then
`import platform_backend_core, platform_workflow.app`); the image itself is
verified only by the CI `image` job. Nothing was pushed to a registry or deployed.

## 422 only for domain input — 2026-10-29 (branch `invalid-input`)

Ported from `platform-developer` `8047108` and `platform-commerce` `main`. The app
mapped every `ValueError` to 422 `invalid job request`, so a server bug surfaced
as a client error. `store.InvalidInput(ValueError)` (exported by the package) is
now raised for every job and worker-API bound: idempotency key, queue, job type,
owner and error-code names, payload/result type, size, Unicode and JSONB-expanded
size, `max_attempts`, naive `run_at`, list limit, lease seconds and the queue
count. Only that maps to 422. Request-schema errors keep the core's 422; any other
exception, including an unexpected `ValueError`, is the shared core's redacted 500.

`_json` now turns `json.dumps(allow_nan=False)`'s own `ValueError` into
`InvalidInput`: the request parser accepts `NaN`/`Infinity`, so without it a
`NaN` payload would have become a 500. Deliberately still a plain `ValueError`
(never 422): the `JobStore` pending bound (service configuration) and the CLI's
missing-environment check. The worker still records `invalid_result` for a
handler result that is a `ValueError` (now `InvalidInput`) or `TypeError`.
Readiness is unchanged.

RED (`tests/runtime/test_error_mapping.py` written first): collection error,
`InvalidInput` missing. With a temporary `InvalidInput = ValueError` stand-in:
2 failed (`test_unexpected_value_error_is_redacted_500` `assert 422 == 500`, and the
pending-bound case), 19 passed.

GREEN: `PLATFORM_TEST_DSN=... pytest -q --cov` 118 passed, 2 skipped (the
pinned-source re-extraction the contract job runs), 127 subtests, total coverage
79% (gate 77), against PostgreSQL 16 (`pgserver`); `bash scripts/check.sh` 67 tests
OK; `ruff check .` and `mypy` clean (locked `uv sync --frozen --all-extras`).
Nothing deployed.
