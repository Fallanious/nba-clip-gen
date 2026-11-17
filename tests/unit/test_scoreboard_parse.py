"""glm-ocr returns a schema fill or plain text. Both must become quarter + clock."""

from match_play_timestamps import parse_scoreboard_response


def test_qwen_json_still_parses():
    parsed = parse_scoreboard_response('{"quarter": 1, "time": "10:34"}')
    assert parsed == {"quarter": 1, "time": "10:34.0"}


def test_glm_ocr_string_fields():
    parsed = parse_scoreboard_response('{"quarter": "1st", "time": "7:42"}')
    assert parsed == {"quarter": 1, "time": "7:42.0"}


def test_glm_ocr_empty_schema_is_unread():
    assert parse_scoreboard_response('{"quarter": "", "time": ""}') is None


def test_plain_ocr_text():
    parsed = parse_scoreboard_response("1st\n7:42\n0:18")
    assert parsed == {"quarter": 1, "time": "7:42.0"}


def test_q_prefix_and_game_clock_over_shot_clock():
    parsed = parse_scoreboard_response("Q3 0:24 11:02")
    assert parsed == {"quarter": 3, "time": "11:02.0"}


def test_blank_quarter_field_uses_period_label():
    parsed = parse_scoreboard_response('{"quarter": "", "time": "7:42"}\n2ND')
    assert parsed == {"quarter": 2, "time": "7:42.0"}


def test_misread_ordinal_and_score_is_not_a_quarter():
    parsed = parse_scoreboard_response("IST\n8:01\n28")
    assert parsed == {"quarter": 1, "time": "8:01.0"}
    assert parse_scoreboard_response('{"quarter": "28", "time": "7:42"}') is None


def test_overtime_periods_survive_normalization():
    for label, quarter in (("OT", 5), ("2OT", 6), ("3OT", 7)):
        assert parse_scoreboard_response(f"TSN {label} 4:53 18\nNY 95 TOR 95\n&#x20;") == {
            "quarter": quarter, "time": "4:53.0",
        }
        assert parse_scoreboard_response(f'{{"quarter": {quarter}, "time": "4:53"}}') == {
            "quarter": quarter, "time": "4:53.0",
        }


def test_decimal_clocks_in_text_and_json():
    for response in (
        "4th 30.5 18\nNY 95 TOR 95",
        '{"quarter": 4, "time": "30.5"}',
        '{"quarter": 4, "time": 30.5}',
        "Q4 0:30.5 0:18",
    ):
        assert parse_scoreboard_response(response) == {"quarter": 4, "time": "0:30.5"}


def test_decimal_clock_comparison_retains_fraction():
    from match_play_timestamps import time_to_seconds

    assert parse_scoreboard_response("OT 9.2 9.8") == {"quarter": 5, "time": "0:09.8"}
    assert time_to_seconds("0:30.5") == 30.5
    assert time_to_seconds("1:00.05") == 60.05


def test_invalid_and_bare_clocks_are_rejected():
    for clock in ("18", "60.5", "130.5", "0:60.5", "13:00", "100:30.5"):
        assert parse_scoreboard_response(f"Q4 {clock}") is None
