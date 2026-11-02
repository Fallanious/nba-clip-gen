-- name: players/role_counts
-- Clip counts per role for one player.
-- params: id bigint
-- returns: role clip_role, n bigint
SELECT cp.role, count(*) AS n
FROM clip_players cp
WHERE cp.player_id = :id
GROUP BY cp.role
ORDER BY cp.role;
