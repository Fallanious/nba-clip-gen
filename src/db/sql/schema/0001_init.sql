-- Schema initialization: extensions, enums, tables, constraints, indexes
-- Mirrors the DBA schema on Neon dev branch as of 2026-11-01

-- Extensions
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Unaccent function
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text AS $$
  SELECT unaccent('unaccent', $1)
$$ LANGUAGE SQL IMMUTABLE;

-- Enums
CREATE TYPE clip_role AS ENUM ('shot', 'assist', 'block', 'steal', 'rebound', 'foul', 'fouled', 'turnover');
CREATE TYPE clip_status AS ENUM ('pending', 'rendered', 'uploaded', 'failed');

-- Teams table
CREATE TABLE teams (
  id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  abbr varchar(3) UNIQUE NOT NULL CHECK (abbr ~ '^[A-Z]{3}$'),
  city text NOT NULL,
  name text NOT NULL,
  created_at date NOT NULL DEFAULT CURRENT_DATE
);

-- Players table
CREATE TABLE players (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bbref_id text UNIQUE NOT NULL,
  full_name text NOT NULL,
  first_name text,
  last_name text NOT NULL,
  position text,
  team_id smallint REFERENCES teams(id) ON DELETE SET NULL,
  headshot_url text,
  is_active boolean NOT NULL DEFAULT true,
  created_at date NOT NULL DEFAULT CURRENT_DATE,
  updated_at date NOT NULL DEFAULT CURRENT_DATE
);

CREATE INDEX players_last_name ON players(last_name, id);
CREATE INDEX players_team ON players(team_id);
CREATE INDEX players_name_trgm ON players USING gin (lower(f_unaccent(full_name)) gin_trgm_ops);

-- Games table
CREATE TABLE games (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bbref_id text UNIQUE NOT NULL,
  game_date date NOT NULL,
  season text NOT NULL,
  home_team_id smallint NOT NULL REFERENCES teams(id),
  away_team_id smallint NOT NULL REFERENCES teams(id),
  s3_key text,
  duration_s numeric,
  created_at date NOT NULL DEFAULT CURRENT_DATE,
  CHECK (home_team_id != away_team_id)
);

CREATE INDEX games_date ON games(game_date DESC);
CREATE INDEX games_home ON games(home_team_id);
CREATE INDEX games_away ON games(away_team_id);

-- Clips table
CREATE TABLE clips (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id bigint NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  game_date date NOT NULL,
  play_index int NOT NULL,
  period smallint NOT NULL CHECK (period >= 1 AND period <= 10),
  game_clock interval,
  team_id smallint REFERENCES teams(id),
  description text NOT NULL,
  action_type text,
  shot_subtype text,
  outcome text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  video_start_s numeric NOT NULL,
  video_end_s numeric NOT NULL,
  duration_s numeric,
  s3_key text UNIQUE,
  status clip_status NOT NULL DEFAULT 'pending'::clip_status,
  created_at date NOT NULL DEFAULT CURRENT_DATE,
  updated_at date NOT NULL DEFAULT CURRENT_DATE,
  CHECK (video_end_s > video_start_s AND video_start_s >= 0),
  UNIQUE (game_id, play_index)
);

CREATE INDEX clips_game_period ON clips(game_id, period, game_clock DESC);
CREATE INDEX clips_team ON clips(team_id);
CREATE INDEX clips_status_pending ON clips(status) WHERE status != 'uploaded';

-- Clip players junction table
CREATE TABLE clip_players (
  player_id bigint NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  clip_id bigint NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  role clip_role NOT NULL,
  game_date date NOT NULL,
  PRIMARY KEY (player_id, clip_id, role)
);

CREATE INDEX clip_players_player_date ON clip_players(player_id, game_date DESC, clip_id DESC);
CREATE INDEX clip_players_clip ON clip_players(clip_id);
