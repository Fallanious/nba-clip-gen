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


def _first_party() -> set[str]:
    names = {"tests", "src"}
    if SRC.is_dir():
        for path in SRC.iterdir():
            if path.name.startswith("_"):
                continue
            if path.suffix == ".py":
                names.add(path.stem)
            elif path.is_dir():
                names.add(path.name)
    return names


def _top_level_imports(root: Path) -> set[str]:
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


def test_lock_matches_requirements_txt():
    declared = _declared()
    stdlib = set(sys.stdlib_module_names)
    first_party = _first_party()
    imported = _top_level_imports(SRC) | _top_level_imports(TESTS)
    missing = []
    for name in sorted(imported):
        if name in stdlib or name in first_party:
            continue
        dist = ALIASES.get(name, name).replace("_", "-").lower()
        if dist not in declared:
            missing.append(f"{name} (expected requirement {dist})")
    assert not missing, (
        "top-level imports not declared in requirements.txt:\n  "
        + "\n  ".join(missing)
    )
