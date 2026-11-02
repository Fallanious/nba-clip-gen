"""Database session and engine management."""

from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError
from config import config


def _create_engine():
    """Create and configure the database engine."""
    if not config.DATABASE_URL:
        return None

    engine = create_engine(
        config.DATABASE_URL,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def set_session_config(dbapi_conn, connection_record):
        """Set per-session configuration."""
        cursor = dbapi_conn.cursor()
        cursor.execute("SET statement_timeout = '5s'")
        cursor.execute("SET pg_trgm.similarity_threshold = 0.2")
        cursor.close()

    return engine


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None


def _open_session():
    """Open a session and check out a connection (pool_pre_ping, no extra SELECT 1)."""
    if not SessionLocal:
        raise RuntimeError("Database not configured. Set DATABASE_URL environment variable.")
    session = SessionLocal()
    try:
        session.connection()
        return session
    except Exception:
        session.close()
        raise


def _unavailable(cause: BaseException):
    from errors import ApiError
    raise ApiError(
        code="db_unavailable",
        message="Database temporarily unavailable",
        status=503,
    ) from cause


@contextmanager
def get_session(retry_on_wake: bool = True) -> Generator[Session, None, None]:
    """One session with commit/rollback/close.

    Neon wake: retry checkout once on OperationalError, then 503.
    Does not issue a per-session SELECT 1; pool_pre_ping handles dead connections.
    """
    session = None
    try:
        try:
            session = _open_session()
        except OperationalError as e:
            if session is not None:
                session.close()
                session = None
            if not retry_on_wake:
                _unavailable(e)
            try:
                session = _open_session()
            except OperationalError as e2:
                if session is not None:
                    session.close()
                    session = None
                _unavailable(e2)

        yield session
        session.commit()
    except Exception:
        if session is not None:
            session.rollback()
        raise
    finally:
        if session is not None:
            session.close()


def get_db() -> Session:
    """Request-scoped session, opened on first use (not in before_request)."""
    from flask import g

    if not hasattr(g, '_db_session_ctx'):
        g._db_session = get_session()
        g._db_session_ctx = g._db_session.__enter__()

    return g._db_session_ctx
