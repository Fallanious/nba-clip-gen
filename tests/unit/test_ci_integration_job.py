"""CI workflow and Neon guard checks (PR #3 D4)."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(".github/workflows/integration.yml")
SHA_PIN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+@[0-9a-f]{40}$")
SECRET_REF = re.compile(r"^\$\{\{\s*secrets\.[A-Z0-9_]+\s*\}\}$")


def _data():
    return yaml.safe_load(WORKFLOW.read_text())


def _jobs():
    return _data()["jobs"]


def _job():
    jobs = _jobs()
    return jobs.get("integration") or next(iter(jobs.values()))


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
    for value in urls:
        assert SECRET_REF.match(str(value)), value

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


def test_no_job_level_secrets_if():
    """GitHub rejects `if: secrets.*` on jobs; skip via step-level has_db instead."""
    for name, job in _jobs().items():
        job_if = str(job.get("if") or "")
        assert "secrets." not in job_if, f"job {name} has secrets in if: {job_if}"


def test_actions_are_sha_pinned():
    for name, job in _jobs().items():
        for step in job.get("steps") or []:
            uses = str(step.get("uses") or "").strip()
            if not uses:
                continue
            assert SHA_PIN.match(uses), f"{name} unpinned action: {uses}"


def test_integration_job_skips_without_secret():
    job = _job()
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
    assert has_db_step and later_gated

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
            assert SHA_PIN.match(uses)
            parent = (step.get("with") or {}).get("parent") or ""
            assert parent in ("dev",) or "vars.NEON_DEV_BRANCH_ID" in str(parent)
        with_ = step.get("with") or {}
        for key, value in with_.items():
            if "branch" in key.lower() or key in {"parent"}:
                blob = str(value).lower()
                for word in ("production", "main", "primary", "default"):
                    assert word not in blob

    assert "python scripts/ci_guard_db.py" in text
    assert "vars.NEON_DEV_ENDPOINT_ID" in text
    assert "vars.NEON_DEV_REGION" in text
    assert "NEON_BRANCH_JSON" not in text
    assert _data().get("permissions") == {"contents": "read"}
    hashed_installs = 0
    for name, j in _jobs().items():
        for s in j.get("steps") or []:
            run = s.get("run") or ""
            assert "pip install -U pip" not in run
            assert "pip install --upgrade pip" not in run
            if "pip install" in run:
                assert "--require-hashes" in run, f"{name} pip install is not hashed"
                assert "requirements.lock" in run
                hashed_installs += 1
    assert hashed_installs >= 2
    assert Path("requirements.lock").is_file()
    assert "--hash=sha256:" in Path("requirements.lock").read_text()
    req = Path("requirements.txt").read_text()
    for name in ("uv", "pylint", "black", "ruff"):
        assert not re.search(rf"^{name}(?:[=<>]|\s|$)", req, re.M), name
    assert Path("requirements-dev.txt").is_file()
    assert Path("requirements-dev.lock").is_file()
    assert "--hash=sha256:" in Path("requirements-dev.lock").read_text()
    dev = Path("requirements-dev.txt").read_text()
    for name in ("uv", "pylint", "black", "ruff"):
        assert re.search(rf"^{name}==", dev, re.M), name
    steps = job.get("steps") or []
    guard_i = next(i for i, s in enumerate(steps) if "ci_guard_db.py" in str(s.get("run") or ""))
    pytest_i = next(i for i, s in enumerate(steps) if "pytest -m integration" in str(s.get("run") or ""))
    assert guard_i < pytest_i
    guard_env = (steps[guard_i].get("env") or {})
    assert "NEON_DEV_ENDPOINT_ID" in guard_env
    assert "vars.NEON_DEV_ENDPOINT_ID" in str(guard_env["NEON_DEV_ENDPOINT_ID"])
    assert "NEON_DEV_REGION" in guard_env
    assert "vars.NEON_DEV_REGION" in str(guard_env["NEON_DEV_REGION"])


def _ok_url(host, extra=""):
    query = "sslmode=require" + (("&" + extra) if extra else "")
    return f"postgresql://u:p@{host}/db?{query}"


def _run_guard(env):
    cleaned = {k: v for k, v in os.environ.items() if k not in {
        "DATABASE_URL", "NEON_DEV_ENDPOINT_ID", "NEON_DEV_REGION", "NEON_BRANCH_JSON",
    }}
    cleaned.update(env)
    return subprocess.run(
        [sys.executable, "scripts/ci_guard_db.py"],
        env=cleaned,
        capture_output=True,
        text=True,
    )


def test_ci_guard_db_requires_endpoint_and_url():
    assert _run_guard({
        "DATABASE_URL": "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db",
    }).returncode != 0
    assert _run_guard({"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"}).returncode != 0
    assert _run_guard({}).returncode != 0


def test_ci_guard_db_rejects_mismatched_or_prod_endpoint():
    env = {"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"}
    mismatch = _run_guard({
        **env,
        "DATABASE_URL": "postgresql://u:p@ep-other-xyz.us-east-1.aws.neon.tech/db",
    })
    assert mismatch.returncode != 0
    prod = _run_guard({
        **env,
        "DATABASE_URL": "postgresql://u:p@ep-x-production.us-east-1.aws.neon.tech/db",
    })
    assert prod.returncode != 0
    ok = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc.us-east-1.aws.neon.tech"),
    })
    assert ok.returncode == 0
    out = ok.stdout + ok.stderr + mismatch.stdout + mismatch.stderr
    assert "u:p" not in out
    assert "postgresql://" not in out


def test_ci_guard_accepts_pooled_host():
    env = {"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"}
    pooled = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc-pooler.us-east-1.aws.neon.tech"),
    })
    assert pooled.returncode == 0
    assert "u:p" not in pooled.stdout + pooled.stderr


@pytest.mark.parametrize("host", [
    "ep-dev-abc.evil.example",
    "ep-dev-abc.neon.tech",
    "ep-dev-abc.aws.neon.tech",
    "ep-dev-abc.us-east-1.neon.tech",
    "not-an-endpoint.us-east-1.aws.neon.tech",
    "ep-dev-abc.us-east-1.aws.neon.tech.evil.example",
    "ep-dev-abc.pooler.us-east-1.aws.neon.tech",
    "ep-dev-abc.c-6.us-east-2.aws.neon.tech.evil.example",
    "ep-dev-abc.c-6.us-east-2.neon.tech",
    "ep-dev-abc.c-0.us-east-2.aws.neon.tech",
    "ep-dev-abc.c-10000.us-east-2.aws.neon.tech",
])
def test_ci_guard_rejects_lookalike_hosts(host):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": f"postgresql://u:p@{host}/db",
    })
    assert result.returncode != 0
    assert "u:p" not in result.stdout + result.stderr


def test_ci_guard_region_optional_when_unset():
    env = {"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"}
    west = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc.us-west-2.aws.neon.tech"),
    })
    assert west.returncode == 0
    empty = _run_guard({
        **env,
        "NEON_DEV_REGION": "",
        "DATABASE_URL": _ok_url("ep-dev-abc.eu-central-1.aws.neon.tech"),
    })
    assert empty.returncode == 0


def test_ci_guard_region_must_match_when_set():
    env = {
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "NEON_DEV_REGION": "us-east-1",
    }
    ok = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc.us-east-1.aws.neon.tech"),
    })
    assert ok.returncode == 0
    pooled = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc-pooler.us-east-1.aws.neon.tech"),
    })
    assert pooled.returncode == 0
    aws_prefix = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "NEON_DEV_REGION": "aws-us-east-1",
        "DATABASE_URL": _ok_url("ep-dev-abc.us-east-1.aws.neon.tech"),
    })
    assert aws_prefix.returncode == 0
    mismatch = _run_guard({
        **env,
        "DATABASE_URL": "postgresql://u:p@ep-dev-abc.us-west-2.aws.neon.tech/db",
    })
    assert mismatch.returncode != 0
    out = ok.stdout + ok.stderr + mismatch.stdout + mismatch.stderr
    assert "u:p" not in out
    assert "postgresql://" not in out


DEV_ENDPOINT_ID = "ep-rapid-smoke-b4t1v549"
DEV_DIRECT_HOST = "ep-rapid-smoke-b4t1v549.c-6.us-east-2.aws.neon.tech"
DEV_POOLED_HOST = "ep-rapid-smoke-b4t1v549-pooler.c-6.us-east-2.aws.neon.tech"


def test_ci_guard_accepts_neon_cell_direct_and_pooled_hosts():
    """Real Neon hosts insert a c-<n> cell label; -pooler is a suffix, not .pooler."""
    env = {"NEON_DEV_ENDPOINT_ID": DEV_ENDPOINT_ID}
    direct = _run_guard({
        **env,
        "DATABASE_URL": f"postgresql://{DEV_DIRECT_HOST}/db?sslmode=require",
    })
    pooled = _run_guard({
        **env,
        "DATABASE_URL": f"postgresql://{DEV_POOLED_HOST}/db?sslmode=require",
    })
    assert direct.returncode == 0, direct.stderr
    assert pooled.returncode == 0, pooled.stderr
    out = direct.stdout + direct.stderr + pooled.stdout + pooled.stderr
    assert "postgresql://" not in out
    assert DEV_DIRECT_HOST not in out
    assert DEV_POOLED_HOST not in out


def test_ci_guard_rejects_mismatched_id_on_cell_host():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": DEV_ENDPOINT_ID,
        "DATABASE_URL": "postgresql://ep-other-xyz-pooler.c-6.us-east-2.aws.neon.tech/db",
    })
    assert result.returncode != 0
    assert "postgresql://" not in result.stdout + result.stderr


def test_ci_guard_rejects_cell_lookalike_domain():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": DEV_ENDPOINT_ID,
        "DATABASE_URL": f"postgresql://{DEV_POOLED_HOST}.evil.example/db",
    })
    assert result.returncode != 0
    assert "postgresql://" not in result.stdout + result.stderr


def test_ci_guard_cell_host_region_label_only():
    env = {
        "NEON_DEV_ENDPOINT_ID": DEV_ENDPOINT_ID,
        "NEON_DEV_REGION": "us-east-2",
    }
    ok = _run_guard({
        **env,
        "DATABASE_URL": f"postgresql://{DEV_POOLED_HOST}/db?sslmode=require",
    })
    assert ok.returncode == 0
    aws_prefix = _run_guard({
        "NEON_DEV_ENDPOINT_ID": DEV_ENDPOINT_ID,
        "NEON_DEV_REGION": "aws-us-east-2",
        "DATABASE_URL": f"postgresql://{DEV_DIRECT_HOST}/db?sslmode=require",
    })
    assert aws_prefix.returncode == 0
    mismatch = _run_guard({
        **env,
        "DATABASE_URL": "postgresql://ep-rapid-smoke-b4t1v549.c-6.us-west-2.aws.neon.tech/db",
    })
    assert mismatch.returncode != 0


@pytest.mark.parametrize("url", [
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "host=ep-prod-xyz.us-east-1.aws.neon.tech"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "hostaddr=1.2.3.4"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "service=prod"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "options=endpoint%3Dprod"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "port=6543"),
    "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech:5433/db?sslmode=require",
])
def test_ci_guard_rejects_host_hostaddr_service_options_port_overrides(url):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": url,
    })
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "u:p" not in out
    assert "postgresql://" not in out


@pytest.mark.parametrize("sslmode", [None, "disable", "prefer", "allow", "verify-ca"])
def test_ci_guard_rejects_missing_or_weak_sslmode(sslmode):
    host = "ep-dev-abc.us-east-1.aws.neon.tech"
    if sslmode is None:
        url = f"postgresql://u:p@{host}/db"
    else:
        url = f"postgresql://u:p@{host}/db?sslmode={sslmode}"
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": url,
    })
    assert result.returncode != 0


@pytest.mark.parametrize("sslmode", ["require", "verify-full"])
def test_ci_guard_accepts_require_or_verify_full_sslmode(sslmode):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": f"postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode={sslmode}",
    })
    assert result.returncode == 0


@pytest.mark.parametrize("url", [
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech") + "&sslmode=disable",
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech") + "&sslmode=require",
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "user=admin"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "dbname=prod"),
    "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require&user=admin&dbname=other",
])
def test_ci_guard_rejects_duplicate_query_keys_and_user_dbname(url):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": url,
    })
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "u:p" not in out
    assert "postgresql://" not in out
    assert "admin" not in out
    assert "prod" not in out


def test_ci_guard_allows_safe_extra_params():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": _ok_url(
            "ep-dev-abc.us-east-1.aws.neon.tech",
            "channel_binding=require&connect_timeout=10&application_name=ci",
        ),
    })
    assert result.returncode == 0


def test_ci_guard_unused_branch_json_ignored(tmp_path):
    branch = tmp_path / "neon.json"
    branch.write_text('{"default": true, "primary": true}')
    result = _run_guard({
        "NEON_BRANCH_JSON": str(branch),
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": _ok_url("ep-dev-abc.us-east-1.aws.neon.tech"),
    })
    assert result.returncode == 0


@pytest.mark.parametrize("url", [
    "postgresql://u:p@ep-x-production.neon.tech/db?sslmode=require",
    "postgresql://u:p@ep-x-main.neon.tech/db?sslmode=require",
])
def test_conftest_integration_fixture_refuses_prod(monkeypatch, request, url):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    with pytest.raises(pytest.fail.Exception, match="not an accepted Neon host"):
        request.getfixturevalue("integration_client")
