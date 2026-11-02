"""Foundation tests."""
def test_config():
    import sys, os
    sys.path.insert(0, 'src')
    os.environ['DATABASE_URL'] = 'postgresql://test'
    os.environ['CURSOR_SECRET'] = 'secret'
    from config import config
    assert config.DATABASE_URL
