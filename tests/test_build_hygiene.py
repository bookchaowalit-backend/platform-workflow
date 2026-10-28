"""Offline supply-chain gates: SHA-pinned workflow actions and a clean-checkout Dockerfile built from uv.lock."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "check.yml"
SERVICE_CMD = '["platform-workflow", "api"]'
RUNTIME_USER = "65532:65532"
CORE = "platform-backend-core"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
SHA = re.compile(r"^[0-9a-f]{40}$")


def unpinned_uses(text: str) -> list[str]:
    """Every remote `uses:` reference that is not a full 40-hex commit SHA."""
    return [ref for ref in USES.findall(text) if not ref.startswith("./") and not PINNED.match(ref)]


def pyproject_core_rev() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["tool"]["uv"]["sources"][CORE]["rev"])


def lock_core_commits() -> set[str]:
    """Commits the lock resolves platform-backend-core to (`?rev=<sha>#<sha>`)."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    sources = [pkg["source"]["git"] for pkg in lock["package"] if pkg["name"] == CORE]
    assert len(sources) == 1, sources
    commits = set(re.findall(r"[0-9a-f]{40}", sources[0]))
    return commits


def dockerfile_instructions() -> list[tuple[str, str]]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keyword, _, rest = line.partition(" ")
            rows.append((keyword.upper(), rest.strip()))
    return rows


def copy_sources(rows: list[tuple[str, str]]) -> list[str]:
    """Build-context paths copied into the image (excludes COPY --from=<stage>)."""
    sources: list[str] = []
    for keyword, rest in rows:
        if keyword == "COPY" and "--from=" not in rest:
            sources.extend(part for part in rest.split()[:-1] if not part.startswith("--"))
    return sources


class WorkflowPinTests(unittest.TestCase):
    def test_checker_flags_tags_and_short_shas(self) -> None:
        sample = (
            "steps:\n"
            "  - uses: actions/checkout@v4\n"
            "  - uses: actions/setup-python@a26af69\n"
            "  - uses: './local-action'\n"
            "  - uses: actions/cache@0123456789abcdef0123456789abcdef01234567 # v4\n"
            "    uses: org/repo/.github/workflows/x.yml@main\n"
        )
        self.assertEqual(
            unpinned_uses(sample),
            ["actions/checkout@v4", "actions/setup-python@a26af69", "org/repo/.github/workflows/x.yml@main"],
        )

    def test_every_workflow_action_is_sha_pinned(self) -> None:
        files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(files)
        found = {path.name: unpinned_uses(path.read_text(encoding="utf-8")) for path in files}
        self.assertEqual({name: refs for name, refs in found.items() if refs}, {})


class CorePinTests(unittest.TestCase):
    def test_lock_resolves_the_reviewed_core_commit(self) -> None:
        rev = pyproject_core_rev()
        self.assertRegex(rev, SHA)
        self.assertEqual(lock_core_commits(), {rev})


class DockerfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = dockerfile_instructions()
        self.text = " ".join(rest for _, rest in self.rows)

    def test_builds_from_a_clean_checkout(self) -> None:
        # A COPY of a git-ignored path (wheels/, wheelhouse/) fails `docker build .` on a fresh clone.
        self.assertNotIn("wheelhouse", self.text)
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for source in copy_sources(self.rows):
            self.assertNotIn(source.rstrip("/") + "/", ignored, source)
            self.assertNotIn(source.rstrip("/"), ignored, source)

    def test_is_multi_stage(self) -> None:
        self.assertGreaterEqual(sum(1 for keyword, _ in self.rows if keyword == "FROM"), 2)

    def test_dependencies_come_only_from_the_lock(self) -> None:
        # uv.lock carries platform-backend-core's reviewed commit and every hash; nothing else may
        # resolve a dependency or name a second core pin that could drift from the lock.
        self.assertIn("uv.lock", copy_sources(self.rows))
        syncs = [rest for keyword, rest in self.rows if keyword == "RUN" and "uv sync" in rest]
        self.assertTrue(syncs, "build stage must run uv sync")
        for rest in syncs:
            self.assertIn("--frozen", rest)
        self.assertNotIn(CORE, self.text)
        self.assertNotRegex(self.text, r"\bpip (install|wheel)\b")

    def test_runtime_stage_is_non_root_and_serves(self) -> None:
        last_from = max(i for i, (keyword, _) in enumerate(self.rows) if keyword == "FROM")
        final = self.rows[last_from:]
        users = [rest for keyword, rest in final if keyword == "USER"]
        self.assertEqual(users[-1:], [RUNTIME_USER])
        self.assertEqual([rest for keyword, rest in final if keyword == "CMD"][-1:], [SERVICE_CMD])

    def test_dockerignore_keeps_local_state_out(self) -> None:
        entries = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for entry in (".git", ".venv", ".env"):
            self.assertIn(entry, entries)

    def test_ci_builds_the_image_without_pushing(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(workflow, re.compile(r"^\s*run:\s*docker build\b.*\s\.\s*$", re.MULTILINE))
        self.assertNotIn("docker push", workflow)
        self.assertNotRegex(workflow, r"--push\b")


if __name__ == "__main__":
    unittest.main()
