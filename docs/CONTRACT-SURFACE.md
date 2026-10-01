# Contract surface: book-workflow-platform

This page documents the API, event, CLI, storage and configuration surface that
`book-workflow-platform` owns or consumes. The machine-readable list is `interfaces` in
[`contract.json`](../contract.json); `scripts/check.py` fails when an interface
listed there is missing from this page.

- Platform: `workflow` (contract `book-platform.contract.v1`, API `v1`)
- Status: `scaffolded`; source status: `current-implementation-in-solo-empire`
- Current implementation: `solo-empire` at `infra/tasks`, `infra/scripts`
- Depends on: `identity`, `observability`

Interfaces marked `current-in-solo-empire` are served by the `solo-empire`
control plane today. This repository does not serve them yet; they are the
compatibility surface a migration must preserve. The `source` column is a path
in the implementing repository.

## Interfaces

| ID | Kind | Direction | Interface | Contract | Auth | Source | Status |
|---|---|---|---|---|---|---|---|
| `workflow.cli.task-includes` | cli | provides | Root Taskfile.yml includes infra/tasks/<namespace>.yml (flattened task names) | - | - | `infra/tasks` | current-in-solo-empire |
| `workflow.cli.scripts` | cli | provides | Python/shell job entry points under infra/scripts invoked by tasks | - | - | `infra/scripts` | current-in-solo-empire |

## Behaviour notes

Derived from the current source; re-check the source before changing a
consumer.

- Jobs are Task (`go-task`) entry points: the root `Taskfile.yml` includes
  `infra/tasks/{ai,data,finance,governance,integrations,learning,operations,platform,portfolio,products,runtime,trading}.yml`
  with flattened names (for example `task platforms:check`).
- There is no durable queue, scheduler state or execution history store yet;
  those registry capabilities are migration targets.
- Business invariants stay in the platform that calls a workflow; this
  platform owns execution state only.

## Migration gates

- durable execution
- retry semantics
- duplicate job protection

## Out of scope

No production provider integration, credential, database writer or customer
payload lives in this repository. Record parity, privacy and rollback evidence
in the parent platform registry before any cutover.
