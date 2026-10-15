# BOO-165 — Scheduler ownership, retry/idempotency and execution-history parity

Status: **evidence only**. Nothing here changes a schedule, installs or removes a
timer, moves a runtime, or records an owner decision. The decision section at the
end is a proposal.

## Gate

- Registry: `bookchaowalit/solo-empire` `repository-catalog/registries/system-repositories.yaml:630-653`
  (`id: workflow-platform`, solo-empire `63efeeb0`).
- Next gate (verbatim): **"Capture scheduler ownership, retry/idempotency behavior, and execution history parity."**
- Contract: `bookchaowalit/architecture-docs` `systems/architecture/platform-repository-split.md` (`ed941809`):
  - `platform-repository-split.md#data-และ-interface-ownership`,
    line 76-77: the workflow platform owns **execution state**; business invariants stay in the calling platform.
  - `platform-repository-split.md#migration-stages`,
    stage 3 *Compatibility* (line 113-115): "Record parity fixtures and idempotency behavior."
  - `platform-repository-split.md#repository-baseline`,
    line 140: sanitized fixtures for success, duplicate delivery, timeout and provider failure.

## What was inspected (read-only, pinned)

| Source key | Repository @ commit | Why |
|---|---|---|
| `solo-empire-retired` | `bookchaowalit/solo-empire` @ `1d0e04bb` | Last tree before `3bf3637c` ("retire the local SQLite control plane, the cron runner …; none of the 40 jobs ever ran"). Holds the cron runner, healer, JOB_MAP, cron_jobs migrations, host installers and CI schedules. |
| `solo-empire-head` | `bookchaowalit/solo-empire` @ `63efeeb0` | Current hub. **Declares no schedule at all**: `infra/tasks` and `infra/scripts/workflows` no longer exist there (moved out in `5f034945`). |
| `platform-ops-cli` | `bookchaowalit/platform-ops-cli` (formerly solo-ops) @ `f3729594` | Current owner of `infra/tasks` and `infra/scripts/workflows`; the only place with schedule installers in source today (3 systemd user timers). |
| `platform-workflow` | this repo @ `origin/main` `1cbd353e` | Target owner. Ships contract/scaffold checks only; **no job, retry, idempotency or history code**. |

Method: `scripts/scheduler_inventory.py extract` runs `git grep -lE <marker regex>` at each
pinned commit (markers: `OnCalendar`, `- cron:`, `crontab -`, `Register-ScheduledTask`,
`New-ScheduledTaskTrigger`, `schtasks`, `launchctl`, `StartCalendarInterval`,
`INTO cron_jobs`, `UPDATE cron_jobs`, …) and routes every hit to a parser or to an
explicit ignore rule (tests, docs, generated reports, two files that only read a registry
`schedule` field, and `integrations/google/setup_calendar.py`, which only prints a
suggested crontab line). Zero files were left unclassified. The 120 raw records are
pinned in [`fixtures/gates/workflow-scheduler-source-snapshot.json`](../../fixtures/gates/workflow-scheduler-source-snapshot.json)
with path, line and the exact source line; cron_jobs schedules are replayed across the
migrations (first INSERT creates the row, `AND is_active = 1` updates skip inactive rows).
No launchd plist exists in any of the three trees.

Live host state (installed crontabs, Task Scheduler tasks, systemd timers, the
`solo-automation` cell rows) was **not** inspected; schedules below are what the source declares.

## Inventory (69 scheduled jobs)

Machine-readable: [`fixtures/gates/workflow-scheduler-inventory.json`](../../fixtures/gates/workflow-scheduler-inventory.json)
(per job: schedule, status, current owner, current runner, target owner, retry policy,
idempotency key, execution-history location, citations). Citations below are
`path:line` in the source key's repository; `local-cron` paths are in `solo-empire-retired`,
`systemd` paths in `platform-ops-cli`.

| Group | Jobs | Status |
|---|---|---|
| `systemd:*` (platform-ops-cli user timers) | 3 | active installer in source |
| `local-cron:*` JOB_MAP jobs of the retired runner | 40 | retired (`3bf3637c`) |
| `local-cron:*` cron_jobs rows with no JOB_MAP entry | 5 | retired orphan rows (never runnable) |
| `crontab:*` data-lake capture installers | 6 | retired host installers (`9f44f602`) |
| `windows-task:*` (scraping 7, discovery 1, DNS sync 1) | 9 | retired host installers (`9f44f602`) |
| `gha:*` GitHub Actions `on: schedule` | 6 | retired CI schedules: `backup` removed in `3bf3637c`; `data-lake-cloud-contract`, `free-cloud-e2e` in `9f44f602`; `free-resource-upstream-audit`, `health-check`, `scorecard` in `5f034945` (`git log --diff-filter=D`) |

