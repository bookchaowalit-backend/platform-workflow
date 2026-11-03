# 15 — CI/CD pipeline

workflow เดียว `.github/workflows/check.yml` (GitHub-hosted, public repo) มี 4 job ที่รันขนานกัน ไม่มี `needs` ระหว่างกัน และ **ไม่มี deploy job**

```mermaid
flowchart TD
  T(["push any branch, pull_request, workflow_dispatch; concurrency check-ref, cancel in progress; permissions contents read"])
  T --> C["contract: ubuntu-latest, 10 min, matrix Python 3.11 and 3.12"]
  T --> P["python: platform-ci-workflows ci-python.yml v1.1.1 at 87dc5e1c"]
  T --> S["security: platform-ci-workflows security.yml v1.1.1 at 87dc5e1c"]
  T --> I["image: ubuntu-latest, 15 min"]
  C --> C1["checkout, setup-python SHA-pinned"]
  C1 --> C2["check_schema_pin.py"]
  C2 --> C3["checkout bookchaowalit/platform-ops-cli at f3729594"]
  C3 --> C4["scheduler_inventory.py verify-source platform-ops-cli"]
  C4 --> C5["scripts/check.sh: check.py, schema pin, inventory check, unittest tests/"]
  P --> P1["uv sync --frozen --all-extras"]
  P1 --> P2["Start PostgreSQL service"]
  P2 --> P3["Ruff check, format check off"]
  P3 --> P4["mypy"]
  P4 --> P5["pytest -q --cov with PLATFORM_TEST_DSN, then uv build --wheel"]
  P5 --> P6["Coverage gate 77 percent"]
  S --> S1["gitleaks"]
  S --> S2["semgrep"]
  S --> S3["osv-scanner"]
  S --> S4["trivy-fs"]
  I --> I1["docker build --pull, no push"]
  I1 --> I2["import platform_backend_core and platform_workflow.app"]
  I2 --> I3["id -u is not 0"]
  C5 --> END["All green = merge allowed by convention"]
  P6 --> END
  S1 --> END
  I3 --> END
  END -.->|"manual, outside GitHub"| DEP["railway up --ci from git archive main"]
```

Sources: `.github/workflows/check.yml`, `scripts/check.sh`, `pyproject.toml` (`[tool.coverage.run]`, `[tool.mypy]`, `[tool.ruff]`), `bookchaowalit/platform-ci-workflows` `.github/workflows/ci-python.yml` and `security.yml` at `87dc5e1c` (step and job names read via `gh api`), ops record: book-platforms-status (2026-11-03) (deploy recipe)

หมายเหตุ

- coverage threshold 77 เป็น ratchet จากค่าวัด 77.89% วันที่ 2026-10-24 ตาม ADR-0004 (TDD/SDD) ของ central architecture ([ADR-0004](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/decisions/ADR-0004-tdd-sdd-standard.md))
- workflow เก่า `Shared runtime` (มี run ถึง 2026-10-23) ถูกรวมเข้า job `python` แล้ว (comment ใน `check.yml`)
- `tests/test_build_hygiene.py` บังคับว่าทุก `uses:` ต้อง pin ด้วย SHA 40 ตัว

## Open questions

- branch protection / required checks บน `main` ตั้งไว้หรือไม่ (ไม่อยู่ใน repo)
- ควรเพิ่ม deploy job หรือ deploy-smoke หลัง merge แทน `railway up` มือหรือไม่
