"""Capability truth table and type gate. No database, no S3."""

import math
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, "src")

from db.bounds import CLIP_MAX_DURATION_S
from db.capability import (
    CapabilityTypeError,
    live_media_access_permitted,
    parse_optional_seconds,
    public_capability,
)
from db.matcher_import import merge_pbp_and_matcher
from db.metadata_allowlist import MetadataLeak, assert_browse_row, assert_capability_row

FIXTURES = Path("tests/fixtures/plays")


def _eligible(**overrides):
    row = {
        "s3_key": "fixtures/fictional-game-a.mkv",
        "source_kind": "original_private",
        "source_version": "etag-fic-a-v1",
        "is_fictional": True,
        "duration_s": Decimal("48"),
        "timing_provenance": "matcher_attested",
        "matched_source_version": "etag-fic-a-v1",
        "video_start_s": Decimal("2"),
        "video_end_s": Decimal("10"),
        "game_clock": "PT2M14S",
    }
    row.update(overrides)
    return row


def test_eligible_zero_start_independent_of_fractional():
    result = public_capability(_eligible(video_start_s=0, video_end_s=8))
    assert result == {"can_create": True, "reason": None}


def test_eligible_fractional_bounds_independent_of_zero_start():
    result = public_capability(
        _eligible(video_start_s=Decimal("0.25"), video_end_s=Decimal("3.5"))
    )
    assert result == {"can_create": True, "reason": None}


def test_missing_source_unavailable():
    assert public_capability(_eligible(s3_key=None))["reason"] == "footage_not_matched"
    assert public_capability(_eligible(s3_key=""))["reason"] == "footage_not_matched"
    assert public_capability(_eligible(s3_key="  "))["reason"] == "footage_not_matched"


def test_url_ref_never_counts_as_original():
    for key in ("https://example.invalid/game.mkv", "http://x", "s3://bucket/key"):
        out = public_capability(_eligible(s3_key=key))
        assert out["can_create"] is False
        assert out["reason"] == "footage_not_matched"


def test_public_and_derived_never_substitute():
    for kind in ("public", "derived", None, "original"):
        out = public_capability(_eligible(source_kind=kind))
        assert out == {"can_create": False, "reason": "footage_not_matched"}


def test_unmatched_none_browsable_not_eligible():
    out = public_capability(
        _eligible(
            timing_provenance="none",
            video_start_s=None,
            video_end_s=None,
            matched_source_version=None,
        )
    )
    assert out == {"can_create": False, "reason": "footage_not_matched"}


def test_legacy_numeric_unavailable():
    out = public_capability(_eligible(timing_provenance="unvalidated_numeric"))
    assert out == {"can_create": False, "reason": "video_timing_unavailable"}


def test_game_clock_derived_unavailable():
    out = public_capability(_eligible(timing_provenance="game_clock_derived"))
    assert out == {"can_create": False, "reason": "video_timing_unavailable"}


def test_game_clock_does_not_rescue_or_alter():
    missing = _eligible(
        timing_provenance="none",
        video_start_s=None,
        video_end_s=None,
        game_clock="PT11M42S",
    )
    assert public_capability(missing)["can_create"] is False
    ok = _eligible(game_clock=None)
    also = _eligible(game_clock="PT99M99S")
    assert public_capability(ok) == public_capability(also) == {"can_create": True, "reason": None}


def test_stale_source_version():
    out = public_capability(_eligible(matched_source_version="old"))
    assert out == {"can_create": False, "reason": "video_timing_unavailable"}


def test_missing_trusted_duration():
    out = public_capability(_eligible(duration_s=None))
    assert out == {"can_create": False, "reason": "video_timing_unavailable"}


@pytest.mark.parametrize(
    "start,end",
    [
        (-1, 8),
        (5, 5),
        (9, 3),
        (0, 0),
        (40, 49),
        (0, Decimal("121")),
    ],
)
def test_invalid_windows(start, end):
    out = public_capability(_eligible(video_start_s=start, video_end_s=end))
    assert out["can_create"] is False
    assert out["reason"] == "video_timing_unavailable"


