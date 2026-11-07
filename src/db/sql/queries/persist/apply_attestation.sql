-- Attest VIDEO-relative seconds only. Never invented from game clock.
UPDATE clips
SET
  timing_provenance = 'matcher_attested',
  video_start_s = %(video_start_s)s,
  video_end_s = %(video_end_s)s,
  video_timestamp_s = %(video_timestamp_s)s,
  matcher_run_id = %(matcher_run_id)s,
  matcher_version = %(matcher_version)s,
  matched_source_version = %(source_version)s
WHERE id = %(clip_id)s
  AND game_id = %(game_id)s
  AND play_index = %(play_index)s
RETURNING id;
