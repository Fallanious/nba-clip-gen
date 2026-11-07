-- name: plays/capability
-- Public eligibility for one play. No keys, offsets, versions, or URLs.
-- Published/deleted predicates are not in this slice (unwired). Later HTTP must
-- enforce published AND not deleted. Do not treat this query as that gate.
-- params: id bigint
SELECT play_id, can_create, reason
FROM play_capability
WHERE play_id = :id;
