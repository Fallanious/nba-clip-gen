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
ALLOWED_ENVS = frozenset({"dev", "test"})
NEON_TLS_MODES = frozenset({"require", "verify-ca", "verify-full"})
# Stable lock id for apply_migrations. Not a secret.
MIGRATION_ADVISORY_LOCK_KEY = 0x4E424143  # 'NBAC'
_NEON_HOST = re.compile(
    r"^ep-[a-z0-9-]+(?:-pooler)?(?:\.c-[1-9][0-9]{0,3})?\.(?P<region>[a-z]{2}-[a-z0-9]+-\d+)\.aws\.neon\.tech$",
    re.ASCII | re.IGNORECASE,
)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_NEON_QUERY_KEYS = frozenset({"sslmode"})
_LOCAL_QUERY_KEYS = frozenset()


class MigrationRefused(SystemExit):
    pass


def _app_env() -> str:
    return os.getenv("APP_ENV") or ""


def _query_pairs(database_url: str) -> list[tuple[str, str]]:
    parsed = urlparse(database_url)
    return parse_qsl(parsed.query, keep_blank_values=True)


def parse_host(database_url: str) -> str:
    if not database_url or not str(database_url).strip():
        raise MigrationRefused("migration refused: missing database target")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise MigrationRefused("migration refused: unsupported scheme")
    query = _query_pairs(database_url)
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
    if env not in ALLOWED_ENVS:
        raise MigrationRefused("migration refused: APP_ENV must be explicit dev or test")
    host = parse_host(database_url)
    query = _query_pairs(database_url)
    keys = {k.lower() for k, _ in query}
    if host in _LOCAL_HOSTS:
        extra = keys - _LOCAL_QUERY_KEYS
        if extra:
            raise MigrationRefused("migration refused: unsupported local DSN query parameter")
        return
    if host.endswith(".neon.tech"):
        extra = keys - _NEON_QUERY_KEYS
        if extra:
            raise MigrationRefused("migration refused: unsupported Neon DSN query parameter")
        expected = os.getenv("NEON_DEV_ENDPOINT_ID") or ""
        if not expected:
            raise MigrationRefused("migration refused: Neon host without NEON_DEV_ENDPOINT_ID")
        match = _NEON_HOST.fullmatch(host)
        if not match:
            raise MigrationRefused("migration refused: unexpected Neon host")
        sslmode = ""
        for key, value in query:
            if key.lower() == "sslmode":
                sslmode = (value or "").lower()
        if sslmode not in NEON_TLS_MODES:
            raise MigrationRefused("migration refused: Neon host requires TLS sslmode")
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


REQUIRED_0001_COLUMNS = {
    "teams": frozenset({"id", "abbr", "city", "name"}),
    "players": frozenset({"id", "bbref_id", "full_name", "last_name"}),
    "games": frozenset({
        "id", "bbref_id", "season", "game_date",
        "home_team_id", "away_team_id", "s3_key", "duration_s",
    }),
    "clips": frozenset({
        "id", "game_id", "game_date", "play_index", "period",
        "description", "action_type", "shot_subtype", "outcome",
        "video_start_s", "video_end_s",
    }),
    "clip_players": frozenset({"clip_id", "player_id", "role", "game_date"}),
}

REQUIRED_0001_UNIQUES = (
    ("teams", ("abbr",)),
    ("players", ("bbref_id",)),
    ("games", ("bbref_id",)),
    ("clips", ("game_id", "play_index")),
)

REQUIRED_0003_COLUMNS = {
    "games": frozenset({"source_kind", "source_version", "is_fictional"}),
    "clips": frozenset({
        "timing_provenance", "matcher_run_id", "matcher_version",
        "video_timestamp_s", "matched_source_version",
    }),
}

REQUIRED_0003_CONSTRAINTS = {
    "games": frozenset({
        "games_s3_key_not_url",
        "games_source_version_trim",
        "games_fictional_key_prefix",
        "games_duration_finite_positive",
    }),
    "clips": frozenset({
        "clips_video_finite_window",
        "clips_none_requires_null_offsets",
        "clips_attested_requires_binding",
    }),
}

