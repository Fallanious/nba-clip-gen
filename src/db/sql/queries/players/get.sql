-- name: players/get
-- One player with team and clip stats.
-- params: id bigint
-- returns: players.*, team_id, abbr, city, name, clip_count, first_clip_date, last_clip_date (0 or 1 row)
SELECT p.*, t.id AS team_id, t.abbr, t.city, t.name,
       (SELECT count(DISTINCT cp.clip_id) FROM clip_players cp WHERE cp.player_id = p.id) AS clip_count,
       (SELECT min(cp.game_date) FROM clip_players cp WHERE cp.player_id = p.id) AS first_clip_date,
       (SELECT max(cp.game_date) FROM clip_players cp WHERE cp.player_id = p.id) AS last_clip_date
FROM players p LEFT JOIN teams t ON t.id = p.team_id WHERE p.id = :id;
