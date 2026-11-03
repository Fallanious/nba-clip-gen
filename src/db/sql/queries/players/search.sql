-- name: players/search
-- Search players by name (trigram + substring), ranked by similarity. Keyset paging.
-- params: q, team_id, active, c_score, c_last_name, c_id, limit
-- returns: id, full_name, first_name, last_name, position, headshot_url, is_active,
--          team_id, abbr, city, name, score  (limit+1 rows for lookahead)
SELECT p.id, p.full_name, p.first_name, p.last_name, p.position, p.headshot_url, p.is_active, p.updated_at,
       t.id AS team_id, t.abbr, t.city, t.name,
       similarity(lower(f_unaccent(p.full_name)), lower(f_unaccent(:q))) AS score
FROM players p LEFT JOIN teams t ON t.id = p.team_id
WHERE ( lower(f_unaccent(p.full_name)) % lower(f_unaccent(:q))
        OR lower(f_unaccent(p.full_name)) LIKE '%' || lower(f_unaccent(:q)) || '%' )
  AND (CAST(:team_id AS smallint) IS NULL OR p.team_id = :team_id)
  AND (CAST(:active AS boolean) IS NULL OR p.is_active = :active)
  AND ( CAST(:c_score AS real) IS NULL
        OR CAST(similarity(lower(f_unaccent(p.full_name)), lower(f_unaccent(:q))) AS real) < CAST(:c_score AS real)
        OR ( CAST(similarity(lower(f_unaccent(p.full_name)), lower(f_unaccent(:q))) AS real) = CAST(:c_score AS real)
             AND (p.last_name, p.id) > (:c_last_name, :c_id) ) )
ORDER BY score DESC, p.last_name, p.id
LIMIT :limit + 1;
