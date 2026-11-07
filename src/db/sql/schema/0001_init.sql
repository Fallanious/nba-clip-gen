-- Minimal local baseline for slice A: teams, players, games, clips, clip_players.
-- Core tables/constraints 0003 and capability tests need. Not a copy of players-search
-- (pg_trgm/unaccent) or old HTTP APIs. Extra Neon columns are allowed as a superset
-- when verifying an already-created database; they are not created here.

DO $$ BEGIN
  CREATE TYPE clip_role AS ENUM (
    'shot', 'assist', 'block', 'steal', 'rebound', 'foul', 'fouled', 'turnover'
  );
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
  created_at timestamptz NOT NULL DEFAULT now()
);

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
  CHECK (home_team_id <> away_team_id),
  -- PostgreSQL numeric NaN equals itself and sorts above finite values.
  CHECK (
    duration_s IS NULL OR (
      duration_s <> 'NaN'::numeric
      AND duration_s <> 'Infinity'::numeric
      AND duration_s <> '-Infinity'::numeric
      AND duration_s > 0
    )
  )
);

CREATE TABLE IF NOT EXISTS clips (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  game_id bigint NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  game_date date NOT NULL,
  play_index integer NOT NULL,
  period smallint NOT NULL CHECK (period >= 1 AND period <= 10),
  game_clock interval,
  description text NOT NULL,
  action_type text,
  shot_subtype text,
  outcome text,
  video_start_s numeric NOT NULL,
  video_end_s numeric NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (game_id, play_index),
  CHECK (video_end_s > video_start_s),
  CHECK (video_start_s >= 0),
  CHECK (
    video_start_s <> 'NaN'::numeric
    AND video_end_s <> 'NaN'::numeric
    AND video_start_s <> 'Infinity'::numeric
    AND video_end_s <> 'Infinity'::numeric
    AND video_start_s <> '-Infinity'::numeric
    AND video_end_s <> '-Infinity'::numeric
  )
);

CREATE TABLE IF NOT EXISTS clip_players (
  clip_id bigint NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  player_id bigint NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  role clip_role NOT NULL,
  game_date date NOT NULL,
  PRIMARY KEY (player_id, clip_id, role)
);
