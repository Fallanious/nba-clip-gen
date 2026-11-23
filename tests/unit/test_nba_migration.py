from datetime import timedelta
import json

from backfill_nba_catalog import align, plan_game
from match_play_timestamps import load_pbp_source
from persist_matcher_clips import source_player_roles


def test_corrected_clock_uses_verified_video_window(tmp_path):
    old = dict(
        quarter=1,
        time="5:03.0",
        team="Knicks",
        score="2-0",
        description="J. Brunson makes 2-pt jump shot",
    )
    new = dict(
        quarter=1,
        time="5:02.0",
        team="NYK",
        score="2-0",
        player="J. Brunson",
        person_id=1,
        nba_game_id="0022500001",
        nba_action_id=3,
        action_type="Made Shot",
        description="Brunson Jump Shot (2 PTS)",
    )
    key = "202511110PHI"
    path = tmp_path / f"{key}.json"
    path.write_text(json.dumps([old]))
    (tmp_path / "nba").mkdir()
    (tmp_path / "nba" / path.name).write_text(json.dumps([new]))
    digest, _ = load_pbp_source(path)
    attrs = {"pbp_sha256": digest, "shared_sequence_indexes": [0]}
    rows = [(42, 0, 1, timedelta(seconds=303), old["description"], attrs, 10, 16)]
    (update,) = plan_game(key, rows, tmp_path, {})
    assert update[4:6] == (10, 16)
    assert update[3]["video_timing_review"] == "needs_footage_review"
    review = {
        "42": dict(
            game=key,
            action=3,
            clock=new["time"],
            previous_window=[10, 16],
            window=[20, 26],
            status="scoreboard_verified",
        )
    }
    (update,) = plan_game(key, rows, tmp_path, review)
    assert update[:3] == (0, new["description"], timedelta(seconds=302))
    assert update[4:] == (20, 26, 42)
    assert not plan_game(
        key, [(42, 0, 1, update[2], update[1], update[3], 20, 26)], tmp_path, review
    )


def test_duplicate_timeouts_cannot_claim_one_action():
    old = dict(
        quarter=2,
        time="5:00.0",
        team="Knicks",
        score="45-40",
        description="New York full timeout",
    )
    new = dict(
        old,
        team="",
        action_type="Timeout",
        player="",
        description="KNICKS Timeout: Regular",
    )
    assert align([old, old], [new]) == {}


def test_nba_roles_use_team_actor_and_unambiguous_assist():
    shot = dict(
        nba_action_id=8,
        action_type="Made Shot",
        player="A. Test",
        team="PHI",
        description="Test Jump Shot (2 PTS) (Jones 1 AST)",
    )
    passer = dict(player="J. Jones", team="PHI")
    assert source_player_roles(shot, [shot, passer]) == [
        ("A. Test", "shot"),
        ("J. Jones", "assist"),
    ]
