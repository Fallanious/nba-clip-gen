"""Database session and engine management."""

from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event, text
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
def get_session(retry_on_wake: bool = True) -> Generator[Session, None, None]:
    """Get a database session with automatic commit/rollback.
    
    Includes Neon wake logic: one retry on OperationalError, then 503.
    
    Args:
        retry_on_wake: If True, retry connection once on OperationalError (for Neon autosuspend wake)
    """
    if not SessionLocal:
        raise RuntimeError("Database not configured. Set DATABASE_URL environment variable.")

    # Retry logic for connection wake (before yield)
    retries_left = 1 if retry_on_wake else 0
    session = None
    
    while session is None:
        try:
            session = SessionLocal()
            # Test connection with a simple query
            session.execute(text("SELECT 1"))
        except OperationalError as e:
            if session:
                session.close()
            if retries_left > 0:
                retries_left -= 1
                session = None
                continue
            # No retries left - raise as service unavailable
            from errors import ApiError
            raise ApiError(
                code="database_unavailable",
                message="Database is temporarily unavailable",
                status=503
            ) from e
    
    # Now we have a working session, yield it
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Session:
    """Get the request-scoped database session (lazy initialization).
    
    Opens the session on first use, not eagerly for every request.
    Session is automatically committed/rolled back in teardown_request.
    """
    from flask import g
    
    # Lazy initialization: only open session on first use
    if not hasattr(g, '_db_session_ctx'):
        g._db_session = get_session()
        g._db_session_ctx = g._db_session.__enter__()
    
    return g._db_session_ctx