def test_tiny_positive_gap_is_eligible():
    out = public_capability(_eligible(video_start_s=0, video_end_s=Decimal("0.1")))
    assert out == {"can_create": True, "reason": None}


def test_ceiling_constant_is_the_security_policy():
    assert CLIP_MAX_DURATION_S == Decimal("120")
    assert public_capability(
        _eligible(duration_s=Decimal("200"), video_start_s=0, video_end_s=Decimal("120"))
    )["can_create"] is True
    assert public_capability(
        _eligible(duration_s=Decimal("200"), video_start_s=0, video_end_s=Decimal("120.1"))
    )["can_create"] is False


@pytest.mark.parametrize("value", [True, False, "1.5", "0", object()])
def test_rejects_wrong_types_including_bool_without_coercion(value):
    with pytest.raises(CapabilityTypeError):
        parse_optional_seconds(value, field="video_start_s")
    out = public_capability(_eligible(video_start_s=value))
    assert out == {"can_create": False, "reason": "video_timing_unavailable"}


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, Decimal("NaN"), Decimal("Infinity")])
def test_rejects_nonfinite(value):
    with pytest.raises(CapabilityTypeError):
        parse_optional_seconds(value, field="video_end_s")


def test_capability_shape_no_refs():
    row = public_capability(_eligible())
    assert_capability_row(row)
    assert "s3_key" not in row


def test_browse_allowlist_structured_fields_are_the_control():
    assert_browse_row({
        "play_id": 1,
        "game_id": 1,
        "game_date": "2026-01-03",
        "play_index": 1,
        "period": 1,
        "game_clock": None,
        "description": "FICTIONAL",
        "action_type": "shot",
        "shot_subtype": None,
        "outcome": "made",
        "can_create": True,
        "reason": None,
    })
    with pytest.raises(MetadataLeak, match="extra fields"):
        assert_browse_row({
            "play_id": 1,
            "s3_key": "fixtures/x.mkv",
            "can_create": False,
            "reason": "footage_not_matched",
        })
    with pytest.raises(MetadataLeak):
        assert_capability_row({"can_create": False, "reason": "footage_not_matched", "url": "https://x"})
    with pytest.raises(MetadataLeak, match="extra fields"):
        assert_browse_row({
            "play_id": 1,
            "game_id": 1,
            "game_date": "2026-01-03",
            "play_index": 1,
            "period": 1,
            "game_clock": None,
            "description": "FICTIONAL",
            "action_type": "shot",
            "shot_subtype": None,
            "outcome": "made",
            "can_create": False,
            "reason": "footage_not_matched",
            "note": "https://evil.example/x",
        })
    with pytest.raises(MetadataLeak, match="extra fields"):
        assert_browse_row({
            "play_id": 1,
            "game_id": 1,
            "game_date": "2026-01-03",
            "play_index": 1,
            "period": 1,
            "game_clock": None,
            "description": "FICTIONAL",
            "action_type": "shot",
            "shot_subtype": None,
            "outcome": "made",
            "can_create": False,
            "reason": "footage_not_matched",
            "href": "//evil.example/x",
        })
    with pytest.raises(MetadataLeak, match="extra fields"):
        assert_browse_row({
            "play_id": 1,
            "game_id": 1,
            "game_date": "2026-01-03",
            "play_index": 1,
            "period": 1,
            "game_clock": None,
            "description": "FICTIONAL",
            "action_type": "shot",
            "shot_subtype": None,
            "outcome": "made",
            "can_create": False,
            "reason": "footage_not_matched",
            "object_key": "games/live.mkv",
        })
    # Allowlisted public text may contain locator-shaped substrings. The
    # extra-field set is the control; prefix scan is not secrecy.
    assert_browse_row({
        "play_id": 1,
        "game_id": 1,
        "game_date": "2026-01-03",
        "play_index": 1,
        "period": 1,
        "game_clock": None,
        "description": "see https://example.invalid/play  and //cdn.example/x",
        "action_type": "shot",
        "shot_subtype": None,
        "outcome": "made",
        "can_create": False,
        "reason": "footage_not_matched",
    })


