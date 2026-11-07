-- Baseline matching Neon nba-clip-gen / branch dev as inspected 2026-11-07.
-- Apply only to empty databases (local tests). Do not CREATE on existing Neon.
-- Live Neon already has these objects; the runner stamps 0001 in that case.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text AS $$
  SELECT unaccent('unaccent', $1)
$$ LANGUAGE SQL IMMUTABLE;

DO $$ BEGIN
  CREATE TYPE clip_role AS ENUM (
    'shot', 'assist', 'block', 'steal', 'rebound', 'foul', 'fouled', 'turnover'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE clip_status AS ENUM ('pending', 'rendered', 'uploaded', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS teams (
  id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  abbr varchar(3) UNIQUE NOT NULL CHECK (abbr ~ '^[A-Z]{3}$'),
  city text NOT NULL,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS players (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bbref_id text UNIQUE NOT NULL,
  full_name text NOT NULL,
  first_name text,
  last_name text NOT NULL,
  team_id smallint REFERENCES teams(id) ON DELETE SET NULL,
  position text,
  headshot_url text,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS players_last_name ON players(last_name, id);
CREATE INDEX IF NOT EXISTS players_team ON players(team_id);
CREATE INDEX IF NOT EXISTS players_name_trgm
  ON players USING gin (lower(f_unaccent(full_name)) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS games (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bbref_id text UNIQUE NOT NULL,
  season text NOT NULL,
  game_date date NOT NULL,
  home_team_id smallint NOT NULL REFERENCES teams(id),
  away_team_id smallint NOT NULL REFERENCES teams(id),
  s3_key text,
  duration_s numeric,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (home_team_id <> away_team_id)
);

CREATE INDEX IF NOT EXISTS games_date ON games(game_date DESC);
CREATE INDEX IF NOT EXISTS games_home ON games(home_team_id);
CREATE INDEX IF NOT EXISTS games_away ON games(away_team_id);

CREATE TABLE IF NOT EXISTS clips (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id bigint NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id smallint REFERENCES teams(id),
  game_date date NOT NULL,
  play_index integer NOT NULL,
  period smallint NOT NULL CHECK (period >= 1 AND period <= 10),
  game_clock interval,
  description text NOT NULL,
  action_type text,
  shot_subtype text,
  outcome text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  s3_key text UNIQUE,
  video_start_s numeric NOT NULL,
  video_end_s numeric NOT NULL,
  duration_s numeric,
  status clip_status NOT NULL DEFAULT 'pending'::clip_status,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (game_id, play_index),
  CHECK (video_end_s > video_start_s),
  CHECK (video_start_s >= 0)
);

CREATE INDEX IF NOT EXISTS clips_game_period ON clips(game_id, period, game_clock DESC);
CREATE INDEX IF NOT EXISTS clips_team ON clips(team_id);
CREATE INDEX IF NOT EXISTS clips_status_pending
  ON clips(status) WHERE (status <> 'uploaded'::clip_status);

CREATE TABLE IF NOT EXISTS clip_players (
  clip_id bigint NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  player_id bigint NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  role clip_role NOT NULL,
  game_date date NOT NULL,
  PRIMARY KEY (player_id, clip_id, role)
);

CREATE INDEX IF NOT EXISTS clip_players_player_date
  ON clip_players(player_id, game_date DESC, clip_id DESC);
CREATE INDEX IF NOT EXISTS clip_players_clip ON clip_players(clip_id);
