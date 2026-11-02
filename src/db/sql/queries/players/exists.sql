-- name: players/exists
-- Check if a player exists.
-- params: id bigint
-- returns: ?column? int (1 row if exists, 0 rows if not)
SELECT 1 FROM players WHERE id = :id;
