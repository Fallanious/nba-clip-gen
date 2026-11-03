-- name: players/search_browse
-- Browse players alphabetically (no q). Keyset paging.
-- params: team_id, active, c_last_name, c_id, limit
-- returns: id, full_name, first_name, last_name, position, headshot_url, is_active, team_id, abbr, city, name
SELECT p.id, p.full_name, p.first_name, p.last_name, p.position, p.headshot_url, p.is_active, p.updated_at,
       t.id AS team_id, t.abbr, t.city, t.name
FROM players p LEFT JOIN teams t ON t.id = p.team_id
WHERE (CAST(:team_id AS smallint) IS NULL OR p.team_id = :team_id)
  AND (CAST(:active AS boolean) IS NULL OR p.is_active = :active)
  AND (CAST(:c_id AS bigint) IS NULL OR (p.last_name, p.id) > (:c_last_name, :c_id))
ORDER BY p.last_name, p.id
LIMIT :limit + 1;
