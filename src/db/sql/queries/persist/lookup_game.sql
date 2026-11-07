-- Internal persist lookup. Not a public serializer.
SELECT
  g.id,
  g.bbref_id,
  g.game_date,
  g.s3_key,
  g.source_kind,
  g.source_version,
  g.duration_s,
  g.is_fictional
FROM games g
WHERE g.bbref_id = %(bbref_id)s
FOR UPDATE;
