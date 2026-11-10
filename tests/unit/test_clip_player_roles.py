"""Play-by-play sentences name the people on a clip."""

from persist_matcher_clips import clip_player_roles, player_initial_and_last


def test_make_names_the_shooter_and_the_assister():
    roles = clip_player_roles(
        "I. Okoro makes 2-pt layup from 1 ft (assist by J. Giddey)"
    )
    assert roles == [("I. Okoro", "shot"), ("J. Giddey", "assist")]


def test_free_throw_is_a_shot():
    assert clip_player_roles("I. Okoro misses free throw 1 of 1") == [
        ("I. Okoro", "shot")
    ]


def test_rebound_foul_block_and_turnover_name_each_role():
    assert clip_player_roles("Offensive rebound by I. Okoro") == [("I. Okoro", "rebound")]
    assert clip_player_roles("Defensive rebound by J. Brown") == [("J. Brown", "rebound")]
    assert clip_player_roles("Shooting foul by J. Brown (drawn by I. Okoro)") == [
        ("J. Brown", "foul"),
        ("I. Okoro", "fouled"),
    ]
    assert clip_player_roles("Block by J. Embiid") == [("J. Embiid", "block")]
    assert clip_player_roles("Turnover by J. Giddey (bad pass; steal by J. Brown)") == [
        ("J. Giddey", "turnover"),
        ("J. Brown", "steal"),
    ]


def test_team_rebound_and_plain_text_name_nobody():
    assert clip_player_roles("Offensive rebound by Team") == []
    assert clip_player_roles("pad event 3") == []


def test_initials_and_suffix_split_to_the_players_table_shape():
    assert player_initial_and_last("P.J. Washington") == ("P", "Washington")
    assert player_initial_and_last("T. Hardaway Jr.") == ("T", "Hardaway")
    assert player_initial_and_last("V. Edgecombe") == ("V", "Edgecombe")
    assert clip_player_roles("P.J. Washington makes 3-pt jump shot") == [
        ("P.J. Washington", "shot")
    ]
    assert clip_player_roles("T. Hardaway Jr. misses 2-pt jump shot") == [
        ("T. Hardaway Jr.", "shot")
    ]
