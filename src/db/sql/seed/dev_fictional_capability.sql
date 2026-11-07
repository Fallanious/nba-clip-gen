-- FICTIONAL SAMPLE METADATA. Not real matches. Not live media.
-- Prefix fixtures/ is required; live object prefixes are forbidden here.

INSERT INTO teams (abbr, city, name)
VALUES
  ('FIC', 'Fiction', 'Samples'),
  ('QAF', 'QA', 'Fixtures')
ON CONFLICT (abbr) DO NOTHING;

INSERT INTO players (bbref_id, full_name, first_name, last_name, team_id, position, is_active)
SELECT 'ficcas01', 'Casey Fixture', 'Casey', 'Fixture', t.id, 'C', true
FROM teams t WHERE t.abbr = 'FIC'
ON CONFLICT (bbref_id) DO NOTHING;

-- Game A: original private fictional object, trusted duration 48s, version v1
INSERT INTO games (
  bbref_id, season, game_date, home_team_id, away_team_id,
  s3_key, duration_s, source_kind, source_version, is_fictional
)
SELECT
  'fic000001FIC', '2025-26', DATE '2026-01-03',
  h.id, a.id,
  'fixtures/fictional-game-a.mkv', 48, 'original_private', 'etag-fic-a-v1', true
FROM teams h, teams a
WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
ON CONFLICT (bbref_id) DO NOTHING;

-- Game B: no original object
INSERT INTO games (
  bbref_id, season, game_date, home_team_id, away_team_id,
  s3_key, duration_s, source_kind, source_version, is_fictional
)
SELECT
  'fic000002FIC', '2025-26', DATE '2026-01-04',
  h.id, a.id,
  NULL, NULL, NULL, NULL, true
FROM teams h, teams a
WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
ON CONFLICT (bbref_id) DO NOTHING;

-- Game C: public kind must never substitute
INSERT INTO games (
  bbref_id, season, game_date, home_team_id, away_team_id,
  s3_key, duration_s, source_kind, source_version, is_fictional
)
SELECT
  'fic000003FIC', '2025-26', DATE '2026-01-05',
  h.id, a.id,
  'fixtures/fictional-public-not-original.mkv', 48, 'public', 'etag-public', true
FROM teams h, teams a
WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
ON CONFLICT (bbref_id) DO NOTHING;

-- Game D: derived kind
INSERT INTO games (
  bbref_id, season, game_date, home_team_id, away_team_id,
  s3_key, duration_s, source_kind, source_version, is_fictional
)
SELECT
  'fic000004FIC', '2025-26', DATE '2026-01-06',
  h.id, a.id,
  'fixtures/fictional-derived.mkv', 48, 'derived', 'etag-derived', true
FROM teams h, teams a
WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
ON CONFLICT (bbref_id) DO NOTHING;

-- Game E: original but no trusted duration
INSERT INTO games (
  bbref_id, season, game_date, home_team_id, away_team_id,
  s3_key, duration_s, source_kind, source_version, is_fictional
)
SELECT
  'fic000005FIC', '2025-26', DATE '2026-01-07',
  h.id, a.id,
  'fixtures/fictional-no-duration.mkv', NULL, 'original_private', 'etag-nodur', true
FROM teams h, teams a
WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
ON CONFLICT (bbref_id) DO NOTHING;

INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  action_type, outcome, timing_provenance,
  video_start_s, video_end_s, video_timestamp_s,
  matcher_run_id, matcher_version, matched_source_version
)
SELECT g.id, g.game_date, 1, 1, INTERVAL '2 minutes 14 seconds',
  'FICTIONAL: Pull-up jumper, 17 ft',
  'shot', 'made', 'matcher_attested',
  0, 8, 7.5,
  'fic-run-a', 'match_play_timestamps/fictional', 'etag-fic-a-v1'
FROM games g WHERE g.bbref_id = 'fic000001FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  action_type, outcome, timing_provenance,
  video_start_s, video_end_s, video_timestamp_s,
  matcher_run_id, matcher_version, matched_source_version
)
SELECT g.id, g.game_date, 2, 1, INTERVAL '1 minutes 12 seconds',
  'FICTIONAL: Fractional window',
  'shot', 'missed', 'matcher_attested',
  0.25, 3.5, 3.0,
  'fic-run-a', 'match_play_timestamps/fictional', 'etag-fic-a-v1'
