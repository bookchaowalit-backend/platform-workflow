#!/usr/bin/env python3
"""Extract and check the scheduled-job inventory for the BOO-165 workflow gate.

The scheduler this platform is meant to own still lives outside this
repository (solo-empire and platform-ops-cli). CI cannot see those
repositories, so this module works in two modes:

* ``extract`` (local, read-only): reads schedule definitions straight from
  the pinned Git commits with ``git grep`` / ``git show`` and writes
  ``fixtures/gates/workflow-scheduler-source-snapshot.json``. Every tracked
  file that contains a scheduling marker must be routed to a parser or to an
  explicit ignore rule; anything else is reported as unclassified.
* ``check`` (offline, CI): validates the curated inventory
  ``fixtures/gates/workflow-scheduler-inventory.json`` against the pinned
  snapshot. A schedule that appears in the snapshot but not in the inventory
  (or with a different schedule) is an error.

Standard library only. Never executes code from the source repositories.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "fixtures" / "gates" / "workflow-scheduler-source-snapshot.json"
INVENTORY_PATH = ROOT / "fixtures" / "gates" / "workflow-scheduler-inventory.json"

# Pinned sources. CI never resolves these; they document what the snapshot
# was extracted from and let `extract`/`--verify-sources` reproduce it.
SOURCES: dict[str, dict[str, str]] = {
    "solo-empire-retired": {
        "repository": "bookchaowalit/solo-empire",
        "sha": "1d0e04bb04af74cc05d4733da35719f2dfad6bf6",
        "role": "last tree before 3bf3637c retired the local cron runner, cron registry and healer",
    },
    "solo-empire-head": {
        "repository": "bookchaowalit/solo-empire",
        "sha": "63efeeb07a50171af934336bec4a05295f494732",
        "role": "current hub HEAD; registry still names infra/tasks and infra/scripts/workflows",
    },
    "platform-ops-cli": {
        "repository": "bookchaowalit/platform-ops-cli",
        "sha": "f372959408a4438eb41ef1a1ad3c8a53454dca0e",
        "role": "current owner of infra/tasks and infra/scripts/workflows after 5f034945",
    },
}

# Any tracked file matching one of these markers is a scheduling candidate.
MARKER_RE = (
    r"OnCalendar|^\s*-\s*cron:|crontab -|Register-ScheduledTask|New-ScheduledTaskTrigger|"
    r"schtasks|launchctl|StartCalendarInterval|INTO cron_jobs|UPDATE cron_jobs|cronSchedule|"
    r"\"schedule\":|systemctl --user enable"
)

CRON5_RE = re.compile(r"^(\S+\s+){4}\S+$")
CRON_ALIASES = {"daily", "weekly", "monthly", "hourly", "every_4h", "every_6h", "twice_daily", "morning"}


@dataclass
class Record:
    id: str
    kind: str
    schedule: str | None
    path: str
    line: int
    text: str
    extra: dict[str, Any]

    def as_json(self, source: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": source,
            "kind": self.kind,
            "schedule": self.schedule,
            "path": self.path,
            "line": self.line,
            "text": self.text,
            "extra": self.extra,
        }


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_text(text: str, line: int) -> str:
    return text.splitlines()[line - 1].strip()


# ---------------------------------------------------------------- parsers


def parse_job_map(text: str, path: str) -> list[Record]:
    """JOB_MAP / JOB_TIMEOUTS from cron_registry.py (no schedule: that lived in the DB)."""
    tree = ast.parse(text)
    timeouts: dict[str, int] = {}
    entries: list[tuple[str, str, list[str], int]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Dict):
            continue
        if target.id == "JOB_MAP":
            for key, value in zip(node.value.keys, node.value.values):
                script, args = ast.literal_eval(value)
                entries.append((ast.literal_eval(key), script, list(args), key.lineno))
        elif target.id == "JOB_TIMEOUTS":
            timeouts = {ast.literal_eval(k): ast.literal_eval(v) for k, v in zip(node.value.keys, node.value.values)}
    records = []
    for job_type, script, args, line in entries:
        records.append(Record(
            id=f"local-cron:{job_type}", kind="job-map", schedule=None, path=path, line=line,
            text=_line_text(text, line),
            extra={"script": f"infra/scripts/{script}", "args": args, "timeout_seconds": timeouts.get(job_type, 120)},
        ))
    return records


_INSERT_RE = re.compile(r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+cron_jobs\s*\(([^)]*)\)", re.I)
_UPDATE_RE = re.compile(r"UPDATE\s+cron_jobs\s+SET\s+(.*?)\s+WHERE\s+(.*)", re.S | re.I)
_LITERAL_RE = re.compile(r"'((?:[^']|'')*)'|(?<![\w.])(\d+)(?![\w.])")


def _sql_views(text: str) -> tuple[str, str]:
    """Return (decommented, masked) copies of ``text`` with identical offsets.

    ``decommented`` blanks ``--`` comments; ``masked`` additionally replaces
    string-literal contents with ``x`` so keywords and ``;`` inside quotes
    (for example a description containing "; write") are not mistaken for SQL.
    """
    decommented, masked = list(text), list(text)
    i, quoted = 0, False
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == "'" and text[i + 1:i + 2] == "'":
                masked[i] = masked[i + 1] = "x"
                i += 2
                continue
            if ch == "'":
                quoted = False
            elif ch != "\n":
                masked[i] = "x"
        elif ch == "'":
            quoted = True
        elif text.startswith("--", i):
            while i < len(text) and text[i] != "\n":
                decommented[i] = masked[i] = " "
                i += 1
            continue
        i += 1
    return "".join(decommented), "".join(masked)


def _sql_statements(text: str) -> Iterable[tuple[int, str, str]]:
    decommented, masked = _sql_views(text)
    start = 0
    for index, ch in enumerate(masked):
        if ch == ";":
            yield start, decommented[start:index], masked[start:index]
            start = index + 1


def parse_cron_migration(text: str, path: str) -> list[Record]:
    """cron_jobs INSERT seeds and UPDATE schedule/is_active statements."""
    records: list[Record] = []
    for offset, statement, masked in _sql_statements(text):
        insert = _INSERT_RE.search(masked)
        if insert:
            columns = [c.strip() for c in statement[insert.start(1):insert.end(1)].split(",")]
            body_end = len(statement)
            cut = re.search(r"\bWHERE\b", masked[insert.end():], re.I)
            if cut:
                body_end = insert.end() + cut.start()
            literals = [
                (m.group(1) if m.group(1) is not None else int(m.group(2)), offset + insert.end() + m.start())
                for m in _LITERAL_RE.finditer(statement[insert.end():body_end])
            ]
            if not literals or len(literals) % len(columns):
                raise ValueError(f"{path}: cannot align cron_jobs INSERT literals")
            for start in range(0, len(literals), len(columns)):
                row = dict(zip(columns, literals[start:start + len(columns)]))
                schedule, position = row["schedule"]
                line = _line_of(text, position)
                is_active = row.get("is_active", (1, 0))[0]
                records.append(Record(
                    id=f"local-cron:{row['job_type'][0]}", kind="cron-jobs-insert", schedule=schedule,
                    path=path, line=line, text=_line_text(text, line),
                    extra={"name": row["name"][0], "is_active": bool(int(is_active)), "params": row["params"][0]},
                ))
            continue
        update = _UPDATE_RE.search(masked)
        if not update:
            continue
        set_clause = statement[update.start(1):update.end(1)]
        where = statement[update.start(2):update.end(2)]
        job = re.search(r"job_type\s*=\s*'([^']+)'", where)
        if not job:
            continue
        schedule = re.search(r"schedule\s*=\s*'([^']+)'", set_clause)
        active = re.search(r"is_active\s*=\s*(\d)", set_clause)
        if not schedule and not active:
            continue  # description-only update
        line = _line_of(text, offset + update.start())
        records.append(Record(
            id=f"local-cron:{job.group(1)}",
            kind="cron-jobs-update",
            schedule=schedule.group(1) if schedule else None,
            path=path, line=line, text=_line_text(text, line),
            extra={
                "sets_active": None if not active else bool(int(active.group(1))),
                "only_if_active": bool(re.search(r"is_active\s*=\s*1", where)),
            },
        ))
    return records


def parse_gha_workflow(text: str, path: str) -> list[Record]:
    stem = Path(path).stem
    records = []
    for number, raw in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*-\s*cron:\s*[\"']([^\"']+)[\"']", raw)
        if match:
            records.append(Record(f"gha:{stem}", "github-actions-schedule", match.group(1), path, number, raw.strip(),
                                  {"timezone": "UTC"}))
    return records


def parse_crontab_installer(text: str, path: str) -> list[Record]:
    """setup_*_cron.sh: either a single CRON_TAG with a quoted cron expression or cron_entry calls."""
    records = []
    tag_match = re.search(r'^CRON_TAG="#\s*([\w-]+)"', text, re.M)
    attempts = re.search(r"--attempts (\d+)", text)
    timeout = re.search(r"--timeout-seconds (\d+)", text)
    lock = re.search(r'^LOCK_PATH="\$\{ROOT_DIR\}/([^"]+)"', text, re.M)
    log = re.search(r'^LOG_FILE="\$\{ROOT_DIR\}/([^"]+)"', text, re.M)
    extra = {
        "attempts": int(attempts.group(1)) if attempts else None,
        "timeout_seconds": int(timeout.group(1)) if timeout else None,
        "lock_path": lock.group(1) if lock else None,
        "log_file": log.group(1) if log else None,
    }
    for number, raw in enumerate(text.splitlines(), 1):
        entry = re.match(r'^\s*cron_entry\s+"([^"]+)"\s+(\w+)\s+"#\s*([\w-]+)"', raw)
        if entry:
            records.append(Record(f"crontab:{entry.group(3)}", "crontab-installer", entry.group(1), path, number,
                                  raw.strip(), dict(extra, product=entry.group(2))))
            continue
        quoted = re.match(r'^\s*"([^"]+)"\s*\\$', raw)
        if quoted and CRON5_RE.match(quoted.group(1)) and tag_match:
            records.append(Record(f"crontab:{tag_match.group(1)}", "crontab-installer", quoted.group(1), path, number,
                                  raw.strip(), dict(extra)))
    return records


def parse_windows_scraping_scheduler(text: str, path: str) -> list[Record]:
    tree = ast.parse(text)
    jobs: tuple[str, ...] = ()
    intervals: dict[str, int] = {}
    daily: dict[str, str] = {}
    lines: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "JOBS":
                jobs = ast.literal_eval(node.value)
            elif name in {"intervals", "daily"} and isinstance(node.value, ast.Dict):
                value = ast.literal_eval(node.value)
                (intervals if name == "intervals" else daily).update(value)
                lines[name] = node.lineno
    records = []
    for job in jobs:
        if job in intervals:
            schedule, line = f"every {intervals[job]}h", lines["intervals"]
        elif job in daily:
            schedule, line = f"daily {daily[job]}", lines["daily"]
        else:
            raise ValueError(f"{path}: windows job {job} has no trigger")
        records.append(Record(f"windows-task:SoloEmpire-Scraping-{job}", "windows-scheduled-task", schedule, path,
                              line, _line_text(text, line),
                              {"overlap": "IgnoreNew", "execution_limit_minutes": 15, "installed_by_default": False}))
    return records


def parse_powershell_task(text: str, path: str) -> list[Record]:
    name = re.search(r"^\s*\$(?:TaskName|taskName)\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    if not name:
        raise ValueError(f"{path}: no task name")
    for number, raw in enumerate(text.splitlines(), 1):
        if "-AtLogOn" in raw:
            return [Record(f"windows-task:{name.group(1)}", "windows-scheduled-task", "at-logon", path, number,
                           raw.strip(), {})]
        hours = re.search(r"-RepetitionInterval \(New-TimeSpan -Hours (\d+)\)", raw)
        if hours:
            return [Record(f"windows-task:{name.group(1)}", "windows-scheduled-task", f"every {hours.group(1)}h",
                           path, number, raw.strip(), {})]
    raise ValueError(f"{path}: no recognised trigger")


def parse_systemd_timer(text: str, path: str) -> list[Record]:
    unit = Path(path).stem
    records = []
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("OnCalendar="):
            records.append(Record(f"systemd:{unit}", "systemd-timer", raw.split("=", 1)[1].strip(), path, number,
                                  raw.strip(), {"persistent": "Persistent=true" in text}))
    return records


def parse_timer_installer(text: str, path: str) -> list[Record]:
    """Python installers that render a systemd timer from module constants."""
    tree = ast.parse(text)
    consts: dict[str, tuple[Any, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = (ast.literal_eval(node.value), node.lineno)
            except ValueError:
                pass
    schedule = consts.get("SCHEDULE") or consts.get("CALENDAR")
    unit = consts.get("UNIT") or consts.get("TIMER")
    if not schedule or not unit:
        raise ValueError(f"{path}: missing SCHEDULE/CALENDAR or UNIT/TIMER")
    name = str(unit[0]).removesuffix(".timer")
    return [Record(f"systemd:{name}", "systemd-timer", schedule[0], path, schedule[1],
                   _line_text(text, schedule[1]), {"persistent": "Persistent=true" in text})]


Parser = Callable[[str, str], list[Record]]

# Ordered routing table: first match wins. ``None`` means "ignore" with reason.
ROUTES: list[tuple[re.Pattern[str], Parser | None, str]] = [
    (re.compile(r"^infra/scripts/utils/cron_registry\.py$"), parse_job_map, "job registry"),
    (re.compile(r"^infra/database/migrations/\d+_.*\.sql$"), parse_cron_migration, "cron_jobs seed/update"),
    (re.compile(r"^\.github/workflows/[^/]+\.ya?ml$"), parse_gha_workflow, "GitHub Actions schedule"),
    (re.compile(r"^infra/scripts/data_lake/setup_\w+_cron\.sh$"), parse_crontab_installer, "crontab installer"),
    (re.compile(r"^infra/scripts/data_lake/windows_scraping_scheduler\.py$"), parse_windows_scraping_scheduler,
     "Windows Task Scheduler plan"),
    (re.compile(r"\.ps1$"), parse_powershell_task, "Windows scheduled task"),
    (re.compile(r"\.timer$"), parse_systemd_timer, "systemd timer"),
    (re.compile(r"/install_\w+_timer\.py$"), parse_timer_installer, "systemd timer installer"),
    # Explicit ignores: matched a marker but define no schedule.
    (re.compile(r"(^|/)tests?/"), None, "test code or test fixture, not an installed schedule"),
    (re.compile(r"\.md$"), None, "documentation or evidence, not a schedule definition"),
    (re.compile(r"^data/generated/"), None, "generated report"),
    (re.compile(r"^integrations/google/setup_calendar\.py$"), None,
     "prints a suggested crontab line for the operator; installs nothing"),
    (re.compile(r"^infra/scripts/data_lake/(publish_collection_outputs|scraping_coverage)\.py$"), None,
     "reads a registry 'schedule' field; defines no schedule"),
]


def route(path: str) -> tuple[Parser | None, str] | None:
    for pattern, parser, reason in ROUTES:
        if pattern.search(path):
            return parser, reason
    return None


# ---------------------------------------------------------------- extraction


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def extract_source(repo: Path, sha: str) -> dict[str, Any]:
    out = subprocess.run(["git", "-C", str(repo), "grep", "-lE", MARKER_RE, sha],
                         capture_output=True, text=True)
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.strip())
    paths = sorted(line.split(":", 1)[1] for line in out.stdout.splitlines() if line)
    # JOB_MAP has no marker of its own; it is the job catalogue for the cron_jobs rows.
    tree = set(_git(repo, "ls-tree", "-r", "--name-only", sha).splitlines())
    if "infra/scripts/utils/cron_registry.py" in tree:
        paths = sorted(set(paths) | {"infra/scripts/utils/cron_registry.py"})
    records: list[Record] = []
    ignored: list[dict[str, str]] = []
    unclassified: list[str] = []
    for path in paths:
        routed = route(path)
        if routed is None:
            unclassified.append(path)
            continue
        parser, reason = routed
        if parser is None:
            ignored.append({"path": path, "reason": reason})
            continue
        records.extend(parser(_git(repo, "show", f"{sha}:{path}"), path))
    return {"records": records, "ignored": ignored, "unclassified": unclassified}


def build_snapshot(repos: dict[str, Path]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "snapshot_version": 1,
        "generated_by": "scripts/scheduler_inventory.py extract",
        "marker_regex": MARKER_RE,
        "sources": {},
        "records": [],
    }
    for key, meta in SOURCES.items():
        result = extract_source(repos[key], meta["sha"])
        snapshot["sources"][key] = dict(meta, ignored=result["ignored"], unclassified=result["unclassified"],
                                        record_count=len(result["records"]))
        snapshot["records"].extend(r.as_json(key) for r in result["records"])
    return snapshot


# ---------------------------------------------------------------- effective schedule


def effective_jobs(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse raw snapshot records into one effective schedule per job id.

    Local cron rows replay migrations in filename order: the first INSERT
    creates the row (later INSERTs are WHERE NOT EXISTS / OR IGNORE no-ops),
    an UPDATE guarded by ``is_active = 1`` only applies to an active row, and
    an UPDATE for a job with no INSERT in source targets a row that existed
    only in the live database. Every other kind has exactly one record.
    """
    jobs: dict[str, dict[str, Any]] = {}
    ordered = sorted(records, key=lambda r: (r["source"], r["path"], r["line"]))
    for record in ordered:
        job = jobs.setdefault(record["id"], {
            "id": record["id"], "source": record["source"], "kind": None, "schedule": None, "active": None,
            "row_origin": None, "in_job_map": False, "citations": [],
        })
        cite = f"{record['source']}:{record['path']}:{record['line']}"
        kind = record["kind"]
        if kind == "job-map":
            job["in_job_map"] = True
            job["citations"].append(cite)
            job["kind"] = job["kind"] or "local-cron"
            continue
        if kind == "cron-jobs-insert":
            job["kind"] = "local-cron"
            if job["row_origin"] is None:
                job.update(schedule=record["schedule"], active=record["extra"]["is_active"], row_origin="migration-seed")
                job["citations"].append(cite)
            continue
        if kind == "cron-jobs-update":
            job["kind"] = "local-cron"
            extra = record["extra"]
            if job["row_origin"] is None:
                job["row_origin"] = "live-db-only"
            if extra["only_if_active"] and job["active"] is False:
                continue
            if record["schedule"] is not None:
                job["schedule"] = record["schedule"]
            if extra["sets_active"] is not None:
                job["active"] = extra["sets_active"]
            job["citations"].append(cite)
            continue
        job.update(kind=kind, schedule=record["schedule"], active=None, row_origin="source-file")
        job["citations"].append(cite)
    return jobs


