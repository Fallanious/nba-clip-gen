"""DB tests."""
import pytest
import sys
import os

sys.path.insert(0, 'src')


def test_models():
    from db.models import Player
    assert Player


def test_session_lifecycle_with_sqlite():
    """Test session commit, rollback-on-error, and close using SQLite."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import patch, MagicMock
    from sqlalchemy.exc import OperationalError
    
    # Create in-memory SQLite engine
    engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(bind=engine)
    
    # Create a test table
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY, value TEXT)"))
        conn.commit()
    
    # Patch SessionLocal to use our test engine
    with patch('db.session.SessionLocal', TestSessionLocal):
        from db.session import get_session
        
        # Test 1: Successful commit
        with get_session(retry_on_wake=False) as session:
            session.execute(text("INSERT INTO test_table (value) VALUES ('test')"))
        
        # Verify commit worked
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM test_table")).scalar()
            assert result == 1
        
        # Test 2: Rollback on error
        try:
            with get_session(retry_on_wake=False) as session:
                session.execute(text("INSERT INTO test_table (value) VALUES ('test2')"))
                raise ValueError("Simulated error")
        except ValueError:
            pass
        
        # Verify rollback worked (still only 1 row)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM test_table")).scalar()
            assert result == 1


def test_session_retry_and_503():
    """Test Neon wake retry logic: one retry, then 503."""
    from unittest.mock import patch, MagicMock
    from sqlalchemy.exc import OperationalError
    from errors import ApiError
    from db.session import get_session
    
    # Test: Retry once on OperationalError, then succeed
    call_count = [0]
    
    def mock_session_factory():
        call_count[0] += 1
        mock_session = MagicMock()
        if call_count[0] == 1:
            # First call: raise OperationalError
            mock_session.execute.side_effect = OperationalError("wake error", None, None)
        else:
            # Second call: succeed
            mock_session.execute.return_value = None
        return mock_session
    
    with patch('db.session.SessionLocal', mock_session_factory):
        with get_session(retry_on_wake=True) as session:
            pass  # Should succeed after one retry
        
        assert call_count[0] == 2  # Called twice (initial + 1 retry)
    
    # Test: No retry when retry_on_wake=False
    call_count[0] = 0
    
    def always_fail_factory():
        call_count[0] += 1
        mock_session = MagicMock()
        mock_session.execute.side_effect = OperationalError("error", None, None)
        return mock_session
    
    with patch('db.session.SessionLocal', always_fail_factory):
        with pytest.raises(ApiError) as exc_info:
            with get_session(retry_on_wake=False) as session:
                pass
        
        assert exc_info.value.code == "database_unavailable"
        assert exc_info.value.status == 503
        assert call_count[0] == 1  # Only called once (no retry)
    
    # Test: 503 after retry exhausted
    call_count[0] = 0
    
    with patch('db.session.SessionLocal', always_fail_factory):
        with pytest.raises(ApiError) as exc_info:
            with get_session(retry_on_wake=True) as session:
                pass
        
        assert exc_info.value.code == "database_unavailable"
        assert exc_info.value.status == 503
        assert call_count[0] == 2  # Called twice (initial + 1 retry)
