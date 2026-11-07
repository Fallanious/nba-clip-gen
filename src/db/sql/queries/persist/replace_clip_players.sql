-- Internal persist. Replace the current association set for this clip.
-- Incoming inserts follow; empty incoming leaves no clip_players rows.
DELETE FROM clip_players
WHERE clip_id = %(clip_id)s;
