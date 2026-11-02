-- name: teams/by_abbr
-- Look up team by abbreviation.
-- params: abbr
-- returns: id (0 or 1 row)
SELECT id FROM teams WHERE UPPER(abbr) = UPPER(:abbr);