### Full table

| # | Job id | Schedule (source) | Status | Current runner | Target owner key | Retry policy | Idempotency key | History | Source citation(s) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `systemd:book-dev-dispatch` | `*-*-* 08,10,12,14,16,18,20,22:00:00 Asia/Bangkok` | active-installer-in-source | systemd --user book-dev-dispatch.timer -> book-dev-dispatch.service (Type=oneshot) | ops-timer-rehome | systemd-oneshot-no-retry | Linear issue identifier in state.json dispatched ledger (book_dev_linear_cycle.py:14-15, :176) | systemd-journal | `infra/scripts/workflows/install_book_dev_dispatch_timer.py:32` |
| 2 | `systemd:bookchaowalit-org-hub-sync` | `*-*-* 09:00:00 Asia/Bangkok` | active-installer-in-source | systemd --user bookchaowalit-org-hub-sync.timer -> bookchaowalit-org-hub-sync.service (Type=oneshot) | ops-timer-rehome | systemd-oneshot-no-retry | exclusive flock .runtime/org-hub-sync.lock (org_hub_registry.py:465); content-addressed registry diff (unchanged -> no write) | systemd-journal | `infra/scripts/sync/install_org_hub_timer.py:18` |
| 3 | `systemd:tracker-sync-pilot` | `*-*-* *:00/15:00` | active-installer-in-source | systemd --user tracker-sync-pilot.timer -> tracker-sync-pilot.service (Type=oneshot) | ops-timer-rehome | systemd-oneshot-no-retry | checkpoint.json under --state-dir plus managed-block markers tracker-sync:task-177 (tracker_sync.py:37, :147) | systemd-journal | `infra/templates/tracker-sync/tracker-sync-pilot.timer:5` |
| 4 | `local-cron:analytics_dashboard` | `45 1 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run analytics_dashboard | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/analytics_dashboard.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:26`<br>`infra/scripts/utils/cron_registry.py:73` |
| 5 | `local-cron:auto_archive` | `0 0 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run auto_archive | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/auto_archive.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/010_automation.sql:172`<br>`infra/database/migrations/069_harden_cron_execution.sql:38`<br>`infra/scripts/utils/cron_registry.py:30` |
| 6 | `local-cron:backup_db` | `0 3 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run backup_db | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/backup_db.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:31`<br>`infra/scripts/utils/cron_registry.py:68` |
| 7 | `local-cron:book_dev_loop` | `30 5 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run book_dev_loop | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/book_dev_loop.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/038_book_dev_engineering_loop.sql:54`<br>`infra/database/migrations/069_harden_cron_execution.sql:36`<br>`infra/scripts/utils/cron_registry.py:52` |
| 8 | `local-cron:brave_bookmarks_maintenance` | `every_4h` (inactive) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run brave_bookmarks_maintenance | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/brave_bookmarks_maintenance.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/058_register_brave_bookmark_maintenance.sql:54`<br>`infra/scripts/utils/cron_registry.py:85` |
| 9 | `local-cron:check_notifications` | `30 0 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run check_notifications | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/check_notifications.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/010_automation.sql:170`<br>`infra/database/migrations/069_harden_cron_execution.sql:21`<br>`infra/scripts/utils/cron_registry.py:27` |
| 10 | `local-cron:cron_heal` | `0 */4 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run cron_heal | local-cron-rehome | legacy-local-cron-heal-self | job_lock:data/locks/cron_heal.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/070_normalize_cron_schedules.sql:5`<br>`infra/scripts/utils/cron_registry.py:78` |
| 11 | `local-cron:daily_diary` | `45 4 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run daily_diary | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/daily_diary.lock (overlap guard only; no per-scheduled-run key); date-keyed overwrite of diary/entries/YYYY/MM/DD/auto-daily.md (cron_registry.py:42-44) | legacy-automation-cell | `infra/database/migrations/037_register_daily_diary_job.sql:53`<br>`infra/database/migrations/069_harden_cron_execution.sql:35`<br>`infra/scripts/utils/cron_registry.py:44` |
| 12 | `local-cron:data_freshness` | `0 */6 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run data_freshness | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/data_freshness.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/070_normalize_cron_schedules.sql:7`<br>`infra/scripts/utils/cron_registry.py:79` |
| 13 | `local-cron:db_health_check` | `15 1 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run db_health_check | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/db_health_check.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:24`<br>`infra/scripts/utils/cron_registry.py:34` |
| 14 | `local-cron:decision_brief_daily` | `30 4 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run decision_brief_daily | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/decision_brief_daily.lock (overlap guard only; no per-scheduled-run key); aggregate files under data/generated only (cron_registry.py:56-57) | legacy-automation-cell | `infra/database/migrations/034_register_decision_brief_jobs.sql:53`<br>`infra/database/migrations/069_harden_cron_execution.sql:34`<br>`infra/scripts/utils/cron_registry.py:58` |
| 15 | `local-cron:decision_brief_weekly` | `15 1 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run decision_brief_weekly | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/decision_brief_weekly.lock (overlap guard only; no per-scheduled-run key); aggregate files under data/generated only (cron_registry.py:56-57) | legacy-automation-cell | `infra/database/migrations/034_register_decision_brief_jobs.sql:67`<br>`infra/database/migrations/069_harden_cron_execution.sql:43`<br>`infra/scripts/utils/cron_registry.py:59` |
| 16 | `local-cron:decision_review` | `15 4 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run decision_review | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/decision_review.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/031_register_decision_review_job.sql:48`<br>`infra/database/migrations/069_harden_cron_execution.sql:33`<br>`infra/scripts/utils/cron_registry.py:41` |
| 17 | `local-cron:diary_monthly_summary` | `0 2 1 * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run diary_monthly_summary | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/diary_monthly_summary.lock (overlap guard only; no per-scheduled-run key); label-keyed overwrite of summaries/<cadence>/<label>.md (cron_registry.py:45-46) | legacy-automation-cell | `infra/database/migrations/061_register_diary_summary_jobs.sql:71`<br>`infra/database/migrations/069_harden_cron_execution.sql:46`<br>`infra/scripts/utils/cron_registry.py:48` |
| 18 | `local-cron:diary_weekly_summary` | `30 1 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run diary_weekly_summary | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/diary_weekly_summary.lock (overlap guard only; no per-scheduled-run key); label-keyed overwrite of summaries/<cadence>/<label>.md (cron_registry.py:45-46) | legacy-automation-cell | `infra/database/migrations/061_register_diary_summary_jobs.sql:57`<br>`infra/database/migrations/069_harden_cron_execution.sql:44`<br>`infra/scripts/utils/cron_registry.py:47` |
| 19 | `local-cron:domain_scanner` | `45 0 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run domain_scanner | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/domain_scanner.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:41`<br>`infra/scripts/utils/cron_registry.py:36` |
| 20 | `local-cron:engagement_notifications` | `0 4 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run engagement_notifications | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/engagement_notifications.lock (overlap guard only; no per-scheduled-run key); queues deduplicated digests (migration 022 description) | legacy-automation-cell | `infra/database/migrations/022_engagement_notification_delivery.sql:15`<br>`infra/database/migrations/069_harden_cron_execution.sql:32`<br>`infra/scripts/utils/cron_registry.py:28` |
| 21 | `local-cron:finance_daily_notification` | `35 7 * * *` | retired-orphan-row | user crontab -> infra/scripts/utils/cron_runner.py --run finance_daily_notification | retire-orphan | legacy-local-cron-unrunnable | job_lock:data/locks/finance_daily_notification.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/073_register_finance_daily_notification.sql:59` |
| 22 | `local-cron:finance_daily_review` | `30 7 * * *` | retired-orphan-row | user crontab -> infra/scripts/utils/cron_runner.py --run finance_daily_review | retire-orphan | legacy-local-cron-unrunnable | job_lock:data/locks/finance_daily_review.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/072_register_finance_daily_review.sql:59` |
| 23 | `local-cron:generate_tasks` | `15 0 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run generate_tasks | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/generate_tasks.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/010_automation.sql:169`<br>`infra/database/migrations/069_harden_cron_execution.sql:20`<br>`infra/scripts/utils/cron_registry.py:26` |
| 24 | `local-cron:gmail_categorize` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run gmail_categorize | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/gmail_categorize.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:93` |
| 25 | `local-cron:gmail_daily_digest` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run gmail_daily_digest | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/gmail_daily_digest.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:95` |
| 26 | `local-cron:gmail_filters` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run gmail_filters | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/gmail_filters.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:96` |
| 27 | `local-cron:gmail_to_todoist` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run gmail_to_todoist | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/gmail_to_todoist.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:94` |
| 28 | `local-cron:goal_progress` | — (see gap) (inactive) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run goal_progress | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/goal_progress.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:10`<br>`infra/scripts/utils/cron_registry.py:33` |
| 29 | `local-cron:goal_velocity` | `30 2 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run goal_velocity | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/goal_velocity.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:29`<br>`infra/scripts/utils/cron_registry.py:76` |
| 30 | `local-cron:habit_analytics` | `15 2 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run habit_analytics | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/habit_analytics.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:28`<br>`infra/scripts/utils/cron_registry.py:75` |
| 31 | `local-cron:job_data_readiness` | `0 */6 * * *` (inactive) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run job_data_readiness | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/job_data_readiness.lock (overlap guard only; no per-scheduled-run key); read-only gate (cron_registry.py:60-63) | legacy-automation-cell | `infra/database/migrations/075_register_job_data_readiness.sql:61`<br>`infra/scripts/utils/cron_registry.py:64` |
| 32 | `local-cron:media_lifecycle` | `0 1 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run media_lifecycle | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/media_lifecycle.lock (overlap guard only; no per-scheduled-run key); read-only plan; quarantine/purge never run by cron (cron_registry.py:69-70) | legacy-automation-cell | `infra/database/migrations/018_media_lifecycle_job.sql:49`<br>`infra/database/migrations/069_harden_cron_execution.sql:42`<br>`infra/scripts/utils/cron_registry.py:71` |
| 33 | `local-cron:reconcile_overdue` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run reconcile_overdue | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/reconcile_overdue.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:97` |
| 34 | `local-cron:refresh_dashboards` | `0 8,20 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run refresh_dashboards | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/refresh_dashboards.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/070_normalize_cron_schedules.sql:10`<br>`infra/scripts/utils/cron_registry.py:38` |
| 35 | `local-cron:revenue_analytics` | `0 2 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run revenue_analytics | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/revenue_analytics.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:27`<br>`infra/scripts/utils/cron_registry.py:74` |
| 36 | `local-cron:revenue_autorun` | `0 1 * * *` | retired-orphan-row | user crontab -> infra/scripts/utils/cron_runner.py --run revenue_autorun | retire-orphan | legacy-local-cron-unrunnable | job_lock:data/locks/revenue_autorun.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:23` |
| 37 | `local-cron:run_alerts` | `0 8,20 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run run_alerts | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/run_alerts.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/070_normalize_cron_schedules.sql:9`<br>`infra/scripts/utils/cron_registry.py:37` |
| 38 | `local-cron:sales_pipeline` | `45 1 * * 0` | retired-orphan-row | user crontab -> infra/scripts/utils/cron_runner.py --run sales_pipeline | retire-orphan | legacy-local-cron-unrunnable | job_lock:data/locks/sales_pipeline.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/063_register_sales_pipeline_job.sql:56`<br>`infra/database/migrations/069_harden_cron_execution.sql:45` |
| 39 | `local-cron:smart_sync` | `30 6 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run smart_sync | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/smart_sync.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/010_automation.sql:168`<br>`infra/database/migrations/069_harden_cron_execution.sql:37`<br>`infra/scripts/utils/cron_registry.py:25` |
| 40 | `local-cron:sync_all` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run sync_all | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/sync_all.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:92` |
| 41 | `local-cron:sync_calendar` | — (see gap) | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run sync_calendar | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/sync_calendar.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/scripts/utils/cron_registry.py:81` |
| 42 | `local-cron:sync_gmail` | `0 6 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run sync_gmail | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/sync_gmail.lock (overlap guard only; no per-scheduled-run key); re-run over the 2-day lookback is idempotent per the registry note (cron_registry.py:89-90) | legacy-automation-cell | `infra/database/migrations/070_normalize_cron_schedules.sql:8`<br>`infra/scripts/utils/cron_registry.py:91` |
| 43 | `local-cron:system_status` | `30 1 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run system_status | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/system_status.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:25`<br>`infra/scripts/utils/cron_registry.py:35` |
| 44 | `local-cron:take_snapshot` | `5 0 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run take_snapshot | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/take_snapshot.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/010_automation.sql:167`<br>`infra/database/migrations/069_harden_cron_execution.sql:19`<br>`infra/scripts/utils/cron_registry.py:24` |
| 45 | `local-cron:time_analytics` | `45 2 * * *` | retired-orphan-row | user crontab -> infra/scripts/utils/cron_runner.py --run time_analytics | retire-orphan | legacy-local-cron-unrunnable | job_lock:data/locks/time_analytics.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:30` |
| 46 | `local-cron:update_goals` | `45 0 * * *` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run update_goals | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/update_goals.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/010_automation.sql:171`<br>`infra/database/migrations/069_harden_cron_execution.sql:22`<br>`infra/scripts/utils/cron_registry.py:29` |
| 47 | `local-cron:weekly_habit_autogranted` | `15 0 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run weekly_habit_autogranted | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/weekly_habit_autogranted.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:39`<br>`infra/scripts/utils/cron_registry.py:31` |
| 48 | `local-cron:weekly_review_gen` | `30 0 * * 0` | retired-local-cron | user crontab -> infra/scripts/utils/cron_runner.py --run weekly_review_gen | local-cron-rehome | legacy-local-cron-heal-once | job_lock:data/locks/weekly_review_gen.lock (overlap guard only; no per-scheduled-run key) | legacy-automation-cell | `infra/database/migrations/069_harden_cron_execution.sql:40`<br>`infra/scripts/utils/cron_registry.py:32` |
| 49 | `crontab:book-crypto-data-feeds` | `0 */4 * * *` | retired-host-installer | user crontab tag '# book-crypto-data-feeds' -> data-lake capture runner | data-capture-rehome | data-lake-capture-in-process-2-attempts | lock file data/market-feeds.lock (overlap); crontab tag '# book-crypto-data-feeds' (install) | data-lake-capture-logs | `infra/scripts/data_lake/setup_market_cron.sh:85` |
| 50 | `crontab:book-defi-data-feeds` | `0 7 * * *` | retired-host-installer | user crontab tag '# book-defi-data-feeds' -> data-lake capture runner | data-capture-rehome | data-lake-capture-in-process-2-attempts | lock file data/market-feeds.lock (overlap); crontab tag '# book-defi-data-feeds' (install) | data-lake-capture-logs | `infra/scripts/data_lake/setup_market_cron.sh:88` |
| 51 | `crontab:book-fx-data-feeds` | `0 */6 * * *` | retired-host-installer | user crontab tag '# book-fx-data-feeds' -> data-lake capture runner | data-capture-rehome | data-lake-capture-in-process-2-attempts | lock file data/market-feeds.lock (overlap); crontab tag '# book-fx-data-feeds' (install) | data-lake-capture-logs | `infra/scripts/data_lake/setup_market_cron.sh:87` |
| 52 | `crontab:book-scraping-scheduler` | `0 */6 * * *` | retired-host-installer | user crontab tag '# book-scraping-scheduler' -> data-lake capture runner | data-capture-rehome | data-lake-capture-in-process-2-attempts | lock file data/jobs-feeds.lock (overlap); crontab tag '# book-scraping-scheduler' (install) | data-lake-capture-logs | `infra/scripts/data_lake/setup_jobs_cron.sh:72` |
| 53 | `crontab:book-seo-data-feeds` | `30 8 * * *` | retired-host-installer | user crontab tag '# book-seo-data-feeds' -> data-lake capture runner | data-capture-rehome | data-lake-capture-in-process-2-attempts | lock file data/seo-feeds.lock (overlap); crontab tag '# book-seo-data-feeds' (install) | data-lake-capture-logs | `infra/scripts/data_lake/setup_seo_cron.sh:62` |
| 54 | `crontab:book-stock-data-feeds` | `0 8 * * *` | retired-host-installer | user crontab tag '# book-stock-data-feeds' -> data-lake capture runner | data-capture-rehome | data-lake-capture-in-process-2-attempts | lock file data/market-feeds.lock (overlap); crontab tag '# book-stock-data-feeds' (install) | data-lake-capture-logs | `infra/scripts/data_lake/setup_market_cron.sh:86` |
| 55 | `windows-task:SoloEmpire-DNS-Sync` | `at-logon` | retired-host-installer | Windows Task Scheduler (at logon) -> wsl.exe sync-dns.sh | retire-workstation | windows-task-no-retry | task name SoloEmpire-DNS-Sync with -Force (install); none per run | windows-task-scheduler-only | `infra/self-hosted-stack/setup-auto-dns.ps1:20` |
| 56 | `windows-task:SoloEmpire-Opportunity-Discovery` | `every 6h` | retired-host-installer | Windows Task Scheduler -> run_windows_acquisition.ps1 -> run_scheduled_acquisition.py | data-capture-rehome | windows-task-no-retry | task name SoloEmpire-Opportunity-Discovery (install); MultipleInstances IgnoreNew (overlap) | windows-discovery-log | `infra/scripts/discovery/setup_windows_task.ps1:75` |
| 57 | `windows-task:SoloEmpire-Scraping-crypto` | `every 4h` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product crypto | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=crypto (install); lock data/logs/windows-scraping/crypto.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:165` |
| 58 | `windows-task:SoloEmpire-Scraping-defi` | `daily 07:00` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product defi | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=defi (install); lock data/logs/windows-scraping/defi.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:166` |
| 59 | `windows-task:SoloEmpire-Scraping-fx` | `every 6h` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product fx | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=fx (install); lock data/logs/windows-scraping/fx.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:165` |
| 60 | `windows-task:SoloEmpire-Scraping-monitor` | `every 1h` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product monitor | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=monitor (install); lock data/logs/windows-scraping/monitor.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:165` |
| 61 | `windows-task:SoloEmpire-Scraping-news` | `every 2h` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product news | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=news (install); lock data/logs/windows-scraping/news.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:165` |
| 62 | `windows-task:SoloEmpire-Scraping-seo` | `daily 08:30` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product seo | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=seo (install); lock data/logs/windows-scraping/seo.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:166` |
| 63 | `windows-task:SoloEmpire-Scraping-stocks` | `daily 08:00` | retired-host-installer | Windows Task Scheduler -> windows_scraping_scheduler.py run --product stocks | data-capture-rehome | data-lake-capture-in-process-2-attempts | task description marker solo-empire.windows-scraping.v1;job=stocks (install); lock data/logs/windows-scraping/stocks.lock + IgnoreNew (overlap) | windows-scraping-logs | `infra/scripts/data_lake/windows_scraping_scheduler.py:166` |
| 64 | `gha:backup` | `0 2 * * *` | retired-ci-schedule | GitHub Actions .github/workflows/backup.yml | ci-stays-in-ci | github-actions-no-retry | none (each scheduled run is independent) | github-actions-run-history | `.github/workflows/backup.yml:5` |
| 65 | `gha:data-lake-cloud-contract` | `30 2 * * 1` | retired-ci-schedule | GitHub Actions .github/workflows/data-lake-cloud-contract.yml | ci-stays-in-ci | github-actions-no-retry | none (each scheduled run is independent) | github-actions-run-history | `.github/workflows/data-lake-cloud-contract.yml:20` |
| 66 | `gha:free-cloud-e2e` | `17 3 * * 1` | retired-ci-schedule | GitHub Actions .github/workflows/free-cloud-e2e.yml | ci-stays-in-ci | github-actions-no-retry | none (each scheduled run is independent) | github-actions-run-history | `.github/workflows/free-cloud-e2e.yml:20` |
| 67 | `gha:free-resource-upstream-audit` | `30 1 * * 1` | retired-ci-schedule | GitHub Actions .github/workflows/free-resource-upstream-audit.yml | ci-stays-in-ci | github-actions-no-retry | none (each scheduled run is independent) | github-actions-run-history | `.github/workflows/free-resource-upstream-audit.yml:6` |
| 68 | `gha:health-check` | `0 */6 * * *` | retired-ci-schedule | GitHub Actions .github/workflows/health-check.yml | ci-stays-in-ci | github-actions-no-retry | none (each scheduled run is independent) | github-actions-run-history | `.github/workflows/health-check.yml:5` |
| 69 | `gha:scorecard` | `0 6 * * 1` | retired-ci-schedule | GitHub Actions .github/workflows/scorecard.yml | ci-stays-in-ci | github-actions-no-retry | none (each scheduled run is independent) | github-actions-run-history | `.github/workflows/scorecard.yml:8` |

## Retry behaviour (current → parity requirement)

| Policy key | Behaviour in source | Max attempts per scheduled run | Evidence |
|---|---|---|---|
| `legacy-local-cron-heal-once` | No in-run retry. `cron_heal` (every 4h) retries an active job with `last_status='failed'` from the last 24h once (`retry_count < 1`) under the same per-job lock; a primary success/failure resets `retry_count`; still failing → Telegram alert; `google_oauth_invalid_grant` → manual recovery, no retry; lock overlap exits 0; `credentials_not_configured` counts as success. | 2 | `cron_healer.py:65-77, 231-236, 238-250, 262, 290`; `cron_runner.py:87-91, 194-197, 419-421`; `069_harden_cron_execution.sql:1-6` |
| `legacy-local-cron-heal-self` | `cron_heal` never retries itself. | 1 | `cron_healer.py:71, 197` |
| `legacy-local-cron-unrunnable` | Row without JOB_MAP entry: `--install` skips it, `--run` rejects it. | 0 | `cron_runner.py:152, 289` |
| `data-lake-capture-in-process-2-attempts` | Capture runner called with `--attempts 2` (`--timeout-seconds 180` for crontab installers); no scheduler retry; lock file blocks overlap. | 2 | `setup_jobs_cron.sh:71`, `setup_seo_cron.sh:61`, `setup_market_cron.sh:70`, `windows_scraping_scheduler.py:284-293` |
| `windows-task-no-retry` | Task Scheduler, no restart settings; `IgnoreNew` drops overlaps. | 1 | `setup_windows_task.ps1:77-81`, `setup-auto-dns.ps1:24` |
| `github-actions-no-retry` | Next schedule or manual dispatch; `health-check` opens an issue. | 1 | `health-check.yml:10` |
| `systemd-oneshot-no-retry` | `Type=oneshot`, `TimeoutStartSec`, no `Restart=`; a running oneshot blocks the next start; `Persistent=true` catch-up for org-hub and tracker-sync, `Persistent=false` for book-dev-dispatch. | 1 | `install_book_dev_dispatch_timer.py:50, 52, 67`; `install_org_hub_timer.py:41, 49`; `tracker-sync-pilot.service:7, 10`; `tracker-sync-pilot.timer:6` |

## Idempotency behaviour

- **Retired local cron:** the only guard is a non-blocking per-job file lock
  `data/locks/<job_type>.lock` (`cron_runtime.py:63-82`). It prevents overlap; there is
  **no per-scheduled-run idempotency key** (no `(job, scheduled_for)` identity), so a
  heal retry and the next primary run are indistinguishable to the job. Some jobs are
  idempotent by construction (date/label-keyed file overwrites for diary and briefs;
  `sync_gmail` re-run over a 2-day lookback, `cron_registry.py:89-90`). Failures
  dedupe downstream as `cron_failed:{job_type}` decision proposals (`docs/architecture/CRON-DAG.md:112-118`).
- **Host installers:** install-time idempotency by crontab tag / Task Scheduler task name
  or description marker; run-time overlap by lock file or `IgnoreNew`.
- **platform-ops-cli timers:** `book-dev-dispatch` has a real per-item key — the Linear
  issue identifier in the `state.json` ledger (`book_dev_linear_cycle.py:14-15, 176-177`);
  `tracker-sync-pilot` keeps `checkpoint.json` and managed-block markers
  (`tracker_sync.py:37, 147`); org-hub sync takes an exclusive flock (`org_hub_registry.py:465-475`).
- **platform-workflow main:** none. PR #1 (`codex/shared-infrastructure-20261015`,
  not merged, not modified here) would add workspace-scoped `idempotency_key` + payload
  fingerprint on enqueue, `max_attempts`, exponential backoff (`2^attempts` s, capped
  3600 s), lease fencing and a `workflow_attempts` table. It needs PostgreSQL and
  third-party packages, so this gate does not exercise it.

## Execution-history parity

| History key | What exists | Durable per-run record? |
|---|---|---|
| `legacy-automation-cell` | Overwritten `last_run_at/last_status/retry_count/last_retry_at` on the `solo-automation` cell `cron-jobs` row; best-effort `automation-log` append (errors swallowed); stdout appended to `data/logs/cron.log`. (`cron_runner.py:87-126, 284-299`; cron_jobs table dropped by `085_…sql`.) | No |
| `data-lake-capture-logs` | `data/logs/<feed>-feeds-cron.log` + `data/logs/<feed>-capture/` | No (host files) |
| `windows-scraping-logs` / `windows-discovery-log` | Rotating logs (2 MB × 3) + Task Scheduler last result | No |
| `windows-task-scheduler-only` | Task Scheduler last result only | No |
| `github-actions-run-history` | GitHub run list (workflows since removed) | Yes, but CI-only |
| `systemd-journal` | journald + job state files (ledger, checkpoint) | No |

Parity conclusion: there is no legacy per-attempt history to migrate or shadow-compare.
Parity can only be defined forward (a new attempt-level record), not by backfill.
`3bf3637c` states none of the 40 local jobs ever ran; that claim was not independently
verified here (no live state inspected).

## Gaps

1. **Registry source paths are stale.** `system-repositories.yaml:634` and this repo's
   `contract.json:23-26` name `infra/tasks` / `infra/scripts` in solo-empire; at solo-empire
   `63efeeb0` those paths do not exist — they live in `platform-ops-cli` since `5f034945`.
2. **No retry/idempotency/history runtime on platform-workflow main** (`origin/main` `1cbd353e`).
   The fixture declares `retry_idempotency_runtime: "pending-pr-1"`. `RuntimeGapTests` does **not** fail when PR #1 lands;
   it prints a warning asking for a BOO-165 follow-up (set `present`, add parity tests). It fails only if the fixture claims
   `present` while no runtime is on the tree.
3. **No per-scheduled-run idempotency key** in any legacy scheduler; overlap locks only.
4. **No durable execution history** anywhere except CI run lists.
5. **18 local jobs have schedules only through `UPDATE … WHERE is_active = 1`** (rows created
   outside migrations, `row_origin: live-db-only`) and **8 have no schedule in source**
   (`sync_calendar`, `sync_all`, four `gmail_*`, `reconcile_overdue`, deactivated `goal_progress`).
   Their real schedules were in the live DB / `solo-automation` cell, which was not inspected.
6. **5 orphan rows** (`revenue_autorun`, `time_analytics`, `sales_pipeline`,
   `finance_daily_review`, `finance_daily_notification`) were scheduled but unrunnable.
   `CRON-DAG.md:157-161` also lists DB-only orphans (`sync_leantime`, `sync_strava`,
   `sync_binance`, `insights`, `generate_pages`, `publish_pages`) that have no source schedule and are not in the fixture.
7. **Doc drift in the retired tree:** `CRON-DAG.md:23` says 5 of 12 workflows had a schedule;
   source has 6 (`free-resource-upstream-audit.yml:6`, `30 1 * * 1`).
8. **Timezones are mixed:** local cron = host local time, GitHub Actions = UTC,
   two ops timers pin `Asia/Bangkok`, `tracker-sync-pilot` uses host local time.
9. **Repository-baseline fixtures** (success, duplicate delivery, timeout, provider failure)
   for a workflow runtime do not exist yet; this gate adds the inventory, not those.
10. **Live install state unknown** for every row (no host inspected).
11. **Out-of-tree installer not inventoried:** `platform-ops-cli@f3729594` `infra/tasks/data.yml:24-27`
    (`scraping:news:schedule:plan`) runs `content-news-scraper/setup_cron.sh` from a separate repository that is not one of
    the three pinned sources; its schedule is not in this fixture.
12. **CI re-extraction covers platform-ops-cli only.** Hosted CI checks out the public `bookchaowalit/platform-ops-cli`
    at `f3729594` and re-extracts its 3 timers against the snapshot (`BOO165_REQUIRE_OPS_SOURCE=1`). solo-empire is
    not reachable from CI, so its 117 records are compared with the real source only by the local re-extraction.

## Owner decision — PROPOSED, NOT ACCEPTED

The owner must accept or amend each item; nothing below is in effect.

1. **Scheduler ownership:** platform-workflow becomes the owner of schedule definitions and
   execution state (attempt history) for re-homed jobs; job logic stays in the owning
   repository/cell (contract line 76-77). First candidates: the three `platform-ops-cli`
   systemd timers, once a runtime is on main.
2. **Retired local cron is not migrated as a set.** The 5 orphan rows are retired. Each of the
   40 JOB_MAP jobs is re-registered only when its owner asks (as `3bf3637c` already says),
   with an explicit schedule and timezone.
3. **Retry parity rule:** legacy "heal once within 24h" is replaced by `max_attempts = 2` per
   scheduled run with backoff, which matches the legacy maximum of 2 attempts;
   `credentials_not_configured` stays a non-failure outcome; OAuth `invalid_grant` stays
   non-retryable.
4. **Idempotency rule:** every scheduled run gets the key `(job_id, scheduled_for)`; overlap is
   blocked by lease, not by a host lock file. Per-item keys such as the Linear ledger stay
   in the job.
5. **History rule:** execution-history parity is defined forward (one durable record per
   attempt with outcome and error class); no backfill from legacy logs.
6. **Out of scope for platform-workflow:** GitHub Actions schedules stay in CI of the repository they check;
   data-capture installers move with their data repositories (scheduling via
   platform-workflow when re-enabled); `SoloEmpire-DNS-Sync` is retired.
7. **Registry fix:** the solo-empire owner updates `workflow-platform.source_paths` (and this
   repo's `contract.json` `current_implementation`, after PR #1 merges to avoid a conflict)
   to the `platform-ops-cli` paths.

## Verification

```bash
bash scripts/check.sh                       # includes the scheduler inventory check + tests
python3 scripts/scheduler_inventory.py check
# local only (read-only git on sibling checkouts):
python3 scripts/scheduler_inventory.py extract --solo-empire ../solo-empire --platform-ops-cli ../platform-ops-cli
SOLO_EMPIRE_ROOT=../solo-empire PLATFORM_OPS_CLI_ROOT=../platform-ops-cli python3 -m unittest tests.test_scheduler_inventory -v
```

`tests/test_scheduler_inventory.py` fails when a schedule in the pinned snapshot is missing from
the inventory or has a different schedule, when a new source file with a scheduling marker is
unclassified, when retry/idempotency/history/owner fields are missing or undefined, when an owner
decision is marked accepted, and when the fixture claims a runtime that is not on the tree (a runtime landing
without a fixture update only prints a warning, so merging PR #1 does not turn CI red). It
also runs the real extractor against a throwaway Git repository. `SnapshotSourceLineTests` only checks that each
record is consistent with the source line stored in the same snapshot; comparison with the real repositories is
`PinnedSourceTests` (local, both sources) and `OpsSourceTests` (hosted CI, platform-ops-cli).
