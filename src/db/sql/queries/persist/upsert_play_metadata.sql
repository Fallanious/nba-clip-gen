-- Internal persist. Insert PBP display fields. Does not write video offsets.
-- On conflict, verify the full stored natural key (period + description +
-- game_clock). Mismatch updates zero rows so the batch can refuse atomically.
-- Identity columns and video windows are never rewritten here. Stale unmatched
-- timing is cleared only by clear_unmatched_timing.sql after attestation.
INSERT INTO clips (
  game_id,
  game_date,
  play_index,
  period,
  game_clock,
  description,
  action_type,
  outcome,
  timing_provenance,
  video_start_s,
  video_end_s
) VALUES (
  %(game_id)s,
  %(game_date)s,
  %(play_index)s,
  %(period)s,
  %(game_clock)s,
  %(description)s,
  %(action_type)s,
  %(outcome)s,
  'none',
  NULL,
  NULL
)
ON CONFLICT (game_id, play_index) DO UPDATE SET
  game_date = EXCLUDED.game_date,
  action_type = EXCLUDED.action_type,
  outcome = EXCLUDED.outcome
WHERE clips.period IS NOT DISTINCT FROM EXCLUDED.period
  AND clips.description IS NOT DISTINCT FROM EXCLUDED.description
  AND clips.game_clock IS NOT DISTINCT FROM EXCLUDED.game_clock
RETURNING
  id,
  timing_provenance,
  matched_source_version,
  video_start_s,
  video_end_s,
  period,
  description,
  game_clock;
