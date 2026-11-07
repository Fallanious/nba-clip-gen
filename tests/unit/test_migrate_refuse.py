"""Migration runner refuses production/Neon mismatch. Never prints secrets."""

import sys

import pytest

sys.path.insert(0, "src")

from db.migrate import MigrationRefused, assert_migration_target_allowed, parse_host


def test_local_hosts_allowed_for_non_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    assert_migration_target_allowed("postgresql://nba@127.0.0.1:5432/nba_clip_gen_test")
    assert_migration_target_allowed("postgresql://nba@localhost:5432/nba_clip_gen_test")


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


def test_query_files_exclude_private_columns():
    from pathlib import Path
    root = Path("src/db/sql/queries")
    for path in root.rglob("*.sql"):
        lower = _sql_without_comments(path.read_text(encoding="utf-8")).lower()
        assert "s3_key" not in lower
        assert "source_version" not in lower
        assert "video_start_s" not in lower
        assert "http://" not in lower
        assert "s3://" not in lower


def test_seed_labeled_fictional_and_not_live_prefix():
    from pathlib import Path
    text = Path("src/db/sql/seed/dev_fictional_capability.sql").read_text(encoding="utf-8")
    assert "FICTIONAL" in text
    executable = _sql_without_comments(text)
    assert "fixtures/" in executable
    assert "'games/" not in executable
    assert "'clips/" not in executable
