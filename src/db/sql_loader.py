"""SQL file loader and registry."""

import re
from pathlib import Path
from typing import Mapping
from sqlalchemy import text, bindparam, BigInteger, Date, Text, Boolean, Float, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql import TextClause


def _strip_header_comments(sql: str) -> str:
    """Remove header comments from SQL files."""
    lines = []
    in_header = True
    for line in sql.split("\n"):
        if in_header and line.strip().startswith("--"):
            continue
        in_header = False
        lines.append(line)
    return "\n".join(lines).strip()


def _apply_type_hints(key: str, sql_text: str) -> TextClause:
    """Apply type hints to bindparams based on the query key."""
    stmt = text(sql_text)

    # Type mappings per query
    type_hints = {
        "players/search": {
            "q": Text,
            "team_id": SmallInteger,
            "active": Boolean,
            "c_score": Float,
            "c_last_name": Text,
            "c_id": BigInteger,
            "limit": Integer,
        },
        "players/search_browse": {
            "team_id": SmallInteger,
            "active": Boolean,
            "c_last_name": Text,
            "c_id": BigInteger,
            "limit": Integer,
        },
        "players/get": {
            "id": BigInteger,
        },
        "players/exists": {
            "id": BigInteger,
        },
        "players/role_counts": {
            "id": BigInteger,
        },
        "clips/by_player": {
            "pid": BigInteger,
            "roles": ARRAY(Text),
            "date_from": Date,
            "date_to": Date,
            "season": Text,
            "game_id": BigInteger,
            "action_types": ARRAY(Text),
            "subtypes": ARRAY(Text),
            "outcome": Text,
            "status_all": Boolean,
            "c_date": Date,
            "c_game_id": BigInteger,
            "c_period": Integer,
            "c_play_index": Integer,
            "c_id": BigInteger,
            "limit": Integer,
        },
        "clips/get": {
            "id": BigInteger,
        },
        "clips/playback_target": {
            "id": BigInteger,
        },
    }

    hints = type_hints.get(key, {})
    used = _extract_params_from_sql(sql_text)
    if hints:
        params = []
        for param_name, param_type in hints.items():
            if param_name in used:
                params.append(bindparam(param_name, type_=param_type))
        if params:
            stmt = stmt.bindparams(*params)

    return stmt


def _validate_sql_file(key: str, sql: str):
    """Validate SQL file content."""
    # Check for exactly one statement (no multiple statements separated by ;)
    # This is tricky because function bodies can contain semicolons
    # For now, we'll do a simple check: no semicolon outside of quotes/comments
    stripped = re.sub(r"'[^']*'", "", sql)  # Remove string literals
    stripped = re.sub(r"--[^\n]*", "", stripped)  # Remove comments
    semicolons = [i for i, c in enumerate(stripped) if c == ";"]

    if len(semicolons) > 1:
        raise ValueError(f"SQL file {key} contains multiple statements (found {len(semicolons)} semicolons)")

    if len(semicolons) == 1 and semicolons[0] < len(stripped) - 5:
        # Semicolon is not at the end (allowing for trailing whitespace)
        raise ValueError(f"SQL file {key} has content after semicolon")


def _extract_params_from_header(sql: str) -> set[str]:
    """Extract parameter names from -- params: header comment."""
    params = set()
    for line in sql.split("\n"):
        line = line.strip()
        if line.startswith("-- params:"):
            param_text = line[10:].strip()
            # Parse format like: "id bigint, name text|null, ..."
            for param_def in param_text.split(","):
                param_def = param_def.strip()
                if param_def:
                    param_name = param_def.split()[0].strip()
                    params.add(param_name)
    return params


def _extract_params_from_sql(sql: str) -> set[str]:
    """Extract :param references from SQL."""
    # Match :param but not ::type (type cast)
    return set(re.findall(r"(?<!:):(\w+)(?!:)", sql))


def load_queries() -> Mapping[str, TextClause]:
    """Load all SQL query files and return a registry."""
    queries_dir = Path(__file__).parent / "sql" / "queries"
    if not queries_dir.exists():
        raise RuntimeError(f"Queries directory not found: {queries_dir}")

    registry = {}
    raw_sql_registry = {}
    required_keys = {
        "players/search",
        "players/search_browse",
        "players/get",
        "players/exists",
        "players/role_counts",
        "clips/by_player",
        "clips/get",
        "clips/playback_target",
    }

    for sql_file in queries_dir.rglob("*.sql"):
        # Build key from relative path without .sql extension
        rel_path = sql_file.relative_to(queries_dir)
        key = str(rel_path.with_suffix("")).replace("\\", "/")

        # Read and process SQL
        raw_sql = sql_file.read_text(encoding="utf-8")
        sql_content = _strip_header_comments(raw_sql)

        # Validate
        _validate_sql_file(key, sql_content)

        # Check that declared params match used params (skip templated queries)
        if "/*dynamic_filters*/" not in sql_content:
            declared_params = _extract_params_from_header(raw_sql)
            used_params = _extract_params_from_sql(sql_content)
            if declared_params and declared_params != used_params:
                missing = used_params - declared_params
                extra = declared_params - used_params
                msg_parts = []
                if missing:
                    msg_parts.append(f"missing from header: {missing}")
                if extra:
                    msg_parts.append(f"not used in SQL: {extra}")
                raise ValueError(f"Parameter mismatch in {key}: {', '.join(msg_parts)}")

        # Apply type hints and store
        registry[key] = _apply_type_hints(key, sql_content)
        raw_sql_registry[key] = sql_content

    # Ensure all required keys are present
    missing = required_keys - registry.keys()
    if missing:
        raise RuntimeError(f"Missing required SQL files: {missing}")

    return registry, raw_sql_registry


# Global registry loaded once at module import
try:
    registry, raw_sql_registry = load_queries()
except Exception as e:
    # In some contexts (tests without SQL files), we may not have queries yet
    registry = {}
    raw_sql_registry = {}
    import sys
    print(f"Warning: Could not load SQL registry: {e}", file=sys.stderr)


def get_registry() -> Mapping[str, TextClause]:
    """Get the loaded SQL registry."""
    return registry


def get_raw_sql(key: str) -> str:
    """Return the stripped SQL text for a query key (for dynamic filter assembly)."""
    return raw_sql_registry[key]
