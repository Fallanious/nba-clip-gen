-- Isolated test schema matching the 2026-11-08 DEV catalog snapshot.
-- Not a migration. Not applied to Neon.

CREATE TYPE clip_role AS ENUM (
  'shot', 'assist', 'block', 'steal', 'rebound', 'foul', 'fouled', 'turnover'
);
CREATE TYPE clip_status AS ENUM ('pending', 'rendered', 'uploaded', 'failed');

CREATE TABLE teams (
  id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  abbr varchar(3) UNIQUE NOT NULL CHECK (abbr ~ '^[A-Z]{3}$'),
  city text NOT NULL,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE players (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bbref_id text UNIQUE NOT NULL,
  full_name text NOT NULL,
  first_name text NOT NULL,
  last_name text NOT NULL,
  team_id smallint REFERENCES teams(id)
);

CREATE TABLE games (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bbref_id text UNIQUE NOT NULL,
  season text NOT NULL,
  game_date date NOT NULL,
  home_team_id smallint NOT NULL REFERENCES teams(id),
  away_team_id smallint NOT NULL REFERENCES teams(id),
  s3_key text,
  duration_s numeric(9,3),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (home_team_id <> away_team_id)
);

CREATE TABLE clips (
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
  video_start_s numeric(9,3) NOT NULL,
  video_end_s numeric(9,3) NOT NULL,
  duration_s numeric(9,3) GENERATED ALWAYS AS (video_end_s - video_start_s) STORED,
  status clip_status NOT NULL DEFAULT 'pending'::clip_status,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (game_id, play_index),
  CHECK (video_end_s > video_start_s),
  CHECK (video_start_s >= 0::numeric)
);

CREATE TABLE clip_players (
  clip_id bigint NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  player_id bigint NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  role clip_role NOT NULL,
  game_date date NOT NULL,
  PRIMARY KEY (player_id, clip_id, role)
);
