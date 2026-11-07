-- Rollback 0003 on local/dev only. Restores 0001 NOT NULL offsets.
-- Fails if unmatched (NULL offset) rows exist — reapply 0003 or delete those rows first.

DROP VIEW IF EXISTS play_capability;

ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_attested_requires_binding;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_none_requires_null_offsets;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_video_finite_window;

-- Restore 0001 window checks only when every row has offsets (legacy shape).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM clips WHERE video_start_s IS NULL OR video_end_s IS NULL) THEN
    RAISE EXCEPTION '0003 down: clips with NULL video offsets exist; cannot restore NOT NULL';
  END IF;
END $$;

UPDATE clips
SET timing_provenance = 'none'
WHERE timing_provenance = 'unvalidated_numeric';

ALTER TABLE clips ALTER COLUMN video_start_s SET NOT NULL;
ALTER TABLE clips ALTER COLUMN video_end_s SET NOT NULL;

ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_check;
ALTER TABLE clips ADD CONSTRAINT clips_check CHECK (video_end_s > video_start_s);
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_video_start_s_check;
ALTER TABLE clips ADD CONSTRAINT clips_video_start_s_check CHECK (video_start_s >= 0);

ALTER TABLE clips
  DROP COLUMN IF EXISTS matched_source_version,
  DROP COLUMN IF EXISTS video_timestamp_s,
  DROP COLUMN IF EXISTS matcher_version,
  DROP COLUMN IF EXISTS matcher_run_id,
  DROP COLUMN IF EXISTS timing_provenance;

ALTER TABLE games DROP CONSTRAINT IF EXISTS games_fictional_key_prefix;
ALTER TABLE games DROP CONSTRAINT IF EXISTS games_source_version_trim;
ALTER TABLE games DROP CONSTRAINT IF EXISTS games_s3_key_not_url;
ALTER TABLE games
  DROP COLUMN IF EXISTS is_fictional,
  DROP COLUMN IF EXISTS source_version,
  DROP COLUMN IF EXISTS source_kind;

DROP TYPE IF EXISTS source_kind;
DROP TYPE IF EXISTS timing_provenance;

DELETE FROM schema_migrations WHERE version = '0003_on_demand';
