"""Foundation tests."""
import pytest
import sys
import os

sys.path.insert(0, 'src')


def test_config():
    os.environ['DATABASE_URL'] = 'postgresql://test'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'  # 32 bytes
    from config import config
    assert config.DATABASE_URL


def test_cursor_secret_required():
    """SECURITY: CURSOR_SECRET must be set."""
    # Remove CURSOR_SECRET
    old_secret = os.environ.pop('CURSOR_SECRET', None)
    
    try:
        # Force reload of config module
        import importlib
        import config as config_module
        importlib.reload(config_module)
        
        with pytest.raises(ValueError, match="CURSOR_SECRET environment variable is required"):
            config_module.config.CURSOR_SECRET
    finally:
        if old_secret:
            os.environ['CURSOR_SECRET'] = old_secret


def test_cursor_secret_minimum_length():
    """SECURITY: CURSOR_SECRET must be at least 32 bytes."""
    os.environ['CURSOR_SECRET'] = 'short'
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    with pytest.raises(ValueError, match="CURSOR_SECRET must be at least 32 bytes"):
        config_module.config.validate()


def test_cursor_secret_valid():
    """SECURITY: Valid CURSOR_SECRET passes validation."""
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'  # exactly 32 bytes
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    # Should not raise
    config_module.config.validate()


def test_debug_defaults_off():
    """SECURITY: DEBUG defaults to False."""
    os.environ.pop('DEBUG', None)
    os.environ['APP_ENV'] = 'dev'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'  # 32 bytes
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    assert config_module.config.DEBUG is False


def test_debug_explicit_true():
    """SECURITY: DEBUG can be enabled explicitly in non-production."""
    os.environ['DEBUG'] = 'true'
    os.environ['APP_ENV'] = 'dev'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'  # 32 bytes
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    assert config_module.config.DEBUG is True


def test_debug_forced_off_in_production():
    """SECURITY: DEBUG is forced off in production, even if DEBUG=true."""
    os.environ['DEBUG'] = 'true'
    os.environ['APP_ENV'] = 'production'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'  # 32 bytes
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    assert config_module.config.DEBUG is False
