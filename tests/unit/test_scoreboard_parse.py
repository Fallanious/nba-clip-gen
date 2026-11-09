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
