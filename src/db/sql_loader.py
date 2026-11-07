"""Load named SQL files from src/db/sql/queries."""

from pathlib import Path

from sqlalchemy import text

SQL_ROOT = Path(__file__).resolve().parent / "sql"
QUERIES_ROOT = SQL_ROOT / "queries"


def load_query_sql(name: str) -> str:
    """name like plays/capability maps to queries/plays/capability.sql"""
    path = QUERIES_ROOT.joinpath(*name.split("/")).with_suffix(".sql")
    if not path.is_file():
        raise FileNotFoundError(path)
    body = path.read_text(encoding="utf-8")
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
