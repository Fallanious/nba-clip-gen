-- name: clips/by_player
-- A player's clips, keyset-paged in display order:
-- (game_date DESC, game_id DESC, period ASC, play_index ASC, clip_id ASC).
-- Optional filters are static CAST(:param IS NULL) predicates (spec §11: no dynamic SQL).
-- params: pid, roles, date_from, date_to, season, game_id, action_types, subtypes, outcome, status_all, c_date, c_game_id, c_period, c_play_index, c_id, limit
-- returns: clip_id, game_date, roles, game_id, play_index, period, game_clock, description,
--          action_type, shot_subtype, outcome, duration_s, status, team_id, has_object,
--          bbref_id, season, home_abbr, away_abbr  (limit+1 rows for lookahead)
WITH page AS (
  SELECT cp.clip_id, cp.game_date, array_agg(cp.role ORDER BY cp.role) AS roles,
         c.game_id, c.period, c.play_index
  FROM clip_players cp
  JOIN clips c ON c.id = cp.clip_id
  JOIN games g ON g.id = c.game_id
  WHERE cp.player_id = :pid
    AND (CAST(:roles AS clip_role[]) IS NULL OR cp.role = ANY(CAST(:roles AS clip_role[])))
    AND (CAST(:date_from AS date) IS NULL OR cp.game_date >= :date_from)
    AND (CAST(:date_to AS date) IS NULL OR cp.game_date <= :date_to)
    AND (CAST(:season AS text) IS NULL OR g.season = :season)
    AND (CAST(:game_id AS bigint) IS NULL OR c.game_id = :game_id)
    AND (CAST(:action_types AS text[]) IS NULL OR c.action_type = ANY(CAST(:action_types AS text[])))
    AND (CAST(:subtypes AS text[]) IS NULL OR c.shot_subtype = ANY(CAST(:subtypes AS text[])))
    AND (CAST(:outcome AS text) IS NULL OR c.outcome = :outcome)
    AND (CAST(:status_all AS boolean) IS TRUE OR (c.status = 'uploaded' AND c.s3_key IS NOT NULL))
    AND (CAST(:c_date AS date) IS NULL
         OR cp.game_date < :c_date
         OR (cp.game_date = :c_date AND c.game_id < :c_game_id)
         OR (cp.game_date = :c_date AND c.game_id = :c_game_id
             AND (c.period, c.play_index, cp.clip_id) > (:c_period, :c_play_index, :c_id)))
  GROUP BY cp.game_date, cp.clip_id, c.game_id, c.period, c.play_index
  ORDER BY cp.game_date DESC, c.game_id DESC, c.period ASC, c.play_index ASC, cp.clip_id ASC
  LIMIT :limit + 1)
SELECT page.clip_id, page.game_date, page.roles,
       c.game_id, c.play_index, c.period, c.game_clock, c.description, c.action_type,
       c.shot_subtype, c.outcome, c.duration_s, c.status, c.team_id, (c.s3_key IS NOT NULL) AS has_object,
       g.bbref_id, g.season, ht.abbr AS home_abbr, at.abbr AS away_abbr
FROM page
JOIN clips c ON c.id = page.clip_id
JOIN games g ON g.id = c.game_id
JOIN teams ht ON ht.id = g.home_team_id
JOIN teams at ON at.id = g.away_team_id
ORDER BY page.game_date DESC, page.game_id DESC, page.period ASC, page.play_index ASC, page.clip_id ASC;
