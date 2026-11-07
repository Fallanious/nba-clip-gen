"""Migration runner refuses production/Neon mismatch. Never prints secrets."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")

from db.migrate import MigrationRefused, assert_migration_target_allowed, parse_host


def test_local_hosts_allowed_for_non_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    assert_migration_target_allowed("postgresql://nba@127.0.0.1:5432/nba_clip_gen_test")
    assert_migration_target_allowed("postgresql://nba@localhost:5432/nba_clip_gen_test")


def test_refuses_empty_or_unknown_app_env(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(MigrationRefused, match="explicit dev or test"):
        assert_migration_target_allowed("postgresql://nba@127.0.0.1:5432/x")
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(MigrationRefused, match="explicit dev or test"):
        assert_migration_target_allowed("postgresql://nba@127.0.0.1:5432/x")


def test_refuses_local_dsn_query_overrides(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    with pytest.raises(MigrationRefused, match="unsupported local DSN"):
        assert_migration_target_allowed(
            "postgresql://nba@127.0.0.1:5432/x?sslmode=disable"
        )


def test_refuses_production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(MigrationRefused, match="production"):
        assert_migration_target_allowed("postgresql://nba@127.0.0.1:5432/x")


def test_refuses_neon_without_endpoint(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("NEON_DEV_ENDPOINT_ID", raising=False)
    with pytest.raises(MigrationRefused, match="NEON_DEV_ENDPOINT_ID"):
        assert_migration_target_allowed(
            "postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech/neondb?sslmode=require"
        )


def test_refuses_neon_endpoint_mismatch(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-odd-smoke-b465tvf8")
    with pytest.raises(MigrationRefused, match="mismatch"):
        assert_migration_target_allowed(
            "postgresql://u@ep-other-host-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require"
        )


def test_refuses_neon_without_tls_sslmode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-odd-smoke-b465tvf8")
    with pytest.raises(MigrationRefused, match="TLS sslmode"):
        assert_migration_target_allowed(
            "postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech/neondb"
        )


def test_neon_hostname_fullmatch_rejects_suffix(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-odd-smoke-b465tvf8")
    with pytest.raises(MigrationRefused, match="unexpected Neon host"):
        assert_migration_target_allowed(
            "postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech.extra.neon.tech/neondb?sslmode=require"
        )


def test_parse_host_does_not_return_user_or_password():
    host = parse_host("postgresql://secret-user:secret-pass@127.0.0.1:5432/db")
    assert host == "127.0.0.1"
    assert "secret" not in host


def _sql_without_comments(body: str) -> str:
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines)


INTERNAL_PERSIST_SQL = frozenset({
    "apply_attestation.sql",
    "insert_clip_player.sql",
    "lookup_game.sql",
    "lookup_player.sql",
    "select_play_capability.sql",
    "upsert_play_metadata.sql",
})
DESIGNATED_PERSIST_DIR = Path("src/db/sql/queries/persist")


def is_designated_persist_query(path: Path) -> bool:
    designated = DESIGNATED_PERSIST_DIR.resolve()
    resolved = path if path.is_absolute() else path
    try:
        resolved = resolved.resolve()
    except OSError:
        return False
    return resolved.parent == designated and resolved.name in INTERNAL_PERSIST_SQL


def test_sql_loader_rejects_path_traversal():
    from db.sql_loader import load_query_sql

    with pytest.raises(ValueError, match="escapes queries root"):
        load_query_sql("persist/../../schema/0001_init")
    with pytest.raises(ValueError, match="escapes queries root"):
        load_query_sql("../schema/0001_init")
    with pytest.raises((ValueError, FileNotFoundError)):
        load_query_sql("/etc/passwd")
    body = load_query_sql("plays/capability")
    assert "can_create" in body.lower() or "play_capability" in body.lower()


def test_query_files_exclude_private_columns():
    from pathlib import Path
    root = Path("src/db/sql/queries")
    persist_names = {
        p.name for p in (root / "persist").glob("*.sql") if p.parent.resolve() == DESIGNATED_PERSIST_DIR.resolve()
    }
    assert persist_names == INTERNAL_PERSIST_SQL
    for path in root.rglob("*.sql"):
        if is_designated_persist_query(path):
            continue
        lower = _sql_without_comments(path.read_text(encoding="utf-8")).lower()
        assert "s3_key" not in lower
        assert "source_version" not in lower
        assert "video_start_s" not in lower
        assert "http://" not in lower
        assert "s3://" not in lower


def test_scanner_excludes_only_designated_persist_dir():
    assert is_designated_persist_query(Path("src/db/sql/queries/persist/lookup_game.sql"))
    assert not is_designated_persist_query(Path("src/db/sql/queries/plays/persist/lookup_game.sql"))
    assert not is_designated_persist_query(Path("src/db/sql/queries/nested/persist/apply_attestation.sql"))
    assert not is_designated_persist_query(Path("src/db/sql/queries/persist/nested/lookup_game.sql"))


def test_seed_labeled_fictional_and_not_live_prefix():
    from pathlib import Path
    text = Path("src/db/sql/seed/dev_fictional_capability.sql").read_text(encoding="utf-8")
    assert "FICTIONAL" in text
    executable = _sql_without_comments(text)
    assert "fixtures/" in executable
    assert "'games/" not in executable
    assert "'clips/" not in executable
