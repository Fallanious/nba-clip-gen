"""Labels for the player browser. No database."""

from datetime import timedelta
from decimal import Decimal

from routes.players import (
    clip_page_request,
    clip_window,
    clock_label,
    duration_label,
    matchup_label,
    player_page_request,
    position_label,
    season_label,
)


def test_position_codes_use_the_card_words():
    assert position_label("PG") == "Guard"
    assert position_label("SF") == "Forward"
    assert position_label("C") == "Center"
    assert position_label("") == ""
    assert position_label("Wing") == "Wing"


def test_clock_and_duration_are_minutes_and_seconds():
    assert clock_label(timedelta(minutes=8, seconds=42)) == "8:42"
    assert clock_label(None) == ""
    assert duration_label(Decimal("12.4")) == "0:12"
    assert duration_label(65) == "1:05"
    assert duration_label(None) == ""


def test_matchup_puts_the_player_team_first():
    assert matchup_label("CHI", "CHI", "PHI") == "CHI vs PHI"
    assert matchup_label("PHI", "CHI", "PHI") == "PHI vs CHI"
    assert matchup_label("", "CHI", "PHI") == "PHI at CHI"


def test_season_uses_the_archive_dash():
    assert season_label("2026-27") == "2026–27"
    assert season_label("") == ""


def test_clip_page_starts_at_the_beginning_and_rejects_a_bad_cursor():
    assert clip_page_request({}) == (None, None)
    assert clip_page_request({"cursor": "50015", "role": "shot"}) == (50015, "shot")
    for args in ({"cursor": "0"}, {"cursor": "next"}, {"role": "dunk"}):
        try:
            clip_page_request(args)
        except ValueError:
            continue
        raise AssertionError(f"page request {args} should be refused")


def test_player_page_starts_at_the_beginning_and_keeps_the_search():
    assert player_page_request({}) == (None, None, None)
    assert player_page_request({"cursor": "2066", "team": "phi", "q": "okoro"}) == (2066, "PHI", "okoro")
    for args in ({"cursor": "nope"}, {"team": "Philadelphia"}, {"q": "x" * 81}):
        try:
            player_page_request(args)
        except ValueError:
            continue
        raise AssertionError(f"player page {args} should be refused")


def test_clip_window_rejects_a_span_that_would_cut_the_game():
    assert clip_window(22, 27) == (22.0, 27.0)
    for start, end in ((-1, 5), (10, 10), (0, 121)):
        try:
            clip_window(start, end)
        except ValueError:
            continue
        raise AssertionError(f"window {start},{end} should be refused")
