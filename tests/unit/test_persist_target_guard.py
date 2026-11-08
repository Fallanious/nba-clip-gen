"""Pre-connect persist target guard. Never opens a remote connection."""

from pathlib import Path

import pytest

from persist_matcher_clips import (
    DEV_ENDPOINT_HOST,
    PersistRefused,
    assert_libpq_routing_env,
    assert_persist_app_env,
    assert_persist_database_url,
    assert_persist_ready,
    persist_connect_params,
    persist_matched_output,
)

DEV_URL = f"postgresql://u@{DEV_ENDPOINT_HOST}/neondb"
DEV_REQUIRE = DEV_URL + "?sslmode=require"
DEV_VERIFY_FULL = DEV_URL + "?sslmode=verify-full"
LOCAL_URL = "postgresql://nba@127.0.0.1:5432/nba_clip_persist_test"
SECRET_URL = (
    f"postgresql://user:supersecret@{DEV_ENDPOINT_HOST}/neondb"
    "?host=evil.example.invalid"
)
SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture(autouse=True)
def _clear_libpq_routing(monkeypatch):
    for name in (
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
        "PGSYSCONFDIR",
        "PGTARGETSESSIONATTRS",
        "PGLOADBALANCEHOSTS",
        "PGREQUIRESSL",
        "PGSSLMODE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_exact_dev_endpoint_requires_strong_tls():
    with pytest.raises(PersistRefused, match="sslmode=require or verify-full"):
        assert_persist_database_url(DEV_URL)
    assert assert_persist_database_url(DEV_REQUIRE) == "dev"
    assert assert_persist_database_url(DEV_VERIFY_FULL) == "dev"
    kind, params = persist_connect_params(DEV_VERIFY_FULL)
    assert kind == "dev"
    assert params["sslmode"] == "verify-full"
    assert params["host"] == DEV_ENDPOINT_HOST
    assert "password" in params
    assert params["password"] == ""


def test_dev_missing_and_weak_sslmode_refused():
    for suffix in (
        "",
        "?sslmode=disable",
        "?sslmode=allow",
        "?sslmode=prefer",
        "?sslmode=verify-ca",
        "?sslmode=",
    ):
        with pytest.raises(PersistRefused, match="sslmode=require or verify-full"):
            assert_persist_database_url(DEV_URL + suffix)


def test_local_fixture_allowlisted():
    assert assert_persist_database_url(LOCAL_URL) == "local"
    assert assert_persist_database_url("postgresql://nba@localhost:5999/db") == "local"
    assert assert_persist_database_url("postgresql://nba@[::1]/db") == "local"


def test_dev_wrong_database_refused():
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            f"postgresql://u@{DEV_ENDPOINT_HOST}/postgres?sslmode=require"
        )


def test_pooled_or_other_neon_endpoint_refused():
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            "postgresql://u@ep-rapid-smoke-b4t1v549-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require"
        )
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            "postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech/neondb?sslmode=require"
        )
    with pytest.raises(PersistRefused, match="allowlisted"):
        assert_persist_database_url(
            "postgresql://u@ep-other-endpoint-x123.us-east-2.aws.neon.tech/neondb?sslmode=require"
        )


def test_query_keys_outside_allowlist_refused_including_percent_encoded():
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(LOCAL_URL + "?host=evil.example.invalid")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&host=evil.example.invalid")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(LOCAL_URL + "?HOST=evil.example.invalid")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&hostaddr=1.2.3.4")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(LOCAL_URL + "?service=ci")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&options=-csearch_path=public")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&dbname=neondb")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&port=5432")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&user=u")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(LOCAL_URL + "?%68ost=evil.example.invalid")
    with pytest.raises(PersistRefused, match="not allowlisted"):
        assert_persist_database_url(DEV_REQUIRE + "&%68ostaddr=1.2.3.4")


def test_duplicate_decoded_query_keys_refused():
    with pytest.raises(PersistRefused, match="duplicate keys"):
        assert_persist_database_url(DEV_URL + "?sslmode=require&sslmode=verify-full")
    with pytest.raises(PersistRefused, match="duplicate keys"):
        assert_persist_database_url(DEV_URL + "?sslmode=require&SSLMODE=require")


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


def test_libpq_routing_env_refused_before_connect(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PGHOSTADDR", "8.8.8.8")
    with pytest.raises(PersistRefused, match="libpq routing environment") as excinfo:
        assert_persist_ready(LOCAL_URL)
    assert "8.8.8.8" not in str(excinfo.value)
    with pytest.raises(PersistRefused, match="libpq routing environment"):
        persist_matched_output(
            [],
            database_url=LOCAL_URL,
            game_bbref_id="202601010FIC",
            pbp_hash="0" * 64,
            original_plays=[],
        )
    monkeypatch.delenv("PGHOSTADDR")
    monkeypatch.setenv("PGSERVICE", "ci")
    with pytest.raises(PersistRefused, match="libpq routing environment"):
        assert_libpq_routing_env()
    monkeypatch.delenv("PGSERVICE")
    monkeypatch.setenv("PGOPTIONS", "-csearch_path=public")
    with pytest.raises(PersistRefused, match="libpq routing environment"):
        assert_persist_ready(DEV_REQUIRE)
    monkeypatch.delenv("PGOPTIONS")
    monkeypatch.setenv("PGHOST", "evil.example.invalid")
    with pytest.raises(PersistRefused, match="libpq routing environment") as host_exc:
        assert_persist_ready(DEV_REQUIRE)
    assert "evil.example.invalid" not in str(host_exc.value)
    assert "supersecret" not in str(host_exc.value)


def test_direct_entrypoint_guards_before_persist():
    text = (SRC / "match_play_timestamps.py").read_text(encoding="utf-8")
    persist_at = text.index("persist_matched_output(")
    ready_at = text.index("assert_persist_ready(")
    flag_at = text.index('os.environ.get("MATCHER_PERSIST") == "1"')
    assert flag_at < ready_at < persist_at
    source = (SRC / "persist_matcher_clips.py").read_text(encoding="utf-8")
    connect_at = source.index("psycopg.connect(**conn_params)")
    ready_def = source.index("def assert_persist_ready")
    routing_def = source.index("def assert_libpq_routing_env")
    assert ready_def < connect_at
    assert routing_def < connect_at
    assert "psycopg.connect(database_url)" not in source
    assert "psycopg.connect(conninfo" not in source
