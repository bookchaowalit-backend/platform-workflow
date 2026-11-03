# 19 — Service dependency map

```mermaid
flowchart LR
  subgraph Callers
    PRODS["Producers: none wired in repo yet"]
    HUB["Remote MCP hub, cell platform-workflow"]
    UPT["platform-uptime-worker"]
  end
  WF["platform-workflow API, Railway"]
  WK["platform-workflow worker, staged"]
  subgraph Runtime deps
    ID["platform-identity: JWKS, /v1/token"]
    PG[("Neon PostgreSQL platform-workflow")]
    SEN["Sentry SaaS us.sentry.io org"]
  end
  subgraph Build deps
    CORE["platform-backend-core at dc1fa7d, Git"]
    PYPI["PyPI: fastapi, psycopg, uvicorn, sentry-sdk via uv.lock"]
    BASE["Docker python 3.12-slim, uv 0.13.1 by digest"]
    CIW["platform-ci-workflows v1.1.1 at 87dc5e1c"]
    OPS["platform-ops-cli at f3729594, CI only"]
  end
  PRODS -.-> WF
  HUB --> WF
  UPT --> WF
  WF -->|"hard: 401 without JWKS"| ID
  WK -->|"hard: no poll without token"| ID
  WF -->|"hard: /readyz 503"| PG
  WK --> PG
  WF -.->|"soft: optional SENTRY_DSN"| SEN
  CORE --> WF
  PYPI --> WF
  BASE --> WF
  CIW -.-> WF
  OPS -.-> WF
```

| Dependency | Type | Failure effect | Source |
|---|---|---|---|
| platform-identity JWKS | runtime, hard | every `/v1/*` → 401 | `cli.py` `verifier_from_env`, core `auth.py` |
| platform-identity `/v1/token` | runtime, hard (worker) | worker skips polls, retries every 30 s | `cli.py` `worker_principal` |
| Neon PostgreSQL | runtime, hard | `/readyz` 503, requests 500/504 | `app.py`, core `db.py` |
| Sentry | runtime, soft | no error reports | `_sentry.py` |
| platform-backend-core | build | pinned commit; change requires re-lock | `pyproject.toml`, `uv.lock` |
| platform-ci-workflows, platform-ops-cli | CI | CI red | `.github/workflows/check.yml` |
| `observability` platform | declared in `contract.json` | none in code | `contract.json` |

Sources: `src/platform_workflow/cli.py`, `src/platform_workflow/app.py`, `src/platform_workflow/_sentry.py`, `pyproject.toml`, `Dockerfile`, `.github/workflows/check.yml`, `contract.json`, ops record: book-platforms-status (2026-11-03) (MCP hub, uptime worker, Neon project, Sentry `us.sentry.io`)

## Open questions

- service ไหนจะเป็น consumer ตัวแรก (ยังไม่มีใน registry ที่อ่านได้จาก repo)
- identity sleep ด้วย; cold start ต่อกันสองชั้น (workflow + identity JWKS) ใช้เวลารวมเท่าไร
