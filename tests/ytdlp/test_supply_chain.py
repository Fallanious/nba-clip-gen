"""Supply-chain pins, hashed locks, CI workflow, and pip-audit fixtures."""
import os
import re
import subprocess
import sys

import pytest
import yaml
from packaging.version import Version

from conftest import REPO_ROOT

PIN_RE = re.compile(r"^[A-Za-z0-9_.\[\]-]+==[0-9][^\s;]*(\s*;.*)?$")
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}")
REQ_LINE_RE = re.compile(r"^([A-Za-z0-9_.\[\]-]+)==([^\s\\]+)")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

FLOORS = {
    "flask": "3.1.3",
    "lxml": "6.1.3",
    "python-dotenv": "1.2.4",
    "requests": "2.34.2",
    "yt-dlp": "2026.10.26",
}


def _network_disabled():
    return not os.environ.get("CI") and not os.environ.get("RUN_NETWORK_TESTS")


def _canon(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_lines(path):
    lines = []
    for raw in open(path, encoding="utf-8"):
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("-r "):
            continue
        lines.append(s)
    return lines


def _pins(path):
    pins = {}
    for line in _requirement_lines(path):
        match = REQ_LINE_RE.match(line)
        assert match, f"unparseable pin in {path}: {line}"
        pins[_canon(match.group(1).split("[", 1)[0])] = match.group(2)
    return pins


def _lock_blocks(path):
    blocks = []
    current = []
    for raw in open(path, encoding="utf-8"):
        if raw.startswith("#") or not raw.strip():
            continue
        if not raw.startswith(" ") and REQ_LINE_RE.match(raw.strip().rstrip("\\").strip()):
            if current:
                blocks.append("".join(current))
            current = [raw]
        else:
            if current:
                current.append(raw)
    if current:
        blocks.append("".join(current))
    return blocks


def _lock_pins(path):
    pins = {}
    for block in _lock_blocks(path):
        match = REQ_LINE_RE.search(block)
        if not match:
            continue
        pins[_canon(match.group(1).split("[", 1)[0])] = match.group(2)
    return pins


def test_runtime_requirements_exact_pinned():
    for filename in ("requirements.txt", "requirements-dev.txt"):
        path = os.path.join(REPO_ROOT, filename)
        for line in _requirement_lines(path):
            assert PIN_RE.match(line), f"{filename}: {line!r} is not an exact == pin"
            assert ">=" not in line and "~=" not in line and "*" not in line


def test_lock_files_fully_hashed():
    for filename in ("requirements.lock", "requirements-dev.lock", "requirements-pip-audit.txt"):
        path = os.path.join(REPO_ROOT, filename)
        blocks = _lock_blocks(path)
        assert blocks, f"{filename} has no package blocks"
        for block in blocks:
            assert "==" in block, f"{filename} block missing ==:\n{block}"
            assert HASH_RE.search(block), f"{filename} block missing sha256 hash:\n{block}"


def test_lock_matches_requirements_txt():
    runtime = _pins(os.path.join(REPO_ROOT, "requirements.txt"))
    runtime_lock = _lock_pins(os.path.join(REPO_ROOT, "requirements.lock"))
    for name, ver in runtime.items():
        assert name in runtime_lock, f"{name} missing from requirements.lock"
        assert runtime_lock[name] == ver, f"{name}: requirements.txt has {ver}, lock has {runtime_lock[name]}"

    dev = _pins(os.path.join(REPO_ROOT, "requirements-dev.txt"))
    dev_lock = _lock_pins(os.path.join(REPO_ROOT, "requirements-dev.lock"))
    for name, ver in dev.items():
        assert name in dev_lock, f"{name} missing from requirements-dev.lock"
        assert dev_lock[name] == ver, f"{name}: requirements-dev.txt has {ver}, lock has {dev_lock[name]}"


@pytest.mark.parametrize(
    "name,floor",
    list(FLOORS.items()),
    ids=[f"{name}>={floor}" for name, floor in FLOORS.items()],
)
def test_bumped_versions_at_least(name, floor):
    pins = _pins(os.path.join(REPO_ROOT, "requirements.txt"))
    key = _canon(name)
    assert key in pins, f"{name} missing from requirements.txt"
    assert Version(pins[key]) >= Version(floor), f"{name} pin {pins[key]} is below {floor}"


def test_installed_versions_match_pins():
    import importlib.metadata

    pins = _pins(os.path.join(REPO_ROOT, "requirements.txt"))
    for name, ver in pins.items():
        dist_name = name
        try:
            installed = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            continue
        if installed != ver:
            if not os.environ.get("CI"):
                pytest.skip(f"{name}: installed {installed} != pin {ver} (stale venv)")
            assert installed == ver, f"{name}: installed {installed} != pin {ver}"


def test_ytdlp_supports_js_runtimes_flag():
    help_text = subprocess.check_output(
        [sys.executable, "-m", "yt_dlp", "--help"],
        text=True,
        timeout=30,
    )
    assert "--js-runtimes" in help_text


def test_ci_workflow_runs_pip_audit_on_all_locks():
    path = os.path.join(REPO_ROOT, ".github", "workflows", "security-audit.yml")
    data = yaml.safe_load(open(path, encoding="utf-8"))
    assert data.get("permissions") == {"contents": "read"}
    on = data.get("on", data.get(True))
    assert on is not None and "pull_request" in on

    uses = []
    runs = []
    jobs = data.get("jobs") or {}
    for job in jobs.values():
        for step in job.get("steps") or []:
            if "uses" in step:
                uses.append(step["uses"])
            if "run" in step:
                runs.append(step["run"])
    joined = "\n".join(runs)
    assert "pip-audit" in joined and "-r requirements.lock" in joined
    assert "-r requirements-dev.lock" in joined
    assert "--require-hashes" in joined
    assert uses, "expected SHA-pinned actions"
    for spec in uses:
        sha = spec.split("@", 1)[-1]
        assert SHA_RE.fullmatch(sha), f"uses: {spec} is not pinned to a 40-hex SHA"


def test_ci_runs_pytest():
    workflows = os.path.join(REPO_ROOT, ".github", "workflows")
    found = False
    for name in os.listdir(workflows):
        if not name.endswith((".yml", ".yaml")):
            continue
        text = open(os.path.join(workflows, name), encoding="utf-8").read()
        if "pytest tests/ytdlp" in text:
            found = True
            break
    assert found, "no workflow step runs `pytest tests/ytdlp`"


@pytest.mark.network
@pytest.mark.skipif(_network_disabled(), reason="network tests skipped by default")
def test_pip_audit_fails_on_known_vuln_fixture(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text("requests==2.19.0\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--no-deps", "--disable-pip", "-r", str(req)],
        capture_output=True,
        timeout=120,
    )
    combined = (result.stdout or b"") + (result.stderr or b"")
    text = combined.decode("utf-8", errors="replace")
    assert result.returncode != 0
    assert "requests" in text


@pytest.mark.network
@pytest.mark.skipif(_network_disabled(), reason="network tests skipped by default")
def test_pip_audit_passes_on_lock():
    lock = os.path.join(REPO_ROOT, "requirements.lock")
    result = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--no-deps", "--disable-pip", "-r", lock],
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, (result.stdout or b"") + (result.stderr or b"")


def test_pip_audit_offline_fixture(tmp_path, monkeypatch):
    from packaging.version import Version as PkgVersion
    from pip_audit._service.interface import VulnerabilityResult
    import pip_audit._cli as pip_audit_cli
    import pip_audit._service.pypi as pypi_mod

    fixture = tmp_path / "req.txt"
    fixture.write_text("requests==2.19.0\n", encoding="utf-8")

    def fake_query(self, spec):
        if spec.is_skipped():
            return spec, []
        version = str(getattr(spec, "version", ""))
        if spec.canonical_name == "requests" and version == "2.19.0":
            vuln = VulnerabilityResult(
                id="CVE-2018-18074",
                description="known-vulnerable-fixture",
                fix_versions=[PkgVersion("2.20.0")],
                aliases=set(),
            )
            return spec, [vuln]
        return spec, []

    monkeypatch.setattr(pypi_mod.PyPIService, "query", fake_query)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pip-audit", "-r", str(fixture), "--no-deps", "--disable-pip", "--progress-spinner", "off"],
    )
    with pytest.raises(SystemExit) as ei:
        pip_audit_cli.audit()
    assert ei.value.code == 1