def test_mapper_keeps_unmatched():
    pbp = [
        {"play_index": 0, "quarter": 1, "time": "11:42.0", "description": "FICTIONAL a"},
        {"play_index": 1, "quarter": 1, "time": "11:23.0", "description": "FICTIONAL b"},
        {"play_index": 2, "quarter": 1, "time": "11:08.0", "description": "FICTIONAL c"},
        {"play_index": 3, "quarter": 1, "time": "10:50.0", "description": "FICTIONAL d"},
        {"play_index": 4, "quarter": 1, "time": "9:12.0", "description": "FICTIONAL e"},
    ]
    matcher = [
        {"play_index": 1, "video_timestamp": 12, "video_start": 7, "video_end": 13},
        {"play_index": 2, "video_timestamp": 20, "video_start": 15, "video_end": 21},
    ]
    rows = merge_pbp_and_matcher(
        pbp, matcher,
        matcher_run_id="fic-run", matcher_version="fictional", source_version="etag-x",
    )
    assert len(rows) == 5
    unmatched = [r for r in rows if r["timing_provenance"] == "none"]
    attested = [r for r in rows if r["timing_provenance"] == "matcher_attested"]
    assert len(unmatched) == 3
    assert all(r["video_start_s"] is None and r["video_end_s"] is None for r in unmatched)
    assert len(attested) == 2


def test_mapper_checked_in_fixtures_labeled_and_unmatched():
    import json
    pbp = json.loads((FIXTURES / "pbp_fictional.json").read_text())
    matcher = json.loads((FIXTURES / "matcher_fictional.json").read_text())
    assert any("FICTIONAL" in p["description"] for p in pbp)
    rows = merge_pbp_and_matcher(
        pbp, matcher,
        matcher_run_id="fic", matcher_version="fic", source_version="etag",
    )
    assert len(rows) == 5
    assert sum(1 for r in rows if r["timing_provenance"] == "none") == 3


def test_mapper_rejects_bool_offsets_as_unattested():
    pbp = [{"play_index": 0, "description": "FICTIONAL"}]
    matcher = [{"play_index": 0, "video_timestamp": True, "video_start": 0, "video_end": 1}]
    rows = merge_pbp_and_matcher(
        pbp, matcher, matcher_run_id="x", matcher_version="x", source_version="v"
    )
    assert rows[0]["timing_provenance"] == "none"
    assert rows[0]["video_start_s"] is None


def test_fictional_fixture_never_grants_live_media():
    row = _eligible(is_fictional=True)
    assert public_capability(row)["can_create"] is True
    assert live_media_access_permitted(row) is False
    assert live_media_access_permitted(_eligible()) is False
    live = _eligible(
        s3_key="games/fic000001FIC.mkv",
        is_fictional=False,
    )
    assert live_media_access_permitted(live) is True
    assert live_media_access_permitted(
        _eligible(s3_key="games/fic000001FIC.mkv", is_fictional=True)
    ) is False
    for fictional in (None, "false", "False", 0, 1, True):
        assert live_media_access_permitted(
            _eligible(s3_key="games/fic000001FIC.mkv", is_fictional=fictional)
        ) is False


def test_unknown_is_fictional_denies_capability():
    missing = _eligible()
    missing.pop("is_fictional")
    assert public_capability(missing) == {"can_create": False, "reason": "footage_not_matched"}
    assert public_capability(_eligible(is_fictional=None))["can_create"] is False
    assert public_capability(_eligible(is_fictional="false"))["can_create"] is False


def test_fictional_capability_true_only_with_fixture_prefix():
    assert public_capability(_eligible())["can_create"] is True
    assert public_capability(
        _eligible(s3_key="games/fic000001FIC.mkv", is_fictional=True)
    )["can_create"] is False
    live = _eligible(s3_key="games/fic000001FIC.mkv", is_fictional=False)
    assert public_capability(live)["can_create"] is True
    assert public_capability(
        _eligible(s3_key="fixtures/x.mkv", is_fictional=False)
    )["can_create"] is False


