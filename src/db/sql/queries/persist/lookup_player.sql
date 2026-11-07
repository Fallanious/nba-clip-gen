-- Internal persist lookup. Not a public serializer.
SELECT p.id, p.bbref_id, p.full_name
FROM players p
WHERE p.bbref_id = %(bbref_id)s;
