-- Internal persist attestation. Not a public serializer.
-- Full trusted bounds and locked analyzed-source binding must hold or this
-- updates zero rows (invalid payload). Never copy a current DB version that
-- was not supplied as the analyzed identity.
UPDATE clips c
SET
  timing_provenance = 'matcher_attested',
  video_start_s = %(video_start_s)s,
  video_end_s = %(video_end_s)s,
  video_timestamp_s = %(video_timestamp_s)s,
  matcher_run_id = %(matcher_run_id)s,
  matcher_version = %(matcher_version)s,
  matched_source_version = %(analyzed_source_identity)s
FROM games g
WHERE c.id = %(clip_id)s
  AND c.game_id = %(game_id)s
  AND c.play_index = %(play_index)s
  AND g.id = c.game_id
  AND g.source_kind = 'original_private'
  AND g.is_fictional IS TRUE
  AND g.s3_key = %(analyzed_source_key)s
  AND g.source_version = %(analyzed_source_identity)s
  AND nba_numeric_is_finite(g.duration_s) IS TRUE
  AND g.duration_s > 0
  AND nba_numeric_is_finite(%(video_timestamp_s)s::numeric) IS TRUE
  AND %(video_timestamp_s)s::numeric >= 0
  AND nba_numeric_is_finite(%(video_start_s)s::numeric) IS TRUE
  AND nba_numeric_is_finite(%(video_end_s)s::numeric) IS TRUE
  AND %(video_start_s)s::numeric >= 0
  AND %(video_end_s)s::numeric > %(video_start_s)s::numeric
  AND %(video_start_s)s::numeric < g.duration_s
  AND %(video_end_s)s::numeric <= g.duration_s
  AND (%(video_end_s)s::numeric - %(video_start_s)s::numeric) <= 120
RETURNING c.id;