def reparse_line(record: dict[str, Any]) -> str | None:
    """Re-derive a snapshot record's schedule from its stored source line.

    Used offline (CI) to prove each pinned record's schedule really is on the
    cited line, using the same patterns as the full-file parsers. For
    ``job-map`` records (no schedule) the job type is returned instead.
    """
    kind, text, path = record["kind"], record["text"], record["path"]
    if kind == "job-map":
        match = re.match(r"^'([A-Za-z0-9_]+)'\s*:", text)
        return match.group(1) if match else None
    if kind == "github-actions-schedule":
        return parse_gha_workflow(text, path)[0].schedule
    if kind == "systemd-timer" and path.endswith(".timer"):
        return parse_systemd_timer(text, path)[0].schedule
    if kind == "systemd-timer":
        return ast.literal_eval(ast.parse(text).body[0].value)
    if kind == "crontab-installer":
        entry = re.match(r'^cron_entry\s+"([^"]+)"', text) or re.match(r'^"([^"]+)"\s*\\$', text)
        return entry.group(1) if entry else None
    if kind == "cron-jobs-insert":
        literals = [m.group(1) for m in _LITERAL_RE.finditer(text) if m.group(1) is not None]
        job_type = record["id"].split(":", 1)[1]
        if job_type in literals[1:]:
            return literals[literals.index(job_type) - 1]  # schedule precedes job_type on the same line
        return literals[0] if len(literals) == 1 else None  # one literal per line
    if kind == "cron-jobs-update":
        match = re.search(r"schedule\s*=\s*'([^']+)'", text)
        return match.group(1) if match else None
    if kind == "windows-scheduled-task" and path.endswith(".py"):
        mapping = ast.literal_eval(ast.parse(text.strip()).body[0].value)
        job = record["id"].rsplit("-", 1)[1]
        value = mapping[job]
        return f"every {value}h" if isinstance(value, int) else f"daily {value}"
    if kind == "windows-scheduled-task":
        if "-AtLogOn" in text:
            return "at-logon"
        hours = re.search(r"-RepetitionInterval \(New-TimeSpan -Hours (\d+)\)", text)
        return f"every {hours.group(1)}h" if hours else None
    raise ValueError(f"no line parser for {kind}")


