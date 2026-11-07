-- On-demand capability: provenance, nullable offsets, original-private source
-- identity, and a default-denied eligibility VIEW.
-- Compatible with live Neon (timestamptz, existing clips_* check names) and
-- with 0001_init. Does not register HTTP. Does not grant media access.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

DO $$ BEGIN
  CREATE TYPE timing_provenance AS ENUM (
    'none',
    'game_clock_derived',
    'unvalidated_numeric',
    'matcher_attested'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE source_kind AS ENUM (
    'original_private',
    'public',
    'derived'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE games
  ADD COLUMN IF NOT EXISTS source_kind source_kind,
  ADD COLUMN IF NOT EXISTS source_version text,
  ADD COLUMN IF NOT EXISTS is_fictional boolean NOT NULL DEFAULT false;

ALTER TABLE games DROP CONSTRAINT IF EXISTS games_s3_key_not_url;
ALTER TABLE games
  ADD CONSTRAINT games_s3_key_not_url CHECK (
    s3_key IS NULL
    OR (
      length(trim(s3_key)) > 0
      AND s3_key !~* '^(https?|s3)://'
      AND s3_key = trim(s3_key)
    )
  );

ALTER TABLE games DROP CONSTRAINT IF EXISTS games_source_version_trim;
ALTER TABLE games
  ADD CONSTRAINT games_source_version_trim CHECK (
    source_version IS NULL OR (
      length(trim(source_version)) > 0
      AND source_version = trim(source_version)
    )
  );

-- Fixture keys are never live objects, even if a later runner is enabled.
UPDATE games
SET is_fictional = true
WHERE s3_key LIKE 'fixtures/%' AND is_fictional = false;

ALTER TABLE games DROP CONSTRAINT IF EXISTS games_fictional_key_prefix;
ALTER TABLE games
  ADD CONSTRAINT games_fictional_key_prefix CHECK (
    (
      is_fictional
      AND (s3_key IS NULL OR s3_key LIKE 'fixtures/%')
    )
    OR (
      (NOT is_fictional)
      AND (s3_key IS NULL OR s3_key NOT LIKE 'fixtures/%')
    )
  );

ALTER TABLE clips
  ADD COLUMN IF NOT EXISTS timing_provenance timing_provenance NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS matcher_run_id text,
  ADD COLUMN IF NOT EXISTS matcher_version text,
  ADD COLUMN IF NOT EXISTS video_timestamp_s numeric,
  ADD COLUMN IF NOT EXISTS matched_source_version text;

-- Legacy / 0001 rows required NOT NULL offsets. Drop so unmatched PBP can exist.
-- Existing numeric rows stay; default provenance 'none' until backfill below.
ALTER TABLE clips ALTER COLUMN video_start_s DROP NOT NULL;
ALTER TABLE clips ALTER COLUMN video_end_s DROP NOT NULL;

ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_check;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_video_start_s_check;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_video_times_check;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_video_finite_window;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_attested_requires_binding;
ALTER TABLE clips DROP CONSTRAINT IF EXISTS clips_none_requires_null_offsets;

-- Preserve legacy numeric rows as unavailable before CHECKs that require
-- provenance=none to have NULL offsets.
UPDATE clips
SET timing_provenance = 'unvalidated_numeric'
WHERE timing_provenance = 'none'
  AND video_start_s IS NOT NULL
  AND video_end_s IS NOT NULL;

-- NaN: NaN = NaN is not true. ±Inf: compare to Infinity literals.
-- No minimum clip length. Tiny positive (end > start) is allowed.
-- App still rejects bool/str before bind. See src/db/sql/POLICY.md.
ALTER TABLE clips
  ADD CONSTRAINT clips_video_finite_window CHECK (
    (video_start_s IS NULL AND video_end_s IS NULL)
    OR (
      video_start_s IS NOT NULL
      AND video_end_s IS NOT NULL
      AND video_start_s = video_start_s
      AND video_end_s = video_end_s
      AND video_start_s <> 'Infinity'::numeric
      AND video_start_s <> '-Infinity'::numeric
      AND video_end_s <> 'Infinity'::numeric
      AND video_end_s <> '-Infinity'::numeric
      AND video_start_s >= 0
      AND video_end_s > video_start_s
    )
  );

ALTER TABLE clips
  ADD CONSTRAINT clips_none_requires_null_offsets CHECK (
    timing_provenance <> 'none'
    OR (video_start_s IS NULL AND video_end_s IS NULL)
  );

ALTER TABLE clips
  ADD CONSTRAINT clips_attested_requires_binding CHECK (
    timing_provenance <> 'matcher_attested'
    OR (
      matcher_run_id IS NOT NULL
      AND length(trim(matcher_run_id)) > 0
      AND matcher_version IS NOT NULL
      AND length(trim(matcher_version)) > 0
      AND video_timestamp_s IS NOT NULL
      AND video_timestamp_s = video_timestamp_s
      AND video_timestamp_s >= 0
      AND video_start_s IS NOT NULL
      AND video_end_s IS NOT NULL
      AND matched_source_version IS NOT NULL
      AND length(trim(matched_source_version)) > 0
    )
  );

CREATE OR REPLACE VIEW play_capability AS
SELECT
  c.id AS play_id,
  (
    g.s3_key IS NOT NULL
    AND length(trim(g.s3_key)) > 0
    AND g.s3_key !~* '^(https?|s3)://'
    AND g.source_kind = 'original_private'
    AND g.source_version IS NOT NULL
    AND length(trim(g.source_version)) > 0
    AND c.timing_provenance = 'matcher_attested'
    AND c.matched_source_version IS NOT NULL
    AND c.matched_source_version = g.source_version
    AND c.video_start_s IS NOT NULL
    AND c.video_end_s IS NOT NULL
    AND c.video_start_s = c.video_start_s
    AND c.video_end_s = c.video_end_s
    AND c.video_start_s >= 0
    AND c.video_end_s > c.video_start_s
    AND c.video_start_s <> 'Infinity'::numeric
    AND c.video_end_s <> 'Infinity'::numeric
    AND c.video_start_s <> '-Infinity'::numeric
    AND c.video_end_s <> '-Infinity'::numeric
    AND g.duration_s IS NOT NULL
    AND g.duration_s = g.duration_s
    AND g.duration_s <> 'Infinity'::numeric
    AND g.duration_s <> '-Infinity'::numeric
    AND g.duration_s > 0
    AND c.video_start_s < g.duration_s
    AND c.video_end_s <= g.duration_s
    AND (c.video_end_s - c.video_start_s) <= 120
  ) AS can_create,
  CASE
    WHEN NOT (
      g.s3_key IS NOT NULL
      AND length(trim(g.s3_key)) > 0
      AND g.s3_key !~* '^(https?|s3)://'
      AND g.source_kind = 'original_private'
      AND g.source_version IS NOT NULL
      AND length(trim(g.source_version)) > 0
    ) THEN 'footage_not_matched'
    WHEN c.timing_provenance = 'none' THEN 'footage_not_matched'
    WHEN c.timing_provenance IN ('game_clock_derived', 'unvalidated_numeric')
      THEN 'video_timing_unavailable'
    WHEN NOT (
      c.timing_provenance = 'matcher_attested'
      AND c.matched_source_version IS NOT NULL
      AND c.matched_source_version = g.source_version
      AND c.video_start_s IS NOT NULL
      AND c.video_end_s IS NOT NULL
      AND c.video_start_s = c.video_start_s
      AND c.video_end_s = c.video_end_s
      AND c.video_start_s >= 0
      AND c.video_end_s > c.video_start_s
      AND c.video_start_s <> 'Infinity'::numeric
      AND c.video_end_s <> 'Infinity'::numeric
      AND c.video_start_s <> '-Infinity'::numeric
      AND c.video_end_s <> '-Infinity'::numeric
      AND g.duration_s IS NOT NULL
      AND g.duration_s = g.duration_s
      AND g.duration_s <> 'Infinity'::numeric
      AND g.duration_s <> '-Infinity'::numeric
      AND g.duration_s > 0
      AND c.video_start_s < g.duration_s
      AND c.video_end_s <= g.duration_s
      AND (c.video_end_s - c.video_start_s) <= 120
    ) THEN 'video_timing_unavailable'
    ELSE NULL
  END AS reason
FROM clips c
JOIN games g ON g.id = c.game_id;

COMMENT ON VIEW play_capability IS
  'Eligibility only. Default denied. Not an authz grant. Does not expose source keys.';
