-- Internal persist. Drop stale/invalid unmatched timing for this clip.
-- Current-identity matcher_attested windows are not rewritten here.
UPDATE clips
SET
  timing_provenance = 'none',
  video_start_s = NULL,
  video_end_s = NULL,
  video_timestamp_s = NULL,
  matcher_run_id = NULL,
  matcher_version = NULL,
  matched_source_version = NULL
WHERE id = %(clip_id)s
  AND game_id = %(game_id)s
  AND play_index = %(play_index)s
  AND NOT (
    timing_provenance = 'matcher_attested'
    AND matched_source_version IS NOT DISTINCT FROM %(analyzed_source_identity)s
  );