def test_python_nan_duration_is_timing_unavailable():
    assert public_capability(_eligible(duration_s=math.nan)) == {
        "can_create": False, "reason": "video_timing_unavailable",
    }
    assert public_capability(_eligible(video_end_s=math.nan))["can_create"] is False


def test_mapper_rejects_duplicate_missing_noninteger_bool_play_index():
    pbp = [{"play_index": 0, "description": "FICTIONAL"}]
    with pytest.raises(CapabilityTypeError, match="duplicate"):
        merge_pbp_and_matcher(
            pbp,
            [
                {"play_index": 0, "video_timestamp": 1, "video_start": 0, "video_end": 1},
                {"play_index": 0, "video_timestamp": 2, "video_start": 1, "video_end": 2},
            ],
            matcher_run_id="fic", matcher_version="fic", source_version="etag",
        )
    with pytest.raises(CapabilityTypeError, match="PBP play_index"):
        merge_pbp_and_matcher(
            [{"description": "FICTIONAL missing index"}],
            [],
            matcher_run_id="fic", matcher_version="fic", source_version="etag",
        )
    for bad in (True, False, 1.5, "0", None):
        with pytest.raises(CapabilityTypeError):
            merge_pbp_and_matcher(
                [{"play_index": bad, "description": "FICTIONAL"}],
                [],
                matcher_run_id="fic", matcher_version="fic", source_version="etag",
            )
        with pytest.raises(CapabilityTypeError):
            merge_pbp_and_matcher(
                [{"play_index": 0, "description": "FICTIONAL"}],
                [{"play_index": bad, "video_timestamp": 1, "video_start": 0, "video_end": 1}],
                matcher_run_id="fic", matcher_version="fic", source_version="etag",
            )


def test_mapper_invalid_windows_stay_unattested():
    pbp = [
        {"play_index": 0, "description": "FICTIONAL reversed"},
        {"play_index": 1, "description": "FICTIONAL negative"},
        {"play_index": 2, "description": "FICTIONAL over max"},
        {"play_index": 3, "description": "FICTIONAL ok"},
    ]
    matcher = [
        {"play_index": 0, "video_timestamp": 5, "video_start": 9, "video_end": 3},
        {"play_index": 1, "video_timestamp": 1, "video_start": -1, "video_end": 4},
        {"play_index": 2, "video_timestamp": 1, "video_start": 0, "video_end": 121},
        {"play_index": 3, "video_timestamp": 1, "video_start": 0, "video_end": 8},
    ]
    rows = merge_pbp_and_matcher(
        pbp, matcher, matcher_run_id="fic", matcher_version="fic", source_version="etag",
    )
    assert [r["timing_provenance"] for r in rows] == ["none", "none", "none", "matcher_attested"]
    assert rows[0]["video_start_s"] is None
    assert rows[3]["matched_source_version"] == "etag"


def test_mapper_requires_canonical_nonempty_provenance():
    pbp = [{"play_index": 0, "description": "FICTIONAL"}]
    matcher = [{"play_index": 0, "video_timestamp": 1, "video_start": 0, "video_end": 1}]
    for field, value in (
        ("matcher_run_id", ""),
        ("matcher_run_id", "  x"),
        ("matcher_version", " "),
        ("source_version", None),
        ("source_version", "etag "),
    ):
        kwargs = dict(matcher_run_id="fic", matcher_version="fic", source_version="etag")
        kwargs[field] = value
        with pytest.raises(CapabilityTypeError):
            merge_pbp_and_matcher(pbp, matcher, **kwargs)


def test_capability_module_does_not_import_boto3_or_jobs():
    import db.capability as cap
    import inspect
    source = inspect.getsource(cap)
    assert "boto3" not in source
    assert "enqueue" not in source
    assert "presign" not in source
