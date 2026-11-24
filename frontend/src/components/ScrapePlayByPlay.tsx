import { useEffect, useRef, useState } from 'react';
import { fetchPlayByPlay, startScrape } from '../api';
import { useJob } from '../hooks/useJob';
import type { PlayByPlayFile, Video } from '../types';
import Section from './Section';

interface Props {
  pbpFiles: PlayByPlayFile[];
  videos: Video[];
  video: string;
  chooseVideo: (path: string) => void;
  onComplete: (file: PlayByPlayFile, files: PlayByPlayFile[]) => void;
}


export default function ScrapePlayByPlay({ pbpFiles, videos, video, chooseVideo, onComplete }: Props) {
  const [manualId, setManualId] = useState<{ video: string; id: string } | null>(null);
  const gameKey = videos.find(file => file.path === video)?.parsed?.bbref_id;
  const url = manualId?.video === video ? manualId.id : gameKey ?? '';
  const [outcome, setOutcome] = useState<'idle' | 'running' | 'success' | 'failure'>('idle');
  const [message, setMessage] = useState('');
  const job = useJob();
  const checking = useRef(false);

  useEffect(() => {
    if (job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') return;
    if (checking.current) return;
    checking.current = true;

    const confirmSaved = async () => {
      try {
        if (job.status !== 'completed') {
          setOutcome('failure');
          setMessage(job.output.trim() || 'Fetch failed.');
          return;
        }
        const path = job.output.match(/^Saved (playbyplay\/nba\/\w+\.json)$/m)?.[1];
        const files = await fetchPlayByPlay();
        const saved = files.find(file => file.path === path && file.size > 2);
        if (saved) {
          setOutcome('success');
          const nbaId = job.output.match(/\((\d{10})\): \d+ NBA actions/)?.[1];
          setMessage(`Saved ${saved.path}${nbaId ? ` for NBA game ${nbaId}` : ''}`);
          onComplete(saved, files);
          return;
        }
      } catch {
        setOutcome('failure');
        setMessage('Fetch failed. Could not check the play-by-play folder.');
        return;
      }
      setOutcome('failure');
      setMessage('Fetch failed. Nothing new was saved to playbyplay.');
    };

    void confirmSaved();
  }, [job.status, job.output, onComplete]);

  const handleScrape = async (key: string) => {
    if (!key.trim() || outcome === 'running') return;
    checking.current = false;
    setOutcome('running');
    setMessage('Fetching NBA play-by-play...');
    try {
      const data = await startScrape(key.trim());
      if (data.error) {
        setOutcome('failure');
        setMessage(data.error);
        return;
      }
      job.startPolling(data.job_id);
    } catch (err) {
      setOutcome('failure');
      setMessage(err instanceof Error ? err.message : 'Fetch failed.');
    }
  };

  return (
    <Section title="1. Fetch NBA Play-by-Play">
      <div className="form-group">
        <label>Game</label>
        <select disabled={outcome === 'running'} value={video} onChange={e => {
          const path = e.target.value;
          chooseVideo(path);
          setManualId(null);
          setOutcome('idle');
          const game = videos.find(file => file.path === path)?.parsed;
          if (!game) return;
          const cached = pbpFiles.find(file => file.name === game.expected_pbp && file.size > 2);
          if (cached) {
            setOutcome('success');
            setMessage(`Using saved NBA play-by-play: ${cached.path}`);
          } else {
            void handleScrape(game.bbref_id);
          }
        }}>
          <option value="">-- Select Video --</option>
          {videos.map(v => (
            <option key={v.path} value={v.path}>
              {v.name} ({v.size_mb} MB)
            </option>
          ))}
        </select>
      </div>
      <div className="form-group">
        <label>NBA game ID or local game key</label>
        <div className="form-row">
          <input
            type="text"
            value={url}
            disabled={outcome === 'running'}
            onChange={e => { setManualId({ video, id: e.target.value }); setOutcome('idle'); }}
            placeholder="0022500209 or 202511110PHI"
          />
        </div>
        <p className="info-text">
          Selecting a video loads its saved NBA play-by-play or fetches it automatically. You can also fetch a game by NBA ID.
        </p>
      </div>
      <button onClick={() => void handleScrape(url)} disabled={outcome === 'running'}>
        Fetch NBA data
      </button>
      {outcome !== 'idle' && (
        <div className="download-status" role="status">
          {outcome === 'running' && (
            <div className="download-track" aria-hidden="true">
              <div className="download-bar" />
            </div>
          )}
          <p className={`download-message ${outcome}`}>{message}</p>
        </div>
      )}
    </Section>
  );
}
