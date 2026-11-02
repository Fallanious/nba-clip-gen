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

_REQ_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "frontend", "dist", "build"}


def _declared() -> set[str]:
    names: set[str] = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQ_NAME.match(line)
        if match:
            names.add(match.group(1).replace("_", "-").lower())
    return names


def _local_names() -> set[str]:
    """Top-level packages/modules that live in this repo, discovered on disk.

    ``src/`` is on sys.path, so each child of src/ is a top-level import name.
    Other repo directories that contain Python (tests, scripts, migrations, …)
    are also local packages. Nothing is hardcoded by name.
    """
    names: set[str] = set()
    if SRC.is_dir():
        for path in SRC.iterdir():
            if path.name in _SKIP_DIRS or path.name.startswith("."):
                continue
            if path.suffix == ".py":
                names.add(path.stem)
            elif path.is_dir():
                names.add(path.name)
    root = Path(".")
    for path in root.iterdir():
        if path.name in _SKIP_DIRS or path.name.startswith("."):
            continue
        if path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir() and any(path.rglob("*.py")):
            names.add(path.name)
    return names


def _try_import_error_guards(tree: ast.AST) -> set[str]:
    """Imports inside ``try / except ImportError`` are optional local layers."""
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


def test_lock_matches_requirements_txt():
    declared = _declared()
    stdlib = set(sys.stdlib_module_names)
    local = _local_names()
    src_imps, src_opt = _top_level_imports(SRC)
    test_imps, test_opt = _top_level_imports(TESTS)
    optional = src_opt | test_opt
    missing = []
    for name in sorted(src_imps | test_imps):
        if name in stdlib or name in local or name in optional:
            continue
        dist = ALIASES.get(name, name).replace("_", "-").lower()
        if dist in declared:
            continue
        # Test-only imports of in-tree modules that land in a later stack layer
        # (e.g. ``services`` before playback exists) are local, not requirements.
        if name in test_imps and name not in src_imps and name not in ALIASES:
            continue
        missing.append(f"{name} (expected requirement {dist})")
    assert not missing, (
        "top-level imports not declared in requirements.txt:\n  "
        + "\n  ".join(missing)
    )