# Canonical body tokens. Names plus substring markers are not enough:
# CHECK (true), a function that returns true, or a view that embeds marker
# text as a literal would pass a name/marker scan.
REQUIRED_0003_CONSTRAINT_TOKENS = {
    ("games", "games_s3_key_not_url"): ("s3_key", "https?", "s3"),
    ("games", "games_source_version_trim"): ("source_version", "trim"),
    ("games", "games_fictional_key_prefix"): ("is_fictional", "fixtures/%"),
    ("games", "games_duration_finite_positive"): (
        "nba_numeric_is_finite", "duration_s", "> 0",
    ),
    ("clips", "clips_video_finite_window"): (
        "nba_numeric_is_finite", "video_start_s", "video_end_s",
    ),
    ("clips", "clips_none_requires_null_offsets"): (
        "timing_provenance", "none", "video_start_s", "video_end_s",
    ),
    ("clips", "clips_attested_requires_binding"): (
        "matcher_attested", "nba_numeric_is_finite", "matcher_run_id",
        "video_timestamp_s",
    ),
}
REQUIRED_FUNCTION_TOKENS = ("NaN", "Infinity", "-Infinity")
REQUIRED_VIEW_CALL_TOKENS = (
    "nba_numeric_is_finite(",
    "timing_provenance",
    "matcher_attested",
    "duration_s",
    "is_fictional",
)


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


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (f"public.{name}",)).fetchone()
    return row[0] is not None


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    ).fetchall()
    return {row[0] for row in rows}


def _unique_column_sets(conn, table: str) -> set[tuple[str, ...]]:
    rows = conn.execute(
        """
        SELECT array_agg(a.attname ORDER BY u.ord)::text[]
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
        WHERE n.nspname = 'public'
          AND t.relname = %s
          AND c.contype IN ('u', 'p')
        GROUP BY c.oid
        """,
        (table,),
    ).fetchall()
    found: set[tuple[str, ...]] = set()
    for row in rows:
        cols = row[0]
        if cols is None:
            continue
        found.add(tuple(cols))
    return found


def _constraintdef(conn, table: str, name: str) -> str | None:
    row = conn.execute(
        """
        SELECT pg_get_constraintdef(c.oid, true)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND t.relname = %s
          AND c.conname = %s
        """,
        (table, name),
    ).fetchone()
    if row is None or not row[0]:
        return None
    return row[0]


def _functiondef(conn) -> str | None:
    row = conn.execute(
        """
        SELECT pg_get_functiondef('public.nba_numeric_is_finite(numeric)'::regprocedure)
        """
    ).fetchone()
    if row is None or not row[0]:
        return None
    return row[0]


