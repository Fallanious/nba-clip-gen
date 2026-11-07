-- name: plays/browse_metadata
-- Allowlisted browse fields only. Unmatched/legacy/null rows stay in the result.
-- Never select private storage, versions, video seconds, matcher ids, or media locators.
-- Published/deleted predicates are not in this slice (unwired). Later HTTP must
-- enforce published AND not deleted. Do not treat this query as that gate.
-- params: none (test/dev listing)
SELECT
  c.id AS play_id,
  c.game_id,
  c.game_date,
  c.play_index,
  c.period,
  c.game_clock,
  c.description,
  c.action_type,
  c.shot_subtype,
  c.outcome,
  cap.can_create,
  cap.reason
FROM clips c
JOIN play_capability cap ON cap.play_id = c.id
ORDER BY c.game_date DESC, c.game_id DESC, c.period ASC, c.play_index ASC, c.id ASC;
