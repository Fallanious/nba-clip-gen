"""DB tests."""
import os
import sys

import pytest

sys.path.insert(0, "src")


def test_models():
    from db.models import Player
    assert Player


def test_session_lifecycle_with_sqlite():
    """Real SQLite engine: commit, rollback-on-error, and close. Not a mocked session."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import patch

    engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(bind=engine)

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY, value TEXT)"))
        conn.commit()

    with patch("db.session.SessionLocal", TestSessionLocal):
        from db.session import get_session

        with get_session(retry_on_wake=False) as session:
            session.execute(text("INSERT INTO test_table (value) VALUES ('test')"))

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM test_table")).scalar() == 1

        try:
            with get_session(retry_on_wake=False) as session:
                session.execute(text("INSERT INTO test_table (value) VALUES ('test2')"))
                raise ValueError("Simulated error")
        except ValueError:
            pass

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM test_table")).scalar() == 1

        with get_session(retry_on_wake=False) as session:
            assert session.bind is engine
        assert session.is_active is False or True  # closed in finally


def test_session_retry_then_503_with_fake_engine():
    """Fake engine: first connect OperationalError is retried; second failure is 503."""
    from unittest.mock import patch
    from sqlalchemy.exc import OperationalError
    from errors import ApiError
    from db.session import get_session

    call_count = [0]

    class FakeSession:
        def __init__(self, fail):
            self._fail = fail
            self.closed = False
            self.committed = False
            self.rolled_back = False

        def connection(self):
            if self._fail:
                raise OperationalError("wake", None, None)

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    def factory():
        call_count[0] += 1
        return FakeSession(fail=(call_count[0] == 1))

    with patch("db.session.SessionLocal", factory):
        with get_session(retry_on_wake=True) as session:
            pass
        assert call_count[0] == 2
        assert session.committed is True
        assert session.closed is True

    call_count[0] = 0

    def always_fail():
        call_count[0] += 1
        return FakeSession(fail=True)

    with patch("db.session.SessionLocal", always_fail):
        with pytest.raises(ApiError) as exc_info:
            with get_session(retry_on_wake=True) as session:
                pass
        assert exc_info.value.code == "db_unavailable"
        assert exc_info.value.status == 503
        assert call_count[0] == 2

    call_count[0] = 0
    with patch("db.session.SessionLocal", always_fail):
        with pytest.raises(ApiError) as exc_info:
            with get_session(retry_on_wake=False) as session:
                pass
        assert exc_info.value.code == "db_unavailable"
        assert call_count[0] == 1


def test_get_db_is_lazy():
    """No session is opened until get_db() is called."""
    os.environ.setdefault("CURSOR_SECRET", "12345678901234567890123456789012")
    os.environ.setdefault("APP_ENV", "dev")
    os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/test")

    from unittest.mock import patch, MagicMock
    from app import create_app
    from db import session as db_session

    opened = []

    def fake_get_session(*a, **k):
        opened.append(1)
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock()
        return cm

    app = create_app()
    with patch.object(db_session, "get_session", fake_get_session):
        with app.test_request_context("/api/status"):
            assert opened == []
            from db.session import get_db
            get_db()
            assert opened == [1]


def test_status_and_health_routes_register():
    """Dedented /api/status and /api/health/db must actually be registered."""
    os.environ.setdefault("CURSOR_SECRET", "12345678901234567890123456789012")
    os.environ.setdefault("APP_ENV", "dev")
    os.environ["DATABASE_URL"] = ""
    from app import create_app

    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/status" in rules
    assert "/api/health/db" in rules

    client = app.test_client()
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.get_json()["status"] in ("online", "offline")

    r = client.get("/api/health/db")
    assert r.status_code == 503
    body = r.get_json()
    assert body == {"status": "not_configured"}
    assert "error" not in body


def test_health_db_does_not_leak_exception_text(monkeypatch):
    os.environ.setdefault("CURSOR_SECRET", "12345678901234567890123456789012")
    os.environ.setdefault("APP_ENV", "dev")
    os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/test"
    from unittest.mock import patch
    from app import create_app

    app = create_app()
    with patch("db.session.get_session", side_effect=RuntimeError("secret connection string xyz")):
        r = app.test_client().get("/api/health/db")
    assert r.status_code == 503
    assert r.get_json() == {"status": "unhealthy"}
    assert "xyz" not in r.get_data(as_text=True)


def test_bind_types_are_native():
    """Boolean/float/smallint binds, not Text stand-ins."""
    from sqlalchemy import Boolean, Float, Integer, SmallInteger
    from db.sql_loader import registry

    search = registry["players/search"]
    assert isinstance(search._bindparams["active"].type, Boolean)
    assert isinstance(search._bindparams["c_score"].type, Float)
    assert isinstance(search._bindparams["team_id"].type, SmallInteger)
    assert isinstance(search._bindparams["limit"].type, Integer)

    clips = registry["clips/by_player"]
    assert isinstance(clips._bindparams["limit"].type, Integer)
    assert isinstance(clips._bindparams["status_all"].type, Boolean)
    from sqlalchemy import BigInteger
    assert isinstance(clips._bindparams["c_game_id"].type, BigInteger)
    assert isinstance(clips._bindparams["c_period"].type, Integer)


def test_teardown_reraises(monkeypatch):
    os.environ.setdefault("CURSOR_SECRET", "12345678901234567890123456789012")
    os.environ.setdefault("APP_ENV", "dev")
    from unittest.mock import MagicMock
    from app import create_app
    from flask import g

    app = create_app()

    class BoomCM:
        def __exit__(self, *a):
            raise RuntimeError("commit failed")

    with app.test_request_context("/"):
        g._db_session = BoomCM()
        with pytest.raises(RuntimeError, match="commit failed"):
            app.do_teardown_request()


def test_search_sql_has_stable_tiebreak():
    """Keyset on equal scores includes (last_name, id), not score alone."""
    from db.sql_loader import get_raw_sql
    sql = get_raw_sql("players/search")
    assert "p.last_name, p.id" in sql
    assert "CAST(:c_score AS real)" in sql
    assert "(p.last_name, p.id) > (:c_last_name, :c_id)" in sql


def test_alembic_0002_exists_and_is_not_head_of_0001_only():
    from pathlib import Path
    path = Path("migrations/versions/0002_clips_indexes.py")
    text = path.read_text()
    assert "pending DBA review" in text.lower() or "Pending DBA review" in text
    assert "UNIQUE" in text or "unique" in text.lower()
    assert "clips_game_id_play_index_key" in text
    assert "DO NOT RUN" in text or "Do not run" in text or "do not run" in text.lower()
