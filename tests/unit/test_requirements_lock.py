"""Every third-party import in src/ and tests/ is declared in requirements.txt."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REQUIREMENTS = Path("requirements.txt")
SRC = Path("src")
TESTS = Path("tests")

# import-name -> requirements.txt distribution name
ALIASES = {
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "flask_cors": "flask-cors",
    "bs4": "beautifulsoup4",
    "yt_dlp": "yt-dlp",
    "botocore": "boto3",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "attr": "attrs",
    "skimage": "scikit-image",
    "rest_framework": "djangorestframework",
}

# Later stack layers only. Names that already exist under src/ are discovered
# by _exists_under_src so we do not also hardcode PyPI-colliding names
# (utils, db, auth, config, errors, app, routes).
#   services      — playback layer
#   repositories  — players layer
#   playbyplay    — scraper used by scripts
#   scripts       — load_players
#   tests         — test package (not under src/)
#   migrations    — Alembic env
LOCAL_PACKAGES = frozenset({
    "services",
    "repositories",
    "playbyplay",
    "scripts",
    "tests",
    "migrations",
})

_REQ_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "frontend", "dist", "build"}


def _declared(requirements: Path = REQUIREMENTS) -> set[str]:
    names: set[str] = set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQ_NAME.match(line)
        if match:
            names.add(match.group(1).replace("_", "-").lower())
    return names


def _exists_under_src(name: str, src: Path = SRC) -> bool:
    if not src.is_dir():
        return False
    return (src / name).is_dir() or (src / f"{name}.py").is_file()


def _is_local(name: str, src: Path = SRC) -> bool:
    return name in LOCAL_PACKAGES or _exists_under_src(name, src)


def _top_level_imports(root: Path) -> set[str]:
    """Return top-level import names under root."""
    found: set[str] = set()
    if not root.exists():
        return found
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    found.add(node.module.split(".")[0])
    return found


def collect_undeclared(
    *,
    src: Path = SRC,
    tests: Path = TESTS,
    requirements: Path = REQUIREMENTS,
) -> list[str]:
    """Third-party top-level imports not declared in requirements.txt.

    Local names (later-layer LOCAL_PACKAGES or a matching path under src/) are
    exempt. A try/except ImportError does not skip an undeclared third-party
    import.
    """
    declared = _declared(requirements)
    stdlib = set(sys.stdlib_module_names)
    src_imps = _top_level_imports(src)
    test_imps = _top_level_imports(tests)
    missing = []
    for name in sorted(src_imps | test_imps):
        if name in stdlib:
            continue
        if _is_local(name, src):
            continue
        dist = ALIASES.get(name, name).replace("_", "-").lower()
        if dist in declared:
            continue
        missing.append(f"{name} (expected requirement {dist})")
    return missing


def test_lock_matches_requirements_txt():
    missing = collect_undeclared()
    assert not missing, (
        "top-level imports not declared in requirements.txt:\n  "
        + "\n  ".join(missing)
    )


def _scratch_tree(tmp_path: Path, tests_source: str, requirements: str = "flask==1.0\n"):
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "app.py").write_text("import flask\n", encoding="utf-8")
    (tests / "test_probe.py").write_text(tests_source, encoding="utf-8")
    req = tmp_path / "requirements.txt"
    req.write_text(requirements, encoding="utf-8")
    return src, tests, req


def test_undeclared_third_party_import_in_tests_is_caught(tmp_path):
    src, tests, req = _scratch_tree(tmp_path, "import httpx\n")
    missing = collect_undeclared(src=src, tests=tests, requirements=req)
    assert any(m.startswith("httpx ") for m in missing), missing


def test_undeclared_third_party_import_in_try_except_is_caught(tmp_path):
    src, tests, req = _scratch_tree(
        tmp_path,
        "try:\n    import httpx\nexcept ImportError:\n    pass\n",
    )
    missing = collect_undeclared(src=src, tests=tests, requirements=req)
    assert any(m.startswith("httpx ") for m in missing), missing


def test_try_except_local_package_is_allowed(tmp_path):
    src, tests, req = _scratch_tree(
        tmp_path,
        "try:\n    from services.playback import get_url\nexcept ImportError:\n    pass\n",
    )
    missing = collect_undeclared(src=src, tests=tests, requirements=req)
    assert not any(m.startswith("services ") for m in missing), missing


def test_try_except_scratch_local_module_is_allowed(tmp_path):
    """A local name discovered only via src/, not LOCAL_PACKAGES, is exempt."""
    src, tests, req = _scratch_tree(
        tmp_path,
        "try:\n    from localmod.x import y\nexcept ImportError:\n    pass\n",
    )
    (src / "localmod").mkdir()
    (src / "localmod" / "__init__.py").write_text("", encoding="utf-8")
    missing = collect_undeclared(src=src, tests=tests, requirements=req)
    assert not any(m.startswith("localmod ") for m in missing), missing


def test_pypi_colliding_name_not_on_disk_is_caught(tmp_path):
    """`utils`/`db` are real PyPI names; without a src/ path they are not local."""
    src, tests, req = _scratch_tree(tmp_path, "import utils\nimport db\n")
    missing = collect_undeclared(src=src, tests=tests, requirements=req)
    assert any(m.startswith("utils ") for m in missing), missing
    assert any(m.startswith("db ") for m in missing), missing
