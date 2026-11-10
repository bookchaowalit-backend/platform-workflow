# 10 — Sequences

## 1. Enqueue (idempotent)

```mermaid
sequenceDiagram
  autonumber
  participant P as Producer
  participant B as Core _Boundary
  participant A as app.py enqueue
  participant V as TokenVerifier
  participant S as JobStore
  participant DB as PostgreSQL
  P->>B: POST /v1/jobs, Bearer, Idempotency-Key, JSON
  B->>B: body <= 100000 bytes, 30 s timeout
  B->>A: request
  A->>V: verify(authorization)
  V-->>A: Principal(actor, ws, scopes) or 401
  A->>A: jobs:write in scopes else 403
  A->>S: enqueue(principal, key, body)
  S->>S: validate names, JSON, max_attempts, run_at tz, sha256 fingerprint
  S->>DB: BEGIN, set_config app.workspace_id = ws
  S->>DB: JSONB size check, pg_advisory_xact_lock(ws)
  S->>DB: SELECT by workspace, operation, key
  alt key exists, same fingerprint
    DB-->>S: prior row
  else key exists, other fingerprint
    S-->>A: Conflict, 409
  else new key
    S->>DB: count queued/running below 10000
    S->>DB: INSERT workflow_jobs RETURNING *
  end
  S-->>A: row
  A-->>P: 201 public_job without lease_token, fingerprint, key
```

Sources: `src/platform_workflow/app.py`, `src/platform_workflow/store.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/http.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/auth.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/db.py`

## 2. Worker claim → heartbeat → complete / fail

```mermaid
sequenceDiagram
  autonumber
  participant W as Worker.run_once
  participant HB as Heartbeat thread
  participant S as JobStore
  participant DB as PostgreSQL
  participant H as Handler
  W->>S: claim(owner, queues, lease_seconds)
  S->>DB: dead-letter exhausted expired leases, SKIP LOCKED
  S->>DB: SELECT eligible job ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT 1
  S->>DB: mark open attempt expired, UPDATE running, attempts+1, new lease_token
  S->>DB: INSERT workflow_attempts
  S-->>W: job or None
  W->>HB: start, every lease_seconds/3
  HB->>S: renew(lease_token)
  S->>DB: _fenced: row lock, token and lease_until vs clock_timestamp
  W->>H: handler(payload)
  H-->>W: result or exception
  W->>HB: stop and join
  alt lease still held and ok
    W->>S: complete(lease_token, result)
    S->>DB: attempt succeeded, job succeeded, result
  else handler error
    W->>S: fail(lease_token, handler_failed)
    S->>DB: attempt failed, job queued with backoff or dead
  else lease lost
    W->>W: skip ack
  end
```

Sources: `src/platform_workflow/worker.py`, `src/platform_workflow/store.py`

## 3. Worker token from identity (service client)

```mermaid
sequenceDiagram
  autonumber
  participant C as cli worker loop
  participant T as ServiceTokenSource
  participant I as platform-identity
  participant V as TokenVerifier
  C->>T: authorization()
  T->>T: sign private_key_jwt assertion, 60 s
  T->>I: POST issuer/v1/token client_credentials scope jobs:work
  I-->>T: access token, cached and renewed before expiry
  T-->>C: Bearer token
  C->>V: verify, before every poll
  alt ServiceTokenError
    C->>C: log worker token unavailable, wait 30 s
  else verified
    C->>C: Worker.run_once with principal
  end
```

Sources: `src/platform_workflow/cli.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/service_token.py`, `docs/RUNTIME.md` ("Workers")

## 4. Readiness probe

```mermaid
sequenceDiagram
  participant R as Railway healthcheck or uptime worker
  participant A as app.py readyz
  participant DB as PostgreSQL
  R->>A: GET /readyz
  A->>DB: SELECT 1 via pool
  A->>DB: pg_class relrowsecurity, relforcerowsecurity, schema USAGE, SELECT INSERT UPDATE privileges for both tables
  A->>DB: SELECT ... LIMIT 0 on both tables
  alt all true
    A-->>R: 200 status ready
  else any failure
    A-->>R: 503 status unavailable
  end
```

Sources: `src/platform_workflow/app.py`, `.railway/railway.ts`, ops record: book-platforms-status (2026-11-03) (uptime worker hourly `/readyz`)