def _constraint_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND t.relname = %s
          AND c.contype = 'c'
        """,
        (table,),
    ).fetchall()
    return {row[0] for row in rows}


def _play_capability_viewdef(conn) -> str:
    if not _table_exists(conn, "play_capability"):
        raise MigrationRefused("migration refused: 0003 schema mismatch: play_capability missing")
    row = conn.execute(
        "SELECT pg_get_viewdef('public.play_capability'::regclass, true)"
    ).fetchone()
    if row is None or not row[0]:
        raise MigrationRefused("migration refused: 0003 schema mismatch: empty play_capability viewdef")
    return row[0]


def _present_0001_tables(conn) -> set[str]:
    return {name for name in REQUIRED_0001_COLUMNS if _table_exists(conn, name)}


def _0001_mismatch_reasons(conn) -> list[str]:
    reasons: list[str] = []
    present = _present_0001_tables(conn)
    expected = set(REQUIRED_0001_COLUMNS)
    if present != expected:
        reasons.append(
            f"0001 tables present={sorted(present)} expected={sorted(expected)}"
        )
        return reasons
    for table, cols in REQUIRED_0001_COLUMNS.items():
        have = _columns(conn, table)
        missing = cols - have
        if missing:
            reasons.append(f"{table} missing columns {sorted(missing)}")
    for table, cols in REQUIRED_0001_UNIQUES:
        uniques = _unique_column_sets(conn, table)
        if cols not in uniques:
            reasons.append(f"{table} missing UNIQUE {cols}")
    return reasons


def _verify_0001(conn) -> None:
    reasons = _0001_mismatch_reasons(conn)
    if reasons:
        raise MigrationRefused(
            "migration refused: 0001 schema mismatch: " + "; ".join(reasons)
        )


def _0003_flags(conn) -> dict[str, bool]:
    games_cols = _columns(conn, "games") if _table_exists(conn, "games") else set()
    clips_cols = _columns(conn, "clips") if _table_exists(conn, "clips") else set()
    return {
        "games_cols": REQUIRED_0003_COLUMNS["games"] <= games_cols,
        "clips_cols": REQUIRED_0003_COLUMNS["clips"] <= clips_cols,
        "view": _table_exists(conn, "play_capability"),
    }


def _0003_definition_mismatch_reasons(conn) -> list[str]:
    reasons: list[str] = []
    flags = _0003_flags(conn)
    if not all(flags.values()):
        reasons.append("0003 flags=" + str(flags))
        return reasons
    for (table, name), tokens in REQUIRED_0003_CONSTRAINT_TOKENS.items():
        defn = _constraintdef(conn, table, name)
        if defn is None:
            reasons.append(f"{table} missing constraint {name}")
            continue
        compact = " ".join(defn.lower().split())
        if compact in {"check (true)", "check(true)"}:
            reasons.append(f"{name} body is CHECK (true)")
            continue
        for token in tokens:
            if token.lower() not in defn.lower():
                reasons.append(f"{name} definition missing {token}")
    fn = _functiondef(conn)
    if fn is None:
        reasons.append("nba_numeric_is_finite missing")
    else:
        for token in REQUIRED_FUNCTION_TOKENS:
            if token not in fn:
                reasons.append("nba_numeric_is_finite body missing " + token)
        if re.search(r"\bSELECT\s+true\b", fn, re.IGNORECASE) and "NaN" not in fn:
            reasons.append("nba_numeric_is_finite body is permissive")
    try:
        viewdef = _play_capability_viewdef(conn)
    except MigrationRefused as exc:
        reasons.append(str(exc))
        return reasons
    compact_view = " ".join(viewdef.lower().split()).replace(" (", "(")
    if re.search(r"(?<!is )true\s+as\s+can_create\b", compact_view):
        reasons.append("play_capability can_create is a constant true")
    for token in REQUIRED_VIEW_CALL_TOKENS:
        if token.lower() not in compact_view:
            reasons.append(f"play_capability viewdef missing {token}")
    return reasons


def _verify_0003(conn) -> None:
    reasons = _0003_definition_mismatch_reasons(conn)
    if reasons:
        raise MigrationRefused(
            "migration refused: 0003 schema mismatch: " + "; ".join(reasons)
        )


def _stamp(conn, version: str) -> None:
    conn.execute(
        "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
        (version,),
    )


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
                "SELECT pg_advisory_xact_lock(%s)",
                (MIGRATION_ADVISORY_LOCK_KEY,),
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version text PRIMARY KEY,
                  applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            have = _applied(conn)

            if "0001_init" in have:
                _verify_0001(conn)
            else:
                present = _present_0001_tables(conn)
                if not present:
                    _exec_file(conn, SCHEMA_DIR / "0001_init.sql")
                    _verify_0001(conn)
                    _stamp(conn, "0001_init")
                    applied_now.append("0001_init")
                else:
                    reasons = _0001_mismatch_reasons(conn)
                    if reasons:
                        raise MigrationRefused(
                            "migration refused: partial or mismatched 0001 "
                            "baseline; will not stamp: " + "; ".join(reasons)
                        )
                    _stamp(conn, "0001_init")
                    applied_now.append("0001_init")
            have = _applied(conn)

            # Authoritatively reapply idempotent 0003 under the lock, even when
            # already stamped. Same-name CHECK (true) / permissive function or
            # view bodies are replaced. Then verify canonical definitions.
            _exec_file(conn, SCHEMA_DIR / "0003_on_demand.sql")
            _verify_0003(conn)
            if "0003_on_demand" not in have:
                _stamp(conn, "0003_on_demand")
                applied_now.append("0003_on_demand")

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
        conn.autocommit = False
        try:
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (MIGRATION_ADVISORY_LOCK_KEY,),
            )
            with conn.cursor() as cur:
                for stmt in _split_sql(text):
                    cur.execute(stmt)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


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
