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
    
    # Skip engine creation in test mode (monkeypatch will provide fake session)
    if config.APP_ENV == "test":
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
    """Get a database session with automatic retry on connection errors."""
    # In test mode, SessionLocal will be None but get_session will be monkeypatched
    if not SessionLocal:
        # This should only be reached if monkeypatch failed
        raise RuntimeError("Database not configured. Set DATABASE_URL environment variable.")

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except OperationalError as e:
        session.rollback()
        # Retry once for Neon autosuspend wake
        if "connection" in str(e).lower():
            session = SessionLocal()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
        else:
            raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """Dependency for Flask routes."""
    session_gen = get_session()
    try:
        session = next(session_gen)
        yield session
    finally:
        try:
            next(session_gen)
        except StopIteration:
            pass
