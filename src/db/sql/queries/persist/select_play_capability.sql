SELECT
  c.id AS play_id,
  c.play_index,
  c.timing_provenance,
  c.matched_source_version,
  cap.can_create,
  cap.reason
FROM clips c
JOIN play_capability cap ON cap.play_id = c.id
WHERE c.game_id = %(game_id)s
  AND c.play_index = %(play_index)s;
