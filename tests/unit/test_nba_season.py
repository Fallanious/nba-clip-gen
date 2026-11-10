from persist_matcher_clips import nba_season


def test_fall_game_starts_the_new_season():
    assert nba_season("2026-11-08") == "2026-27"


def test_spring_game_stays_in_the_season_that_started_last_fall():
    assert nba_season("2026-02-02") == "2025-26"
