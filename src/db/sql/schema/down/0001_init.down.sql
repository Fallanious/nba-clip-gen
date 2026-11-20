-- Drop baseline objects. Local empty-DB tests only.

DROP TABLE IF EXISTS clip_players;
DROP TABLE IF EXISTS clips;
DROP TABLE IF EXISTS games;
DROP TABLE IF EXISTS players;
DROP TABLE IF EXISTS teams;
DROP TABLE IF EXISTS schema_migrations;

DROP TYPE IF EXISTS clip_role;
DROP TYPE IF EXISTS clip_status;
DROP TYPE IF EXISTS source_kind;
DROP TYPE IF EXISTS timing_provenance;
