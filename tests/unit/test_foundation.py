"""Foundation tests."""
import pytest
import sys
import os
import re

sys.path.insert(0, 'src')


def test_config():
    os.environ['APP_ENV'] = 'dev'
    os.environ['DATABASE_URL'] = 'postgresql://test'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'  # 32 bytes
    from config import config
    assert config.DATABASE_URL


def test_cursor_secret_required():
    """SECURITY: CURSOR_SECRET must be set (outside dev)."""
    # Remove CURSOR_SECRET and set non-dev env
    old_secret = os.environ.pop('CURSOR_SECRET', None)
    os.environ['APP_ENV'] = 'production'
    
    try:
        # Force reload of config module
        import importlib
        import config as config_module
        importlib.reload(config_module)
        
        with pytest.raises(ValueError, match="no dev fallback outside APP_ENV=dev"):
            config_module.config.CURSOR_SECRET
    finally:
        if old_secret:
            os.environ['CURSOR_SECRET'] = old_secret
        os.environ['APP_ENV'] = 'dev'


def test_cursor_secret_minimum_length():
    """SECURITY: CURSOR_SECRET must be at least 32 bytes."""
    os.environ['APP_ENV'] = 'dev'
    os.environ['CURSOR_SECRET'] = 'short'
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    with pytest.raises(ValueError, match="CURSOR_SECRET must be at least 32 bytes"):
        config_module.config.validate()


def test_cursor_secret_valid():
    """SECURITY: Valid CURSOR_SECRET passes validation."""
    os.environ['APP_ENV'] = 'dev'
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


def test_cursor_secret_dev_fallback_in_dev():
    """SECURITY: CURSOR_SECRET falls back to dev value only when APP_ENV=dev."""
    os.environ.pop('CURSOR_SECRET', None)
    os.environ['APP_ENV'] = 'dev'
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    secret = config_module.config.CURSOR_SECRET
    assert len(secret.encode()) >= 32
    assert secret != "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    assert secret != "dev-secret-change-in-production"
    assert config_module.config.CURSOR_SECRET == secret


def test_cursor_secret_no_fallback_in_production():
    """SECURITY: CURSOR_SECRET has no fallback outside dev."""
    os.environ.pop('CURSOR_SECRET', None)
    os.environ['APP_ENV'] = 'production'
    
    import importlib
    import config as config_module
    importlib.reload(config_module)
    
    with pytest.raises(ValueError, match="no dev fallback outside APP_ENV=dev"):
        config_module.config.CURSOR_SECRET


def test_auth_refuses_non_dev_without_real_auth():
    """SECURITY: App refuses to start in non-dev without real auth."""
    os.environ['APP_ENV'] = 'production'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'
    
    from auth import validate_auth_for_startup
    
    with pytest.raises(ValueError, match="APP_ENV=production requires real authentication"):
        validate_auth_for_startup()


def test_auth_allows_dev():
    """SECURITY: Stub auth allowed in dev."""
    os.environ['APP_ENV'] = 'dev'
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'
    
    from auth import validate_auth_for_startup
    
    # Should not raise
    validate_auth_for_startup()


def test_request_id_validation():
    """SECURITY: X-Request-Id validated before use."""
    os.environ['CURSOR_SECRET'] = '12345678901234567890123456789012'
    os.environ['APP_ENV'] = 'dev'
    
    import importlib
    import sys
    
    # Reload modules to pick up env changes
    if 'app' in sys.modules:
        importlib.reload(sys.modules['app'])
    
    from app import create_app
    app = create_app()
    
    with app.test_client() as client:
        # Valid UUID
        r = client.get('/api/jobs', headers={'X-Request-Id': 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'})
        assert r.headers.get('X-Request-Id') == 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
        
        # Valid alphanumeric with hyphens
        r = client.get('/api/jobs', headers={'X-Request-Id': 'req-123-abc'})
        assert r.headers.get('X-Request-Id') == 'req-123-abc'
        
        # Invalid (has special chars) - should generate new UUID
        r = client.get('/api/jobs', headers={'X-Request-Id': 'bad<script>id'})
        res_id = r.headers.get('X-Request-Id')
        assert res_id != 'bad<script>id'
        # Should be a valid UUID
        import uuid
        uuid.UUID(res_id)  # Raises if invalid

        import inspect
        from app import create_app as factory
        src = inspect.getsource(factory)
        assert "re.fullmatch" in src
        assert "re.match" not in src
        # '$' in re.match allows a trailing newline; fullmatch does not
        assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", "req-123-abc\n") is None


@pytest.mark.parametrize("raw", [None, "", " ", "Prod ", "qa"])
def test_app_env_required(raw, monkeypatch):
    """APP_ENV has no default; missing or unknown values refuse to start."""
    if raw is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", raw)
    monkeypatch.setenv("CURSOR_SECRET", "k" * 32)
    from config import Config
    with pytest.raises(ValueError, match="APP_ENV") as ei:
        Config().validate()
    assert "k" * 32 not in str(ei.value)


@pytest.mark.parametrize("env", ["dev", "test", "prod", "production", "staging"])
def test_known_app_env_accepted(env, monkeypatch):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("CURSOR_SECRET", "k" * 32)
    from config import Config
    Config().validate()


def test_stub_admin_only_in_explicit_dev(monkeypatch):
    from auth import resolve_user, validate_auth_for_startup
    monkeypatch.setenv("APP_ENV", "dev")
    validate_auth_for_startup()
    resolve_user(None)
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(ValueError, match="explicitly dev"):
        validate_auth_for_startup()
    with pytest.raises(RuntimeError, match="explicitly dev"):
        resolve_user(None)
