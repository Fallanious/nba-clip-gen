"""Pre-connect persist target guard. Never opens a remote connection."""

import pytest

from persist_matcher_clips import (
    DEV_ENDPOINT_HOST,
    PersistRefused,
    assert_persist_app_env,
    assert_persist_database_url,
    persist_matched_output,
)

DEV_URL = f"postgresql://u@{DEV_ENDPOINT_HOST}/neondb"
LOCAL_URL = "postgresql://nba@127.0.0.1:5432/nba_clip_persist_test"
SECRET_URL = (
    f"postgresql://user:supersecret@{DEV_ENDPOINT_HOST}/neondb"
    "?host=evil.example.invalid"
)


def test_exact_dev_endpoint_allowlisted():
    assert assert_persist_database_url(DEV_URL) == "dev"
    assert assert_persist_database_url(DEV_URL + "?sslmode=require") == "dev"


def test_local_fixture_allowlisted():
    assert assert_persist_database_url(LOCAL_URL) == "local"
    assert assert_persist_database_url("postgresql://nba@localhost:5999/db") == "local"
    assert assert_persist_database_url("postgresql://nba@[::1]/db") == "local"


def test_dev_wrong_database_refused():
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(f"postgresql://u@{DEV_ENDPOINT_HOST}/postgres")


def test_pooled_or_other_neon_endpoint_refused():
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            f"postgresql://u@ep-rapid-smoke-b4t1v549-pooler.c-6.us-east-2.aws.neon.tech/neondb"
        )
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            "postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech/neondb"
        )
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            "postgresql://u@ep-other-endpoint-x123.us-east-2.aws.neon.tech/neondb"
        )


def test_query_host_override_refused_even_when_authority_is_local_or_dev():
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(LOCAL_URL + "?host=evil.example.invalid")
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(DEV_URL + "?host=evil.example.invalid")
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(LOCAL_URL + "?HOST=evil.example.invalid")
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(DEV_URL + "?hostaddr=1.2.3.4")
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(LOCAL_URL + "?service=ci")
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(DEV_URL + "?options=-csearch_path=public")


def test_multi_and_missing_hosts_refused():
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url("postgresql://localhost,127.0.0.1/db")
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url("postgresql://localhost:5432,other:5432/db")
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url("postgresql:///db")
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url("postgresql://u@/db")


def test_refusal_does_not_include_userinfo():
    with pytest.raises(PersistRefused) as excinfo:
        assert_persist_database_url(SECRET_URL)
    message = str(excinfo.value)
    assert "supersecret" not in message
    assert "user:" not in message
    assert SECRET_URL not in message


def test_app_env_must_be_dev_or_test(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(PersistRefused, match="APP_ENV"):
        assert_persist_app_env()
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(PersistRefused, match="APP_ENV"):
        assert_persist_app_env()
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(PersistRefused, match="APP_ENV"):
        assert_persist_app_env()
    monkeypatch.setenv("APP_ENV", "dev")
    assert assert_persist_app_env() == "dev"
    monkeypatch.setenv("APP_ENV", "test")
    assert assert_persist_app_env() == "test"


def test_persist_refuses_production_env_before_connect(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(PersistRefused, match="APP_ENV"):
        persist_matched_output(
            [],
            database_url=LOCAL_URL,
            game_bbref_id="202601010FIC",
            pbp_hash="0" * 64,
            original_plays=[],
        )


def test_dev_query_override_refused_before_any_connect():
    with pytest.raises(PersistRefused, match="query overrides"):
        assert_persist_database_url(
            f"postgresql://u:supersecret@{DEV_ENDPOINT_HOST}/neondb?hostaddr=8.8.8.8"
        )
