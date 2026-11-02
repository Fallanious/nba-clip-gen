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


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Get a database session with automatic commit/rollback.
    
    Note: Retry logic for Neon autosuspend should be handled at a higher level,
    not in the context manager which must yield exactly once.
    """
    if not SessionLocal:
        raise RuntimeError("Database not configured. Set DATABASE_URL environment variable.")

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Session:
    """Get the request-scoped database session.
    
    Returns the session created in Flask's before_request hook.
    Session is automatically committed/rolled back in teardown_request.
    """
    from flask import g
    if not hasattr(g, '_db_session_ctx'):
        raise RuntimeError("No database session available. Are you calling this outside a request context?")
    return g._db_session_ctx
