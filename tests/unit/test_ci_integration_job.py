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
    assert "-rs" in str(steps[pytest_i].get("run") or "")
    pytest_env = steps[pytest_i].get("env") or {}
    assert pytest_env.get("ALLOW_INTEGRATION_DB") == "1"
    guard_env = (steps[guard_i].get("env") or {})
    assert "NEON_DEV_ENDPOINT_ID" in guard_env
    assert "vars.NEON_DEV_ENDPOINT_ID" in str(guard_env["NEON_DEV_ENDPOINT_ID"])
    assert "NEON_DEV_REGION" in guard_env
    assert "vars.NEON_DEV_REGION" in str(guard_env["NEON_DEV_REGION"])
    assert "unset NEON_DEV_REGION" in str(steps[guard_i].get("run") or "")


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
    missing_endpoint = _run_guard({
        "DATABASE_URL": "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db",
    })
    assert missing_endpoint.returncode != 0
    assert "NEON_DEV_ENDPOINT_ID is required" in missing_endpoint.stderr
    missing_url = _run_guard({"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"})
    assert missing_url.returncode != 0
    assert "DATABASE_URL is empty" in missing_url.stderr
    both_missing = _run_guard({})
    assert both_missing.returncode != 0
    assert "NEON_DEV_ENDPOINT_ID is required" in both_missing.stderr


def test_ci_guard_db_rejects_mismatched_or_prod_endpoint():
    env = {"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"}
    mismatch = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-other-xyz.us-east-1.aws.neon.tech"),
    })
    assert mismatch.returncode != 0
    assert "does not match NEON_DEV_ENDPOINT_ID" in mismatch.stderr
    prod = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-x-production.us-east-1.aws.neon.tech"),
    })
    assert prod.returncode != 0
    assert "does not match NEON_DEV_ENDPOINT_ID" in prod.stderr
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
    "ep-dev-abc.c-1\u0661.us-east-1.aws.neon.tech",
    "ep-dev-abc.us-east-\u0661.aws.neon.tech",
])
def test_ci_guard_rejects_lookalike_hosts(host):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": _ok_url(host),
    })
    assert result.returncode != 0
    assert "not an accepted Neon host" in result.stderr
    assert "sslmode" not in result.stderr
    assert "u:p" not in result.stdout + result.stderr


def test_ci_guard_rejects_non_ascii_cell_digit():
    """Cell tail c-1١ must not match [0-9]; a \\d cell without re.ASCII would accept it."""
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": _ok_url("ep-dev-abc.c-1\u0661.us-east-1.aws.neon.tech"),
    })
    assert result.returncode != 0
    assert "not an accepted Neon host" in result.stderr
    assert "sslmode" not in result.stderr


def test_ci_guard_rejects_non_ascii_region_digit():
    """Region us-east-١ must not match ASCII \\d; dropping re.ASCII would accept it."""
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": _ok_url("ep-dev-abc.us-east-\u0661.aws.neon.tech"),
    })
    assert result.returncode != 0
    assert "not an accepted Neon host" in result.stderr
    assert "sslmode" not in result.stderr


def test_qa_bad_host_with_sslmode_disable_is_rejected_for_host():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": "postgresql://u:p@evil.example/db?sslmode=disable",
    })
    assert result.returncode != 0
    assert "host is not an accepted Neon host" in result.stderr
    assert "sslmode" not in result.stderr
    assert "u:p" not in result.stdout + result.stderr


def test_qa_region_is_not_checked_when_NEON_DEV_REGION_is_unset():
    """Unset NEON_DEV_REGION skips the region check; any Neon region is accepted."""
    env = {"NEON_DEV_ENDPOINT_ID": "ep-dev-abc"}
    west = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc.us-west-2.aws.neon.tech"),
    })
    assert west.returncode == 0, west.stderr
    eu = _run_guard({
        **env,
        "DATABASE_URL": _ok_url("ep-dev-abc.eu-central-1.aws.neon.tech"),
    })
    assert eu.returncode == 0, eu.stderr
    assert "NEON_DEV_REGION" not in west.stderr + eu.stderr


def test_qa_empty_NEON_DEV_REGION_fails_closed():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "NEON_DEV_REGION": "",
        "DATABASE_URL": _ok_url("ep-dev-abc.us-east-1.aws.neon.tech"),
    })
    assert result.returncode != 0
    assert "NEON_DEV_REGION is empty" in result.stderr
    assert "sslmode" not in result.stderr


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
        "DATABASE_URL": _ok_url("ep-dev-abc.us-west-2.aws.neon.tech"),
    })
    assert mismatch.returncode != 0
    assert "region does not match NEON_DEV_REGION" in mismatch.stderr
    assert "sslmode" not in mismatch.stderr
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
        "DATABASE_URL": _ok_url("ep-other-xyz-pooler.c-6.us-east-2.aws.neon.tech"),
    })
    assert result.returncode != 0
    assert "does not match NEON_DEV_ENDPOINT_ID" in result.stderr
    assert "sslmode" not in result.stderr
    assert "postgresql://" not in result.stdout + result.stderr


