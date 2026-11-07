-- Internal persist. Insert one validated incoming participant after
-- replace_clip_players.sql deleted the prior set for this clip.
INSERT INTO clip_players (clip_id, player_id, role, game_date)
VALUES (%(clip_id)s, %(player_id)s, %(role)s, %(game_date)s)
ON CONFLICT (player_id, clip_id, role) DO NOTHING;
