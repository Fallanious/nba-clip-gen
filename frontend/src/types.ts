// ============ API Response Types ============

export interface Video {
  name: string;
  path: string;
  size_mb: number;
  parsed: ParsedVideo | null;
}

export interface ParsedVideo {
  away_team: string;
  home_team: string;
  home_code: string;
  date: string;
  bbref_url: string;
  expected_pbp: string;
}

export interface PlayByPlayFile {
  name: string;
  path: string;
}

export interface TimestampFile {
  name: string;
  path: string;
  play_count: number;
}

export interface ClipFolder {
  name: string;
  path: string;
  clip_count: number;
}

export interface Attributes {
  action_type: string;
  shot_subtype: string | null;
  outcome: string | null;
  shot_distance_bucket: string | null;
  assisted: string | null;
  team: string | null;
}

export interface Clip {
  filename: string;
  path: string;
  primary: string;
  attributes: Attributes;
  play_index: number;
  team: string;
  quarter: number;
  game_time: string;
  description: string;
  video_start: number;
  video_end: number;
  duration: number;
  file_size: string;
}

export interface Play {
  play_index: number;
  team: string;
  description: string;
  quarter: number;
  game_time: string;
  score: string;
  video_timestamp: number;
  video_start: number;
  video_end: number;
  primary_action?: string;
  is_compound: boolean;
  play_count: number;
}

export interface Job {
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  output: string;
  error: string | null;
  return_code: number | null;
  pid: number | null;
}

export interface OllamaStatus {
  status: 'online' | 'offline';
  models: string[];
}

export interface BBRefResult {
  away_team?: string;
  home_team?: string;
  home_code?: string;
  date?: string;
  bbref_url?: string;
  expected_pbp?: string;
  error?: string;
}

export interface SuggestedPBP {
  suggested: string | null;
  filename?: string;
  game_info?: string;
  expected?: string;
  bbref_url?: string;
  reason?: string;
}

export interface PlaysResponse {
  plays: Play[];
  file: string;
  video: string | null;
}

export interface MatchResponse {
  job_id: string;
  job_ids: string[];
  message: string;
  error?: string;
}

export interface JobStartResponse {
  job_id: string;
  message: string;
  error?: string;
}
