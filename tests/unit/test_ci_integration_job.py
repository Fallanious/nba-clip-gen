"""CI workflow and Neon guard checks (PR #3 D4)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(".github/workflows/integration.yml")


def _job():
    data = yaml.safe_load(WORKFLOW.read_text())
    jobs = data["jobs"]
    return next(iter(jobs.values()))


def _walk_env(node, found):
    if isinstance(node, dict):
        env = node.get("env") or {}
        if "DATABASE_URL" in env:
            found.append(env["DATABASE_URL"])
        for value in node.values():
            _walk_env(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_env(item, found)


def test_integration_job_uses_secret_not_literal():
    job = _job()
    urls = []
    _walk_env(job, urls)
    assert urls, "expected DATABASE_URL on the job or a step"
    secret_re = re.compile(r"^\$\{\{\s*secrets\.[A-Z0-9_]+\s*\}\}$")
    for value in urls:
        assert secret_re.match(str(value)), value

    github_root = Path(".github")
    for path in github_root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text()
        assert "postgres://" not in text.lower()
        assert "postgresql://" not in text.lower()
    for step in job.get("steps") or []:
        run = step.get("run") or ""
        assert not re.search(r"(>|tee)\s*\.?env\b", run)
        assert not re.search(r"echo\s+[\"']?\$\{?DATABASE_URL", run)
        assert "printenv" not in run


def test_integration_job_skips_without_secret():
    job = _job()
    job_if = str(job.get("if") or "")
    secret_if = bool(re.search(r"secrets\.[A-Z0-9_]+\s*!=\s*''", job_if))

    steps = job.get("steps") or []
    has_db_step = False
    later_gated = True
    check_id = None
    for i, step in enumerate(steps):
        run = step.get("run") or ""
        if "has_db" in run and "DATABASE_URL" in run and "GITHUB_OUTPUT" in run:
            has_db_step = True
            check_id = step.get("id")
            for later in steps[i + 1:]:
                later_if = str(later.get("if") or "")
                if f"steps.{check_id}.outputs.has_db == 'true'" not in later_if:
                    later_gated = False
            break
    assert secret_if or (has_db_step and later_gated)

    pytest_runs = False
    for step in steps:
        if "pytest -m integration" in str(step.get("run") or ""):
            pytest_runs = True
    assert pytest_runs


def test_integration_skip_behaviour_local():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "integration", "-q"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path.cwd()),
    )
    assert result.returncode in (0, 5)
    out = result.stdout + result.stderr
    assert "skipped" in out.lower()


def test_integration_job_never_targets_default_or_production():
    job = _job()
    text = WORKFLOW.read_text()
    for step in job.get("steps") or []:
        uses = str(step.get("uses") or "")
        if "neondatabase/create-branch-action" in uses:
            assert "@" in uses and re.search(r"@[0-9a-f]{40}$", uses)
            parent = (step.get("with") or {}).get("parent") or ""
            assert parent in ("dev",) or "vars.NEON_DEV_BRANCH_ID" in str(parent)
        with_ = step.get("with") or {}
        for key, value in with_.items():
            if "branch" in key.lower() or key in {"parent"}:
                blob = str(value).lower()
                for word in ("production", "main", "primary", "default"):
                    assert word not in blob

    assert "python scripts/ci_guard_db.py" in text
    steps = job.get("steps") or []
    guard_i = next(i for i, s in enumerate(steps) if "ci_guard_db.py" in str(s.get("run") or ""))
    pytest_i = next(i for i, s in enumerate(steps) if "pytest -m integration" in str(s.get("run") or ""))
    assert guard_i < pytest_i


def test_ci_guard_db_rejects_prod(tmp_path):
    script = "scripts/ci_guard_db.py"

    def run(url, meta):
        branch = tmp_path / "neon.json"
        branch.write_text(json.dumps(meta))
        env = {**os.environ, "DATABASE_URL": url, "NEON_BRANCH_JSON": str(branch)}
        return subprocess.run(
            [sys.executable, script],
            env=env,
            capture_output=True,
            text=True,
        )

    assert run("postgresql://u:p@ep-x-prod.neon.tech/db", {"default": False, "primary": False}).returncode != 0
    assert run("postgresql://u:p@ep-dev.neon.tech/db", {"default": True}).returncode != 0
    assert run("postgresql://u:p@ep-dev.neon.tech/db", {"primary": True}).returncode != 0
    assert run("postgresql://u:p@ep-dev.neon.tech/db", {"default": False, "primary": False}).returncode == 0


@pytest.mark.parametrize("url", [
    "postgresql://u:p@ep-x-production.neon.tech/db",
    "postgresql://u:p@ep-x-main.neon.tech/db",
])
def test_conftest_integration_fixture_refuses_prod(monkeypatch, request, url):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(pytest.skip.Exception) as ei:
        request.getfixturevalue("integration_client")
    assert "production" in str(ei.value).lower()
