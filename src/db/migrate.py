"""Apply src/db/sql/schema versions. Never prints DATABASE_URL or secrets."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from db.sql_loader import SQL_ROOT

SCHEMA_DIR = SQL_ROOT / "schema"
VERSIONS = ("0001_init", "0003_on_demand")
PRODUCTION_ENVS = frozenset({"prod", "production"})
_NEON_HOST = re.compile(
    r"^ep-[a-z0-9-]+(?:-pooler)?(?:\.c-[1-9][0-9]{0,3})?\.(?P<region>[a-z]{2}-[a-z0-9]+-\d+)\.aws\.neon\.tech$",
    re.ASCII | re.IGNORECASE,
)


class MigrationRefused(SystemExit):
    pass


def _app_env() -> str:
    return os.getenv("APP_ENV") or ""


def parse_host(database_url: str) -> str:
    if not database_url or not str(database_url).strip():
        raise MigrationRefused("migration refused: missing database target")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise MigrationRefused("migration refused: unsupported scheme")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    keys = [k.lower() for k, _ in query]
    if len(keys) != len(set(keys)):
        raise MigrationRefused("migration refused: duplicate query parameter")
    forbidden = {"host", "hostaddr", "service", "options", "port", "user", "dbname"}
    if any(k in forbidden for k in keys):
        raise MigrationRefused("migration refused: identity override query parameter")
    host = unquote(parsed.hostname or "")
    if not host:
        raise MigrationRefused("migration refused: missing host")
    if host.endswith("."):
        raise MigrationRefused("migration refused: trailing-dot host")
    return host.lower()


def assert_migration_target_allowed(database_url: str, app_env: str | None = None) -> None:
    env = (app_env if app_env is not None else _app_env()).lower()
    if env in PRODUCTION_ENVS:
        raise MigrationRefused("migration refused: production APP_ENV")
    host = parse_host(database_url)
    if host in {"127.0.0.1", "localhost", "::1"}:
        return
    if host.endswith(".neon.tech"):
        expected = os.getenv("NEON_DEV_ENDPOINT_ID") or ""
        if not expected:
            raise MigrationRefused("migration refused: Neon host without NEON_DEV_ENDPOINT_ID")
        match = _NEON_HOST.match(host)
        if not match:
            raise MigrationRefused("migration refused: unexpected Neon host")
        label = host.split(".")[0]
        endpoint = label[:-7] if label.endswith("-pooler") else label
        if endpoint != expected.lower():
            raise MigrationRefused("migration refused: Neon endpoint mismatch")
        return
    raise MigrationRefused("migration refused: host is not a local test database")


def _split_sql(script: str) -> list[str]:
    """Split on semicolons not inside quotes or dollar-quotes."""
    stmts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    dollar = None
    in_single = False
    while i < n:
        ch = script[i]
        if dollar:
            end = script.find(dollar, i)
            if end == -1:
                buf.append(script[i:])
                break
            buf.append(script[i:end + len(dollar)])
            i = end + len(dollar)
            dollar = None
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and script[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if ch == "-" and i + 1 < n and script[i + 1] == "-":
            nl = script.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            buf.append("\n")
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", script[i:])
            if m:
                dollar = m.group(0)
                buf.append(dollar)
                i += len(dollar)
                continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url)


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT to_regclass('public.schema_migrations')
            """
        )
        if cur.fetchone()[0] is None:
            return set()
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _teams_exist(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.teams')")
        return cur.fetchone()[0] is not None


def _exec_file(conn, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        for stmt in _split_sql(sql):
            cur.execute(stmt)


def apply_migrations(database_url: str) -> list[str]:
    assert_migration_target_allowed(database_url)
    applied_now: list[str] = []
    with _connect(database_url) as conn:
        conn.autocommit = False
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version text PRIMARY KEY,
                  applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            have = _applied(conn)
            if "0001_init" not in have and _teams_exist(conn):
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                    ("0001_init",),
                )
                have.add("0001_init")
            for version in VERSIONS:
                if version in have:
                    continue
                path = SCHEMA_DIR / f"{version}.sql"
                _exec_file(conn, path)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                    (version,),
                )
                applied_now.append(version)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return applied_now


def apply_seed(database_url: str, name: str = "dev_fictional_capability") -> None:
    assert_migration_target_allowed(database_url)
    path = SQL_ROOT / "seed" / f"{name}.sql"
    text = path.read_text(encoding="utf-8")
    if "FICTIONAL" not in text:
        raise MigrationRefused("migration refused: seed is not labeled fictional")
    if re.search(r"games/[A-Za-z0-9]", text) or re.search(r"clips/[A-Za-z0-9]", text):
        raise MigrationRefused("migration refused: seed uses live object prefix")
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql(text):
                cur.execute(stmt)
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    url = os.environ.get("DATABASE_URL", "")
    try:
        if argv and argv[0] == "seed":
            apply_seed(url)
        else:
            apply_migrations(url)
    except MigrationRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
