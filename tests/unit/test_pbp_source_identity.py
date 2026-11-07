"""Scraper stamps source event identity and participants from HTML, not persist enumerate."""

import pytest

from pbp_source_identity import SourcePbpError, stamp_source_pbp_identity
from scrape_playbyplay import plays_from_pbp_table

HTML = """
<html><body>
<table class="suppress_all">
<tr><th colspan="4">1st Q</th></tr>
<tr>
  <td>11:08.0</td>
  <td><a href="/players/f/ficcas01.html">Casey</a> makes 2-pt jumper (<a href="/players/f/ficoth01.html">Other</a> assists)</td>
  <td class="center">2-0</td>
  <td></td>
</tr>
<tr>
  <td>10:50.0</td>
  <td></td>
  <td class="center">2-0</td>
  <td>Defensive rebound</td>
</tr>
</table>
</body></html>
"""


def test_stamp_assigns_source_order_and_link_participants():
    plays = stamp_source_pbp_identity([
        {"quarter": 1, "time": "11:42.0", "description": "Jump ball"},
        {
            "quarter": 1,
            "time": "11:08.0",
            "description": "Casey Fixture makes 2-pt jumper (Other Fixture assists)",
            "player_links": [
                "/players/f/ficcas01.html",
                "/players/f/ficoth01.html",
            ],
        },
    ])
    assert [play["play_index"] for play in plays] == [0, 1]
    assert plays[0]["participants"] == []
    assert plays[1]["participants"] == [
        {"bbref_id": "ficcas01", "role": "shot"},
        {"bbref_id": "ficoth01", "role": "assist"},
    ]
    assert "player_links" not in plays[1]


def test_stamp_preserves_explicit_indexes():
    plays = stamp_source_pbp_identity([
        {"play_index": 7, "quarter": 1, "description": "FICTIONAL"},
        {"play_index": 9, "quarter": 1, "description": "FICTIONAL two"},
    ])
    assert [play["play_index"] for play in plays] == [7, 9]


def test_partial_indexes_are_refused():
    with pytest.raises(SourcePbpError, match="partial"):
        stamp_source_pbp_identity([
            {"play_index": 0, "description": "a"},
            {"description": "b"},
        ])


def test_existing_participants_are_not_invented_from_links():
    plays = stamp_source_pbp_identity([
        {
            "play_index": 0,
            "description": "makes jumper",
            "participants": [{"bbref_id": "ficcas01", "role": "shot"}],
            "player_links": ["/players/f/ficoth01.html"],
        }
    ])
    assert plays[0]["participants"] == [{"bbref_id": "ficcas01", "role": "shot"}]


def test_html_table_stamps_source_identity_and_participants():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(HTML, "html.parser")
    table = soup.find("table")
    plays = plays_from_pbp_table(table, "Fiction", "QA")
    assert [play["play_index"] for play in plays] == [0, 1]
    assert plays[0]["time"] == "11:08.0"
    assert plays[0]["participants"] == [
        {"bbref_id": "ficcas01", "role": "shot"},
        {"bbref_id": "ficoth01", "role": "assist"},
    ]
    assert plays[1]["participants"] == []
    assert "player_links" not in plays[0]
