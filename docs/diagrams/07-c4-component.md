# 07 — C4 Component (API service)

```mermaid
C4Component
  title platform-workflow API - components
  Container_Boundary(api, "API service") {
    Component(boundary, "_Boundary middleware", "core http.py", "Body cap 100000 bytes, 30 s timeout, x-request-id, redacted 500, route metrics")
    Component(capture, "CaptureErrors", "_sentry.py", "Innermost ASGI layer, reports then re-raises")
    Component(scope, "require_scope", "core http.py", "Verifies Bearer, checks exact scope, 401/403")
    Component(verifier, "TokenVerifier", "core auth.py", "RS256, iss, aud, ws, exp, jti; JWKS or PEM")
    Component(routes, "Routes", "app.py", "9 /v1 endpoints, /readyz override, error mapping 404/403/409/422")
    Component(store, "JobStore", "store.py", "Validation, idempotency fingerprint, leases, retries, dead-letter")
    Component(dbc, "Database", "core db.py", "psycopg pool, rejects SUPERUSER/BYPASSRLS role, sets app.workspace_id")
    Component(sentrymod, "init_sentry and _scrub_event", "_sentry.py", "Drops messages, breadcrumbs, request, user, extra")
  }
  ContainerDb(db, "PostgreSQL", "Neon", "workflow tables")
  System_Ext(jwks, "Identity JWKS", "")
  System_Ext(sentry, "Sentry", "")
  Rel(boundary, capture, "wraps")
  Rel(capture, routes, "wraps")
  Rel(routes, scope, "Depends")
  Rel(scope, verifier, "verify")
  Rel(verifier, jwks, "fetch keys")
  Rel(routes, store, "calls")
  Rel(store, dbc, "transaction(workspace)")
  Rel(dbc, db, "SQL")
  Rel(capture, sentrymod, "capture_event")
  Rel(sentrymod, sentry, "send scrubbed event")
```

Sources: `src/platform_workflow/app.py`, `src/platform_workflow/store.py`, `src/platform_workflow/_sentry.py`, `src/platform_workflow/__init__.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/http.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/auth.py`, `platform-backend-core@dc1fa7d:src/platform_backend_core/db.py`

## Worker components

```mermaid
flowchart LR
  CLI["cli.main worker"] --> AUTH["worker_authorization_from_env"]
  AUTH --> STS["core ServiceTokenSource"]
  CLI --> WP["worker_principal: TokenVerifier.verify"]
  CLI --> WK["Worker.run_once"]
  WK --> JS["JobStore claim, renew, complete, fail"]
  WK --> HB["heartbeat thread"]
  WK --> HR["handlers: health.noop"]
  JS --> DB["core Database"]
```

Sources: `src/platform_workflow/cli.py`, `src/platform_workflow/worker.py`

## Open questions

- core commit: `pyproject.toml` pin `dc1fa7d` แต่ `docs/RUNTIME.md` ยังเขียน `21d5f44` — เอกสารต้องอัปเดต
