"""DB tests."""
def test_models():
    import sys; sys.path.insert(0, 'src')
    from db.models import Player
    assert Player
