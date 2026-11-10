"""Labels for the player browser. No database."""

from datetime import timedelta
from decimal import Decimal

from routes.players import (
    clock_label,
    duration_label,
    matchup_label,
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