# ---------------------------------------------------------------- validation

REQUIRED_JOB_FIELDS = (
    "id", "schedule", "status", "current_owner", "current_runner", "target_owner",
    "retry_policy", "idempotency_key", "execution_history", "citations",
)
STATUSES = {
    "retired-local-cron",          # JOB_MAP job of the retired solo-empire cron runner
    "retired-orphan-row",          # cron_jobs row without a JOB_MAP entry (never runnable)
    "retired-ci-schedule",         # GitHub Actions schedule removed from solo-empire
    "retired-host-installer",      # crontab / Task Scheduler installer removed from solo-empire
    "active-installer-in-source",  # installer present at the pinned current owner
}


def validate(inventory: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, source in snapshot.get("sources", {}).items():
        if source.get("unclassified"):
            errors.append(f"snapshot source {key} has unclassified schedule files: {source['unclassified']}")
    expected = effective_jobs(snapshot["records"])
    cites = {f"{r['source']}:{r['path']}:{r['line']}" for r in snapshot["records"]}
    policies = inventory.get("retry_policies", {})
    histories = inventory.get("execution_history_locations", {})
    targets = inventory.get("target_owners", {})
    jobs = {}
    for job in inventory.get("jobs", []):
        if job.get("id") in jobs:
            errors.append(f"duplicate inventory job {job.get('id')}")
        jobs[job.get("id")] = job
    for job_id, source_job in sorted(expected.items()):
        job = jobs.get(job_id)
        if job is None:
            errors.append(f"{job_id}: schedule found in source ({source_job['citations'][0]}) is missing from inventory")
            continue
        if job.get("schedule") != source_job["schedule"]:
            errors.append(f"{job_id}: inventory schedule {job.get('schedule')!r} != source {source_job['schedule']!r}")
        for citation in source_job["citations"]:
            if citation not in job.get("citations", []):
                errors.append(f"{job_id}: inventory lacks source citation {citation}")
    for job_id, job in sorted(jobs.items()):
        if job_id not in expected:
            errors.append(f"{job_id}: inventory job has no record in the pinned source snapshot")
        for field in REQUIRED_JOB_FIELDS:
            if field not in job or (field != "schedule" and job[field] in (None, "", [])):
                errors.append(f"{job_id}: missing {field}")
        if job.get("schedule") is None and not job.get("schedule_gap"):
            errors.append(f"{job_id}: null schedule needs a schedule_gap explanation")
        if job.get("status") not in STATUSES:
            errors.append(f"{job_id}: unknown status {job.get('status')!r}")
        if not isinstance(job.get("target_owner"), str) or job.get("target_owner") not in targets:
            errors.append(f"{job_id}: target_owner {job.get('target_owner')!r} is not defined")
        if not isinstance(job.get("retry_policy"), str) or job.get("retry_policy") not in policies:
            errors.append(f"{job_id}: retry_policy {job.get('retry_policy')!r} is not defined")
        if not isinstance(job.get("execution_history"), str) or job.get("execution_history") not in histories:
            errors.append(f"{job_id}: execution_history {job.get('execution_history')!r} is not defined")
        for citation in job.get("citations", []):
            if citation not in cites:
                errors.append(f"{job_id}: citation {citation} is not a snapshot record")
    for name, policy in policies.items():
        if not policy.get("citations"):
            errors.append(f"retry policy {name} has no citation")
    decision = inventory.get("owner_decision")
    if not isinstance(decision, dict):
        errors.append("owner_decision must be an object with status/date/accepted/verbatim")
    else:
        status = decision.get("status")
        if status not in {"proposed", "accepted"}:
            errors.append(f"owner_decision.status {status!r} must be 'proposed' or 'accepted'")
        if status == "accepted":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(decision.get("date") or "")):
                errors.append("accepted owner_decision needs an ISO date")
            for field in ("accepted", "verbatim", "relayed_via"):
                if not decision.get(field):
                    errors.append(f"accepted owner_decision needs {field}")
    for name, target in targets.items():
        if target.get("decision") != "PROPOSED, NOT ACCEPTED":
            errors.append(f"target owner {name} must stay 'PROPOSED, NOT ACCEPTED': the 2026-10-16 decision accepted the inventory, not target owners")
    for name, history in histories.items():
        if not history.get("citations"):
            errors.append(f"execution history {name} has no citation")
    return errors


