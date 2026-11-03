# 18 — Git & release workflow

จาก `git log --first-parent origin/main` และ `gh pr list`: งานส่วนใหญ่ทำบน feature branch → PR → CI `Check` เขียว → merge commit `Merge PR #n` บน `main`; บางครั้งเป็น merge branch ตรง (`Merge branch 'sentry'`, `Merge core pin 21d5f44`) หรือ commit ตรงบน main (`a0c280c`, ก่อน 2026-10-16)
ไม่มี tag หรือ GitHub release; deploy เป็นขั้นมือ `railway up --ci` จาก `git archive main`

```mermaid
gitGraph
  commit id: "31a34a2 scaffold"
  commit id: "447f700 rename"
  commit id: "4cdc3b2 fix pin"
  branch boo-165-scheduler-inventory
  commit id: "BOO-165 inventory"
  checkout main
  merge boo-165-scheduler-inventory id: "0656d39 PR 2"
  branch codex-shared-infra
  commit id: "durable jobs"
  checkout main
  merge codex-shared-infra id: "1429550 PR 1"
  commit id: "a0c280c shared CI"
  branch worker-service-token
  commit id: "3bbf571"
  checkout main
  merge worker-service-token id: "9bfe8bc PR 4"
  branch fix-sentry-no-free-text
  commit id: "3728376"
  checkout main
  merge fix-sentry-no-free-text id: "301da88 PR 19"
```

Sources: `git log --first-parent --format='%h %ad %s' origin/main`, `gh pr list -R bookchaowalit-backend/platform-workflow --state all`

```mermaid
flowchart LR
  B["Feature branch"] --> PR["Pull request"]
  PR --> CI["check.yml on push + pull_request"]
  CI -->|"green"| M["Merge commit on main, often by agent"]
  M --> CIM["check.yml on push main"]
  M --> D["Owner or agent: git archive main, railway up --ci"]
  D --> RZ["Verify /readyz 200 and sentry enabled log"]
```

Sources: `.github/workflows/check.yml`, `git log`, ops record: book-platforms-status (2026-11-03) (deploy recipe, "all CLI-deployed, /readyz 200, `sentry: enabled` logged")

ข้อสังเกต: PR #1 แสดงสถานะ CLOSED ใน GitHub แต่เนื้อหาเข้า main ผ่าน branch `codex/shared-infrastructure-20261015-rebased` เป็น `1429550`; PR #5 (`sentry-hardened-scrubber`) ยัง OPEN และถูก supersede แล้ว (ops record)

## Open questions

- ปิด PR #5 ที่ค้างหรือไม่
- ควร tag release ต่อ deploy (เช่น ใช้ `RAILWAY_GIT_COMMIT_SHA` ที่ Sentry อ่านอยู่แล้ว) เพื่อให้ RTM/DORA ตามได้หรือไม่