def test_ci_guard_rejects_cell_lookalike_domain():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": DEV_ENDPOINT_ID,
        "DATABASE_URL": _ok_url(f"{DEV_POOLED_HOST}.evil.example"),
    })
    assert result.returncode != 0
    assert "not an accepted Neon host" in result.stderr
    assert "sslmode" not in result.stderr
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
        "DATABASE_URL": _ok_url("ep-rapid-smoke-b4t1v549.c-6.us-west-2.aws.neon.tech"),
    })
    assert mismatch.returncode != 0
    assert "region does not match NEON_DEV_REGION" in mismatch.stderr
    assert "sslmode" not in mismatch.stderr


@pytest.mark.parametrize("url", [
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "host=ep-prod-xyz.us-east-1.aws.neon.tech"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "hostaddr=1.2.3.4"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "service=prod"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "options=endpoint%3Dprod"),
    _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "port=6543"),
])
def test_ci_guard_rejects_host_hostaddr_service_options_port_overrides(url):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": url,
    })
    assert result.returncode != 0
    assert "override parameters are not allowed" in result.stderr
    assert "sslmode" not in result.stderr
    out = result.stdout + result.stderr
    assert "u:p" not in out
    assert "postgresql://" not in out


def test_ci_guard_rejects_authority_port_as_disallowed_parameter():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech:5433/db?sslmode=require",
    })
    assert result.returncode != 0
    assert "parameters are not allowed" in result.stderr
    assert "override parameters" not in result.stderr
    out = result.stdout + result.stderr
    assert "u:p" not in out
    assert "postgresql://" not in out


def test_ci_guard_rejects_non_allowlisted_extra_parameter():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": _ok_url(
            "ep-dev-abc.us-east-1.aws.neon.tech",
            "gssencmode=disable",
        ),
    })
    assert result.returncode != 0
    assert "parameters are not allowed" in result.stderr
    assert "override parameters" not in result.stderr
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
    assert "sslmode must be require or verify-full" in result.stderr
    assert "not an accepted Neon host" not in result.stderr
    assert "does not match NEON_DEV_ENDPOINT_ID" not in result.stderr


@pytest.mark.parametrize("sslmode", ["require", "verify-full"])
def test_ci_guard_accepts_require_or_verify_full_sslmode(sslmode):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": f"postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode={sslmode}",
    })
    assert result.returncode == 0


@pytest.mark.parametrize("url, reason", [
    (
        _ok_url("ep-dev-abc.us-east-1.aws.neon.tech") + "&sslmode=disable",
        "query has duplicate keys",
    ),
    (
        _ok_url("ep-dev-abc.us-east-1.aws.neon.tech") + "&sslmode=require",
        "query has duplicate keys",
    ),
    (
        _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "user=admin"),
        "override parameters are not allowed",
    ),
    (
        _ok_url("ep-dev-abc.us-east-1.aws.neon.tech", "dbname=prod"),
        "override parameters are not allowed",
    ),
    (
        "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require&user=admin&dbname=other",
        "override parameters are not allowed",
    ),
])
def test_ci_guard_rejects_duplicate_query_keys_and_user_dbname(url, reason):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "ep-dev-abc",
        "DATABASE_URL": url,
    })
    assert result.returncode != 0
    assert reason in result.stderr
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


_QA_HOST = "ep-dev-abc.us-east-1.aws.neon.tech"
_QA_ENDPOINT = "ep-dev-abc"
_QA_PASSWORD = "qa-pw-must-not-leak"


def _qa_url(host=_QA_HOST, sslmode="require", password=_QA_PASSWORD, extra=""):
    query = f"sslmode={sslmode}" + (("&" + extra) if extra else "")
    return f"postgresql://ciuser:{password}@{host}/db?{query}"


def _assert_password_absent(result, password=_QA_PASSWORD):
    out = (result.stdout or "") + (result.stderr or "")
    assert password not in out
    assert "ciuser:" not in out
    assert "postgresql://" not in out


def test_qa_good_host_without_sslmode_is_rejected_for_sslmode():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": f"postgresql://ciuser:{_QA_PASSWORD}@{_QA_HOST}/db",
    })
    assert result.returncode != 0
    assert "sslmode must be require or verify-full" in result.stderr
    assert "not an accepted Neon host" not in result.stderr
    assert "does not match NEON_DEV_ENDPOINT_ID" not in result.stderr
    _assert_password_absent(result)


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
def test_qa_weak_sslmode_is_rejected(sslmode):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(sslmode=sslmode),
    })
    assert result.returncode != 0
    assert "sslmode must be require or verify-full" in result.stderr
    _assert_password_absent(result)


@pytest.mark.parametrize("sslmode", ["verify-full", "require"])
def test_qa_verify_full_and_require_sslmode_are_accepted(sslmode):
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(sslmode=sslmode),
    })
    assert result.returncode == 0, result.stderr
    _assert_password_absent(result)


def test_qa_duplicated_sslmode_require_and_disable_is_rejected_for_duplicate_key():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(sslmode="require", extra="sslmode=disable"),
    })
    assert result.returncode != 0
    assert "query has duplicate keys" in result.stderr
    assert "sslmode must be require or verify-full" not in result.stderr
    _assert_password_absent(result)