def verify_source(key: str, repo: Path, snapshot: dict[str, Any]) -> list[str]:
    """Re-extract one pinned source from a real checkout and diff it against the snapshot."""
    result = extract_source(repo, SOURCES[key]["sha"])
    rebuilt = [r.as_json(key) for r in result["records"]]
    pinned = [r for r in snapshot["records"] if r["source"] == key]
    problems = []
    if rebuilt != pinned:
        missing = {(r["id"], r["schedule"]) for r in rebuilt} - {(r["id"], r["schedule"]) for r in pinned}
        extra = {(r["id"], r["schedule"]) for r in pinned} - {(r["id"], r["schedule"]) for r in rebuilt}
        problems.append(f"{key}: snapshot records differ from source (missing {sorted(missing)}, stale {sorted(extra)})")
    meta = snapshot["sources"][key]
    if result["unclassified"] != meta["unclassified"] or result["ignored"] != meta["ignored"]:
        problems.append(f"{key}: classification differs (unclassified {result['unclassified']})")
    return problems


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ext = sub.add_parser("extract", help="re-extract the snapshot from local checkouts (read-only git)")
    ext.add_argument("--solo-empire", type=Path, required=True)
    ext.add_argument("--platform-ops-cli", type=Path, required=True)
    ext.add_argument("--write", action="store_true", help="overwrite the committed snapshot")
    sub.add_parser("check", help="validate the inventory against the committed snapshot")
    ver = sub.add_parser("verify-source", help="re-extract one pinned source and compare it with the snapshot")
    ver.add_argument("--source", choices=sorted(SOURCES), required=True)
    ver.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "extract":
        repos = {"solo-empire-retired": args.solo_empire, "solo-empire-head": args.solo_empire,
                 "platform-ops-cli": args.platform_ops_cli}
        snapshot = build_snapshot(repos)
        rendered = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
        if args.write:
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_PATH.write_text(rendered, encoding="utf-8")
            print(f"wrote {SNAPSHOT_PATH.relative_to(ROOT)} ({len(snapshot['records'])} records)")
            return 0
        committed = load(SNAPSHOT_PATH) if SNAPSHOT_PATH.exists() else None
        if committed != snapshot:
            print("snapshot drift: committed snapshot differs from the pinned sources", file=sys.stderr)
            return 1
        print(f"OK: snapshot matches pinned sources ({len(snapshot['records'])} records)")
        return 0

    if args.command == "verify-source":
        problems = verify_source(args.source, args.repo, load(SNAPSHOT_PATH))
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        if problems:
            return 1
        print(f"OK: {args.source}@{SOURCES[args.source]['sha'][:8]} re-extracted and matches the snapshot")
        return 0

    errors = validate(load(INVENTORY_PATH), load(SNAPSHOT_PATH))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"OK: scheduler inventory covers {len(load(INVENTORY_PATH)['jobs'])} jobs from the pinned snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
