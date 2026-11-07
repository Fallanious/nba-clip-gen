"""Local byte hash is the analyzed identity. No S3, no DB self-certification."""

import pytest

from db.analyzed_source import (
    AnalyzedSourceError,
    assert_local_input_unmutated,
    capture_local_analyzed_source,
    matcher_output_document,
)


def test_capture_hashes_consumed_bytes(tmp_path):
    path = tmp_path / "clip.mkv"
    path.write_bytes(b"stable-local-bytes")
    captured = capture_local_analyzed_source(
        path=str(path),
        key="fixtures/clip.mkv",
        matcher_run_id="run-1",
    )
    assert captured["analyzed_identity_kind"] == "content_sha256"
    assert captured["analyzed_source_identity"].startswith("sha256:")
    assert captured["input_sha256"] in captured["analyzed_source_identity"]
    assert_local_input_unmutated(str(path), captured)
    document = matcher_output_document(
        plays=[],
        analyzed_source=captured,
        matcher_version="match_play_timestamps/persist",
    )
    assert document["analyzed_source"]["analyzed_source_identity"] == captured["analyzed_source_identity"]
    assert document["matcher_run_id"] == "run-1"


def test_mutation_after_analysis_is_refused(tmp_path):
    path = tmp_path / "clip.mkv"
    path.write_bytes(b"v1-bytes")
    captured = capture_local_analyzed_source(
        path=str(path),
        key="fixtures/clip.mkv",
        matcher_run_id="run-1",
    )
    path.write_bytes(b"v2-bytes-mutated")
    with pytest.raises(AnalyzedSourceError, match="mutated"):
        assert_local_input_unmutated(str(path), captured)


def test_non_fixture_key_is_refused(tmp_path):
    path = tmp_path / "clip.mkv"
    path.write_bytes(b"bytes")
    with pytest.raises(AnalyzedSourceError, match="fixtures/"):
        capture_local_analyzed_source(
            path=str(path),
            key="games/live.mkv",
            matcher_run_id="run-1",
        )
