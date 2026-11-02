-- name: clips/playback_target
-- Minimal fields needed to authorize and sign playback for one clip.
-- params: id bigint
-- returns: id, game_id, status, s3_key, video_start_s, video_end_s, game_key (0 or 1 row)
SELECT c.id, c.game_id, c.status, c.s3_key, c.video_start_s, c.video_end_s, g.s3_key AS game_key
FROM clips c JOIN games g ON g.id = c.game_id
WHERE c.id = :id;
