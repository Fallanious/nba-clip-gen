import type {
  Video,
  PlayByPlayFile,
  TimestampFile,
  ClipFolder,
  OllamaStatus,
  BBRefResult,
  SuggestedPBP,
  PlaysResponse,
  Job,
  JobStartResponse,
  MatchResponse,
} from './types';

const BASE = '';

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init);
  return res.json();
}

function postJSON<T>(url: string, body: Record<string, unknown>): Promise<T> {
  return fetchJSON<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ============ Status ============

export async function fetchOllamaStatus(): Promise<OllamaStatus> {
  return fetchJSON('/api/status');
}

export async function fetchJob(jobId: string): Promise<Job> {
  return fetchJSON(`/api/job/${jobId}`);
}

export async function stopJob(jobId: string): Promise<{ message: string }> {
  return postJSON(`/api/job/${jobId}/stop`, {});
}

// ============ File Listings ============

export async function fetchVideos(): Promise<Video[]> {
  const data = await fetchJSON<{ videos: Video[] }>('/api/videos');
  return data.videos;
}

export async function fetchPlayByPlay(): Promise<PlayByPlayFile[]> {
  const data = await fetchJSON<{ files: PlayByPlayFile[] }>('/api/playbyplay');
  return data.files;
}

export async function fetchTimestamps(): Promise<TimestampFile[]> {
  const data = await fetchJSON<{ files: TimestampFile[] }>('/api/timestamps');
  return data.files;
}

export async function fetchClipRuns(): Promise<ClipFolder[]> {
  const data = await fetchJSON<{ runs: ClipFolder[] }>('/api/clip-runs');
  return data.runs;
}

export async function fetchClips(run?: string): Promise<string[]> {
  const query = run ? `?run=${encodeURIComponent(run)}` : '';
  const data = await fetchJSON<{ clips: string[] }>(`/api/clips${query}`);
  return data.clips;
}

export async function fetchBBRefUrl(videoPath: string): Promise<BBRefResult> {
  return fetchJSON(`/api/videos/${encodeURIComponent(videoPath)}/bbref`);
}

export async function fetchSuggestedPBP(videoPath: string): Promise<SuggestedPBP> {
  return fetchJSON(`/api/videos/${encodeURIComponent(videoPath)}/suggested-pbp`);
}

export async function fetchPlays(filePath: string): Promise<PlaysResponse> {
  return fetchJSON(`/api/timestamps/${encodeURIComponent(filePath)}/plays`);
}

// ============ Actions ============

export async function startDownload(url: string): Promise<JobStartResponse> {
  return postJSON('/api/download', { url });
}

export async function startScrape(url: string): Promise<JobStartResponse> {
  return postJSON('/api/scrape', { url });
}

export interface MatchParams {
  video: string;
  playbyplay: string;
  buffer: number;
  sample_interval: number;
  max_plays: number;
  start_time: number;
  num_workers: number;
}

export async function startMatch(params: MatchParams): Promise<MatchResponse> {
  return postJSON('/api/match', params as unknown as Record<string, unknown>);
}

export interface GenerateParams {
  video: string;
  timestamps: string;
  no_audio: boolean;
}

export async function startGenerate(params: GenerateParams): Promise<JobStartResponse> {
  return postJSON('/api/generate', params as unknown as Record<string, unknown>);
}

// ============ Video URL ============

export function getVideoUrl(videoPath: string, start?: number, end?: number): string {
  const filename = videoPath.replace('film/', '');
  const base = `${BASE}/api/film/${encodeURIComponent(filename)}`;
  if (start != null && end != null) {
    return `${base}#t=${start},${end}`;
  }
  return base;
}
