"""Load named SQL files from src/db/sql/queries."""

from pathlib import Path

from sqlalchemy import text

SQL_ROOT = Path(__file__).resolve().parent / "sql"
QUERIES_ROOT = SQL_ROOT / "queries"


def load_query_sql(name: str) -> str:
    """name like plays/capability maps to queries/plays/capability.sql"""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError("query name must be a nonempty canonical relative path")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("query path escapes queries root")
    root = QUERIES_ROOT.resolve()
    path = QUERIES_ROOT.joinpath(*parts).with_suffix(".sql")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("query path escapes queries root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    body = resolved.read_text(encoding="utf-8")
    lines = []
    skipping = True
    for line in body.splitlines():
        if skipping and line.strip().startswith("--"):
            continue
        skipping = False
        lines.append(line)
    return "\n".join(lines).strip()


def load_query(name: str):
    return text(load_query_sql(name))
