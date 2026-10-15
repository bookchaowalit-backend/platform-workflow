"""BOO-165: scheduler inventory parity tests.

The inventory (fixtures/gates/workflow-scheduler-inventory.json) must cover
every schedule in the pinned source snapshot
(fixtures/gates/workflow-scheduler-source-snapshot.json). The snapshot is
extracted read-only from solo-empire and platform-ops-cli at pinned commits by
scripts/scheduler_inventory.py; CI cannot see those repositories, so the
committed snapshot is the pinned copy. Set SOLO_EMPIRE_ROOT and
PLATFORM_OPS_CLI_ROOT to also re-extract it from the real repositories.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("scheduler_inventory", ROOT / "scripts" / "scheduler_inventory.py")
assert _spec is not None and _spec.loader is not None
si = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("scheduler_inventory", si)  # dataclasses resolve the defining module
_spec.loader.exec_module(si)


def _load():
    return si.load(si.INVENTORY_PATH), si.load(si.SNAPSHOT_PATH)


class InventoryParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory, self.snapshot = _load()

    def test_committed_inventory_covers_pinned_snapshot(self) -> None:
        self.assertEqual(si.validate(self.inventory, self.snapshot), [])

    def test_every_source_schedule_has_an_inventory_job(self) -> None:
        source_ids = set(si.effective_jobs(self.snapshot["records"]))
        inventory_ids = {job["id"] for job in self.inventory["jobs"]}
        self.assertEqual(source_ids - inventory_ids, set())
        self.assertEqual(inventory_ids - source_ids, set())

    def test_missing_job_is_reported(self) -> None:
        inventory = copy.deepcopy(self.inventory)
        inventory["jobs"] = [j for j in inventory["jobs"] if j["id"] != "systemd:book-dev-dispatch"]
        errors = si.validate(inventory, self.snapshot)
        self.assertTrue(any("systemd:book-dev-dispatch" in e and "missing from inventory" in e for e in errors), errors)

    def test_schedule_drift_is_reported(self) -> None:
        inventory = copy.deepcopy(self.inventory)
        job = next(j for j in inventory["jobs"] if j["id"] == "local-cron:sync_gmail")
        job["schedule"] = "0 7 * * *"
        errors = si.validate(inventory, self.snapshot)
        self.assertTrue(any("local-cron:sync_gmail" in e and "!= source" in e for e in errors), errors)

    def test_new_schedule_in_source_is_reported(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["records"].append({
            "id": "systemd:new-nightly-job", "source": "platform-ops-cli", "kind": "systemd-timer",
            "schedule": "*-*-* 03:00:00", "path": "infra/templates/new/new-nightly-job.timer", "line": 5,
            "text": "OnCalendar=*-*-* 03:00:00", "extra": {},
        })
        errors = si.validate(self.inventory, snapshot)
        self.assertTrue(any("systemd:new-nightly-job" in e and "missing from inventory" in e for e in errors), errors)

    def test_unclassified_source_file_is_reported(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["sources"]["platform-ops-cli"]["unclassified"] = ["infra/scripts/new_cron_installer.sh"]
        errors = si.validate(self.inventory, snapshot)
        self.assertTrue(any("unclassified" in e for e in errors), errors)

    def test_required_retry_idempotency_history_fields(self) -> None:
        for field in ("retry_policy", "idempotency_key", "execution_history", "current_owner", "target_owner"):
            inventory = copy.deepcopy(self.inventory)
            inventory["jobs"][0][field] = ""
            errors = si.validate(inventory, self.snapshot)
            self.assertTrue(any(f"missing {field}" in e for e in errors), (field, errors))

    def test_undefined_policy_is_reported(self) -> None:
        inventory = copy.deepcopy(self.inventory)
        inventory["jobs"][0]["retry_policy"] = "retry-forever"
        errors = si.validate(inventory, self.snapshot)
        self.assertTrue(any("retry-forever" in e for e in errors), errors)

    def test_owner_decision_cannot_be_marked_accepted(self) -> None:
        inventory = copy.deepcopy(self.inventory)
        next(iter(inventory["target_owners"].values()))["decision"] = "ACCEPTED"
        errors = si.validate(inventory, self.snapshot)
        self.assertTrue(any("PROPOSED, NOT ACCEPTED" in e for e in errors), errors)
        self.assertEqual(self.inventory["owner_decision"], "PROPOSED, NOT ACCEPTED")

    def test_citation_must_point_at_a_snapshot_record(self) -> None:
        inventory = copy.deepcopy(self.inventory)
        inventory["jobs"][0]["citations"].append("solo-empire-retired:infra/nowhere.py:1")
        errors = si.validate(inventory, self.snapshot)
        self.assertTrue(any("infra/nowhere.py" in e for e in errors), errors)


class SnapshotSourceLineTests(unittest.TestCase):
    """Each pinned record's schedule must be re-derivable from its cited source line."""

    def test_records_reparse_from_their_source_line(self) -> None:
        _, snapshot = _load()
        for record in snapshot["records"]:
            expected = record["id"].split(":", 1)[1] if record["kind"] == "job-map" else record["schedule"]
            with self.subTest(record=f"{record['path']}:{record['line']}"):
                self.assertEqual(si.reparse_line(record), expected)

    def test_effective_schedule_replays_migrations(self) -> None:
        _, snapshot = _load()
        jobs = si.effective_jobs(snapshot["records"])
        # 010 seeds 'daily'; 069 restaggers active rows to 00:05.
        self.assertEqual(jobs["local-cron:take_snapshot"]["schedule"], "5 0 * * *")
        # 058 seeds brave inactive; 070's "AND is_active = 1" update must not apply.
        self.assertEqual(jobs["local-cron:brave_bookmarks_maintenance"]["schedule"], "every_4h")
        self.assertIs(jobs["local-cron:brave_bookmarks_maintenance"]["active"], False)
        # 069 deactivates the duplicate goal_progress row.
        self.assertIs(jobs["local-cron:goal_progress"]["active"], False)
        # Rows only ever touched by UPDATE existed only in the live database.
        self.assertEqual(jobs["local-cron:revenue_autorun"]["row_origin"], "live-db-only")
        self.assertFalse(jobs["local-cron:revenue_autorun"]["in_job_map"])

    def test_current_hub_head_declares_no_schedule(self) -> None:
        _, snapshot = _load()
        self.assertEqual(snapshot["sources"]["solo-empire-head"]["record_count"], 0)
        self.assertFalse([r for r in snapshot["records"] if r["source"] == "solo-empire-head"])