def test_qa_endpoint_in_userinfo_evil_host_is_rejected_for_host():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": f"postgresql://{_QA_ENDPOINT}.neon.tech@evil.com/db",
    })
    assert result.returncode != 0
    assert "host is not an accepted Neon host" in result.stderr
    assert "sslmode" not in result.stderr
    _assert_password_absent(result)


def test_qa_uppercase_host_is_accepted_trailing_dot_host_is_rejected():
    upper = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(host=_QA_HOST.upper()),
    })
    assert upper.returncode == 0, upper.stderr
    _assert_password_absent(upper)

    trailing = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(host=_QA_HOST + "."),
    })
    assert trailing.returncode != 0
    assert "host is not an accepted Neon host" in trailing.stderr
    assert "sslmode" not in trailing.stderr
    _assert_password_absent(trailing)


def test_qa_pooler_host_accepted_for_matching_endpoint_rejected_for_wrong_endpoint():
    ok = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(host="ep-dev-abc-pooler.us-east-1.aws.neon.tech"),
    })
    assert ok.returncode == 0, ok.stderr
    _assert_password_absent(ok)

    wrong = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": _qa_url(host="ep-other-xyz-pooler.us-east-1.aws.neon.tech"),
    })
    assert wrong.returncode != 0
    assert "does not match NEON_DEV_ENDPOINT_ID" in wrong.stderr
    assert "sslmode" not in wrong.stderr
    _assert_password_absent(wrong)


def test_qa_unset_or_empty_NEON_DEV_ENDPOINT_ID_fails_closed():
    unset = _run_guard({
        "DATABASE_URL": _qa_url(),
    })
    assert unset.returncode != 0
    assert "NEON_DEV_ENDPOINT_ID is required" in unset.stderr
    _assert_password_absent(unset)

    empty = _run_guard({
        "NEON_DEV_ENDPOINT_ID": "",
        "DATABASE_URL": _qa_url(),
    })
    assert empty.returncode != 0
    assert "NEON_DEV_ENDPOINT_ID is empty" in empty.stderr
    _assert_password_absent(empty)


def test_qa_empty_malformed_mysql_and_http_urls_rejected_for_parse_or_scheme():
    empty = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": "",
    })
    assert empty.returncode != 0
    assert "DATABASE_URL is empty" in empty.stderr

    malformed = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": "not-a-url",
    })
    assert malformed.returncode != 0
    assert "DATABASE_URL is invalid" in malformed.stderr

    mysql = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": f"mysql://ciuser:{_QA_PASSWORD}@evil.com/db",
    })
    assert mysql.returncode != 0
    assert "scheme must be postgresql" in mysql.stderr
    _assert_password_absent(mysql)

    http = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": f"http://ciuser:{_QA_PASSWORD}@evil.com/db",
    })
    assert http.returncode != 0
    assert "scheme must be postgresql" in http.stderr
    _assert_password_absent(http)


def test_qa_rejection_output_never_contains_the_password():
    result = _run_guard({
        "NEON_DEV_ENDPOINT_ID": _QA_ENDPOINT,
        "DATABASE_URL": (
            f"postgresql://ciuser:{_QA_PASSWORD}@"
            f"{_QA_HOST}/db?sslmode=disable"
        ),
    })
    assert result.returncode != 0
    assert "sslmode must be require or verify-full" in result.stderr
    _assert_password_absent(result)
    assert _QA_PASSWORD not in result.stdout
    assert _QA_PASSWORD not in result.stderr


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


def test_conftest_integration_fixture_requires_allow_flag(monkeypatch, request):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db")
    monkeypatch.delenv("ALLOW_INTEGRATION_DB", raising=False)
    with pytest.raises(pytest.skip.Exception, match="ALLOW_INTEGRATION_DB"):
        request.getfixturevalue("integration_client")


def test_conftest_integration_fixture_fails_closed_without_endpoint_id(monkeypatch, request):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:secret-pass@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require",
    )
    monkeypatch.delenv("NEON_DEV_ENDPOINT_ID", raising=False)
    with pytest.raises(pytest.fail.Exception, match="NEON_DEV_ENDPOINT_ID") as ei:
        request.getfixturevalue("integration_client")
    assert "secret-pass" not in str(ei.value)


def test_conftest_integration_fixture_fails_closed_on_mismatched_host(monkeypatch, request):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:secret-pass@ep-other.us-east-1.aws.neon.tech/db?sslmode=require",
    )
    with pytest.raises(pytest.fail.Exception, match="NEON_DEV_ENDPOINT_ID") as ei:
        request.getfixturevalue("integration_client")
    assert "secret-pass" not in str(ei.value)


def test_require_neon_integration_url_accepts_matching_host(monkeypatch):
    from tests.conftest import _require_neon_integration_url
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.delenv("NEON_DEV_REGION", raising=False)
    _require_neon_integration_url(
        "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require"
    )
    _require_neon_integration_url(
        "postgresql://u:p@ep-dev-abc-pooler.us-east-1.aws.neon.tech/db?sslmode=require"
    )
