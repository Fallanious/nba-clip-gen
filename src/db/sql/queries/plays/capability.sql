-- name: plays/capability
-- Public eligibility for one play. No keys, offsets, versions, or URLs.
-- params: id bigint
SELECT play_id, can_create, reason
FROM play_capability
WHERE play_id = :id;
