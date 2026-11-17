"""Scraped overtime periods remain distinct throughout matching."""

from match_play_timestamps import (
    condense_plays, find_next_matching_play, parse_scoreboard_response,
    stamp_original_play_indexes,
)
from scrape_playbyplay import scrape_playbyplay


def play(clock, quarter=4, description="Player makes jump shot"):
    return dict(quarter=quarter, time=clock, description=description, team="Knicks", score="95-95")


def test_overtime_matches_past_unmatched_regulation():
    raw = [play("4:47.0"), play("0:08.0"), play("4:47.0", 5), play("0:04.0", 5), play("4:47.0", 6)]
    groups = condense_plays(stamp_original_play_indexes(raw))
    for label, expected, quarter in (("4th", 0, 4), ("OT", 2, 5), ("2OT", 4, 6)):
        index, matched = find_next_matching_play(parse_scoreboard_response(f"{label} 4:47"), groups, 0)
        assert index == expected
        assert matched["original_play_indexes"] == [expected]
        assert matched["original_plays"][0]["quarter"] == quarter


def test_overtime_matching_after_player_filter():
    stamped = stamp_original_play_indexes([
        play("4:47.0"), play("0:08.0", description="Other player"), play("4:47.0", 5),
    ])
    groups = [g for g in condense_plays(stamped) if "Player" in g["description"]]
    _, matched = find_next_matching_play({"quarter": 5, "time": "4:47.0"}, groups, 0)
    assert matched["original_play_indexes"] == [2]


def test_scraper_recognizes_overtime_headers(monkeypatch):
    rows = []
    for header in ("4th Q", "1st OT", "2nd OT", "3OT"):
        rows.append(f'<tr><th>{header}</th></tr>')
        rows.append('<tr><td>4:47.0</td><td>Player makes jump shot</td><td class="center">95-95</td><td></td></tr>')
    html = '<title>Knicks at Raptors Play-By-Play</title><table class="suppress_all">' + ''.join(rows) + '</table>'
    class Response:
        status_code = 200
        content = html.encode()
    monkeypatch.setattr("scrape_playbyplay.requests.get", lambda url: Response())
    plays = scrape_playbyplay("https://example.test/pbp")
    assert [p["quarter"] for p in plays] == [4, 5, 6, 7]
