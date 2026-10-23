# Workflow staging and operations

Stage: **implemented-local**; container/Railway configuration is staged only.
No service was deployed, no provider was activated and no consumer was cut over.

## Configure and migrate

Supply `DATABASE_URL` explicitly for the service-owned PostgreSQL database.
Run `platform-workflow migrate` as a separate release step with a migration
role. Shared-core `MigrationRunner(namespace='workflow')` applies checksummed,
advisory-locked migrations. Never run migrations during each API startup.

The API/worker runtime role must be NOSUPERUSER/NOBYPASSRLS, with schema USAGE
and SELECT/INSERT/UPDATE on `workflow_jobs` and `workflow_attempts`, and no DDL.
Do not grant access to another service's tables. All tenant tables ENABLE and
FORCE RLS with matching USING/WITH CHECK `app.workspace_id` policies. Missing
workspace sees zero tenant rows. Migrations can use a distinct owner; table
ownership is unnecessary for runtime.

Set `JWT_ISSUER`, `JWT_AUDIENCE`, and exactly one of
`JWT_PUBLIC_KEY` or `JWT_JWKS_URL`. Backward-compatible aliases are
`WORKFLOW_JWT_ISSUER`, `WORKFLOW_JWT_AUDIENCE`, `WORKFLOW_JWT_PUBLIC_KEY` and
`WORKFLOW_JWKS_URL`; standard names take precedence. These are explicit issuer trust
settings; this staging does not replace the identity shadow issuer. Configure
keys/authorization through the deployment environment, never image layers or
repository files. Missing trust settings fail startup closed.

Run `platform-workflow api`, which binds `PORT` (default 8080). API pool maximum
is `WORKFLOW_POOL_MAX` (default 8); worker default is 2. Connections across all
replicas must remain below the database budget. `railway.toml` stages one API
replica with `/readyz`; replicas can increase independently after connection,
load, migration and backup gates are proven. A worker is a separate service;
it must have explicit workspace authorization and queues as described in
[RUNTIME.md](RUNTIME.md). SIGTERM/SIGINT stops CLI polling; a running handler
finishes before the process closes its pool. Set handler timeouts and a process
termination grace appropriate to that bound.

Build `Dockerfile` only after pinning a published core wheel/revision in the
release dependency lock. The package pins `platform-backend-core==0.1.0`;
the parent release supplies the offline wheel bundle and immutable artifact digests. This checkout does not
invent a published artifact or download a hub checkout.

## Diagnose and recover

`/healthz` checks process liveness. `/readyz` checks connectivity, both service
tables, schema USAGE, each required SELECT/INSERT/UPDATE privilege, and
enabled/forced RLS; it returns 503 for missing migrations or failed
dependencies. `/metrics` exposes shared redacted HTTP counters/duration. Alert
on sustained readiness failure, queue age, dead-letter growth and exhausted
connections. Backlog/age collection must use separately authorized workspace
polls; no privileged cross-workspace data endpoint is provided here.

Worker process loss leaves a durable lease. After database expiry a poller
reclaims it, marks the former attempt expired and increments attempts; the last
failed/expired attempt becomes an inspectable dead-letter record. Read the
job/attempts API with the relevant workspace token. Reconcile ambiguous provider
effects before creating a new business operation. There is no automated
replay of a dead-letter that could discard dedupe history.

Take database-native backups using the owning environment's tooling; test
restore into a **new disposable database** with the same migration version,
nonprivileged runtime role and tenant policies before any production release.
No destructive restore command is included. The parent infrastructure program
owns integrated backup/restore and load evidence; the local queue suite alone
does not certify production recovery or throughput.

Rollback by routing consumers back to the existing task adapter and stopping
new queue producers/workers. Retain this database and idempotency/attempt
history for reconciliation. Do not drop tables or rewrite migration checksums.
Before release obtain independent review, pin all artifacts, rehearse restore,
measure multi-replica load and approve the exact environment/resource change.
