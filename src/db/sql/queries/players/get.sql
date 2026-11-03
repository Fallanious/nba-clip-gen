-- name: players/get
-- One player with team, clip stats, and role counts (single round trip).
-- params: id bigint
-- returns: players.*, team_id, abbr, city, name, clip_count, first_clip_date, last_clip_date, role_counts (0 or 1 row)
SELECT p.*, t.id AS team_id, t.abbr, t.city, t.name,
       (SELECT count(DISTINCT cp.clip_id) FROM clip_players cp WHERE cp.player_id = p.id) AS clip_count,
       (SELECT min(cp.game_date) FROM clip_players cp WHERE cp.player_id = p.id) AS first_clip_date,
       (SELECT max(cp.game_date) FROM clip_players cp WHERE cp.player_id = p.id) AS last_clip_date,
       (SELECT max(c.updated_at) FROM clip_players cp JOIN clips c ON c.id = cp.clip_id WHERE cp.player_id = p.id) AS clips_updated_at,
       (SELECT coalesce(json_object_agg(sub.role::text, sub.n), '{}'::json)
        FROM (
          SELECT cp.role, count(*)::int AS n
          FROM clip_players cp
          WHERE cp.player_id = p.id
          GROUP BY cp.role
        ) sub) AS role_counts
FROM players p LEFT JOIN teams t ON t.id = p.team_id WHERE p.id = :id;