FROM games g WHERE g.bbref_id = 'fic000001FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

-- Unmatched: game clock present, offsets NULL, still browsable
INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  action_type, outcome, timing_provenance,
  video_start_s, video_end_s
)
SELECT g.id, g.game_date, 3, 2, INTERVAL '6 minutes 40 seconds',
  'FICTIONAL: Unmatched play, game clock only',
  'turnover', NULL, 'none',
  NULL, NULL
FROM games g WHERE g.bbref_id = 'fic000001FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

-- Legacy numeric without attestation (upgrade path)
INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  action_type, outcome, timing_provenance,
  video_start_s, video_end_s
)
SELECT g.id, g.game_date, 4, 3, INTERVAL '9 minutes 12 seconds',
  'FICTIONAL: Legacy numeric offsets',
  'steal', NULL, 'unvalidated_numeric',
  10, 18
FROM games g WHERE g.bbref_id = 'fic000001FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

-- Game-clock-derived numbers (explicitly forbidden)
INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  action_type, outcome, timing_provenance,
  video_start_s, video_end_s
)
SELECT g.id, g.game_date, 5, 4, INTERVAL '4 minutes 26 seconds',
  'FICTIONAL: Game-clock-derived seconds',
  'foul', NULL, 'game_clock_derived',
  12, 20
FROM games g WHERE g.bbref_id = 'fic000001FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

-- Stale source version
INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  action_type, outcome, timing_provenance,
  video_start_s, video_end_s, video_timestamp_s,
  matcher_run_id, matcher_version, matched_source_version
)
SELECT g.id, g.game_date, 6, 4, INTERVAL '0 minutes 51 seconds',
  'FICTIONAL: Stale source version',
  'shot', 'made', 'matcher_attested',
  20, 28, 27,
  'fic-run-old', 'match_play_timestamps/fictional', 'etag-fic-a-v0'
FROM games g WHERE g.bbref_id = 'fic000001FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

-- Empty-ish: game without source, unmatched row
INSERT INTO clips (
  game_id, game_date, play_index, period, game_clock, description,
  timing_provenance, video_start_s, video_end_s
)
SELECT g.id, g.game_date, 1, 1, INTERVAL '11 minutes 42 seconds',
  'FICTIONAL: No source game unmatched',
  'none', NULL, NULL
FROM games g WHERE g.bbref_id = 'fic000002FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

INSERT INTO clips (
  game_id, game_date, play_index, period, description,
  timing_provenance, video_start_s, video_end_s, video_timestamp_s,
  matcher_run_id, matcher_version, matched_source_version
)
SELECT g.id, g.game_date, 1, 1,
  'FICTIONAL: Public source never substitutes',
  'matcher_attested', 0, 8, 7,
  'fic-run-pub', 'match_play_timestamps/fictional', 'etag-public'
FROM games g WHERE g.bbref_id = 'fic000003FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

INSERT INTO clips (
  game_id, game_date, play_index, period, description,
  timing_provenance, video_start_s, video_end_s, video_timestamp_s,
  matcher_run_id, matcher_version, matched_source_version
)
SELECT g.id, g.game_date, 1, 1,
  'FICTIONAL: Derived source never substitutes',
  'matcher_attested', 0, 8, 7,
  'fic-run-der', 'match_play_timestamps/fictional', 'etag-derived'
FROM games g WHERE g.bbref_id = 'fic000004FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

INSERT INTO clips (
  game_id, game_date, play_index, period, description,
  timing_provenance, video_start_s, video_end_s, video_timestamp_s,
  matcher_run_id, matcher_version, matched_source_version
)
SELECT g.id, g.game_date, 1, 1,
  'FICTIONAL: Missing trusted duration',
  'matcher_attested', 0, 8, 7,
  'fic-run-nodur', 'match_play_timestamps/fictional', 'etag-nodur'
FROM games g WHERE g.bbref_id = 'fic000005FIC'
ON CONFLICT (game_id, play_index) DO NOTHING;

INSERT INTO clip_players (clip_id, player_id, role, game_date)
SELECT c.id, p.id, 'shot', c.game_date
FROM clips c
JOIN games g ON g.id = c.game_id
JOIN players p ON p.bbref_id = 'ficcas01'
WHERE g.bbref_id = 'fic000001FIC' AND c.play_index IN (1, 2)
ON CONFLICT DO NOTHING;