@unittest.skipUnless(shutil.which("git"), "git is required")
class ExtractorTests(unittest.TestCase):
    """Run the real extractor against a throwaway Git repository."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.repo = Path(self._temp.name)
        files = {
            ".github/workflows/nightly.yml": 'on:\n  schedule:\n    - cron: "15 4 * * *"\n',
            "infra/templates/demo/demo.timer": "[Timer]\nOnCalendar=*-*-* 02:00:00\nPersistent=true\n",
            "infra/database/migrations/001_seed.sql": (
                "-- seed; with a ; in a comment\n"
                "INSERT INTO cron_jobs (name, description, schedule, job_type, params, is_active)\n"
                "SELECT 'Demo', 'one; two', 'daily', 'demo_job', '{}', 1\n"
                "WHERE NOT EXISTS (SELECT 1 FROM cron_jobs WHERE job_type = 'demo_job');\n"
                "UPDATE cron_jobs SET schedule = '0 5 * * *' WHERE job_type = 'demo_job' AND is_active = 1;\n"
            ),
            "infra/scripts/rogue.sh": "echo '0 * * * * job' | crontab -\n",
            "infra/tests/fixture.sh": "crontab -l\n",
        }
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=env, capture_output=True)
        self.sha = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_extracts_schedules_and_flags_unclassified_files(self) -> None:
        result = si.extract_source(self.repo, self.sha)
        records = [r.as_json("demo") for r in result["records"]]
        jobs = si.effective_jobs(records)
        self.assertEqual(jobs["gha:nightly"]["schedule"], "15 4 * * *")
        self.assertEqual(jobs["systemd:demo"]["schedule"], "*-*-* 02:00:00")
        self.assertEqual(jobs["local-cron:demo_job"]["schedule"], "0 5 * * *")
        self.assertEqual(result["unclassified"], ["infra/scripts/rogue.sh"])
        self.assertEqual([i["path"] for i in result["ignored"]], ["infra/tests/fixture.sh"])
        for record in records:
            expected = record["schedule"]
            self.assertEqual(si.reparse_line(record), expected)


@unittest.skipUnless(os.environ.get("SOLO_EMPIRE_ROOT") and os.environ.get("PLATFORM_OPS_CLI_ROOT"),
                     "set SOLO_EMPIRE_ROOT and PLATFORM_OPS_CLI_ROOT to re-extract from the pinned sources")
class PinnedSourceTests(unittest.TestCase):
    def test_pinned_sources_reproduce_committed_snapshot(self) -> None:
        solo, ops = Path(os.environ["SOLO_EMPIRE_ROOT"]), Path(os.environ["PLATFORM_OPS_CLI_ROOT"])
        rebuilt = si.build_snapshot({"solo-empire-retired": solo, "solo-empire-head": solo, "platform-ops-cli": ops})
        self.assertEqual(rebuilt, si.load(si.SNAPSHOT_PATH))


class RuntimeGapTests(unittest.TestCase):
    """platform-workflow main has no retry/idempotency runtime to exercise yet."""

    def test_runtime_gap_matches_repository(self) -> None:
        inventory, _ = _load()
        claimed = inventory["platform_workflow_main"]["retry_idempotency_runtime"]
        present = (ROOT / "src" / "platform_workflow").is_dir()
        self.assertEqual(
            claimed, "present" if present else "absent",
            "platform-workflow runtime presence changed: update platform_workflow_main in "
            "fixtures/gates/workflow-scheduler-inventory.json and add parity tests that exercise "
            "its retry/idempotency/attempt history against retry_policies (BOO-165).",
        )

    def test_legacy_history_has_no_durable_per_run_record(self) -> None:
        # Execution-history parity gap: the legacy local scheduler overwrote last-run
        # state and appended a best-effort log; there is no per-attempt record to
        # migrate. A target runtime must add one rather than copy the old shape.
        inventory, _ = _load()
        self.assertFalse(inventory["execution_history_locations"]["legacy-automation-cell"]["durable_per_run_record"])
        for name, policy in inventory["retry_policies"].items():
            with self.subTest(policy=name):
                self.assertIsInstance(policy["max_attempts_per_scheduled_run"], int)
                self.assertTrue(policy["backoff"])


if __name__ == "__main__":
    unittest.main()
