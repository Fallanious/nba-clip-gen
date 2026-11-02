-- name: clips/get
-- One clip with its game, teams and tagged players.
-- params: id bigint
-- returns: clips.*, bbref_id, season, has_source, home_abbr, away_abbr, team_abbr, players json (0 or 1 row)
SELECT c.*, g.bbref_id, g.season, g.s3_key IS NOT NULL AS has_source,
       ht.abbr AS home_abbr, at.abbr AS away_abbr, t.abbr AS team_abbr,
       coalesce(json_agg(json_build_object('player_id', p.id::text, 'full_name', p.full_name, 'role', cp.role))
                FILTER (WHERE p.id IS NOT NULL), '[]') AS players
FROM clips c JOIN games g ON g.id = c.game_id
JOIN teams ht ON ht.id = g.home_team_id JOIN teams at ON at.id = g.away_team_id
LEFT JOIN teams t ON t.id = c.team_id
LEFT JOIN clip_players cp ON cp.clip_id = c.id LEFT JOIN players p ON p.id = cp.player_id
WHERE c.id = :id GROUP BY c.id, g.id, ht.id, at.id, t.id;
