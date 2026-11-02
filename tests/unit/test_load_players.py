"""load_players.py security/perf tests (no network, no DB)."""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "load_players.py")


def _load():
    spec = importlib.util.spec_from_file_location("load_players", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_bbref_id_accepts_alnum():
    lp = _load()
    assert lp.validate_bbref_id("embiijo01") == "embiijo01"


@pytest.mark.parametrize("bad", ["", "Embiid", "foo-bar", "../x", "a/b", "id;drop"])
def test_validate_bbref_id_rejects(bad):
    lp = _load()
    with pytest.raises(ValueError):
        lp.validate_bbref_id(bad)


def test_upsert_sql_is_parameterized():
    lp = _load()
    assert "%s" in lp.UPSERT_ROW
    assert "{values}" not in lp.UPSERT_ROW
    src = open(SCRIPT).read()
    assert "def lit" not in src
    assert ".format(values" not in src


def test_upsert_rows_do_not_embed_names_in_sql():
    lp = _load()
    players = {"embiijo01": ("embiijo01", "Joel O'Brien", "C", "PHI")}
    rows = lp.upsert_rows(players, {}, {"PHI": 20})
    assert rows == [("embiijo01", "Joel O'Brien", "Joel", "O'Brien", 20, "C", None)]
    # Apostrophe is data, not SQL quoting.
    assert all("''" not in str(c) for row in rows for c in row if isinstance(c, str))


def test_headshot_cache_ignores_bad_ids(tmp_path, monkeypatch):
    lp = _load()
    cache = tmp_path / "cache.tsv"
    cache.write_text("embiijo01\t1\n../evil\t1\nDROP;x\t1\n")
    monkeypatch.setattr(lp, "CACHE", str(cache))
    monkeypatch.setattr(lp, "check_image", lambda url: True)
    monkeypatch.setattr(lp, "nba_fallback", lambda missing: {})
    res = lp.headshots([("embiijo01", "Joel Embiid", "PHI")], delay=0)
    assert "embiijo01" in res
    assert "../evil" not in res
