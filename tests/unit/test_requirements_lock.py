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

# Repo-owned packages/modules. Names here are local even if this stack layer
# has not created the matching src/ path yet (e.g. services before playback).
LOCAL_PACKAGES = frozenset({
    "app",
    "auth",
    "authz",
    "backfill_clip_attributes",
    "config",
    "db",
    "download_video",
    "errors",
    "generate_clips",
    "match_play_timestamps",
    "pagination",
    "playbyplay",
    "repositories",
    "routes",
    "scrape_playbyplay",
    "scripts",
    "services",
    "utils",
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


def _try_import_error_guards(tree: ast.AST) -> set[str]:
    """Imports inside ``try / except ImportError``."""
    optional: set[str] = set()

    def _is_import_error(handler: ast.ExceptHandler) -> bool:
        t = handler.type
        if t is None:
            return False
        names = []
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, ast.Tuple):
            names.extend(e.id for e in t.elts if isinstance(e, ast.Name))
        return "ImportError" in names or "ModuleNotFoundError" in names

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(_is_import_error(h) for h in node.handlers):
            continue
        for child in node.body:
            for sub in ast.walk(child):
                if isinstance(sub, ast.Import):
                    for alias in sub.names:
                        optional.add(alias.name.split(".")[0])
                elif isinstance(sub, ast.ImportFrom) and sub.module and sub.level == 0:
                    optional.add(sub.module.split(".")[0])
    return optional


def _top_level_imports(root: Path) -> tuple[set[str], set[str]]:
    """Return (required imports, optional ImportError-guarded imports)."""
    found: set[str] = set()
    optional: set[str] = set()
    if not root.exists():
        return found, optional
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        optional |= _try_import_error_guards(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    found.add(node.module.split(".")[0])
    return found, optional


def collect_undeclared(
    *,
    src: Path = SRC,
    tests: Path = TESTS,
    requirements: Path = REQUIREMENTS,
) -> list[str]:
    """Third-party top-level imports not declared in requirements.txt.

    Local names (explicit LOCAL_PACKAGES or a matching path under src/) are
    exempt. try/except ImportError exempts a name only when it is local;
    an undeclared third-party import is reported even inside that guard.
    """
    declared = _declared(requirements)
    stdlib = set(sys.stdlib_module_names)
    src_imps, src_opt = _top_level_imports(src)
    test_imps, test_opt = _top_level_imports(tests)
    # try/except ImportError is recorded only so local names can be skipped
    # via _is_local; third-party names in src_opt/test_opt are still required.
    _optional_local = {n for n in (src_opt | test_opt) if _is_local(n, src)}
    missing = []
    for name in sorted(src_imps | test_imps):
        if name in stdlib:
            continue
        if _is_local(name, src) or name in _optional_local:
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
